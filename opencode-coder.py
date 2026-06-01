#!/usr/bin/env python3
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("opencode-coder")

TAIL_LINES = 120
MAX_TAIL_CHARS = 12_000
MAX_DELTA_BUFFER_CHARS = 64_000
DEFAULT_WAIT_SECONDS = 120.0
DEFAULT_MAX_MCP_WAIT_SECONDS = 110.0
DEFAULT_FINISHED_JOB_TTL_SECONDS = 3600.0
MAX_STATUS_WAIT_SECONDS = 30.0
DEFAULT_CANCEL_GRACE_SECONDS = 5.0
MAX_FINISHED_JOBS = 100
DEFAULT_DIFF_MAX_CHARS = 20_000
MAX_DIFF_CHARS = 200_000
ACTIVE_STATUSES = {"starting", "running", "timed_out"}
VALID_WAIT_POLICIES = {"completion", "start_only", "first_output", "first_change"}
SERVER_REGISTRY_VERSION = 1
SERVER_REGISTRY_ENV_VAR = "OPENCODE_CODER_REGISTRY_PATH"

_JOBS: dict[str, "OpenCodeJob"] = {}
_CWD_ACTIVE_JOBS: dict[str, set[str]] = {}
_REGISTRY_LOCK = threading.RLock()
_SERVERS: dict[str, "OpenCodeServer"] = {}
_SERVER_REGISTRY_LOCK = threading.RLock()


@dataclass
class GitStatusSnapshot:
    available: bool
    files: list[str] = field(default_factory=list)
    fingerprints: dict[str, str] = field(default_factory=dict)
    error: str | None = None


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
    wait_policy: str = "completion"
    allowed_paths: list[str] | None = None
    forbidden_paths: list[str] | None = None
    server_id: str | None = None
    server_url: str | None = None
    session_id: str | None = None
    attached_to_server: bool = False
    server_recovered_from_registry: bool = False
    status: str = "starting"
    pid: int | None = None
    exit_code: int | None = None
    started_at: str = field(default_factory=lambda: utc_now())
    finished_at: str | None = None
    process: subprocess.Popen[str] | None = None
    stdout_tail: deque[str] = field(default_factory=lambda: deque(maxlen=TAIL_LINES))
    stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=TAIL_LINES))
    stdout_buffer: str = ""
    stderr_buffer: str = ""
    stdout_buffer_start: int = 0
    stderr_buffer_start: int = 0
    stdout_cursor: int = 0
    stderr_cursor: int = 0
    preexisting_changed_files: list[str] = field(default_factory=list)
    preexisting_file_fingerprints: dict[str, str] = field(default_factory=dict)
    all_changed_files: list[str] = field(default_factory=list)
    new_changed_files: list[str] = field(default_factory=list)
    observed_change_fingerprints: dict[str, str | None] = field(default_factory=dict)
    changed_files: list[str] = field(default_factory=list)
    policy_violation: bool = False
    extra_changed_files: list[str] = field(default_factory=list)
    forbidden_changed_files: list[str] = field(default_factory=list)
    git_status_available: bool = False
    git_status_error: str | None = None
    first_output_at: str | None = None
    first_change_at: str | None = None
    last_activity_at: str | None = None
    output_version: int = 0
    change_version: int = 0
    cancel_requested: bool = False
    cancel_signal_sent: bool = False
    cancel_kill_sent: bool = False
    process_tree_kill_attempted: bool = False
    process_tree_kill_succeeded: bool = False
    process_tree_kill_error: str | None = None
    summary: str = ""
    error: str | None = None
    output_event: threading.Event = field(default_factory=threading.Event)
    change_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)


@dataclass
class OpenCodeServer:
    server_id: str
    url: str
    hostname: str
    port: int
    working_dir: str
    command: list[str]
    status: str = "starting"
    pid: int | None = None
    exit_code: int | None = None
    started_at: str = field(default_factory=lambda: utc_now())
    finished_at: str | None = None
    process: subprocess.Popen[str] | None = None
    stdout_tail: deque[str] = field(default_factory=lambda: deque(maxlen=TAIL_LINES))
    stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=TAIL_LINES))
    error: str | None = None
    stop_requested: bool = False
    process_tree_kill_attempted: bool = False
    process_tree_kill_succeeded: bool = False
    process_tree_kill_error: str | None = None
    recovered_from_registry: bool = False
    registry_path: str | None = None
    registry_error: str | None = None
    done_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)


@dataclass
class ProcessTreeKillResult:
    attempted: bool = False
    succeeded: bool = False
    error: str | None = None


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


def build_opencode_command(
    prompt: str,
    *,
    working_dir: str | None = None,
    server_url: str | None = None,
    session_id: str | None = None,
    continue_last: bool = False,
    fork_session: bool = False,
    title: str | None = None,
) -> list[str]:
    cmd = [
        resolve_opencode(),
        "run",
    ]
    if server_url is not None:
        cmd.extend(["--attach", server_url])
        if working_dir is not None:
            cmd.extend(["--dir", working_dir])

    cmd.extend([
        "--format",
        "json",
        "--dangerously-skip-permissions",
    ])

    if session_id:
        cmd.extend(["--session", session_id])
    if continue_last:
        cmd.append("--continue")
    if fork_session:
        cmd.append("--fork")
    if title:
        cmd.extend(["--title", title])

    cmd.append(prompt)
    return cmd


def build_opencode_server_command(hostname: str, port: int) -> list[str]:
    return [
        resolve_opencode(),
        "serve",
        "--hostname",
        hostname,
        "--port",
        str(port),
    ]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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


def popen_platform_kwargs() -> dict:
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": creationflags} if creationflags else {}
    return {"start_new_session": True}


def process_tree_kill(process: subprocess.Popen, timeout_seconds: float = 5.0) -> ProcessTreeKillResult:
    if process is None or process.pid is None:
        return ProcessTreeKillResult(error="process_not_available")
    if process.poll() is not None:
        return ProcessTreeKillResult()

    result = ProcessTreeKillResult(attempted=True)
    pid = int(process.pid)
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            if completed.returncode != 0:
                output = (completed.stderr or completed.stdout or "").strip()
                result.error = output or f"taskkill exited with code {completed.returncode}"
        else:
            os.killpg(pid, signal.SIGKILL)

        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            result.error = result.error or "process did not exit after process tree kill"

        result.succeeded = process.poll() is not None
        if result.succeeded and result.error:
            result.error = None
        return result
    except ProcessLookupError:
        result.succeeded = process.poll() is not None
        if not result.succeeded:
            result.error = "process tree was not found"
        return result
    except subprocess.TimeoutExpired as exc:
        result.error = f"process tree kill command timed out: {exc}"
        result.succeeded = process.poll() is not None
        return result
    except Exception as exc:
        result.error = str(exc)
        result.succeeded = process.poll() is not None
        return result


def record_process_tree_kill_result(target, result: ProcessTreeKillResult) -> None:
    with target.lock:
        if result.attempted:
            target.process_tree_kill_attempted = True
        if result.succeeded:
            target.process_tree_kill_succeeded = True
        if result.error:
            target.process_tree_kill_error = result.error


def normalize_wait_policy(wait_policy: str | None) -> str:
    if wait_policy in VALID_WAIT_POLICIES:
        return wait_policy
    return "completion"


