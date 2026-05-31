#!/usr/bin/env python3
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import threading
import uuid

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("opencode-coder")

TAIL_LINES = 200
MAX_TAIL_CHARS = 24_000
DEFAULT_WAIT_SECONDS = 120.0
DEFAULT_MAX_MCP_WAIT_SECONDS = 110.0
DEFAULT_FINISHED_JOB_TTL_SECONDS = 3600.0
MAX_FINISHED_JOBS = 100
ACTIVE_STATUSES = {"starting", "running", "timed_out"}

_JOBS: dict[str, "OpenCodeJob"] = {}
_CWD_ACTIVE_JOBS: dict[str, set[str]] = {}
_REGISTRY_LOCK = threading.RLock()


@dataclass
class OpenCodeJob:
    job_id: str
    working_dir: str
    cwd_key: str
    command: list[str]
    command_summary: str
    requested_timeout_seconds: float
    effective_timeout_seconds: float
    timeout_policy: str
    status: str = "starting"
    pid: int | None = None
    exit_code: int | None = None
    started_at: str = field(default_factory=lambda: utc_now())
    finished_at: str | None = None
    process: subprocess.Popen[str] | None = None
    stdout_tail: deque[str] = field(default_factory=lambda: deque(maxlen=TAIL_LINES))
    stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=TAIL_LINES))
    changed_files: list[str] = field(default_factory=list)
    summary: str = ""
    error: str | None = None
    done_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)


def resolve_opencode() -> str:
    cmd_path = shutil.which("opencode.cmd")
    if cmd_path:
        exe_path = os.path.join(
            os.path.dirname(cmd_path),
            "node_modules",
            "opencode-ai",
            "bin",
            "opencode.exe",
        )
        if os.path.exists(exe_path):
            return exe_path

    return shutil.which("opencode") or shutil.which("opencode.cmd") or "opencode"


def build_opencode_command(prompt: str) -> list[str]:
    return [
        resolve_opencode(),
        "run",
        "--format",
        "json",
        "--dangerously-skip-permissions",
        prompt,
    ]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def env_float(name: str, default_value: float) -> float:
    value = os.environ.get(name)
    if not value:
        return default_value
    try:
        parsed = float(value)
    except ValueError:
        return default_value
    return max(0.0, parsed)


def compute_effective_timeout(timeout_seconds: float | int | None) -> tuple[float, float, str]:
    requested = DEFAULT_WAIT_SECONDS if timeout_seconds is None else float(timeout_seconds)
    requested = max(0.0, requested)

    max_wait = env_float("OPENCODE_CODER_MAX_WAIT_SECONDS", DEFAULT_MAX_MCP_WAIT_SECONDS)
    effective = min(requested, max_wait)
    if requested > effective:
        policy = (
            "capped_by_wrapper_to_return_before_mcp_client_timeout; "
            "job_continues_in_background"
        )
    else:
        policy = "requested_timeout_seconds"
    return requested, effective, policy


def normalize_working_dir(working_dir: str) -> tuple[str, str]:
    path = Path(working_dir).expanduser()
    resolved = path.resolve(strict=False)
    normalized = os.path.normcase(os.path.realpath(str(resolved)))
    return str(resolved), normalized


def summarize_command(cmd: list[str], prompt: str) -> str:
    prompt_hash = hashlib.sha256(prompt.encode("utf-8", errors="replace")).hexdigest()[:12]
    if not cmd:
        return "<empty command>"
    display = [Path(cmd[0]).name, *cmd[1:-1], f"<prompt chars={len(prompt)} sha256={prompt_hash}>"]
    return " ".join(display)


def trim_tail(text: str) -> str:
    if len(text) <= MAX_TAIL_CHARS:
        return text
    return text[-MAX_TAIL_CHARS:]


def tail_to_text(lines: deque[str]) -> str:
    return trim_tail("".join(lines))


def append_tail(job: OpenCodeJob, kind: str, text: str) -> None:
    if len(text) > MAX_TAIL_CHARS:
        text = text[-MAX_TAIL_CHARS:]
    with job.lock:
        if kind == "stdout":
            job.stdout_tail.append(text)
        else:
            job.stderr_tail.append(text)