def clamp_wait_seconds(wait_seconds: float | int | None) -> float:
    if wait_seconds is None:
        return 0.0
    try:
        parsed = float(wait_seconds)
    except (TypeError, ValueError):
        return 0.0
    return min(max(parsed, 0.0), MAX_STATUS_WAIT_SECONDS)


def clamp_diff_max_chars(max_chars) -> int:
    try:
        parsed = int(max_chars)
    except (TypeError, ValueError):
        return DEFAULT_DIFF_MAX_CHARS
    return min(max(parsed, 0), MAX_DIFF_CHARS)


def get_server_registry_path() -> str:
    override = os.environ.get(SERVER_REGISTRY_ENV_VAR)
    if override:
        return str(Path(override).expanduser().resolve(strict=False))

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or tempfile.gettempdir()
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return str(Path(base) / "LiteOpenCodeMcp" / "opencode_coder_registry.json")


def empty_server_registry() -> dict:
    return {
        "version": SERVER_REGISTRY_VERSION,
        "servers": {},
    }


def load_server_registry_unlocked() -> tuple[dict, str | None]:
    registry_path = Path(get_server_registry_path())
    if not registry_path.exists():
        return empty_server_registry(), None

    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("registry root must be a JSON object")
        servers = data.get("servers")
        if not isinstance(servers, dict):
            servers = {}
        return {
            "version": data.get("version", SERVER_REGISTRY_VERSION),
            "servers": servers,
        }, None
    except Exception as exc:
        return empty_server_registry(), f"failed to read registry {registry_path}: {exc}"


def write_server_registry_unlocked(data: dict) -> str | None:
    registry_path = Path(get_server_registry_path())
    tmp_path = registry_path.with_name(f"{registry_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SERVER_REGISTRY_VERSION,
            "servers": data.get("servers", {}),
        }
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_path, registry_path)
        return None
    except Exception as exc:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        return f"failed to write registry {registry_path}: {exc}"


def server_to_registry_record(server: OpenCodeServer) -> dict:
    with server.lock:
        return {
            "server_id": server.server_id,
            "url": server.url,
            "hostname": server.hostname,
            "port": server.port,
            "working_dir": server.working_dir,
            "pid": server.pid,
            "started_at": server.started_at,
            "command": list(server.command),
            "command_summary": " ".join(server.command),
        }


def persist_server_record(server: OpenCodeServer) -> None:
    with _SERVER_REGISTRY_LOCK:
        data, load_error = load_server_registry_unlocked()
        data.setdefault("servers", {})[server.server_id] = server_to_registry_record(server)
        write_error = write_server_registry_unlocked(data)
        with server.lock:
            server.registry_path = get_server_registry_path()
            server.registry_error = write_error or load_error


def remove_server_record_unlocked(server_id: str) -> str | None:
    data, load_error = load_server_registry_unlocked()
    servers = data.setdefault("servers", {})
    servers.pop(server_id, None)
    write_error = write_server_registry_unlocked(data)
    return write_error or load_error


def remove_server_record(server_id: str) -> str | None:
    with _SERVER_REGISTRY_LOCK:
        return remove_server_record_unlocked(server_id)


def check_pid_alive(pid: int | None) -> tuple[bool, str | None]:
    if pid is None or int(pid) <= 0:
        return False, "pid_not_available"
    pid = int(pid)
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=2,
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            if completed.returncode != 0:
                return False, f"tasklist exited with code {completed.returncode}: {output.strip()}"
            if str(pid) not in output:
                return False, "pid_not_alive"
            return True, None

        os.kill(pid, 0)
        return True, None
    except ProcessLookupError:
        return False, "pid_not_alive"
    except PermissionError:
        return True, None
    except Exception as exc:
        return False, f"pid_check_failed: {exc}"


def check_tcp_reachable(hostname: str, port: int, timeout_seconds: float = 0.5) -> tuple[bool, str | None]:
    try:
        with socket.create_connection((hostname, int(port)), timeout=timeout_seconds):
            return True, None
    except OSError as exc:
        return False, f"server_not_reachable: {exc}"


def validate_server_runtime(pid: int | None, hostname: str, port: int) -> tuple[bool, str | None]:
    pid_ok, pid_error = check_pid_alive(pid)
    if not pid_ok:
        return False, pid_error
    tcp_ok, tcp_error = check_tcp_reachable(hostname, port)
    if not tcp_ok:
        return False, tcp_error
    return True, None


def recover_server_from_registry(server_id: str) -> tuple[OpenCodeServer | None, str | None]:
    with _SERVER_REGISTRY_LOCK:
        existing = _SERVERS.get(server_id)
        if existing is not None:
            return existing, None

        data, load_error = load_server_registry_unlocked()
        if load_error:
            return None, load_error

        record = data.get("servers", {}).get(server_id)
        if not isinstance(record, dict):
            return None, None

        try:
            hostname = str(record["hostname"])
            port = int(record["port"])
            pid = int(record["pid"])
            url = str(record.get("url") or f"http://{hostname}:{port}")
            working_dir = str(record["working_dir"])
            command = record.get("command")
            if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
                command = []
            started_at = str(record.get("started_at") or utc_now())
        except Exception as exc:
            remove_error = remove_server_record_unlocked(server_id)
            detail = f"registry_stale: malformed server record: {exc}"
            if remove_error:
                detail = f"{detail}; cleanup_error: {remove_error}"
            return None, detail

        runtime_ok, runtime_error = validate_server_runtime(pid, hostname, port)
        if not runtime_ok:
            remove_error = remove_server_record_unlocked(server_id)
            detail = f"registry_stale: {runtime_error}"
            if remove_error:
                detail = f"{detail}; cleanup_error: {remove_error}"
            return None, detail

        server = OpenCodeServer(
            server_id=server_id,
            url=url,
            hostname=hostname,
            port=port,
            working_dir=working_dir,
            command=command,
            status="running",
            pid=pid,
            started_at=started_at,
            recovered_from_registry=True,
            registry_path=get_server_registry_path(),
        )
        _SERVERS[server_id] = server
        return server, None


def get_server_for_lookup(server_id: str) -> tuple[OpenCodeServer | None, str | None]:
    with _SERVER_REGISTRY_LOCK:
        server = _SERVERS.get(server_id)
    if server is not None:
        return server, None
    return recover_server_from_registry(server_id)


def working_dir_matches_filter(server_working_dir: str | None, working_dir_filter: str | None) -> bool:
    if working_dir_filter is None:
        return True
    if server_working_dir is None:
        return False
    _resolved_filter, filter_key = normalize_working_dir(working_dir_filter)
    _resolved_server, server_key = normalize_working_dir(server_working_dir)
    return server_key == filter_key


def make_server_lost_result(server_id: str, record: dict | None, registry_error: str | None) -> dict:
    record = record if isinstance(record, dict) else {}
    return {
        "server_id": server_id,
        "url": record.get("url"),
        "hostname": record.get("hostname"),
        "port": record.get("port"),
        "working_dir": record.get("working_dir"),
        "pid": record.get("pid"),
        "status": "lost",
        "exit_code": None,
        "started_at": record.get("started_at"),
        "finished_at": None,
        "stdout_tail": "",
        "stderr_tail": "",
        "process_tree_kill_attempted": False,
        "process_tree_kill_succeeded": False,
        "process_tree_kill_error": None,
        "recovered_from_registry": False,
        "registry_path": get_server_registry_path(),
        "registry_error": registry_error,
        "process_running": False,
        "command": " ".join(record.get("command", [])) if isinstance(record.get("command"), list) else record.get("command_summary"),
        "success": False,
        "error": "server_lost",
    }


def normalize_working_dir(working_dir: str) -> tuple[str, str]:
    path = Path(working_dir).expanduser()
    resolved = path.resolve(strict=False)
    normalized = os.path.normcase(os.path.realpath(str(resolved)))
    return str(resolved), normalized


def normalize_git_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def git_path_key(path: str) -> str:
    normalized = normalize_git_path(path)
    if os.name == "nt":
        return normalized.casefold()
    return normalized


def unique_sorted_paths(paths: list[str]) -> list[str]:
    by_key: dict[str, str] = {}
    for path in paths:
        normalized = normalize_git_path(path)
        if not normalized:
            continue
        by_key.setdefault(git_path_key(normalized), normalized)
    return sorted(by_key.values(), key=lambda item: item.casefold())


def sort_paths(paths: list[str]) -> list[str]:
    return sorted(paths, key=lambda item: item.casefold())


def normalize_abs_path_key(working_dir: str, path: str) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(working_dir) / candidate
    resolved = candidate.resolve(strict=False)
    normalized = os.path.normcase(os.path.realpath(str(resolved)))
    return normalized.rstrip("\\/")


def path_matches_policy(working_dir: str, changed_file: str, policy_path: str) -> bool:
    changed_key = normalize_abs_path_key(working_dir, changed_file)
    policy_key = normalize_abs_path_key(working_dir, policy_path)
    if changed_key == policy_key:
        return True
    separator = os.sep
    return changed_key.startswith(policy_key + separator)


def get_path_policy_summary(job: OpenCodeJob) -> dict:
    return {
        "allowed_paths": list(job.allowed_paths) if job.allowed_paths is not None else None,
        "forbidden_paths": list(job.forbidden_paths) if job.forbidden_paths is not None else None,
        "checked_files_basis": "new_changed_files",
        "match_rule": "same path or descendant path; relative paths resolve against working_dir",
        "case_sensitive": os.name != "nt",
    }


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


def parse_output_cursor(cursor) -> int | None:
    if cursor is None:
        return None
    try:
        parsed = int(cursor)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def append_output_buffer_locked(target, kind: str, text: str) -> None:
    if not hasattr(target, "stdout_cursor"):
        return

    if kind == "stdout":
        buffer_attr = "stdout_buffer"
        start_attr = "stdout_buffer_start"
        cursor_attr = "stdout_cursor"
    else:
        buffer_attr = "stderr_buffer"
        start_attr = "stderr_buffer_start"
        cursor_attr = "stderr_cursor"

    buffer = getattr(target, buffer_attr) + text
    buffer_start = getattr(target, start_attr)
    cursor = getattr(target, cursor_attr) + len(text)
    if len(buffer) > MAX_DELTA_BUFFER_CHARS:
        dropped_chars = len(buffer) - MAX_DELTA_BUFFER_CHARS
        buffer = buffer[dropped_chars:]
        buffer_start += dropped_chars

    setattr(target, buffer_attr, buffer)
    setattr(target, start_attr, buffer_start)
    setattr(target, cursor_attr, cursor)


def output_delta_locked(target, kind: str, cursor) -> tuple[str, int, bool]:
    if kind == "stdout":
        buffer = target.stdout_buffer
        buffer_start = target.stdout_buffer_start
        current_cursor = target.stdout_cursor
    else:
        buffer = target.stderr_buffer
        buffer_start = target.stderr_buffer_start
        current_cursor = target.stderr_cursor

    parsed_cursor = parse_output_cursor(cursor)
    if parsed_cursor is None:
        return "", current_cursor, False

    if parsed_cursor > current_cursor:
        parsed_cursor = current_cursor
    delta_start = max(parsed_cursor, buffer_start)
    delta_offset = max(0, delta_start - buffer_start)
    return buffer[delta_offset:], current_cursor, parsed_cursor < buffer_start


def has_pending_output_delta(job: OpenCodeJob, stdout_cursor, stderr_cursor) -> bool:
    with job.lock:
        stdout_delta, _stdout_current, stdout_truncated = output_delta_locked(
            job,
            "stdout",
            stdout_cursor,
        )
        stderr_delta, _stderr_current, stderr_truncated = output_delta_locked(
            job,
            "stderr",
            stderr_cursor,
        )
    return bool(stdout_delta or stderr_delta or stdout_truncated or stderr_truncated)


def find_session_id(value) -> str | None:
    if isinstance(value, dict):
        session_id = value.get("sessionID")
        if isinstance(session_id, str) and session_id:
            return session_id
        for nested in value.values():
            found = find_session_id(nested)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_session_id(item)
            if found:
                return found
    return None