def read_stream(job: OpenCodeJob, stream, kind: str) -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            append_tail(job, kind, line)
    except Exception as exc:  # pragma: no cover - defensive reader guard
        append_tail(job, "stderr", f"[opencode_coder] Failed to read {kind}: {exc}\n")
    finally:
        try:
            stream.close()
        except Exception:
            pass


def is_job_active(job: OpenCodeJob) -> bool:
    with job.lock:
        if job.status in ACTIVE_STATUSES:
            return True
        if job.process is not None and job.process.poll() is None:
            return True
    return False


def release_cwd_lock(job: OpenCodeJob) -> None:
    with _REGISTRY_LOCK:
        active_jobs = _CWD_ACTIVE_JOBS.get(job.cwd_key)
        if not active_jobs:
            return
        active_jobs.discard(job.job_id)
        if not active_jobs:
            _CWD_ACTIVE_JOBS.pop(job.cwd_key, None)


def find_active_job_for_cwd_locked(cwd_key: str) -> OpenCodeJob | None:
    active_jobs = _CWD_ACTIVE_JOBS.get(cwd_key)
    if not active_jobs:
        return None

    stale_ids: list[str] = []
    for job_id in list(active_jobs):
        job = _JOBS.get(job_id)
        if job is None:
            stale_ids.append(job_id)
            continue
        if is_job_active(job):
            return job
        stale_ids.append(job_id)

    for job_id in stale_ids:
        active_jobs.discard(job_id)
    if not active_jobs:
        _CWD_ACTIVE_JOBS.pop(cwd_key, None)
    return None