def parse_session_id_from_text(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        found = find_session_id(event)
        if found:
            return found
    return None


def append_tail(target, kind: str, text: str) -> None:
    output_text = text
    tail_text = text
    if len(tail_text) > MAX_TAIL_CHARS:
        tail_text = tail_text[-MAX_TAIL_CHARS:]
    with target.lock:
        if kind == "stdout":
            append_output_buffer_locked(target, kind, output_text)
            target.stdout_tail.append(tail_text)
            if hasattr(target, "session_id"):
                now = utc_now()
                if target.first_output_at is None:
                    target.first_output_at = now
                target.last_activity_at = now
                target.output_version += 1
                target.output_event.set()
                session_id = parse_session_id_from_text(output_text)
                if session_id:
                    target.session_id = session_id
        else:
            append_output_buffer_locked(target, kind, output_text)
            target.stderr_tail.append(tail_text)
            if hasattr(target, "session_id"):
                now = utc_now()
                if target.first_output_at is None:
                    target.first_output_at = now
                target.last_activity_at = now
                target.output_version += 1
                target.output_event.set()


def read_stream(target, stream, kind: str) -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            append_tail(target, kind, line)
    except Exception as exc:  # pragma: no cover - defensive reader guard
        append_tail(target, "stderr", f"[opencode_coder] Failed to read {kind}: {exc}\n")
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


def choose_free_port(hostname: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((hostname, 0))
        return int(sock.getsockname()[1])


def is_server_running(server: OpenCodeServer) -> bool:
    with server.lock:
        process = server.process
        recovered_from_registry = server.recovered_from_registry
        status = server.status
        pid = server.pid
        hostname = server.hostname
        port = server.port
    if process is not None:
        return process.poll() is None
    if recovered_from_registry and status == "running":
        runtime_ok, runtime_error = validate_server_runtime(pid, hostname, port)
        if runtime_ok:
            return True
        with server.lock:
            server.status = "lost"
            server.finished_at = utc_now()
            server.error = f"Recovered server is no longer reachable: {runtime_error}"
            server.registry_error = runtime_error
            server.done_event.set()
        remove_server_record(server.server_id)
    return False


def refresh_server_status(server: OpenCodeServer) -> None:
    with server.lock:
        process = server.process
        recovered_from_registry = server.recovered_from_registry
        status = server.status
        pid = server.pid
        hostname = server.hostname
        port = server.port
        if process is None and not recovered_from_registry:
            return
    if process is None and recovered_from_registry:
        if status == "running":
            runtime_ok, runtime_error = validate_server_runtime(pid, hostname, port)
            if not runtime_ok:
                remove_error = remove_server_record(server.server_id)
                with server.lock:
                    server.status = "lost"
                    server.finished_at = utc_now()
                    server.error = f"Recovered server is no longer reachable: {runtime_error}"
                    server.registry_error = remove_error or runtime_error
                    server.done_event.set()
        return

    with server.lock:
        if server.process is None:
            return
        exit_code = server.process.poll()
        if exit_code is None:
            if server.status in {"starting", "ready"}:
                server.status = "running"
            return
        if not server.done_event.is_set():
            server.exit_code = exit_code
            server.finished_at = utc_now()
            server.status = "stopped" if exit_code == 0 or server.stop_requested else "failed"
            if server.status == "failed" and server.error is None:
                server.error = f"opencode serve exited with code {exit_code}"
            server.done_event.set()


def server_to_result(server: OpenCodeServer) -> dict:
    refresh_server_status(server)
    with server.lock:
        stdout_tail = tail_to_text(server.stdout_tail)
        stderr_tail = tail_to_text(server.stderr_tail)
        process_running = (
            server.process is not None and server.process.poll() is None
        ) or (
            server.process is None
            and server.recovered_from_registry
            and server.status == "running"
        )
        return {
            "server_id": server.server_id,
            "url": server.url,
            "hostname": server.hostname,
            "port": server.port,
            "working_dir": server.working_dir,
            "pid": server.pid,
            "status": server.status,
            "exit_code": server.exit_code,
            "started_at": server.started_at,
            "finished_at": server.finished_at,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "process_tree_kill_attempted": server.process_tree_kill_attempted,
            "process_tree_kill_succeeded": server.process_tree_kill_succeeded,
            "process_tree_kill_error": server.process_tree_kill_error,
            "recovered_from_registry": server.recovered_from_registry,
            "registry_path": server.registry_path or get_server_registry_path(),
            "registry_error": server.registry_error,
            "process_running": process_running,
            "command": " ".join(server.command),
            "success": server.status in {"running", "stopped"} and server.error is None,
            "error": server.error,
        }


def monitor_server(server: OpenCodeServer, reader_threads: list[threading.Thread]) -> None:
    exit_code: int | None = None
    error: str | None = None
    try:
        if server.process is None:
            error = "process was not started"
        else:
            exit_code = server.process.wait()
    except Exception as exc:  # pragma: no cover - defensive process guard
        error = str(exc)

    for thread in reader_threads:
        thread.join(timeout=2)

    with server.lock:
        server.exit_code = exit_code
        server.finished_at = utc_now()
        server.error = error
        if error is not None:
            server.status = "failed"
        elif exit_code == 0 or server.stop_requested:
            server.status = "stopped"
        else:
            server.status = "failed"
            server.error = f"opencode serve exited with code {exit_code}"
        server.done_event.set()
    remove_error = remove_server_record(server.server_id)
    if remove_error:
        with server.lock:
            server.registry_path = get_server_registry_path()
            server.registry_error = remove_error


def wait_for_server_port(server: OpenCodeServer, timeout_seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        refresh_server_status(server)
        if server.done_event.is_set():
            return False
        try:
            with socket.create_connection((server.hostname, server.port), timeout=0.25):
                with server.lock:
                    server.status = "running"
                return True
        except OSError:
            time.sleep(0.1)
    return False


def make_server_not_found_result(
    server_id: str,
    *,
    status: str = "not_found",
    error: str = "server_not_found",
    registry_error: str | None = None,
) -> dict:
    return {
        "server_id": server_id,
        "url": None,
        "hostname": None,
        "port": None,
        "working_dir": None,
        "pid": None,
        "status": status,
        "exit_code": None,
        "started_at": None,
        "finished_at": None,
        "stdout_tail": "",
        "stderr_tail": "",
        "process_tree_kill_attempted": False,
        "process_tree_kill_succeeded": False,
        "process_tree_kill_error": None,
        "recovered_from_registry": False,
        "registry_path": get_server_registry_path(),
        "registry_error": registry_error,
        "process_running": False,
        "command": None,
        "success": False,
        "error": error,
    }


def parse_git_status_entries(stdout: str) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for line in stdout.splitlines():
        if not line:
            continue
        status_code = line[:2] if len(line) >= 2 else "??"
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        normalized_path = normalize_git_path(path.strip().strip('"'))
        if normalized_path:
            entries[git_path_key(normalized_path)] = (normalized_path, status_code)
    return entries


def file_fingerprint(working_dir: str, path: str, status_code: str) -> str:
    absolute_path = Path(working_dir) / path
    try:
        stat_result = absolute_path.lstat()
    except OSError:
        return f"{status_code}|missing"

    if absolute_path.is_symlink():
        try:
            target = os.readlink(absolute_path)
        except OSError as exc:
            target = f"<readlink-error:{exc}>"
        return f"{status_code}|symlink|{target}"

    if absolute_path.is_file():
        digest = hashlib.sha256()
        try:
            with absolute_path.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            return f"{status_code}|file-read-error|{stat_result.st_size}|{exc}"
        return f"{status_code}|file|{stat_result.st_size}|{digest.hexdigest()}"

    if absolute_path.is_dir():
        return f"{status_code}|dir|{stat_result.st_mtime_ns}"

    return f"{status_code}|other|{stat_result.st_mode}|{stat_result.st_size}|{stat_result.st_mtime_ns}"


def build_git_status_snapshot(working_dir: str, stdout: str) -> GitStatusSnapshot:
    entries = parse_git_status_entries(stdout)
    files_by_key = {key: path for key, (path, _status_code) in entries.items()}
    fingerprints = {
        key: file_fingerprint(working_dir, path, status_code)
        for key, (path, status_code) in entries.items()
    }
    return GitStatusSnapshot(
        available=True,
        files=sort_paths(list(files_by_key.values())),
        fingerprints=fingerprints,
    )


def collect_git_status(working_dir: str) -> GitStatusSnapshot:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                working_dir,
                "-c",
                "core.quotepath=false",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except Exception as exc:
        return GitStatusSnapshot(available=False, error=str(exc))

    if result.returncode != 0:
        error = (result.stderr or result.stdout or "git status failed").strip()
        return GitStatusSnapshot(available=False, error=trim_tail(error))

    return build_git_status_snapshot(working_dir, result.stdout)


def collect_git_status_entry_map(working_dir: str) -> tuple[dict[str, tuple[str, str]], str | None]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                working_dir,
                "-c",
                "core.quotepath=false",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except Exception as exc:
        return {}, str(exc)

    if result.returncode != 0:
        error = (result.stderr or result.stdout or "git status failed").strip()
        return {}, trim_tail(error)

    return parse_git_status_entries(result.stdout), None


def run_git_diff(working_dir: str, paths: list[str], *, cached: bool = False) -> tuple[str, str | None]:
    if not paths:
        return "", None
    command = [
        "git",
        "-C",
        working_dir,
        "-c",
        "core.quotepath=false",
        "diff",
    ]
    if cached:
        command.append("--cached")
    command.extend(["--", *paths])
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception as exc:
        return "", str(exc)

    if result.returncode != 0:
        error = (result.stderr or result.stdout or "git diff failed").strip()
        return "", trim_tail(error)
    return result.stdout, None


def make_untracked_file_diff(working_dir: str, path: str) -> tuple[str, str | None]:
    absolute_path = Path(working_dir) / path
    if not absolute_path.is_file():
        return "", "not_a_regular_file"
    try:
        with absolute_path.open("rb") as file:
            data = file.read(MAX_DIFF_CHARS + 1)
    except OSError as exc:
        return "", str(exc)
    if len(data) > MAX_DIFF_CHARS:
        data = data[:MAX_DIFF_CHARS]

    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff(
            [],
            lines,
            fromfile="/dev/null",
            tofile=f"b/{normalize_git_path(path)}",
        )
    )
    return f"diff --git a/{normalize_git_path(path)} b/{normalize_git_path(path)}\nnew file mode 100644\n{diff}", None


def truncate_diff(diff: str, max_chars: int) -> tuple[str, bool]:
    if len(diff) <= max_chars:
        return diff, False
    return diff[:max_chars], True


def evaluate_path_policy(
    working_dir: str,
    new_changed_files: list[str],
    allowed_paths: list[str] | None,
    forbidden_paths: list[str] | None,
) -> tuple[bool, list[str], list[str]]:
    allowed = [path for path in list(allowed_paths or []) if str(path).strip()]
    forbidden = [path for path in list(forbidden_paths or []) if str(path).strip()]
    forbidden_changed_files: list[str] = []
    extra_changed_files: list[str] = []

    for changed_file in new_changed_files:
        if any(path_matches_policy(working_dir, changed_file, forbidden_path) for forbidden_path in forbidden):
            forbidden_changed_files.append(changed_file)
            continue

        if allowed and not any(path_matches_policy(working_dir, changed_file, allowed_path) for allowed_path in allowed):
            extra_changed_files.append(changed_file)

    policy_violation = bool(forbidden_changed_files or extra_changed_files)
    return policy_violation, extra_changed_files, forbidden_changed_files


def apply_git_snapshot(job: OpenCodeJob, snapshot: GitStatusSnapshot, *, set_preexisting: bool = False) -> None:
    with job.lock:
        if set_preexisting:
            job.preexisting_changed_files = list(snapshot.files) if snapshot.available else []
            job.preexisting_file_fingerprints = dict(snapshot.fingerprints) if snapshot.available else {}

        all_changed_files = list(snapshot.files) if snapshot.available else []
        current_files_by_key = {git_path_key(path): path for path in all_changed_files}
        preexisting_files_by_key = {
            git_path_key(path): path
            for path in job.preexisting_changed_files
        }
        changed_during_job_keys = [
            key
            for key in sorted(set(current_files_by_key) | set(job.preexisting_file_fingerprints))
            if snapshot.fingerprints.get(key) != job.preexisting_file_fingerprints.get(key)
        ]
        new_changed_files = sort_paths([
            current_files_by_key.get(key) or preexisting_files_by_key.get(key) or key
            for key in changed_during_job_keys
        ])
        current_change_fingerprints = {
            key: snapshot.fingerprints.get(key)
            for key in changed_during_job_keys
        }
        previous_change_fingerprints = dict(job.observed_change_fingerprints)
        policy_violation, extra_changed_files, forbidden_changed_files = evaluate_path_policy(
            job.working_dir,
            new_changed_files,
            job.allowed_paths,
            job.forbidden_paths,
        )

        job.all_changed_files = all_changed_files
        job.new_changed_files = new_changed_files
        job.observed_change_fingerprints = current_change_fingerprints
        job.changed_files = all_changed_files
        if new_changed_files and current_change_fingerprints != previous_change_fingerprints:
            now = utc_now()
            if job.first_change_at is None:
                job.first_change_at = now
            job.last_activity_at = now
            job.change_version += 1
            job.change_event.set()
        job.policy_violation = policy_violation
        job.extra_changed_files = extra_changed_files
        job.forbidden_changed_files = forbidden_changed_files
        job.git_status_available = snapshot.available
        job.git_status_error = snapshot.error


def refresh_job_snapshot(job: OpenCodeJob) -> None:
    apply_git_snapshot(job, collect_git_status(job.working_dir))


def finish_job(job: OpenCodeJob, exit_code: int | None, error: str | None = None) -> None:
    final_snapshot = collect_git_status(job.working_dir)
    apply_git_snapshot(job, final_snapshot)
    with job.lock:
        cancel_requested = job.cancel_requested
        job.exit_code = exit_code
        job.finished_at = utc_now()
        if cancel_requested:
            job.error = error or "job_cancelled"
            job.status = "cancelled"
            job.summary = "OpenCode job was cancelled by request."
        elif error is not None:
            job.error = error
            job.status = "failed"
            job.summary = f"OpenCode process failed in wrapper: {error}"
        elif exit_code == 0:
            job.error = None
            job.status = "completed"
            job.summary = "OpenCode completed successfully."
        else:
            job.error = None
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


def mark_job_timed_out_if_active(job: OpenCodeJob) -> None:
    with job.lock:
        if job.status in {"starting", "running"}:
            job.status = "timed_out"
            job.summary = (
                "OpenCode is still running after the MCP wait window. "
                "Call opencode_coder_status with job_id for the final result."
            )


def wait_for_job_policy(job: OpenCodeJob, wait_policy: str, wait_seconds: float) -> None:
    if wait_policy == "start_only":
        return
    if wait_seconds <= 0:
        if not job.done_event.is_set():
            mark_job_timed_out_if_active(job)
        return

    if wait_policy == "completion":
        if not job.done_event.wait(wait_seconds):
            mark_job_timed_out_if_active(job)
        return

    deadline = time.monotonic() + wait_seconds
    while True:
        if job.done_event.is_set():
            return
        if wait_policy == "first_output" and job.output_event.is_set():
            return
        if wait_policy == "first_change":
            refresh_job_snapshot(job)
            if job.change_event.is_set():
                return

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            mark_job_timed_out_if_active(job)
            return
        time.sleep(min(0.1, remaining))


def wait_for_status_activity(job: OpenCodeJob, wait_seconds: float) -> None:
    wait_seconds = clamp_wait_seconds(wait_seconds)
    if wait_seconds <= 0 or job.done_event.is_set():
        return

    with job.lock:
        output_version = job.output_version
        change_version = job.change_version

    deadline = time.monotonic() + wait_seconds
    while True:
        if job.done_event.is_set():
            return
        refresh_job_snapshot(job)
        with job.lock:
            if job.output_version != output_version or job.change_version != change_version:
                return

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


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
    stdout_cursor: int | None = None,
    stderr_cursor: int | None = None,
) -> dict:
    if is_job_active(job):
        refresh_job_snapshot(job)

    with job.lock:
        stdout_tail = tail_to_text(job.stdout_tail)
        stderr_tail = tail_to_text(job.stderr_tail)
        stdout_delta, current_stdout_cursor, stdout_delta_truncated = output_delta_locked(
            job,
            "stdout",
            stdout_cursor,
        )
        stderr_delta, current_stderr_cursor, stderr_delta_truncated = output_delta_locked(
            job,
            "stderr",
            stderr_cursor,
        )
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
            elif status == "cancelled":
                summary = "OpenCode job was cancelled by request."

        return {
            "job_id": job.job_id,
            "status": status,
            "working_dir": job.working_dir,
            "pid": job.pid,
            "exit_code": job.exit_code,
            "summary": summary,
            "session_id": job.session_id,
            "server_id": job.server_id,
            "server_url": job.server_url,
            "attached_to_server": job.attached_to_server,
            "server_recovered_from_registry": job.server_recovered_from_registry,
            "changed_files": list(job.changed_files),
            "preexisting_changed_files": list(job.preexisting_changed_files),
            "all_changed_files": list(job.all_changed_files),
            "new_changed_files": list(job.new_changed_files),
            "policy_violation": job.policy_violation,
            "extra_changed_files": list(job.extra_changed_files),
            "forbidden_changed_files": list(job.forbidden_changed_files),
            "path_policy": get_path_policy_summary(job),
            "git_status_available": job.git_status_available,
            "git_status_error": job.git_status_error,
            "tests_run": [],
            "validation_skipped_reason": "MCP wrapper does not run validation; inspect OpenCode output or task-level tooling.",
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "stdout_delta": stdout_delta,
            "stderr_delta": stderr_delta,
            "stdout_cursor": current_stdout_cursor,
            "stderr_cursor": current_stderr_cursor,
            "stdout_delta_truncated": stdout_delta_truncated,
            "stderr_delta_truncated": stderr_delta_truncated,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "first_output_at": job.first_output_at,
            "first_change_at": job.first_change_at,
            "last_activity_at": job.last_activity_at,
            "command": job.command_summary,
            "wait_policy": job.wait_policy,
            "requested_timeout_seconds": job.requested_timeout_seconds,
            "effective_timeout_seconds": job.effective_timeout_seconds,
            "timeout_policy": job.timeout_policy,
            "cancel_requested": job.cancel_requested,
            "cancel_signal_sent": job.cancel_signal_sent,
            "cancel_kill_sent": job.cancel_kill_sent,
            "process_tree_kill_attempted": job.process_tree_kill_attempted,
            "process_tree_kill_succeeded": job.process_tree_kill_succeeded,
            "process_tree_kill_error": job.process_tree_kill_error,
            "process_running": process_running,
            "lock_rejected": lock_rejected,
            "new_job_started": new_job_started,
            "success": success,
            "output": output,
            "return_code": job.exit_code,
            "error": job.error,
        }


def make_job_not_found_result(job_id: str) -> dict:
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
        "session_id": None,
        "server_id": None,
        "server_url": None,
        "attached_to_server": False,
        "server_recovered_from_registry": False,
        "changed_files": [],
        "preexisting_changed_files": [],
        "all_changed_files": [],
        "new_changed_files": [],
        "policy_violation": False,
        "extra_changed_files": [],
        "forbidden_changed_files": [],
        "path_policy": None,
        "git_status_available": False,
        "git_status_error": "job_not_found",
        "tests_run": [],
        "validation_skipped_reason": "No job was found to validate.",
        "stdout_tail": "",
        "stderr_tail": "",
        "stdout_delta": "",
        "stderr_delta": "",
        "stdout_cursor": 0,
        "stderr_cursor": 0,
        "stdout_delta_truncated": False,
        "stderr_delta_truncated": False,
        "started_at": None,
        "finished_at": None,
        "first_output_at": None,
        "first_change_at": None,
        "last_activity_at": None,
        "command": None,
        "wait_policy": None,
        "requested_timeout_seconds": None,
        "effective_timeout_seconds": None,
        "timeout_policy": None,
        "cancel_requested": False,
        "cancel_signal_sent": False,
        "cancel_kill_sent": False,
        "process_tree_kill_attempted": False,
        "process_tree_kill_succeeded": False,
        "process_tree_kill_error": None,
        "process_running": False,
        "lock_rejected": False,
        "new_job_started": False,
        "success": False,
        "output": "",
        "return_code": None,
        "error": "job_not_found",
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
    wait_policy: str,
    allowed_paths: list[str] | None,
    forbidden_paths: list[str] | None,
    server_id: str | None,
    server_url: str | None,
    session_id: str | None,
    attached_to_server: bool,
    error: str,
    server_recovered_from_registry: bool = False,
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
        wait_policy=wait_policy,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
        server_id=server_id,
        server_url=server_url,
        session_id=session_id,
        attached_to_server=attached_to_server,
        server_recovered_from_registry=server_recovered_from_registry,
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
    allowed_paths: list[str] | None,
    forbidden_paths: list[str] | None,
    wait_policy: str | None,
    server_id: str | None,
    session_id: str | None,
    continue_last: bool,
    fork_session: bool,
    title: str | None,
) -> tuple[OpenCodeJob | None, dict | None]:
    cleanup_jobs()
    requested_timeout, effective_timeout, timeout_policy = compute_effective_timeout(timeout_seconds)
    normalized_wait_policy = normalize_wait_policy(wait_policy)
    resolved_working_dir, cwd_key = normalize_working_dir(working_dir)
    server_url: str | None = None
    attached_to_server = False
    server_recovered_from_registry = False

    if server_id:
        server, registry_error = get_server_for_lookup(server_id)
        if server is None:
            command = [
                resolve_opencode(),
                "run",
                "--attach",
                f"<missing server_id={server_id}>",
                f"<prompt chars={len(prompt)}>",
            ]
            error = f"server_id not found: {server_id}"
            if registry_error:
                error = f"{error}; {registry_error}"
            result = make_start_failed_result(
                working_dir=resolved_working_dir,
                cwd_key=cwd_key,
                command=command,
                command_summary=" ".join(command),
                requested_timeout_seconds=requested_timeout,
                effective_timeout_seconds=effective_timeout,
                timeout_policy=timeout_policy,
                wait_policy=normalized_wait_policy,
                allowed_paths=allowed_paths,
                forbidden_paths=forbidden_paths,
                server_id=server_id,
                server_url=None,
                session_id=session_id,
                attached_to_server=False,
                error=error,
            )
            return None, result

        refresh_server_status(server)
        if not is_server_running(server):
            result = make_start_failed_result(
                working_dir=resolved_working_dir,
                cwd_key=cwd_key,
                command=build_opencode_command(
                    prompt,
                    working_dir=resolved_working_dir,
                    server_url=server.url,
                    session_id=session_id,
                    continue_last=continue_last,
                    fork_session=fork_session,
                    title=title,
                ),
                command_summary=(
                    f"{Path(resolve_opencode()).name} run --attach {server.url} "
                    f"<server not running> <prompt chars={len(prompt)}>"
                ),
                requested_timeout_seconds=requested_timeout,
                effective_timeout_seconds=effective_timeout,
                timeout_policy=timeout_policy,
                wait_policy=normalized_wait_policy,
                allowed_paths=allowed_paths,
                forbidden_paths=forbidden_paths,
                server_id=server_id,
                server_url=server.url,
                session_id=session_id,
                attached_to_server=True,
                error=f"server_id is not running: {server_id}",
                server_recovered_from_registry=server.recovered_from_registry,
            )
            return None, result
        server_url = server.url
        attached_to_server = True
        server_recovered_from_registry = server.recovered_from_registry

    command = build_opencode_command(
        prompt,
        working_dir=resolved_working_dir if attached_to_server else None,
        server_url=server_url,
        session_id=session_id,
        continue_last=continue_last,
        fork_session=fork_session,
        title=title,
    )
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
            wait_policy=normalized_wait_policy,
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            server_id=server_id,
            server_url=server_url,
            session_id=session_id,
            attached_to_server=attached_to_server,
            server_recovered_from_registry=server_recovered_from_registry,
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
            wait_policy=normalized_wait_policy,
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            server_id=server_id,
            server_url=server_url,
            session_id=session_id,
            attached_to_server=attached_to_server,
            server_recovered_from_registry=server_recovered_from_registry,
            summary="OpenCode process is starting.",
        )
        initial_snapshot = collect_git_status(resolved_working_dir)
        apply_git_snapshot(job, initial_snapshot, set_preexisting=True)
        _JOBS[job.job_id] = job
        _CWD_ACTIVE_JOBS.setdefault(cwd_key, set()).add(job.job_id)

    try:
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
            **popen_platform_kwargs(),
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
def opencode_server_start(
    working_dir: str = ".",
    hostname: str = "127.0.0.1",
    port: int = 0,
) -> dict:
    """启动由 MCP 托管的 opencode headless server。"""
    resolved_working_dir, _cwd_key = normalize_working_dir(working_dir)
    server_port = choose_free_port(hostname) if port == 0 else int(port)
    url = f"http://{hostname}:{server_port}"
    command = build_opencode_server_command(hostname, server_port)
    server = OpenCodeServer(
        server_id=uuid.uuid4().hex,
        url=url,
        hostname=hostname,
        port=server_port,
        working_dir=resolved_working_dir,
        command=command,
    )

    with _SERVER_REGISTRY_LOCK:
        _SERVERS[server.server_id] = server

    if not Path(resolved_working_dir).is_dir():
        with server.lock:
            server.status = "failed"
            server.finished_at = utc_now()
            server.error = f"working_dir does not exist or is not a directory: {resolved_working_dir}"
            server.done_event.set()
        return server_to_result(server)

    try:
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
            **popen_platform_kwargs(),
        )
    except Exception as exc:
        with server.lock:
            server.status = "failed"
            server.finished_at = utc_now()
            server.error = str(exc)
            server.done_event.set()
        return server_to_result(server)

    with server.lock:
        server.process = process
        server.pid = process.pid
        server.status = "starting"

    stdout_thread = threading.Thread(
        target=read_stream,
        args=(server, process.stdout, "stdout"),
        name=f"opencode-server-{server.server_id}-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=read_stream,
        args=(server, process.stderr, "stderr"),
        name=f"opencode-server-{server.server_id}-stderr",
        daemon=True,
    )
    reader_threads = [stdout_thread, stderr_thread]
    for thread in reader_threads:
        thread.start()

    monitor_thread = threading.Thread(
        target=monitor_server,
        args=(server, reader_threads),
        name=f"opencode-server-{server.server_id}-monitor",
        daemon=True,
    )
    monitor_thread.start()

    if wait_for_server_port(server):
        persist_server_record(server)
        return server_to_result(server)

    with server.lock:
        if server.error is None:
            server.error = f"Timed out waiting for {url} to accept connections."
        server.status = "failed"

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            tree_result = process_tree_kill(process)
            record_process_tree_kill_result(server, tree_result)

    return server_to_result(server)


@mcp.tool()
def opencode_server_status(server_id: str) -> dict:
    """查询 MCP 托管 opencode server 的运行状态。"""
    server, registry_error = get_server_for_lookup(server_id)
    if server is None:
        status = "lost" if registry_error and "registry_stale" in registry_error else "not_found"
        error = "server_lost" if status == "lost" else "server_not_found"
        return make_server_not_found_result(
            server_id,
            status=status,
            error=error,
            registry_error=registry_error,
        )
    return server_to_result(server)


@mcp.tool()
def opencode_server_list(
    working_dir: str | None = None,
    include_lost: bool = False,
) -> dict:
    """列出当前内存和 registry 中可见的 MCP 托管 opencode servers。"""
    registry_path = get_server_registry_path()
    registry_errors: list[str] = []
    with _SERVER_REGISTRY_LOCK:
        memory_servers = list(_SERVERS.values())
        registry_data, registry_error = load_server_registry_unlocked()
        registry_records = dict(registry_data.get("servers", {})) if registry_error is None else {}
    if registry_error:
        registry_errors.append(registry_error)

    servers: list[dict] = []
    seen_ids: set[str] = set()

    for server in memory_servers:
        result = server_to_result(server)
        seen_ids.add(result["server_id"])
        if not working_dir_matches_filter(result.get("working_dir"), working_dir):
            continue
        if not include_lost and result["status"] == "lost":
            continue
        servers.append(result)

    for server_id, record in registry_records.items():
        if server_id in seen_ids:
            continue
        if not working_dir_matches_filter(record.get("working_dir") if isinstance(record, dict) else None, working_dir):
            continue

        recovered, recover_error = recover_server_from_registry(server_id)
        if recovered is not None:
            result = server_to_result(recovered)
            if include_lost or result["status"] != "lost":
                servers.append(result)
            continue

        if recover_error:
            registry_errors.append(f"{server_id}: {recover_error}")
        if include_lost:
            servers.append(make_server_lost_result(server_id, record, recover_error or "server_not_found"))

    servers.sort(key=lambda item: ((item.get("working_dir") or "").casefold(), item.get("server_id") or ""))
    return {
        "servers": servers,
        "count": len(servers),
        "registry_path": registry_path,
        "registry_error": "; ".join(dict.fromkeys(registry_errors)) or None,
        "success": registry_error is None,
    }