def collect_changed_files(working_dir: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", working_dir, "status", "--porcelain=v1", "--untracked-files=all"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except Exception:
        return []

    if result.returncode != 0:
        return []

    changed: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed.append(path.strip().strip('"'))
    return changed


def finish_job(job: OpenCodeJob, exit_code: int | None, error: str | None = None) -> None:
    changed_files = collect_changed_files(job.working_dir)
    with job.lock:
        job.exit_code = exit_code
        job.finished_at = utc_now()
        job.changed_files = changed_files
        job.error = error
        if error is not None:
            job.status = "failed"
            job.summary = f"OpenCode process failed in wrapper: {error}"
        elif exit_code == 0:
            job.status = "completed"
            job.summary = "OpenCode completed successfully."
        else:
            job.status = "failed"
            job.summary = f"OpenCode exited with non-zero code {exit_code}."
        job.done_event.set()
    release_cwd_lock(job)
    cleanup_jobs()


def monitor_job(job: OpenCodeJob, reader_threads: list[threading.Thread]) -> None:
    exit_code: int | None = None
    error: str | None = None
    try:
        if job.process is None:
            error = "process was not started"
        else:
            exit_code = job.process.wait()
    except Exception as exc:  # pragma: no cover - defensive process guard
        error = str(exc)

    for thread in reader_threads:
        thread.join(timeout=2)

    finish_job(job, exit_code, error)


def cleanup_jobs() -> None:
    ttl_seconds = env_float("OPENCODE_CODER_FINISHED_JOB_TTL_SECONDS", DEFAULT_FINISHED_JOB_TTL_SECONDS)
    now_timestamp = datetime.now(timezone.utc).timestamp()
    with _REGISTRY_LOCK:
        finished_jobs = [
            job
            for job in _JOBS.values()
            if job.done_event.is_set() and job.finished_at is not None
        ]

        remove_ids: set[str] = set()
        for job in finished_jobs:
            try:
                finished_timestamp = datetime.fromisoformat(
                    job.finished_at.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                continue
            if now_timestamp - finished_timestamp > ttl_seconds:
                remove_ids.add(job.job_id)

        remaining_finished = [job for job in finished_jobs if job.job_id not in remove_ids]
        if len(remaining_finished) > MAX_FINISHED_JOBS:
            remaining_finished.sort(key=lambda item: item.finished_at or "")
            overflow = len(remaining_finished) - MAX_FINISHED_JOBS
            remove_ids.update(job.job_id for job in remaining_finished[:overflow])

        for job_id in remove_ids:
            job = _JOBS.pop(job_id, None)
            if job is not None:
                active_jobs = _CWD_ACTIVE_JOBS.get(job.cwd_key)
                if active_jobs:
                    active_jobs.discard(job_id)
                    if not active_jobs:
                        _CWD_ACTIVE_JOBS.pop(job.cwd_key, None)


def job_to_result(
    job: OpenCodeJob,
    *,
    lock_rejected: bool = False,
    new_job_started: bool = True,
    summary_override: str | None = None,
) -> dict:
    with job.lock:
        stdout_tail = tail_to_text(job.stdout_tail)
        stderr_tail = tail_to_text(job.stderr_tail)
        process_running = job.process is not None and job.process.poll() is None
        output = trim_tail((stdout_tail + "\n" + stderr_tail).strip())
        status = job.status
        success = status == "completed" and job.exit_code == 0
        summary = summary_override or job.summary
        if not summary:
            if status == "timed_out":
                summary = (
                    "OpenCode is still running after the MCP wait window. "
                    "Call opencode_coder_status with job_id for the final result."
                )
            elif status in ACTIVE_STATUSES:
                summary = "OpenCode is still running."
            elif status == "failed":
                summary = "OpenCode failed."

        return {
            "job_id": job.job_id,
            "status": status,
            "working_dir": job.working_dir,
            "pid": job.pid,
            "exit_code": job.exit_code,
            "summary": summary,
            "changed_files": list(job.changed_files),
            "tests_run": [],
            "validation_skipped_reason": "MCP wrapper does not run validation; inspect OpenCode output or task-level tooling.",
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "command": job.command_summary,
            "requested_timeout_seconds": job.requested_timeout_seconds,
            "effective_timeout_seconds": job.effective_timeout_seconds,
            "timeout_policy": job.timeout_policy,
            "process_running": process_running,
            "lock_rejected": lock_rejected,
            "new_job_started": new_job_started,
            "success": success,
            "output": output,
            "return_code": job.exit_code,
            "error": job.error,
        }


def make_start_failed_result(
    *,
    working_dir: str,
    cwd_key: str,
    command: list[str],
    command_summary: str,
    requested_timeout_seconds: float,
    effective_timeout_seconds: float,
    timeout_policy: str,
    error: str,
) -> dict:
    job = OpenCodeJob(
        job_id=uuid.uuid4().hex,
        working_dir=working_dir,
        cwd_key=cwd_key,
        command=command,
        command_summary=command_summary,
        requested_timeout_seconds=requested_timeout_seconds,
        effective_timeout_seconds=effective_timeout_seconds,
        timeout_policy=timeout_policy,
        status="failed",
        exit_code=None,
        finished_at=utc_now(),
        summary=f"OpenCode job could not be started: {error}",
        error=error,
    )
    job.done_event.set()
    with _REGISTRY_LOCK:
        _JOBS[job.job_id] = job
    return job_to_result(job)


def start_job(
    prompt: str,
    working_dir: str,
    timeout_seconds: float | int | None,
    allow_concurrent: bool,
) -> tuple[OpenCodeJob | None, dict | None]:
    cleanup_jobs()
    requested_timeout, effective_timeout, timeout_policy = compute_effective_timeout(timeout_seconds)
    resolved_working_dir, cwd_key = normalize_working_dir(working_dir)
    command = build_opencode_command(prompt)
    command_summary = summarize_command(command, prompt)

    if not Path(resolved_working_dir).is_dir():
        result = make_start_failed_result(
            working_dir=resolved_working_dir,
            cwd_key=cwd_key,
            command=command,
            command_summary=command_summary,
            requested_timeout_seconds=requested_timeout,
            effective_timeout_seconds=effective_timeout,
            timeout_policy=timeout_policy,
            error=f"working_dir does not exist or is not a directory: {resolved_working_dir}",
        )
        return None, result

    with _REGISTRY_LOCK:
        existing_job = find_active_job_for_cwd_locked(cwd_key)
        if existing_job is not None and not allow_concurrent:
            result = job_to_result(
                existing_job,
                lock_rejected=True,
                new_job_started=False,
                summary_override=(
                    "A job is already running for this working_dir; "
                    "no new OpenCode process was started. "
                    "Use opencode_coder_status with job_id, or pass allow_concurrent=true if intentional."
                ),
            )
            return existing_job, result

        job = OpenCodeJob(
            job_id=uuid.uuid4().hex,
            working_dir=resolved_working_dir,
            cwd_key=cwd_key,
            command=command,
            command_summary=command_summary,
            requested_timeout_seconds=requested_timeout,
            effective_timeout_seconds=effective_timeout,
            timeout_policy=timeout_policy,
            summary="OpenCode process is starting.",
        )
        _JOBS[job.job_id] = job
        _CWD_ACTIVE_JOBS.setdefault(cwd_key, set()).add(job.job_id)

    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            command,
            cwd=resolved_working_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
    except Exception as exc:
        finish_job(job, None, str(exc))
        return None, job_to_result(job)

    with job.lock:
        job.process = process
        job.pid = process.pid
        job.status = "running"
        job.summary = "OpenCode is running."

    stdout_thread = threading.Thread(
        target=read_stream,
        args=(job, process.stdout, "stdout"),
        name=f"opencode-coder-{job.job_id}-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=read_stream,
        args=(job, process.stderr, "stderr"),
        name=f"opencode-coder-{job.job_id}-stderr",
        daemon=True,
    )
    reader_threads = [stdout_thread, stderr_thread]
    for thread in reader_threads:
        thread.start()

    monitor_thread = threading.Thread(
        target=monitor_job,
        args=(job, reader_threads),
        name=f"opencode-coder-{job.job_id}-monitor",
        daemon=True,
    )
    monitor_thread.start()
    return job, None


@mcp.tool()
def opencode_coder(
    prompt: str,
    working_dir: str = ".",
    timeout_seconds: float = DEFAULT_WAIT_SECONDS,
    allow_concurrent: bool = False,
) -> dict:
    """调用 OpenCode 在指定项目目录里编写或修改代码，并返回可查询的结构化 job 结果。"""
    job, early_result = start_job(prompt, working_dir, timeout_seconds, allow_concurrent)
    if early_result is not None:
        return early_result
    if job is None:
        raise RuntimeError("opencode_coder internal error: no job and no result")

    if not job.done_event.wait(job.effective_timeout_seconds):
        with job.lock:
            if job.status in {"starting", "running"}:
                job.status = "timed_out"
                job.summary = (
                    "OpenCode is still running after the MCP wait window. "
                    "Call opencode_coder_status with job_id for the final result."
                )
        return job_to_result(job)

    return job_to_result(job)


@mcp.tool()
def opencode_coder_status(job_id: str) -> dict:
    """通过 job_id 查询 opencode_coder 后台任务状态和输出尾部。"""
    cleanup_jobs()
    with _REGISTRY_LOCK:
        job = _JOBS.get(job_id)

    if job is None:
        return {
            "job_id": job_id,
            "status": "not_found",
            "working_dir": None,
            "pid": None,
            "exit_code": None,
            "summary": (
                "Job id was not found. It may be invalid, belong to another MCP server process, "
                "or have expired from the in-memory retention window."
            ),
            "changed_files": [],
            "tests_run": [],
            "validation_skipped_reason": "No job was found to validate.",
            "stdout_tail": "",
            "stderr_tail": "",
            "started_at": None,
            "finished_at": None,
            "command": None,
            "requested_timeout_seconds": None,
            "effective_timeout_seconds": None,
            "timeout_policy": None,
            "process_running": False,
            "lock_rejected": False,
            "new_job_started": False,
            "success": False,
            "output": "",
            "return_code": None,
            "error": "job_not_found",
        }

    if job.process is not None and job.process.poll() is not None and not job.done_event.is_set():
        job.done_event.wait(timeout=0.2)
    return job_to_result(job, new_job_started=False)


def _reset_jobs_for_tests() -> None:
    with _REGISTRY_LOCK:
        jobs = list(_JOBS.values())

    for job in jobs:
        process = job.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    with _REGISTRY_LOCK:
        _JOBS.clear()
        _CWD_ACTIVE_JOBS.clear()


if __name__ == "__main__":
    mcp.run()