@mcp.tool()
def opencode_server_stop(server_id: str) -> dict:
    """停止 MCP 托管 opencode server。"""
    server, registry_error = get_server_for_lookup(server_id)
    if server is None:
        status = "lost" if registry_error and "registry_stale" in registry_error else "not_found"
        error = "server_lost" if status == "lost" else "server_not_found"
        return make_server_not_found_result(
            server_id,
            status=status,
            error=error,
            registry_error=registry_error,
        )

    process = server.process
    if process is None and server.recovered_from_registry:
        refresh_server_status(server)
        with server.lock:
            server.registry_path = server.registry_path or get_server_registry_path()
            should_report_limitation = server.status == "running"
        result = server_to_result(server)
        if should_report_limitation:
            result["success"] = False
            result["error"] = (
                "server was recovered from registry; process handle and stdout/stderr "
                "pipes are not available, so opencode_server_stop will not blind-kill the pid"
            )
        return result

    if process is not None and process.poll() is None:
        with server.lock:
            server.status = "stopping"
            server.stop_requested = True
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tree_result = process_tree_kill(process)
            record_process_tree_kill_result(server, tree_result)

    server.done_event.wait(timeout=2)
    refresh_server_status(server)
    remove_error = remove_server_record(server.server_id)
    if remove_error:
        with server.lock:
            server.registry_path = get_server_registry_path()
            server.registry_error = remove_error
    return server_to_result(server)


@mcp.tool()
def opencode_coder(
    prompt: str,
    working_dir: str = ".",
    timeout_seconds: float = DEFAULT_WAIT_SECONDS,
    allow_concurrent: bool = False,
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    wait_policy: str = "completion",
    server_id: str | None = None,
    session_id: str | None = None,
    continue_last: bool = False,
    fork_session: bool = False,
    title: str | None = None,
) -> dict:
    """调用 OpenCode 在指定项目目录里编写或修改代码，并返回可查询的结构化 job 结果。"""
    job, early_result = start_job(
        prompt,
        working_dir,
        timeout_seconds,
        allow_concurrent,
        allowed_paths,
        forbidden_paths,
        wait_policy,
        server_id,
        session_id,
        continue_last,
        fork_session,
        title,
    )
    if early_result is not None:
        return early_result
    if job is None:
        raise RuntimeError("opencode_coder internal error: no job and no result")

    wait_for_job_policy(job, job.wait_policy, job.effective_timeout_seconds)
    return job_to_result(job)


@mcp.tool()
def opencode_coder_cancel(job_id: str) -> dict:
    """取消仍在运行的 opencode_coder job，并返回结构化 job 结果。"""
    cleanup_jobs()
    with _REGISTRY_LOCK:
        job = _JOBS.get(job_id)

    if job is None:
        return make_job_not_found_result(job_id)

    process = job.process
    if process is None:
        if not job.done_event.is_set():
            refresh_job_snapshot(job)
        return job_to_result(job, new_job_started=False)

    already_exited = False
    with job.lock:
        if job.done_event.is_set():
            return job_to_result(job, new_job_started=False)
        already_exited = process.poll() is not None
        if not already_exited:
            job.cancel_requested = True
            job.summary = "Cancellation requested; waiting for OpenCode process to exit."

    if already_exited:
        job.done_event.wait(timeout=2)
        return job_to_result(job, new_job_started=False)

    try:
        process.terminate()
        with job.lock:
            job.cancel_signal_sent = True
    except OSError as exc:
        with job.lock:
            job.summary = f"Cancellation requested, but terminate failed: {exc}"

    try:
        process.wait(timeout=DEFAULT_CANCEL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        with job.lock:
            job.cancel_kill_sent = True
        tree_result = process_tree_kill(process)
        record_process_tree_kill_result(job, tree_result)
        if tree_result.error:
            with job.lock:
                job.summary = f"Cancellation requested, but process tree kill did not finish cleanly: {tree_result.error}"

    job.done_event.wait(timeout=2)
    return job_to_result(job, new_job_started=False)


@mcp.tool()
def opencode_coder_status(
    job_id: str,
    wait_seconds: float = 0.0,
    stdout_cursor: int | None = None,
    stderr_cursor: int | None = None,
) -> dict:
    """通过 job_id 查询 opencode_coder 后台任务状态和输出尾部。"""
    cleanup_jobs()
    with _REGISTRY_LOCK:
        job = _JOBS.get(job_id)

    if job is None:
        return make_job_not_found_result(job_id)

    if not has_pending_output_delta(job, stdout_cursor, stderr_cursor):
        wait_for_status_activity(job, wait_seconds)
    if job.process is not None and job.process.poll() is not None and not job.done_event.is_set():
        job.done_event.wait(timeout=0.2)
    return job_to_result(
        job,
        new_job_started=False,
        stdout_cursor=stdout_cursor,
        stderr_cursor=stderr_cursor,
    )


@mcp.tool()
def opencode_coder_diff(job_id: str, max_chars: int = DEFAULT_DIFF_MAX_CHARS) -> dict:
    """基于 opencode_coder job 的本轮变更文件返回可 review 的 git diff。"""
    cleanup_jobs()
    effective_max_chars = clamp_diff_max_chars(max_chars)
    with _REGISTRY_LOCK:
        job = _JOBS.get(job_id)

    if job is None:
        return {
            "job_id": job_id,
            "status": "not_found",
            "working_dir": None,
            "new_changed_files": [],
            "preexisting_changed_files": [],
            "diff": "",
            "diff_truncated": False,
            "max_chars": effective_max_chars,
            "undiffed_files": [],
            "includes_preexisting_dirty_changes": False,
            "git_status_available": False,
            "error": "job_not_found",
            "success": False,
        }

    if is_job_active(job):
        refresh_job_snapshot(job)

    with job.lock:
        status = job.status
        working_dir = job.working_dir
        new_changed_files = list(job.new_changed_files)
        preexisting_changed_files = list(job.preexisting_changed_files)
        git_status_available = job.git_status_available
        git_status_error = job.git_status_error

    if not git_status_available:
        return {
            "job_id": job_id,
            "status": status,
            "working_dir": working_dir,
            "new_changed_files": new_changed_files,
            "preexisting_changed_files": preexisting_changed_files,
            "diff": "",
            "diff_truncated": False,
            "max_chars": effective_max_chars,
            "undiffed_files": list(new_changed_files),
            "includes_preexisting_dirty_changes": False,
            "git_status_available": False,
            "error": git_status_error or "git_status_unavailable",
            "success": False,
        }

    status_entries, status_error = collect_git_status_entry_map(working_dir)
    if status_error is not None:
        return {
            "job_id": job_id,
            "status": status,
            "working_dir": working_dir,
            "new_changed_files": new_changed_files,
            "preexisting_changed_files": preexisting_changed_files,
            "diff": "",
            "diff_truncated": False,
            "max_chars": effective_max_chars,
            "undiffed_files": list(new_changed_files),
            "includes_preexisting_dirty_changes": False,
            "git_status_available": False,
            "error": status_error,
            "success": False,
        }

    preexisting_keys = {git_path_key(path) for path in preexisting_changed_files}
    includes_preexisting_dirty_changes = any(
        git_path_key(path) in preexisting_keys
        for path in new_changed_files
    )

    tracked_paths: list[str] = []
    untracked_paths: list[str] = []
    for path in new_changed_files:
        entry = status_entries.get(git_path_key(path))
        if entry is not None and entry[1] == "??":
            untracked_paths.append(path)
        else:
            tracked_paths.append(path)

    diff_parts: list[str] = []
    undiffed_files: list[str] = []
    errors: list[str] = []

    unstaged_diff, unstaged_error = run_git_diff(working_dir, tracked_paths, cached=False)
    if unstaged_error:
        errors.append(f"git diff failed: {unstaged_error}")
        undiffed_files.extend(tracked_paths)
    elif unstaged_diff:
        diff_parts.append(unstaged_diff)

    cached_diff, cached_error = run_git_diff(working_dir, tracked_paths, cached=True)
    if cached_error:
        errors.append(f"git diff --cached failed: {cached_error}")
        for path in tracked_paths:
            if path not in undiffed_files:
                undiffed_files.append(path)
    elif cached_diff:
        diff_parts.append(cached_diff)

    for path in untracked_paths:
        untracked_diff, untracked_error = make_untracked_file_diff(working_dir, path)
        if untracked_error:
            undiffed_files.append(path)
            errors.append(f"{path}: {untracked_error}")
        elif untracked_diff:
            diff_parts.append(untracked_diff)

    full_diff = "\n".join(part.rstrip("\n") for part in diff_parts if part)
    diff, diff_truncated = truncate_diff(full_diff, effective_max_chars)
    undiffed_files = unique_sorted_paths(undiffed_files)
    return {
        "job_id": job_id,
        "status": status,
        "working_dir": working_dir,
        "new_changed_files": new_changed_files,
        "preexisting_changed_files": preexisting_changed_files,
        "diff": diff,
        "diff_truncated": diff_truncated,
        "max_chars": effective_max_chars,
        "undiffed_files": undiffed_files,
        "includes_preexisting_dirty_changes": includes_preexisting_dirty_changes,
        "git_status_available": True,
        "error": "; ".join(errors) or None,
        "success": not errors,
    }


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
                process_tree_kill(process, timeout_seconds=2)

    with _REGISTRY_LOCK:
        _JOBS.clear()
        _CWD_ACTIVE_JOBS.clear()


def _reset_servers_for_tests() -> None:
    with _SERVER_REGISTRY_LOCK:
        servers = list(_SERVERS.values())

    for server in servers:
        process = server.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process_tree_kill(process, timeout_seconds=2)

    with _SERVER_REGISTRY_LOCK:
        _SERVERS.clear()


if __name__ == "__main__":
    mcp.run()
