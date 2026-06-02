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
import re
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
DEFAULT_STATUS_TAIL_MAX_CHARS = 4_000
MAX_DELTA_BUFFER_CHARS = 64_000
DEFAULT_WAIT_SECONDS = 120.0
DEFAULT_MCP_CLIENT_TIMEOUT_SECONDS = 240.0
DEFAULT_MCP_WAIT_MARGIN_SECONDS = 25.0
DEFAULT_MAX_MCP_WAIT_SECONDS = 215.0
DEFAULT_FINISHED_JOB_TTL_SECONDS = 3600.0
MAX_STATUS_WAIT_SECONDS = 30.0
DEFAULT_WAIT_WAIT_SECONDS = DEFAULT_MAX_MCP_WAIT_SECONDS
MAX_WAIT_WAIT_SECONDS = 600.0
WAIT_POLL_INTERVAL = 0.5
VALID_RETURN_ON = {"interesting", "terminal"}
DEFAULT_CANCEL_GRACE_SECONDS = 5.0
MAX_FINISHED_JOBS = 100
DEFAULT_DIFF_MAX_CHARS = 20_000
MAX_DIFF_CHARS = 200_000
MAX_PATH_POLICY_DIAGNOSTIC_ENTRIES = 50
MAX_PATH_POLICY_DIAGNOSTIC_CHARS = 300
RECENT_EVENT_LIMIT = 20
DEFAULT_RECENT_EVENTS_LIMIT = 5
EVENT_TEXT_PREVIEW_CHARS = 300
PROGRESS_MESSAGE_MAX_CHARS = 240
PROGRESS_STARTUP_SECONDS = 3.0
NO_FIRST_CHANGE_BUDGET_SECONDS = 120.0
RECENT_FIRST_CHANGE_UPDATE_SECONDS = 30.0
MAX_LONG_GAP_SEGMENTS = 3
LONG_GAP_MIN_SECONDS = 60.0
LONG_GAP_LABEL_MAX_CHARS = 80
SLOW_FIRST_OUTPUT_SECONDS = NO_FIRST_CHANGE_BUDGET_SECONDS
SLOW_FIRST_EVENT_SECONDS = NO_FIRST_CHANGE_BUDGET_SECONDS
STALL_NO_ACTIVITY_SECONDS = 120.0
STALL_NO_OUTPUT_SECONDS = 120.0
STALL_CHANGED_FILES_NO_ACTIVITY_SECONDS = 120.0
SLOW_AFTER_CHANGE_SECONDS = STALL_NO_ACTIVITY_SECONDS
ACTIVE_STATUSES = {"starting", "running", "timed_out"}
VALID_WAIT_POLICIES = {"completion", "start_only", "first_output", "first_change"}
SERVER_REGISTRY_VERSION = 1
SERVER_REGISTRY_ENV_VAR = "OPENCODE_CODER_REGISTRY_PATH"
TOOL_ACTIVITY_CATEGORIES = ("read", "edit", "bash", "list", "unity", "other")
VALIDATION_EXECUTION_TOOL_CATEGORIES = {"bash", "unity"}
OBSERVED_VALIDATION_TOOL_ORDER = (
    "unity_skills_debug_force_recompile",
    "unity_skills_debug_check_compilation",
    "unity_skills_console_get_logs",
    "python_unittest",
    "py_compile",
    "git_diff_check",
    "unknown_test_command",
)
DEFAULT_OPENCODE_MODEL = "deepseek/deepseek-v4-pro"
DEFAULT_OPENCODE_VARIANT = "max"
WAIT_COMPACT_SNAPSHOT_FIELDS = (
    "job_id",
    "status",
    "success",
    "error",
    "working_dir",
    "exit_code",
    "summary",
    "work_summary_text",
    "assistant_last_text",
    "last_text_output",
    "new_changed_files",
    "all_changed_files",
    "preexisting_changed_files",
    "policy_violation",
    "extra_changed_files",
    "forbidden_changed_files",
    "validation_status",
    "validation_note",
    "observed_validation_summary",
    "progress_phase",
    "progress_message",
    "caller_update_recommended",
    "caller_update_reason",
    "next_poll_after_seconds",
    "is_stalled",
    "stall_reason",
    "no_event_noop_risk",
    "suggested_action",
    "requested_model",
    "requested_variant",
    "requested_agent",
    "requested_show_thinking",
)

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
    git_root: str | None = None
    git_root_error: str | None = None


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
    requested_session_id: str | None = None
    continue_last: bool = False
    fork_session: bool = False
    attached_to_server: bool = False
    server_recovered_from_registry: bool = False
    requested_model: str | None = DEFAULT_OPENCODE_MODEL
    requested_variant: str | None = DEFAULT_OPENCODE_VARIANT
    requested_agent: str | None = None
    requested_show_thinking: bool = False
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
    last_event_type: str | None = None
    last_event_at: str | None = None
    last_event_summary: dict | None = None
    recent_events: deque[dict] = field(default_factory=lambda: deque(maxlen=RECENT_EVENT_LIMIT))
    recent_event_count: int = 0
    last_text_output: str | None = None
    last_tool_name: str | None = None
    last_tool_event: dict | None = None
    last_step_reason: str | None = None
    last_step_status: str | None = None
    last_session_id: str | None = None
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
    git_root: str | None = None
    git_root_error: str | None = None
    path_policy_diagnostics: dict = field(default_factory=dict)
    first_output_at: str | None = None
    first_event_at: str | None = None
    first_tool_at: str | None = None
    first_change_at: str | None = None
    last_change_at: str | None = None
    last_activity_at: str | None = None
    last_event_observed_at: str | None = None
    last_git_snapshot_at: str | None = None
    last_trusted_change_activity_at: str | None = None
    tool_activity_counts: dict[str, int] = field(
        default_factory=lambda: {category: 0 for category in TOOL_ACTIVITY_CATEGORIES}
    )
    observed_validation_tools: list[str] = field(default_factory=list)
    observed_validation_texts: deque[str] = field(default_factory=lambda: deque(maxlen=RECENT_EVENT_LIMIT))
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


def append_opencode_run_option_flags(
    cmd: list[str],
    *,
    model: str | None = DEFAULT_OPENCODE_MODEL,
    variant: str | None = DEFAULT_OPENCODE_VARIANT,
    agent: str | None = None,
    show_thinking: bool = False,
) -> None:
    if model:
        cmd.extend(["--model", model])
    if variant:
        cmd.extend(["--variant", variant])
    if agent:
        cmd.extend(["--agent", agent])
    if show_thinking:
        cmd.append("--thinking")


def build_opencode_command(
    prompt: str,
    *,
    working_dir: str | None = None,
    server_url: str | None = None,
    session_id: str | None = None,
    continue_last: bool = False,
    fork_session: bool = False,
    title: str | None = None,
    model: str | None = DEFAULT_OPENCODE_MODEL,
    variant: str | None = DEFAULT_OPENCODE_VARIANT,
    agent: str | None = None,
    show_thinking: bool = False,
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
    append_opencode_run_option_flags(
        cmd,
        model=model,
        variant=variant,
        agent=agent,
        show_thinking=show_thinking,
    )

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


def default_mcp_wait_budget_seconds() -> float:
    client_timeout = env_float(
        "OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS",
        DEFAULT_MCP_CLIENT_TIMEOUT_SECONDS,
    )
    margin = env_float(
        "OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS",
        DEFAULT_MCP_WAIT_MARGIN_SECONDS,
    )
    return max(0.0, client_timeout - margin)


def compute_effective_timeout(timeout_seconds: float | int | None) -> tuple[float, float, str]:
    requested = DEFAULT_WAIT_SECONDS if timeout_seconds is None else float(timeout_seconds)
    requested = max(0.0, requested)

    max_wait = env_float("OPENCODE_CODER_MAX_WAIT_SECONDS", default_mcp_wait_budget_seconds())
    effective = min(requested, max_wait)
    if requested > effective:
        policy = (
            "capped_by_wrapper_to_return_before_mcp_client_timeout; "
            "job_continues_in_background"
        )
    else:
        policy = "requested_timeout_seconds"
    return requested, effective, policy


def effective_mcp_wait_cap_seconds() -> float:
    return min(
        env_float("OPENCODE_CODER_MAX_WAIT_SECONDS", default_mcp_wait_budget_seconds()),
        MAX_WAIT_WAIT_SECONDS,
    )


def compute_effective_wait_wait_seconds(wait_seconds: float | int | None) -> tuple[float, float, str]:
    requested = clamp_wait_wait_seconds(wait_seconds)
    cap = effective_mcp_wait_cap_seconds()
    effective = min(requested, cap)
    if requested > effective:
        policy = "capped_by_wrapper_to_return_before_mcp_client_timeout"
    else:
        policy = "requested_wait_seconds"
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


def clamp_wait_wait_seconds(wait_seconds: float | int | None) -> float:
    if wait_seconds is None:
        return DEFAULT_WAIT_WAIT_SECONDS
    try:
        parsed = float(wait_seconds)
    except (TypeError, ValueError):
        return DEFAULT_WAIT_WAIT_SECONDS
    return min(max(parsed, 0.0), MAX_WAIT_WAIT_SECONDS)


def clamp_diff_max_chars(max_chars) -> int:
    try:
        parsed = int(max_chars)
    except (TypeError, ValueError):
        return DEFAULT_DIFF_MAX_CHARS
    return min(max(parsed, 0), MAX_DIFF_CHARS)


def clamp_tail_max_chars(tail_max_chars, *, default: int = MAX_TAIL_CHARS) -> int:
    try:
        parsed = int(tail_max_chars)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, 0), MAX_TAIL_CHARS)


def clamp_recent_events_limit(recent_events_limit) -> int:
    try:
        parsed = int(recent_events_limit)
    except (TypeError, ValueError):
        parsed = DEFAULT_RECENT_EVENTS_LIMIT
    return min(max(parsed, 0), RECENT_EVENT_LIMIT)


def clamp_delta_max_chars(delta_max_chars) -> int | None:
    if delta_max_chars is None:
        return None
    try:
        parsed = int(delta_max_chars)
    except (TypeError, ValueError):
        return None
    return min(max(parsed, 0), MAX_DELTA_BUFFER_CHARS)


def truncate_delta_for_response(delta: str, delta_max_chars: int | None) -> tuple[str, bool]:
    if delta_max_chars is None or len(delta) <= delta_max_chars:
        return delta, False
    if delta_max_chars == 0:
        return "", True
    return delta[-delta_max_chars:], True


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


def normalize_abs_path_key_from_path(path: Path) -> str:
    resolved = path.expanduser().resolve(strict=False)
    normalized = os.path.normcase(os.path.realpath(str(resolved)))
    return normalized.rstrip("\\/")


def normalize_abs_path_key(working_dir: str, path: str) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(working_dir) / candidate
    return normalize_abs_path_key_from_path(candidate)


def path_key_matches(child_key: str, parent_key: str) -> bool:
    if not parent_key:
        return False
    if child_key == parent_key:
        return True
    return child_key.startswith(parent_key + os.sep)


def trim_diagnostic_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= MAX_PATH_POLICY_DIAGNOSTIC_CHARS:
        return text
    return text[: MAX_PATH_POLICY_DIAGNOSTIC_CHARS - 3] + "..."


def is_suffix_policy_candidate(policy_path: str) -> bool:
    candidate = Path(policy_path).expanduser()
    if candidate.is_absolute():
        return False
    normalized = normalize_git_path(policy_path)
    if not normalized:
        return False
    parts = [part for part in normalized.split("/") if part]
    if len(parts) < 2:
        return False
    return not any(part == ".." for part in parts)


def path_policy_candidates(working_dir: str, git_root: str | None, policy_path: str) -> list[dict]:
    policy_text = str(policy_path).strip()
    if not policy_text:
        return []

    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add_candidate(basis: str, display_path: str, key: str | None = None, relative_path: str | None = None) -> None:
        marker = key or relative_path or display_path
        dedupe_key = (basis, marker.casefold() if os.name == "nt" else marker)
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        candidates.append(
            {
                "basis": basis,
                "path": trim_diagnostic_text(display_path),
                "key": key,
                "relative_path": relative_path,
            }
        )

    expanded = Path(policy_text).expanduser()
    if expanded.is_absolute():
        add_candidate("absolute", str(expanded.resolve(strict=False)), normalize_abs_path_key_from_path(expanded))
        return candidates

    working_candidate = Path(working_dir) / expanded
    add_candidate(
        "working_dir",
        str(working_candidate.resolve(strict=False)),
        normalize_abs_path_key_from_path(working_candidate),
    )
    if git_root:
        git_candidate = Path(git_root) / expanded
        add_candidate(
            "git_root",
            str(git_candidate.resolve(strict=False)),
            normalize_abs_path_key_from_path(git_candidate),
        )
    if is_suffix_policy_candidate(policy_text):
        normalized = normalize_git_path(policy_text)
        add_candidate("git_relative_suffix", f"*/{normalized}", relative_path=normalized)
    return candidates


def public_policy_candidates(candidates: list[dict]) -> list[dict]:
    return [
        {
            "basis": candidate["basis"],
            "path": candidate["path"],
        }
        for candidate in candidates
    ]


def path_policy_match(
    working_dir: str,
    git_root: str | None,
    changed_file: str,
    policy_path: str,
) -> dict | None:
    changed_base = git_root or working_dir
    changed_key = normalize_abs_path_key(changed_base, changed_file)
    changed_git_key = git_path_key(changed_file)

    for candidate in path_policy_candidates(working_dir, git_root, policy_path):
        if candidate["basis"] == "git_relative_suffix":
            suffix_key = git_path_key(candidate["relative_path"] or "")
            changed_with_bounds = f"/{changed_git_key}/"
            suffix_with_bounds = f"/{suffix_key}/"
            if suffix_key and (
                changed_git_key == suffix_key
                or changed_git_key.startswith(f"{suffix_key}/")
                or changed_git_key.endswith(f"/{suffix_key}")
                or suffix_with_bounds in changed_with_bounds
            ):
                return {
                    "input": trim_diagnostic_text(str(policy_path)),
                    "basis": candidate["basis"],
                    "path": candidate["path"],
                }
            continue

        key = candidate.get("key")
        if key and path_key_matches(changed_key, key):
            return {
                "input": trim_diagnostic_text(str(policy_path)),
                "basis": candidate["basis"],
                "path": candidate["path"],
            }
    return None


def first_path_policy_match(
    working_dir: str,
    git_root: str | None,
    changed_file: str,
    policy_paths: list[str],
) -> dict | None:
    for policy_path in policy_paths:
        match = path_policy_match(working_dir, git_root, changed_file, policy_path)
        if match is not None:
            return match
    return None


def normalize_policy_paths_for_diagnostics(
    working_dir: str,
    git_root: str | None,
    policy_paths: list[str],
) -> list[dict]:
    diagnostics: list[dict] = []
    for policy_path in policy_paths[:MAX_PATH_POLICY_DIAGNOSTIC_ENTRIES]:
        diagnostics.append(
            {
                "input": trim_diagnostic_text(str(policy_path)),
                "candidates": public_policy_candidates(path_policy_candidates(working_dir, git_root, policy_path)),
            }
        )
    return diagnostics


def get_path_policy_summary(job: OpenCodeJob) -> dict:
    diagnostics = dict(job.path_policy_diagnostics or {})
    return {
        "allowed_paths": list(job.allowed_paths) if job.allowed_paths is not None else None,
        "forbidden_paths": list(job.forbidden_paths) if job.forbidden_paths is not None else None,
        "checked_files_basis": "new_changed_files",
        "working_dir": diagnostics.get("working_dir", trim_diagnostic_text(job.working_dir)),
        "git_root": diagnostics.get("git_root", trim_diagnostic_text(job.git_root)),
        "git_root_error": diagnostics.get("git_root_error", trim_diagnostic_text(job.git_root_error)),
        "allowed_paths_normalized": diagnostics.get("allowed_paths_normalized", []),
        "forbidden_paths_normalized": diagnostics.get("forbidden_paths_normalized", []),
        "checked_files_count": diagnostics.get("checked_files_count", 0),
        "file_matches": diagnostics.get("file_matches", []),
        "file_matches_truncated": diagnostics.get("file_matches_truncated", False),
        "match_rule": (
            "forbidden paths are checked first; exact path or descendant path matches; "
            "git status paths are treated as git-root-relative when git_root is available; "
            "relative policy paths are tried relative to working_dir and git_root; "
            "multi-component relative policy paths may also match a git-relative suffix"
        ),
        "case_sensitive": os.name != "nt",
    }


def summarize_command(cmd: list[str], prompt: str) -> str:
    prompt_hash = hashlib.sha256(prompt.encode("utf-8", errors="replace")).hexdigest()[:12]
    if not cmd:
        return "<empty command>"
    display = [Path(cmd[0]).name, *cmd[1:-1], f"<prompt chars={len(prompt)} sha256={prompt_hash}>"]
    return " ".join(display)


def trim_tail_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def trim_tail(text: str) -> str:
    return trim_tail_chars(text, MAX_TAIL_CHARS)


def tail_to_text(lines: deque[str], max_chars: int = MAX_TAIL_CHARS) -> str:
    return trim_tail_chars("".join(lines), max_chars)


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


def preview_event_text(value, max_chars: int = EVENT_TEXT_PREVIEW_CHARS) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        except Exception:
            text = str(value)

    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def coerce_short_string(value) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def find_string_by_keys(value, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            found = coerce_short_string(value.get(key))
            if found:
                return found
        for nested in value.values():
            found = find_string_by_keys(nested, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_string_by_keys(item, keys)
            if found:
                return found
    return None


def find_part_dict(value) -> dict | None:
    if isinstance(value, dict):
        for key in ("part", "inputPart", "outputPart"):
            part = value.get(key)
            if isinstance(part, dict):
                return part
        for key in ("parts", "content"):
            items = value.get(key)
            if isinstance(items, list):
                for item in reversed(items):
                    if isinstance(item, dict):
                        return item
        for key in ("data", "message", "body", "payload"):
            nested = value.get(key)
            if isinstance(nested, (dict, list)):
                found = find_part_dict(nested)
                if found is not None:
                    return found
    elif isinstance(value, list):
        for item in reversed(value):
            found = find_part_dict(item)
            if found is not None:
                return found
    return None


def extract_event_type(event: dict) -> str:
    return (
        coerce_short_string(event.get("type"))
        or coerce_short_string(event.get("event"))
        or coerce_short_string(event.get("eventType"))
        or coerce_short_string(event.get("event_type"))
        or "json_event"
    )


def extract_part_type(event: dict, part: dict | None) -> str | None:
    if part is not None:
        found = (
            coerce_short_string(part.get("type"))
            or coerce_short_string(part.get("partType"))
            or coerce_short_string(part.get("kind"))
        )
        if found:
            return found
    return (
        coerce_short_string(event.get("part_type"))
        or coerce_short_string(event.get("partType"))
        or coerce_short_string(event.get("kind"))
    )


def find_tool_name(value) -> str | None:
    if isinstance(value, dict):
        for key in ("toolName", "tool_name"):
            found = coerce_short_string(value.get(key))
            if found:
                return found

        tool = value.get("tool")
        if isinstance(tool, str) and tool.strip():
            return tool.strip()
        if isinstance(tool, dict):
            found = (
                coerce_short_string(tool.get("name"))
                or coerce_short_string(tool.get("id"))
                or find_tool_name(tool)
            )
            if found:
                return found

        type_value = coerce_short_string(value.get("type")) or ""
        name = coerce_short_string(value.get("name"))
        if name and "tool" in type_value.lower():
            return name

        for nested in value.values():
            found = find_tool_name(nested)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_tool_name(item)
            if found:
                return found
    return None


def extract_event_text(event: dict, part: dict | None) -> str | None:
    text_keys = ("text", "delta", "content", "output")
    if part is not None:
        for key in text_keys:
            found = coerce_short_string(part.get(key))
            if found:
                return found
    return find_string_by_keys(event, text_keys)


def summarize_opencode_event(event: dict, observed_at: str) -> dict:
    part = find_part_dict(event)
    event_type = extract_event_type(event)
    summary = {
        "type": event_type,
        "timestamp": (
            find_string_by_keys(event, ("timestamp", "time", "createdAt", "created_at"))
            or observed_at
        ),
    }

    session_id = find_session_id(event)
    if session_id:
        summary["sessionID"] = session_id

    message_id = find_string_by_keys(event, ("messageID", "messageId", "message_id"))
    if message_id:
        summary["messageID"] = message_id

    part_type = extract_part_type(event, part)
    if part_type:
        summary["part_type"] = part_type

    tool_name = find_tool_name(event)
    if tool_name:
        summary["tool_name"] = tool_name

    text_preview = preview_event_text(extract_event_text(event, part))
    if text_preview:
        summary["text_preview"] = text_preview

    reason = find_string_by_keys(event, ("reason", "finishReason", "finish_reason", "stopReason", "stop_reason"))
    if reason:
        summary["reason"] = preview_event_text(reason, 120)

    status = find_string_by_keys(event, ("status", "state"))
    if status:
        summary["status"] = preview_event_text(status, 120)

    return summary


def summary_has_tool_activity(summary: dict) -> bool:
    event_type = str(summary.get("type") or "").lower()
    part_type = str(summary.get("part_type") or "").lower()
    return bool(summary.get("tool_name") or "tool" in event_type or "tool" in part_type)


def summary_has_text_activity(summary: dict) -> bool:
    event_type = str(summary.get("type") or "").lower()
    part_type = str(summary.get("part_type") or "").lower()
    return bool(
        summary.get("text_preview")
        and not summary_has_tool_activity(summary)
        and ("text" in event_type or "text" in part_type or "message" in event_type or not part_type)
    )


def compact_summary_text(summary: dict | None) -> str:
    if not summary:
        return ""
    parts = [
        summary.get("type"),
        summary.get("part_type"),
        summary.get("tool_name"),
        summary.get("status"),
        summary.get("reason"),
        summary.get("text_preview"),
    ]
    return " ".join(str(part) for part in parts if part)


def classify_tool_activity(tool_name: str | None, summary: dict | None = None) -> str:
    if tool_name:
        name_category = classify_tool_name(tool_name)
        if name_category != "other":
            return name_category

    text = f"{tool_name or ''} {compact_summary_text(summary)}".lower()
    if not text.strip():
        return "other"

    if any(marker in text for marker in ("unity", "debug_force_recompile", "debug_check_compilation", "console_get_logs")):
        return "unity"
    if any(marker in text for marker in ("bash", "shell", "terminal", "exec", "command", "powershell", "cmd")):
        return "bash"
    if any(marker in text for marker in ("edit", "write", "patch", "apply_patch", "replace", "delete", "move", "rename", "create")):
        return "edit"
    if any(marker in text for marker in ("glob", "list", "ls", "directory", "dir", "get-childitem")):
        return "list"
    if any(marker in text for marker in ("read", "open", "view", "cat", "grep", "rg", "search", "find")):
        return "read"
    return "other"


def classify_tool_name(tool_name: str | None) -> str:
    text = str(tool_name or "").lower().strip()
    if not text:
        return "other"

    if any(marker in text for marker in ("unity", "debug_force_recompile", "debug_check_compilation", "console_get_logs")):
        return "unity"
    if any(marker in text for marker in ("bash", "shell", "terminal", "exec", "command", "powershell", "cmd", "pytest", "unittest")):
        return "bash"
    if any(marker in text for marker in ("glob", "list", "ls", "directory", "dir", "get-childitem")):
        return "list"
    if any(marker in text for marker in ("read", "open", "view", "cat", "grep", "rg", "search", "find")):
        return "read"
    if any(marker in text for marker in ("edit", "write", "patch", "apply_patch", "replace", "delete", "move", "rename", "create")):
        return "edit"
    return "other"


def ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def detect_observed_validation_tools_from_text(text: str) -> list[str]:
    lowered = text.lower()
    tools: list[str] = []
    if "debug_force_recompile" in lowered or "unity_skills_debug_force_recompile" in lowered:
        tools.append("unity_skills_debug_force_recompile")
    if (
        "debug_check_compilation" in lowered
        or "unity_skills_debug_check_compilation" in lowered
        or "compile check" in lowered
        or "compilation check" in lowered
    ):
        tools.append("unity_skills_debug_check_compilation")
    if "console_get_logs" in lowered or "unity_skills_console_get_logs" in lowered:
        tools.append("unity_skills_console_get_logs")
    if "py_compile" in lowered:
        tools.append("py_compile")
    if "unittest" in lowered or "python -b -m unittest" in lowered or "python -m unittest" in lowered:
        tools.append("python_unittest")
    if "git diff --check" in lowered:
        tools.append("git_diff_check")
    if any(
        marker in lowered
        for marker in (
            "pytest",
            "npm test",
            "dotnet test",
            "go test",
            "cargo test",
            "mvn test",
            "gradle test",
            "test command",
            "running tests",
            "tests failed",
            "test failed",
            "tests passed",
            "all tests passed",
            "compilation failed",
            "compile check passed",
        )
    ):
        tools.append("unknown_test_command")
    return ordered_unique(tools)


def text_has_validation_result_marker(text: str) -> bool:
    lowered = text.lower()
    return bool(
        re.search(r"\b\d+\s+errors?\b", lowered)
        or re.search(r"\berrors?\s*[:=]\s*\d+\b", lowered)
        or re.search(r"\berror\s+count\s*[:=]\s*\d+\b", lowered)
        or any(
            marker in lowered
            for marker in (
                "tests failed",
                "test failed",
                "compilation failed",
                "build failed",
                "tests passed",
                "all tests passed",
                "compilation succeeded",
                "compile check passed",
            )
        )
    )


def summary_has_validation_execution_context(summary: dict) -> bool:
    if not summary_has_tool_activity(summary):
        return False
    tool_name = str(summary.get("tool_name") or "").lower()
    name_category = classify_tool_name(tool_name)
    if name_category in {"read", "list"}:
        return False
    if name_category in VALIDATION_EXECUTION_TOOL_CATEGORIES:
        return True
    if any(
        marker in tool_name
        for marker in (
            "bash",
            "shell",
            "cmd",
            "powershell",
            "terminal",
            "exec",
            "command",
            "test",
            "pytest",
            "unittest",
            "debug_force_recompile",
            "debug_check_compilation",
            "console_get_logs",
        )
    ):
        return True

    if tool_name:
        return False

    event_type = str(summary.get("type") or "").lower()
    part_type = str(summary.get("part_type") or "").lower()
    text = compact_summary_text(summary).lower()
    if not ("tool" in event_type or "tool" in part_type):
        return False
    if not detect_observed_validation_tools_from_text(text):
        return False
    return any(
        marker in text
        for marker in (
            "command",
            "executed",
            "ran",
            "running",
            "exit code",
            "return code",
            "stdout",
            "stderr",
        )
    )


def remember_observed_validation_tools_locked(target, text: str, *, execution_context: bool = False) -> None:
    if not hasattr(target, "observed_validation_tools"):
        return
    if not execution_context:
        return
    for tool in detect_observed_validation_tools_from_text(text):
        if tool not in target.observed_validation_tools:
            target.observed_validation_tools.append(tool)
    if (target.observed_validation_tools or text_has_validation_result_marker(text)) and hasattr(
        target,
        "observed_validation_texts",
    ):
        target.observed_validation_texts.append(preview_event_text(text, EVENT_TEXT_PREVIEW_CHARS) or "")


def record_stdout_event_diagnostics_locked(target, text: str, observed_at: str) -> None:
    if not hasattr(target, "recent_events"):
        return

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        summary = summarize_opencode_event(event, observed_at)
        target.recent_events.append(summary)
        target.recent_event_count += 1
        if getattr(target, "first_event_at", None) is None:
            target.first_event_at = observed_at
        target.last_event_observed_at = observed_at
        target.last_event_type = summary.get("type")
        target.last_event_at = summary.get("timestamp")
        target.last_event_summary = dict(summary)

        session_id = summary.get("sessionID")
        if session_id:
            target.last_session_id = session_id

        if summary_has_text_activity(summary):
            target.last_text_output = summary.get("text_preview")

        if summary_has_tool_activity(summary):
            if getattr(target, "first_tool_at", None) is None:
                target.first_tool_at = observed_at
            target.last_tool_name = summary.get("tool_name")
            target.last_tool_event = dict(summary)
            category = classify_tool_activity(summary.get("tool_name"), summary)
            counts = getattr(target, "tool_activity_counts", None)
            if isinstance(counts, dict):
                counts[category] = int(counts.get(category, 0)) + 1
            if summary_has_validation_execution_context(summary):
                remember_observed_validation_tools_locked(
                    target,
                    compact_summary_text(summary),
                    execution_context=True,
                )

        event_type = str(summary.get("type") or "").lower()
        part_type = str(summary.get("part_type") or "").lower()
        if "step" in event_type or "step" in part_type or summary.get("reason"):
            if summary.get("status"):
                target.last_step_status = summary.get("status")
            if summary.get("reason"):
                target.last_step_reason = summary.get("reason")


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
                record_stdout_event_diagnostics_locked(target, output_text, now)
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


def file_fingerprint(base_dir: str, path: str, status_code: str) -> str:
    absolute_path = Path(base_dir) / path
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


def build_git_status_snapshot(base_dir: str, stdout: str, *, git_root: str | None = None, git_root_error: str | None = None) -> GitStatusSnapshot:
    entries = parse_git_status_entries(stdout)
    files_by_key = {key: path for key, (path, _status_code) in entries.items()}
    fingerprints = {
        key: file_fingerprint(base_dir, path, status_code)
        for key, (path, status_code) in entries.items()
    }
    return GitStatusSnapshot(
        available=True,
        files=sort_paths(list(files_by_key.values())),
        fingerprints=fingerprints,
        git_root=git_root,
        git_root_error=git_root_error,
    )


def collect_git_root(working_dir: str) -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            ["git", "-C", working_dir, "rev-parse", "--show-toplevel"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except Exception as exc:
        return None, str(exc)

    if result.returncode != 0:
        error = (result.stderr or result.stdout or "git rev-parse failed").strip()
        return None, trim_tail(error)

    git_root = result.stdout.strip()
    if not git_root:
        return None, "git rev-parse returned an empty git root"
    return str(Path(git_root).resolve(strict=False)), None


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

    git_root, git_root_error = collect_git_root(working_dir)
    base_dir = git_root or working_dir
    return build_git_status_snapshot(
        base_dir,
        result.stdout,
        git_root=git_root,
        git_root_error=git_root_error,
    )


def collect_git_status_entry_map(working_dir: str) -> tuple[dict[str, tuple[str, str]], str | None, str | None, str | None]:
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
        return {}, str(exc), None, None

    if result.returncode != 0:
        error = (result.stderr or result.stdout or "git status failed").strip()
        return {}, trim_tail(error), None, None

    git_root, git_root_error = collect_git_root(working_dir)
    return parse_git_status_entries(result.stdout), None, git_root, git_root_error


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


def make_untracked_file_diff(base_dir: str, path: str) -> tuple[str, str | None]:
    absolute_path = Path(base_dir) / path
    if not absolute_path.is_file():
        return "", "not_a_regular_file"
    try:
        with absolute_path.open("rb") as file:
            data = file.read(MAX_DIFF_CHARS + 1)
    except OSError as exc:
        return "", str(exc)
    if len(data) > MAX_DIFF_CHARS:
        return "", "file_too_large"
    if b"\x00" in data:
        return "", "binary_file"

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
    git_root: str | None,
    git_root_error: str | None,
    new_changed_files: list[str],
    allowed_paths: list[str] | None,
    forbidden_paths: list[str] | None,
) -> tuple[bool, list[str], list[str], dict]:
    allowed = [path for path in list(allowed_paths or []) if str(path).strip()]
    forbidden = [path for path in list(forbidden_paths or []) if str(path).strip()]
    forbidden_changed_files: list[str] = []
    extra_changed_files: list[str] = []
    file_matches: list[dict] = []

    for changed_file in new_changed_files:
        forbidden_match = first_path_policy_match(working_dir, git_root, changed_file, forbidden)
        allowed_match = first_path_policy_match(working_dir, git_root, changed_file, allowed)

        if forbidden_match is not None:
            forbidden_changed_files.append(changed_file)
            verdict = "forbidden"
        elif allowed and allowed_match is None:
            extra_changed_files.append(changed_file)
            verdict = "extra"
        else:
            verdict = "allowed"

        if len(file_matches) < MAX_PATH_POLICY_DIAGNOSTIC_ENTRIES:
            file_matches.append(
                {
                    "file": trim_diagnostic_text(changed_file),
                    "verdict": verdict,
                    "allowed_by": allowed_match,
                    "forbidden_by": forbidden_match,
                }
            )

    policy_violation = bool(forbidden_changed_files or extra_changed_files)
    diagnostics = {
        "working_dir": trim_diagnostic_text(working_dir),
        "git_root": trim_diagnostic_text(git_root),
        "git_root_error": trim_diagnostic_text(git_root_error),
        "checked_files_basis": "new_changed_files",
        "checked_files_count": len(new_changed_files),
        "allowed_paths_normalized": normalize_policy_paths_for_diagnostics(working_dir, git_root, allowed),
        "forbidden_paths_normalized": normalize_policy_paths_for_diagnostics(working_dir, git_root, forbidden),
        "file_matches": file_matches,
        "file_matches_truncated": len(new_changed_files) > MAX_PATH_POLICY_DIAGNOSTIC_ENTRIES,
    }
    return policy_violation, extra_changed_files, forbidden_changed_files, diagnostics


def apply_git_snapshot(job: OpenCodeJob, snapshot: GitStatusSnapshot, *, set_preexisting: bool = False) -> None:
    with job.lock:
        now = utc_now()
        now_seconds = parse_timestamp_seconds(now)
        previous_snapshot_seconds = parse_timestamp_seconds(job.last_git_snapshot_at)
        snapshot_gap_seconds = elapsed_seconds(previous_snapshot_seconds, now_seconds)
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
        policy_violation, extra_changed_files, forbidden_changed_files, path_policy_diagnostics = evaluate_path_policy(
            job.working_dir,
            snapshot.git_root,
            snapshot.git_root_error,
            new_changed_files,
            job.allowed_paths,
            job.forbidden_paths,
        )

        job.all_changed_files = all_changed_files
        job.new_changed_files = new_changed_files
        job.observed_change_fingerprints = current_change_fingerprints
        job.changed_files = all_changed_files
        if new_changed_files and current_change_fingerprints != previous_change_fingerprints:
            if job.first_change_at is None:
                job.first_change_at = now
            job.last_change_at = now
            if snapshot_gap_seconds is not None and snapshot_gap_seconds <= STALL_CHANGED_FILES_NO_ACTIVITY_SECONDS:
                job.last_activity_at = now
                job.last_trusted_change_activity_at = now
            job.change_version += 1
            job.change_event.set()
        job.policy_violation = policy_violation
        job.extra_changed_files = extra_changed_files
        job.forbidden_changed_files = forbidden_changed_files
        job.git_status_available = snapshot.available
        job.git_status_error = snapshot.error
        job.git_root = snapshot.git_root
        job.git_root_error = snapshot.git_root_error
        job.path_policy_diagnostics = path_policy_diagnostics
        job.last_git_snapshot_at = now


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


def wait_for_update(
    job: OpenCodeJob,
    wait_seconds: float,
    return_on: str,
) -> dict:
    wait_seconds = clamp_wait_wait_seconds(wait_seconds)
    return_on = return_on if return_on in VALID_RETURN_ON else "interesting"

    with job.lock:
        prev_change_version = job.change_version

    was_zero_at_start = prev_change_version == 0

    deadline = time.monotonic() + wait_seconds
    started_at = time.monotonic()
    first_change_during_wait = False

    def _first_change_occurred(result: dict) -> bool:
        return first_change_during_wait or (
            was_zero_at_start and bool(result.get("new_changed_files"))
        )

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        if return_on == "terminal":
            if job.done_event.wait(min(WAIT_POLL_INTERVAL, remaining)):
                break
            continue

        refresh_job_snapshot(job)
        with job.lock:
            if job.change_version != prev_change_version:
                prev_change_version = job.change_version
                first_change_during_wait = True
            if job.done_event.is_set():
                break
            if job.policy_violation:
                break

        result = job_to_result(job)
        if result["caller_update_recommended"]:
            if result["caller_update_reason"] == "first_change_seen" and not _first_change_occurred(result):
                pass
            else:
                waited = round(time.monotonic() - started_at, 3)
                return {
                    "wait_return_reason": result["caller_update_reason"],
                    "interesting_update": True,
                    "waited_seconds": waited,
                }

        job.done_event.wait(min(WAIT_POLL_INTERVAL, max(remaining, 0)))

    final = job_to_result(job)
    waited = round(time.monotonic() - started_at, 3)

    if final["status"] in {"completed", "failed", "cancelled"}:
        if _first_change_occurred(final):
            reason = "first_change_seen"
        else:
            reason = "terminal_status"
        interesting = True
    elif final["caller_update_recommended"]:
        if final["caller_update_reason"] == "first_change_seen" and not _first_change_occurred(final):
            reason = "wait_timeout"
            interesting = False
        else:
            reason = final["caller_update_reason"]
            interesting = True
    else:
        reason = "wait_timeout"
        interesting = False

    return {
        "wait_return_reason": reason,
        "interesting_update": interesting,
        "waited_seconds": waited,
    }


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


def parse_timestamp_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def elapsed_seconds(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max(0.0, end - start), 3)


def most_recent_activity_seconds(job: OpenCodeJob) -> float | None:
    candidates = [
        parse_timestamp_seconds(job.last_activity_at),
        parse_timestamp_seconds(job.first_output_at),
        parse_timestamp_seconds(job.last_trusted_change_activity_at),
        parse_timestamp_seconds(job.started_at),
    ]
    available = [candidate for candidate in candidates if candidate is not None]
    if not available:
        return None
    return max(available)


def build_stall_diagnostics(
    job: OpenCodeJob,
    *,
    status: str,
    process_running: bool,
    runtime_seconds: float | None,
    idle_seconds: float | None,
) -> dict:
    is_active = status in ACTIVE_STATUSES and process_running
    is_stalled = False
    stall_reason = None

    if is_active:
        has_new_changes = bool(job.new_changed_files)
        has_output_or_changes = bool(job.first_output_at or job.first_change_at)
        if (
            has_new_changes
            and idle_seconds is not None
            and idle_seconds >= STALL_CHANGED_FILES_NO_ACTIVITY_SECONDS
        ):
            is_stalled = True
            stall_reason = "changed_files_no_recent_activity"
        elif (
            not has_output_or_changes
            and runtime_seconds is not None
            and runtime_seconds >= STALL_NO_OUTPUT_SECONDS
        ):
            is_stalled = True
            stall_reason = "no_output_no_change_after_start"
        elif (
            idle_seconds is not None
            and idle_seconds >= STALL_NO_ACTIVITY_SECONDS
        ):
            is_stalled = True
            stall_reason = "timed_out_waiting_for_completion" if status == "timed_out" else "no_recent_activity"

    if is_active:
        if is_stalled:
            suggested_action = "review_diff_then_consider_cancel" if job.new_changed_files else "consider_cancel"
        elif status == "timed_out":
            suggested_action = "continue_polling_or_consider_cancel"
        else:
            suggested_action = "continue_polling"
    elif status in {"failed", "cancelled", "timed_out"} and job.new_changed_files:
        suggested_action = "review_diff_or_git_status"
    elif status == "completed" and job.policy_violation:
        suggested_action = "review_policy_violation"
    elif status == "completed":
        suggested_action = "review_result"
    else:
        suggested_action = "inspect_status"

    return {
        "is_stalled": is_stalled,
        "stall_reason": stall_reason,
        "suggested_action": suggested_action,
        "potential_incomplete_changes_risk": is_active and is_stalled and bool(job.new_changed_files),
    }


def build_change_risk_fields(job: OpenCodeJob, status: str) -> dict:
    has_new_changes = bool(job.new_changed_files)
    review_required = (
        ((status in ACTIVE_STATUSES or status in {"failed", "cancelled"}) and has_new_changes)
        or (status == "completed" and job.policy_violation)
    )
    incomplete_changes_risk = status in {"failed", "cancelled", "timed_out"} and has_new_changes
    if job.preexisting_changed_files:
        preexisting_dirty_warning = (
            f"Worktree had {len(job.preexisting_changed_files)} preexisting dirty file(s) before this job; "
            "all_changed_files may include changes that cannot be attributed solely to this job."
        )
    else:
        preexisting_dirty_warning = ""
    return {
        "review_required": review_required,
        "incomplete_changes_risk": incomplete_changes_risk,
        "preexisting_dirty_warning": preexisting_dirty_warning,
    }


def session_reuse_requested(job: OpenCodeJob) -> bool:
    return bool(job.requested_session_id or job.continue_last or job.fork_session)


def has_effective_output(*values: str | None) -> bool:
    return any(bool(value and value.strip()) for value in values)


def build_no_event_noop_diagnostics(
    job: OpenCodeJob,
    *,
    status: str,
    stdout_tail: str,
    stderr_tail: str,
    stdout_delta: str,
    stderr_delta: str,
) -> dict:
    no_event_noop_reason = "completed_attached_session_reuse_without_events_changes_or_output"
    no_event_noop_risk = (
        status == "completed"
        and job.exit_code == 0
        and (job.attached_to_server or bool(job.server_id))
        and session_reuse_requested(job)
        and job.recent_event_count == 0
        and not job.new_changed_files
        and not has_effective_output(
            stdout_tail,
            stderr_tail,
            stdout_delta,
            stderr_delta,
            job.stdout_buffer,
            job.stderr_buffer,
        )
    )
    return {
        "no_event_noop_risk": no_event_noop_risk,
        "no_event_noop_reason": no_event_noop_reason if no_event_noop_risk else None,
    }


def build_no_event_noop_note() -> str:
    return (
        "OpenCode completed with no stdout JSON events and no job-scoped changes; "
        "session reuse may have no-oped. Do not treat completed/success as normal completion "
        "without review; check the session scope or retry without session_id, a fresh session, "
        "or a fresh server."
    )


def build_validation_note(status: str) -> str:
    base = (
        "The MCP wrapper does not run validation. A prompt asking OpenCode to run validation "
        "is not proof that validation actually ran; inspect stdout/stderr, the OpenCode report, "
        "or local validation results."
    )
    if status != "completed":
        return f"{base} This job is not completed, so prompt-requested validation may not have run."
    return base


def compact_progress_message(text: str) -> str:
    return preview_event_text(text, PROGRESS_MESSAGE_MAX_CHARS) or ""


def tool_activity_summary_locked(job: OpenCodeJob) -> dict:
    counts = dict(job.tool_activity_counts or {})
    return {category: int(counts.get(category, 0)) for category in TOOL_ACTIVITY_CATEGORIES}


def ordered_validation_tools(tools: list[str]) -> list[str]:
    unique = ordered_unique([tool for tool in tools if tool])
    order = {tool: index for index, tool in enumerate(OBSERVED_VALIDATION_TOOL_ORDER)}
    return sorted(unique, key=lambda item: (order.get(item, len(order)), item))


def extract_validation_error_counts(text: str) -> list[int]:
    patterns = (
        r"\b(\d+)\s+errors?\b",
        r"\berrors?\s*[:=]\s*(\d+)\b",
        r"\berror\s+count\s*[:=]\s*(\d+)\b",
    )
    counts: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            try:
                counts.append(int(match.group(1)))
            except (TypeError, ValueError):
                continue
    return counts


def extract_validation_errors_count(text: str) -> int | None:
    counts = extract_validation_error_counts(text)
    if not counts:
        return None
    nonzero_counts = [count for count in counts if count > 0]
    if nonzero_counts:
        return nonzero_counts[-1]
    return 0


def build_observed_validation_diagnostics_locked(job: OpenCodeJob) -> dict:
    execution_text = " ".join(str(item) for item in job.observed_validation_texts if item)
    scan_text = " ".join(
        part
        for part in (
            execution_text,
            " ".join(job.observed_validation_tools),
        )
        if part
    )
    tools = ordered_validation_tools(
        list(job.observed_validation_tools) + detect_observed_validation_tools_from_text(execution_text)
    )

    if not tools:
        return {
            "observed_validation_summary": "No validation activity observed.",
            "observed_validation_tools": [],
            "observed_validation_result": "none",
            "observed_validation_errors_count": None,
        }

    error_counts = extract_validation_error_counts(scan_text)
    errors_count = extract_validation_errors_count(scan_text)
    lowered = scan_text.lower()
    has_failure_marker = any(
        marker in lowered
        for marker in ("tests failed", "test failed", "compilation failed", "build failed")
    )
    has_pass_marker = any(
        marker in lowered
        for marker in ("tests passed", "all tests passed", "compilation succeeded", "compile check passed")
    )
    if has_failure_marker or any(count > 0 for count in error_counts):
        result = "failed"
    elif errors_count == 0:
        result = "passed"
    elif has_pass_marker:
        result = "passed"
    elif re.search(r"\b(ok|passed|success|succeeded)\b", lowered) and not re.search(r"\b(fail|failed|error)\b", lowered):
        result = "passed"
    else:
        result = "inconclusive"

    if result == "failed":
        summary = "Validation activity observed with a failing-looking result."
    elif errors_count is not None and any(tool.startswith("unity_skills_") for tool in tools):
        summary = f"Unity Skills validation activity observed: {errors_count} error(s)."
    elif result == "passed":
        summary = "Validation activity observed with a passing-looking result."
    else:
        summary = "Validation activity observed, but the result is inconclusive."

    return {
        "observed_validation_summary": compact_progress_message(summary),
        "observed_validation_tools": tools,
        "observed_validation_result": result,
        "observed_validation_errors_count": errors_count,
    }


def build_time_diagnostics_locked(job: OpenCodeJob, end_timestamp: float | None) -> dict:
    started_timestamp = parse_timestamp_seconds(job.started_at)
    first_output_timestamp = parse_timestamp_seconds(job.first_output_at)
    first_event_timestamp = parse_timestamp_seconds(job.first_event_at)
    first_tool_timestamp = parse_timestamp_seconds(job.first_tool_at)
    first_change_timestamp = parse_timestamp_seconds(job.first_change_at)
    last_event_timestamp = parse_timestamp_seconds(job.last_event_observed_at)
    last_change_timestamp = parse_timestamp_seconds(job.last_change_at)
    return {
        "time_to_first_output_seconds": elapsed_seconds(started_timestamp, first_output_timestamp),
        "time_to_first_event_seconds": elapsed_seconds(started_timestamp, first_event_timestamp),
        "time_to_first_tool_seconds": elapsed_seconds(started_timestamp, first_tool_timestamp),
        "time_to_first_change_seconds": elapsed_seconds(started_timestamp, first_change_timestamp),
        "seconds_since_last_event": elapsed_seconds(last_event_timestamp, end_timestamp),
        "seconds_since_last_change": elapsed_seconds(last_change_timestamp, end_timestamp),
    }


def short_gap_label(label: str) -> str:
    return preview_event_text(label.replace("_", " "), LONG_GAP_LABEL_MAX_CHARS) or label


def guess_gap_phase(after_label: str, before_label: str, progress_phase: str, validation_observed: bool) -> str:
    if before_label in {"first_output", "current_status", "finished"} and after_label == "job_started":
        return "waiting_first_output"
    if before_label == "first_event":
        return "slow_before_first_event"
    if before_label in {"first_change", "last_change"}:
        return "slow_before_first_change"
    if after_label in {"first_change", "last_change"}:
        return "slow_validation" if validation_observed else "slow_after_edit"
    if validation_observed:
        return "slow_validation"
    if progress_phase in {"reading_context", "long_context_or_planning"}:
        return "slow_context_reading"
    if progress_phase in {"planning_or_reasoning", "waiting_first_output"}:
        return progress_phase
    return "unknown"


def build_long_gap_segments_locked(
    job: OpenCodeJob,
    *,
    end_timestamp: float | None,
    progress_phase: str,
    validation_observed: bool,
) -> list[dict]:
    points: list[tuple[float, str, int]] = []

    def add_point(timestamp_text: str | None, label: str, order: int) -> None:
        timestamp = parse_timestamp_seconds(timestamp_text)
        if timestamp is not None:
            points.append((timestamp, label, order))

    add_point(job.started_at, "job_started", 0)
    add_point(job.first_output_at, "first_output", 1)
    add_point(job.first_event_at, "first_event", 2)
    add_point(job.first_tool_at, "first_tool", 3)
    add_point(job.first_change_at, "first_change", 4)
    add_point(job.last_change_at, "last_change", 5)
    add_point(job.last_event_observed_at, "last_event", 6)
    if end_timestamp is not None:
        end_label = "finished" if job.finished_at else "current_status"
        points.append((end_timestamp, end_label, 7))

    points.sort(key=lambda item: (item[0], item[2]))
    if len(points) < 2:
        return []

    gaps: list[dict] = []
    for index in range(1, len(points)):
        previous_timestamp, previous_label, _previous_order = points[index - 1]
        current_timestamp, current_label, _current_order = points[index]
        duration = elapsed_seconds(previous_timestamp, current_timestamp)
        if duration is None or duration < LONG_GAP_MIN_SECONDS:
            continue
        gaps.append(
            {
                "duration_seconds": duration,
                "after": short_gap_label(previous_label),
                "before": short_gap_label(current_label),
                "phase_guess": guess_gap_phase(previous_label, current_label, progress_phase, validation_observed),
            }
        )

    gaps.sort(key=lambda item: item["duration_seconds"], reverse=True)
    return gaps[:MAX_LONG_GAP_SEGMENTS]


def session_reuse_mode_from_flags(
    requested_session_id: str | None,
    continue_last: bool,
    fork_session: bool,
) -> str:
    if fork_session:
        return "fork_session"
    if continue_last:
        return "continue_last"
    if requested_session_id:
        return "explicit_session"
    return "none"


def session_keys_for_snapshot(snapshot: dict) -> set[str]:
    return {
        str(value)
        for value in (
            snapshot.get("requested_session_id"),
            snapshot.get("session_id"),
            snapshot.get("last_session_id"),
        )
        if value
    }


def job_session_snapshot(job: OpenCodeJob) -> dict:
    with job.lock:
        return {
            "job_id": job.job_id,
            "working_dir": job.working_dir,
            "requested_session_id": job.requested_session_id,
            "session_id": job.session_id,
            "last_session_id": job.last_session_id,
            "continue_last": job.continue_last,
            "fork_session": job.fork_session,
            "status": job.status,
            "started_at": job.started_at,
            "new_changed_files": list(job.new_changed_files),
            "preexisting_changed_files": list(job.preexisting_changed_files),
        }


def collect_session_history_context(job: OpenCodeJob) -> dict:
    target = job_session_snapshot(job)
    session_keys = session_keys_for_snapshot(target)
    preferred_key = (
        target.get("requested_session_id")
        or target.get("session_id")
        or target.get("last_session_id")
    )
    reuse_detected = bool(
        target.get("requested_session_id")
        or target.get("continue_last")
        or target.get("fork_session")
    )
    context = {
        "reuse_detected": reuse_detected,
        "mode": session_reuse_mode_from_flags(
            target.get("requested_session_id"),
            bool(target.get("continue_last")),
            bool(target.get("fork_session")),
        ),
        "same_session_recent_job_count": 0,
        "same_session_last_job_status": None,
        "previous_abnormal_status": False,
        "working_dir_mismatch": False,
        "likely_preexisting_same_session_files": [],
        "history_available": False,
    }
    if not preferred_key and not session_keys:
        return context

    with _REGISTRY_LOCK:
        job_list = list(_JOBS.values())

    matches: list[dict] = []
    for candidate in job_list:
        snapshot = job_session_snapshot(candidate)
        candidate_keys = session_keys_for_snapshot(snapshot)
        if preferred_key:
            matched = preferred_key in candidate_keys
        else:
            matched = bool(session_keys & candidate_keys)
        if matched:
            matches.append(snapshot)

    matches.sort(key=lambda item: parse_timestamp_seconds(item.get("started_at")) or 0.0)
    current_started = parse_timestamp_seconds(target.get("started_at")) or 0.0
    previous_matches = [
        item
        for item in matches
        if item.get("job_id") != target.get("job_id")
        and (parse_timestamp_seconds(item.get("started_at")) or 0.0) <= current_started
    ]
    context["same_session_recent_job_count"] = len(matches)
    context["history_available"] = bool(previous_matches)
    if previous_matches:
        last_job = previous_matches[-1]
        context["same_session_last_job_status"] = last_job.get("status")
    context["previous_abnormal_status"] = any(
        item.get("status") in {"failed", "cancelled", "timed_out"}
        for item in previous_matches
    )
    context["working_dir_mismatch"] = any(
        item.get("working_dir") and item.get("working_dir") != target.get("working_dir")
        for item in previous_matches
    )

    preexisting_by_key = {
        git_path_key(path): path
        for path in target.get("preexisting_changed_files", [])
    }
    related_files: list[str] = []
    for previous in previous_matches:
        for changed_file in previous.get("new_changed_files", []):
            key = git_path_key(changed_file)
            if key in preexisting_by_key:
                related_files.append(preexisting_by_key[key])
    context["likely_preexisting_same_session_files"] = sort_paths(ordered_unique(related_files))[:20]
    return context


def build_session_reuse_diagnostics(
    context: dict,
    *,
    no_event_noop_risk: bool,
) -> dict:
    reuse_detected = bool(context.get("reuse_detected"))
    likely_files = list(context.get("likely_preexisting_same_session_files") or [])
    session_reuse_risk = False
    if not reuse_detected:
        note = "no_session_reuse"
    elif no_event_noop_risk:
        note = "no_event_noop_risk"
        session_reuse_risk = True
    elif context.get("working_dir_mismatch"):
        note = "working_dir_mismatch"
        session_reuse_risk = True
    elif context.get("previous_abnormal_status"):
        note = "previous_session_job_failed"
        session_reuse_risk = True
    elif context.get("history_available"):
        note = "same_working_dir_recent_session"
    else:
        note = "session_history_unavailable"

    return {
        "session_reuse_detected": reuse_detected,
        "session_reuse_mode": context.get("mode") or "none",
        "session_reuse_risk": session_reuse_risk,
        "session_reuse_note": note,
        "same_session_recent_job_count": int(context.get("same_session_recent_job_count") or 0),
        "same_session_last_job_status": context.get("same_session_last_job_status"),
        "likely_preexisting_from_same_session": bool(likely_files),
        "likely_preexisting_same_session_files": likely_files,
    }


def is_validation_progress(validation_diagnostics: dict) -> bool:
    return bool(validation_diagnostics.get("observed_validation_tools"))


def build_root_cause_guess(
    *,
    status: str,
    progress_phase: str,
    runtime_seconds: float | None,
    idle_seconds: float | None,
    time_diagnostics: dict,
    tool_activity_summary: dict,
    no_event_noop_risk: bool,
    is_stalled: bool,
    validation_observed: bool,
) -> str:
    if no_event_noop_risk:
        return "no_event_noop"
    if is_stalled:
        return "stalled_running"
    if status not in ACTIVE_STATUSES and status != "completed":
        return "unknown"

    runtime = runtime_seconds or 0.0
    time_to_first_output = time_diagnostics["time_to_first_output_seconds"]
    time_to_first_event = time_diagnostics["time_to_first_event_seconds"]
    time_to_first_change = time_diagnostics["time_to_first_change_seconds"]
    seconds_since_last_change = time_diagnostics["seconds_since_last_change"]

    if time_to_first_output is not None and time_to_first_output >= SLOW_FIRST_OUTPUT_SECONDS:
        return "slow_startup_or_attach"
    if time_to_first_output is None and status in ACTIVE_STATUSES and runtime >= PROGRESS_STARTUP_SECONDS:
        return "slow_startup_or_attach"
    if time_to_first_event is not None and time_to_first_event >= SLOW_FIRST_EVENT_SECONDS:
        return "slow_before_first_event"
    if time_to_first_event is None and time_to_first_output is not None and status in ACTIVE_STATUSES:
        return "slow_before_first_event"
    if time_to_first_change is None and runtime >= NO_FIRST_CHANGE_BUDGET_SECONDS:
        if tool_activity_summary.get("read", 0) or tool_activity_summary.get("list", 0):
            return "slow_context_reading"
        return "slow_before_first_change"
    if time_to_first_change is not None and time_to_first_change >= NO_FIRST_CHANGE_BUDGET_SECONDS:
        if tool_activity_summary.get("read", 0) or tool_activity_summary.get("list", 0):
            return "slow_context_reading"
        return "slow_before_first_change"
    if (
        time_to_first_change is not None
        and seconds_since_last_change is not None
        and seconds_since_last_change >= SLOW_AFTER_CHANGE_SECONDS
    ):
        return "slow_validation" if validation_observed else "slow_after_edit"
    if time_to_first_change is not None and validation_observed and status in ACTIVE_STATUSES:
        return "slow_validation"
    if time_to_first_change is not None and idle_seconds is not None and idle_seconds >= STALL_NO_ACTIVITY_SECONDS / 2:
        return "slow_after_edit"
    if progress_phase == "validating":
        return "slow_validation"
    if status == "completed":
        return "completed_normally"
    return "unknown"


def build_progress_diagnostics_locked(
    job: OpenCodeJob,
    *,
    status: str,
    process_running: bool,
    runtime_seconds: float | None,
    idle_seconds: float | None,
    stall_diagnostics: dict,
    event_diagnostics: dict,
    no_event_noop_diagnostics: dict,
    validation_diagnostics: dict,
    time_diagnostics: dict,
    tool_activity_summary: dict,
) -> dict:
    validation_observed = is_validation_progress(validation_diagnostics)
    first_change_recent = (
        time_diagnostics["seconds_since_last_change"] is not None
        and time_diagnostics["seconds_since_last_change"] <= RECENT_FIRST_CHANGE_UPDATE_SECONDS
    )

    if no_event_noop_diagnostics["no_event_noop_risk"]:
        phase = "no_event_noop_risk"
    elif status == "completed":
        phase = "completed"
    elif status == "failed":
        phase = "failed"
    elif status == "cancelled":
        phase = "cancelled"
    elif status == "timed_out":
        phase = "stalled" if stall_diagnostics["is_stalled"] else "timed_out"
    elif stall_diagnostics["is_stalled"]:
        phase = "stalled"
    elif status == "starting":
        phase = "starting"
    elif validation_observed:
        phase = "validating"
    elif job.first_change_at:
        if process_running and event_diagnostics.get("last_step_status") in {"finished", "completed", "done"}:
            phase = "finalizing"
        else:
            phase = "editing"
    elif not job.first_output_at:
        phase = "starting" if (runtime_seconds or 0.0) < PROGRESS_STARTUP_SECONDS else "waiting_first_output"
    elif runtime_seconds is not None and runtime_seconds >= NO_FIRST_CHANGE_BUDGET_SECONDS:
        phase = "long_context_or_planning"
    elif tool_activity_summary.get("read", 0) or tool_activity_summary.get("list", 0):
        phase = "reading_context"
    elif job.recent_event_count > 0 or event_diagnostics.get("last_text_output"):
        phase = "planning_or_reasoning"
    else:
        phase = "waiting_first_output"

    if phase == "no_event_noop_risk":
        message = "Completed with no events, no output, and no job-scoped changes under session reuse; review or retry with a fresh session."
    elif phase == "completed":
        message = "OpenCode completed; review changed files and observed validation before accepting the job."
    elif phase == "failed":
        message = "OpenCode failed; inspect status, stderr/tail only if needed, and any changed files."
    elif phase == "cancelled":
        message = "OpenCode was cancelled; review any job-scoped file changes left in the worktree."
    elif phase == "timed_out":
        message = "The MCP wait window elapsed while OpenCode kept running; poll status or consider cancellation."
    elif phase == "stalled":
        message = f"No trusted activity recently ({stall_diagnostics['stall_reason']}); consider review or cancellation."
    elif phase == "starting":
        message = "OpenCode process is starting; no first output has been observed yet."
    elif phase == "waiting_first_output":
        message = "Waiting for the first OpenCode stdout/stderr output."
    elif phase == "reading_context":
        message = "OpenCode has tool activity that looks like context reading/listing; no job-scoped change observed yet."
    elif phase == "planning_or_reasoning":
        message = "OpenCode has produced events/text, but no job-scoped file change has been observed yet."
    elif phase == "long_context_or_planning":
        message = "No job-scoped file change after the planning budget; likely reading context or planning."
    elif phase == "editing":
        message = "Job-scoped file changes have been observed; OpenCode appears to be editing."
    elif phase == "validating":
        tools = ", ".join(validation_diagnostics.get("observed_validation_tools") or [])
        message = f"Validation-like activity observed{': ' + tools if tools else ''}."
    elif phase == "finalizing":
        message = "A finishing step was observed after edits; OpenCode may be wrapping up."
    else:
        message = "OpenCode progress is observable, but the wrapper cannot classify the current phase."

    if no_event_noop_diagnostics["no_event_noop_risk"]:
        caller_update_recommended = True
        caller_update_reason = "no_event_noop_risk"
    elif job.policy_violation:
        caller_update_recommended = True
        caller_update_reason = "policy_violation"
    elif stall_diagnostics["is_stalled"]:
        caller_update_recommended = True
        caller_update_reason = "stalled"
    elif status in {"completed", "failed", "cancelled", "timed_out"}:
        caller_update_recommended = True
        caller_update_reason = "terminal_status"
    elif validation_observed and validation_diagnostics.get("observed_validation_result") in {"passed", "failed"}:
        caller_update_recommended = True
        caller_update_reason = "validation_observed"
    elif first_change_recent:
        caller_update_recommended = True
        caller_update_reason = "first_change_seen"
    elif phase == "long_context_or_planning":
        caller_update_recommended = True
        caller_update_reason = "no_first_change_after_budget"
    else:
        caller_update_recommended = False
        caller_update_reason = "continue_silent_poll"

    next_poll_by_phase = {
        "not_found": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "no_event_noop_risk": 0,
        "timed_out": 0,
        "stalled": 0,
        "starting": 120,
        "waiting_first_output": 120,
        "reading_context": 120,
        "planning_or_reasoning": 120,
        "long_context_or_planning": 120,
        "editing": 120,
        "validating": 120,
        "finalizing": 120,
    }
    root_cause_guess = build_root_cause_guess(
        status=status,
        progress_phase=phase,
        runtime_seconds=runtime_seconds,
        idle_seconds=idle_seconds,
        time_diagnostics=time_diagnostics,
        tool_activity_summary=tool_activity_summary,
        no_event_noop_risk=no_event_noop_diagnostics["no_event_noop_risk"],
        is_stalled=stall_diagnostics["is_stalled"],
        validation_observed=validation_observed,
    )
    _next_poll_zero_reasons = {
        "terminal_status",
        "policy_violation",
        "stalled",
        "no_event_noop_risk",
        "validation_observed",
        "no_first_change_after_budget",
        "not_found",
    }
    return {
        "progress_phase": phase,
        "progress_message": compact_progress_message(message),
        "caller_update_recommended": caller_update_recommended,
        "caller_update_reason": caller_update_reason,
        "next_poll_after_seconds": 0 if caller_update_reason in _next_poll_zero_reasons else next_poll_by_phase.get(phase, 10),
        "root_cause_guess": root_cause_guess,
    }


def event_summary_phase(summary: dict | None) -> str:
    if not summary:
        return "no_event_seen"

    event_type = str(summary.get("type") or "").lower()
    part_type = str(summary.get("part_type") or "").lower()
    status = str(summary.get("status") or "").lower()
    reason = str(summary.get("reason") or "").lower()

    finished_markers = ("finish", "finished", "complete", "completed", "done", "success", "failed", "error", "stop")
    started_markers = ("start", "started", "running", "pending")

    if "step" in event_type or "step" in part_type:
        if any(marker in status for marker in finished_markers) or any(marker in event_type for marker in finished_markers):
            return "step_finished"
        if any(marker in status for marker in started_markers) or any(marker in event_type for marker in started_markers):
            return "step_started"
        return "step_started"

    if reason and any(marker in reason for marker in finished_markers):
        return "step_finished"

    if summary_has_tool_activity(summary):
        return "tool_activity"
    if summary_has_text_activity(summary):
        return "model_text"
    return "unknown"


def build_event_diagnostics_locked(
    job: OpenCodeJob,
    *,
    status: str,
    process_running: bool,
    stall_diagnostics: dict,
    recent_events_limit: int = DEFAULT_RECENT_EVENTS_LIMIT,
) -> dict:
    last_event_summary = dict(job.last_event_summary) if job.last_event_summary else None
    last_event_phase = event_summary_phase(last_event_summary)
    effective_recent_events_limit = clamp_recent_events_limit(recent_events_limit)
    if effective_recent_events_limit > 0:
        recent_events = [dict(event) for event in list(job.recent_events)[-effective_recent_events_limit:]]
    else:
        recent_events = []

    if status in {"completed", "failed", "cancelled"} or (not process_running and job.finished_at is not None):
        diagnostic_phase = "process_finished"
    elif job.recent_event_count <= 0:
        diagnostic_phase = "no_event_seen"
    elif process_running and stall_diagnostics["is_stalled"]:
        diagnostic_phase = "process_running_no_recent_event"
    elif last_event_phase in {"model_text", "tool_activity", "step_started", "step_finished"}:
        diagnostic_phase = last_event_phase
    else:
        diagnostic_phase = "unknown"

    last_event_at = job.last_event_at or "unknown time"
    if job.recent_event_count <= 0:
        if process_running:
            diagnostic_note = "No OpenCode stdout JSON event has been observed yet; process is still running."
        else:
            diagnostic_note = f"No OpenCode stdout JSON event was observed before process reached status {status}."
    elif stall_diagnostics["is_stalled"]:
        diagnostic_note = (
            f"Job is stalled ({stall_diagnostics['stall_reason']}); last observed OpenCode stdout "
            f"JSON event phase was {last_event_phase} at {last_event_at}."
        )
    elif diagnostic_phase == "process_finished":
        diagnostic_note = (
            f"OpenCode process reached status {status}; last observed stdout JSON event phase was "
            f"{last_event_phase} at {last_event_at}."
        )
    elif process_running:
        diagnostic_note = (
            f"OpenCode process is running; last observed stdout JSON event phase was "
            f"{last_event_phase} at {last_event_at}."
        )
    else:
        diagnostic_note = (
            f"Last observed OpenCode stdout JSON event phase was {last_event_phase} "
            f"at {last_event_at}."
        )

    return {
        "last_event_type": job.last_event_type,
        "last_event_at": job.last_event_at,
        "last_event_summary": last_event_summary,
        "recent_events": recent_events,
        "recent_event_count": job.recent_event_count,
        "last_text_output": job.last_text_output,
        "last_tool_name": job.last_tool_name,
        "last_tool_event": dict(job.last_tool_event) if job.last_tool_event else None,
        "last_step_reason": job.last_step_reason,
        "last_step_status": job.last_step_status,
        "last_session_id": job.last_session_id,
        "diagnostic_phase": diagnostic_phase,
        "diagnostic_note": diagnostic_note,
    }


def empty_event_diagnostics(note: str, phase: str = "no_event_seen") -> dict:
    return {
        "last_event_type": None,
        "last_event_at": None,
        "last_event_summary": None,
        "recent_events": [],
        "recent_event_count": 0,
        "last_text_output": None,
        "last_tool_name": None,
        "last_tool_event": None,
        "last_step_reason": None,
        "last_step_status": None,
        "last_session_id": None,
        "diagnostic_phase": phase,
        "diagnostic_note": note,
    }


def job_to_result(
    job: OpenCodeJob,
    *,
    lock_rejected: bool = False,
    new_job_started: bool = True,
    summary_override: str | None = None,
    stdout_cursor: int | None = None,
    stderr_cursor: int | None = None,
    include_tail: bool = False,
    include_output: bool = False,
    include_delta: bool = False,
    recent_events_limit: int = DEFAULT_RECENT_EVENTS_LIMIT,
    delta_max_chars: int | None = None,
    tail_max_chars: int | None = None,
) -> dict:
    if is_job_active(job):
        refresh_job_snapshot(job)

    session_history_context = collect_session_history_context(job)
    effective_tail_max_chars = clamp_tail_max_chars(tail_max_chars)
    with job.lock:
        full_stdout_tail = tail_to_text(job.stdout_tail, effective_tail_max_chars)
        full_stderr_tail = tail_to_text(job.stderr_tail, effective_tail_max_chars)
        stdout_tail = full_stdout_tail if include_tail else ""
        stderr_tail = full_stderr_tail if include_tail else ""
        stdout_delta_raw, current_stdout_cursor, stdout_delta_truncated = output_delta_locked(
            job,
            "stdout",
            stdout_cursor,
        )
        stderr_delta_raw, current_stderr_cursor, stderr_delta_truncated = output_delta_locked(
            job,
            "stderr",
            stderr_cursor,
        )
        effective_delta_max_chars = clamp_delta_max_chars(delta_max_chars)
        stdout_delta, stdout_delta_response_truncated = truncate_delta_for_response(
            stdout_delta_raw if include_delta else "",
            effective_delta_max_chars,
        )
        stderr_delta, stderr_delta_response_truncated = truncate_delta_for_response(
            stderr_delta_raw if include_delta else "",
            effective_delta_max_chars,
        )
        process_running = job.process is not None and job.process.poll() is None
        output = ""
        if include_output:
            output = trim_tail_chars((full_stdout_tail + "\n" + full_stderr_tail).strip(), effective_tail_max_chars)
        status = job.status
        success = status == "completed" and job.exit_code == 0
        now_timestamp = datetime.now(timezone.utc).timestamp()
        started_timestamp = parse_timestamp_seconds(job.started_at)
        finished_timestamp = parse_timestamp_seconds(job.finished_at)
        end_timestamp = finished_timestamp if finished_timestamp is not None else now_timestamp
        runtime_seconds = elapsed_seconds(started_timestamp, end_timestamp)
        idle_seconds = elapsed_seconds(most_recent_activity_seconds(job), end_timestamp)
        stall_diagnostics = build_stall_diagnostics(
            job,
            status=status,
            process_running=process_running,
            runtime_seconds=runtime_seconds,
            idle_seconds=idle_seconds,
        )
        time_diagnostics = build_time_diagnostics_locked(job, end_timestamp)
        tool_activity_summary = tool_activity_summary_locked(job)
        validation_observation = build_observed_validation_diagnostics_locked(job)
        event_diagnostics = build_event_diagnostics_locked(
            job,
            status=status,
            process_running=process_running,
            stall_diagnostics=stall_diagnostics,
            recent_events_limit=recent_events_limit,
        )
        change_risk_fields = build_change_risk_fields(job, status)
        no_event_noop_diagnostics = build_no_event_noop_diagnostics(
            job,
            status=status,
            stdout_tail=full_stdout_tail,
            stderr_tail=full_stderr_tail,
            stdout_delta=stdout_delta_raw,
            stderr_delta=stderr_delta_raw,
        )
        session_reuse_diagnostics = build_session_reuse_diagnostics(
            session_history_context,
            no_event_noop_risk=no_event_noop_diagnostics["no_event_noop_risk"],
        )
        progress_diagnostics = build_progress_diagnostics_locked(
            job,
            status=status,
            process_running=process_running,
            runtime_seconds=runtime_seconds,
            idle_seconds=idle_seconds,
            stall_diagnostics=stall_diagnostics,
            event_diagnostics=event_diagnostics,
            no_event_noop_diagnostics=no_event_noop_diagnostics,
            validation_diagnostics=validation_observation,
            time_diagnostics=time_diagnostics,
            tool_activity_summary=tool_activity_summary,
        )
        long_gap_segments = build_long_gap_segments_locked(
            job,
            end_timestamp=end_timestamp,
            progress_phase=progress_diagnostics["progress_phase"],
            validation_observed=is_validation_progress(validation_observation),
        )
        suggested_action = stall_diagnostics["suggested_action"]
        review_required = change_risk_fields["review_required"]
        diagnostic_note = event_diagnostics["diagnostic_note"]
        if no_event_noop_diagnostics["no_event_noop_risk"]:
            suggested_action = "check_session_or_retry_without_session"
            review_required = True
            diagnostic_note = build_no_event_noop_note()
        validation_note = build_validation_note(status)
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
        work_summary_text = event_diagnostics["last_text_output"]

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
            "validation_skipped_reason": "not_run_by_wrapper",
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "stdout_delta": stdout_delta,
            "stderr_delta": stderr_delta,
            "stdout_cursor": current_stdout_cursor,
            "stderr_cursor": current_stderr_cursor,
            "stdout_delta_truncated": stdout_delta_truncated,
            "stderr_delta_truncated": stderr_delta_truncated,
            "stdout_delta_response_truncated": stdout_delta_response_truncated,
            "stderr_delta_response_truncated": stderr_delta_response_truncated,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "first_output_at": job.first_output_at,
            "first_change_at": job.first_change_at,
            "last_activity_at": job.last_activity_at,
            "runtime_seconds": runtime_seconds,
            "idle_seconds": idle_seconds,
            "progress_phase": progress_diagnostics["progress_phase"],
            "progress_message": progress_diagnostics["progress_message"],
            "caller_update_recommended": progress_diagnostics["caller_update_recommended"],
            "caller_update_reason": progress_diagnostics["caller_update_reason"],
            "next_poll_after_seconds": progress_diagnostics["next_poll_after_seconds"],
            "time_to_first_output_seconds": time_diagnostics["time_to_first_output_seconds"],
            "time_to_first_event_seconds": time_diagnostics["time_to_first_event_seconds"],
            "time_to_first_tool_seconds": time_diagnostics["time_to_first_tool_seconds"],
            "time_to_first_change_seconds": time_diagnostics["time_to_first_change_seconds"],
            "seconds_since_last_event": time_diagnostics["seconds_since_last_event"],
            "seconds_since_last_change": time_diagnostics["seconds_since_last_change"],
            "tool_activity_summary": tool_activity_summary,
            "long_gap_segments": long_gap_segments,
            "root_cause_guess": progress_diagnostics["root_cause_guess"],
            "session_reuse_detected": session_reuse_diagnostics["session_reuse_detected"],
            "session_reuse_mode": session_reuse_diagnostics["session_reuse_mode"],
            "session_reuse_risk": session_reuse_diagnostics["session_reuse_risk"],
            "session_reuse_note": session_reuse_diagnostics["session_reuse_note"],
            "same_session_recent_job_count": session_reuse_diagnostics["same_session_recent_job_count"],
            "same_session_last_job_status": session_reuse_diagnostics["same_session_last_job_status"],
            "likely_preexisting_from_same_session": session_reuse_diagnostics["likely_preexisting_from_same_session"],
            "likely_preexisting_same_session_files": session_reuse_diagnostics["likely_preexisting_same_session_files"],
            "observed_validation_summary": validation_observation["observed_validation_summary"],
            "observed_validation_tools": validation_observation["observed_validation_tools"],
            "observed_validation_result": validation_observation["observed_validation_result"],
            "observed_validation_errors_count": validation_observation["observed_validation_errors_count"],
            "last_event_type": event_diagnostics["last_event_type"],
            "last_event_at": event_diagnostics["last_event_at"],
            "last_event_summary": event_diagnostics["last_event_summary"],
            "recent_events": event_diagnostics["recent_events"],
            "recent_event_count": event_diagnostics["recent_event_count"],
            "last_text_output": event_diagnostics["last_text_output"],
            "work_summary_text": work_summary_text,
            "assistant_last_text": work_summary_text,
            "last_tool_name": event_diagnostics["last_tool_name"],
            "last_tool_event": event_diagnostics["last_tool_event"],
            "last_step_reason": event_diagnostics["last_step_reason"],
            "last_step_status": event_diagnostics["last_step_status"],
            "last_session_id": event_diagnostics["last_session_id"],
            "diagnostic_phase": event_diagnostics["diagnostic_phase"],
            "diagnostic_note": diagnostic_note,
            "no_event_noop_risk": no_event_noop_diagnostics["no_event_noop_risk"],
            "no_event_noop_reason": no_event_noop_diagnostics["no_event_noop_reason"],
            "is_stalled": stall_diagnostics["is_stalled"],
            "stall_reason": stall_diagnostics["stall_reason"],
            "suggested_action": suggested_action,
            "review_required": review_required,
            "incomplete_changes_risk": change_risk_fields["incomplete_changes_risk"],
            "potential_incomplete_changes_risk": stall_diagnostics["potential_incomplete_changes_risk"],
            "preexisting_dirty_warning": change_risk_fields["preexisting_dirty_warning"],
            "command": job.command_summary,
            "wait_policy": job.wait_policy,
            "requested_model": job.requested_model,
            "requested_variant": job.requested_variant,
            "requested_agent": job.requested_agent,
            "requested_show_thinking": job.requested_show_thinking,
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
            "validation_status": "not_run_by_wrapper",
            "validation_note": validation_note,
        }


def make_job_not_found_result(job_id: str) -> dict:
    event_diagnostics = empty_event_diagnostics(
        "Job id was not found; no OpenCode stdout JSON events are available."
    )
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
        "validation_skipped_reason": "not_run_by_wrapper",
        "stdout_tail": "",
        "stderr_tail": "",
        "stdout_delta": "",
        "stderr_delta": "",
        "stdout_cursor": 0,
        "stderr_cursor": 0,
        "stdout_delta_truncated": False,
        "stderr_delta_truncated": False,
        "stdout_delta_response_truncated": False,
        "stderr_delta_response_truncated": False,
        "started_at": None,
        "finished_at": None,
        "first_output_at": None,
        "first_change_at": None,
        "last_activity_at": None,
        "runtime_seconds": None,
        "idle_seconds": None,
        "progress_phase": "not_found",
        "progress_message": "Job id was not found; no progress data is available.",
        "caller_update_recommended": True,
        "caller_update_reason": "not_found",
        "next_poll_after_seconds": 0,
        "time_to_first_output_seconds": None,
        "time_to_first_event_seconds": None,
        "time_to_first_tool_seconds": None,
        "time_to_first_change_seconds": None,
        "seconds_since_last_event": None,
        "seconds_since_last_change": None,
        "tool_activity_summary": {category: 0 for category in TOOL_ACTIVITY_CATEGORIES},
        "long_gap_segments": [],
        "root_cause_guess": "unknown",
        "session_reuse_detected": False,
        "session_reuse_mode": "none",
        "session_reuse_risk": False,
        "session_reuse_note": "no_session_reuse",
        "same_session_recent_job_count": 0,
        "same_session_last_job_status": None,
        "likely_preexisting_from_same_session": False,
        "likely_preexisting_same_session_files": [],
        "observed_validation_summary": "No validation activity observed.",
        "observed_validation_tools": [],
        "observed_validation_result": "none",
        "observed_validation_errors_count": None,
        "last_event_type": event_diagnostics["last_event_type"],
        "last_event_at": event_diagnostics["last_event_at"],
        "last_event_summary": event_diagnostics["last_event_summary"],
        "recent_events": event_diagnostics["recent_events"],
        "recent_event_count": event_diagnostics["recent_event_count"],
        "last_text_output": event_diagnostics["last_text_output"],
        "work_summary_text": None,
        "assistant_last_text": None,
        "last_tool_name": event_diagnostics["last_tool_name"],
        "last_tool_event": event_diagnostics["last_tool_event"],
        "last_step_reason": event_diagnostics["last_step_reason"],
        "last_step_status": event_diagnostics["last_step_status"],
        "last_session_id": event_diagnostics["last_session_id"],
        "diagnostic_phase": event_diagnostics["diagnostic_phase"],
        "diagnostic_note": event_diagnostics["diagnostic_note"],
        "no_event_noop_risk": False,
        "no_event_noop_reason": None,
        "is_stalled": False,
        "stall_reason": None,
        "suggested_action": "check_job_id",
        "review_required": False,
        "incomplete_changes_risk": False,
        "potential_incomplete_changes_risk": False,
        "preexisting_dirty_warning": "",
        "command": None,
        "wait_policy": None,
        "requested_model": None,
        "requested_variant": None,
        "requested_agent": None,
        "requested_show_thinking": False,
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
        "validation_status": "not_run_by_wrapper",
        "validation_note": "No job was found. The MCP wrapper did not run validation.",
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
    continue_last: bool = False,
    fork_session: bool = False,
    attached_to_server: bool,
    error: str,
    server_recovered_from_registry: bool = False,
    model: str | None = DEFAULT_OPENCODE_MODEL,
    variant: str | None = DEFAULT_OPENCODE_VARIANT,
    agent: str | None = None,
    show_thinking: bool = False,
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
        requested_session_id=session_id,
        continue_last=continue_last,
        fork_session=fork_session,
        attached_to_server=attached_to_server,
        server_recovered_from_registry=server_recovered_from_registry,
        requested_model=model,
        requested_variant=variant,
        requested_agent=agent,
        requested_show_thinking=bool(show_thinking),
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
    model: str | None,
    variant: str | None,
    agent: str | None,
    show_thinking: bool,
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
            ]
            append_opencode_run_option_flags(
                command,
                model=model,
                variant=variant,
                agent=agent,
                show_thinking=show_thinking,
            )
            command.append(f"<prompt chars={len(prompt)}>")
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
                continue_last=continue_last,
                fork_session=fork_session,
                attached_to_server=False,
                error=error,
                model=model,
                variant=variant,
                agent=agent,
                show_thinking=show_thinking,
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
                    model=model,
                    variant=variant,
                    agent=agent,
                    show_thinking=show_thinking,
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
                continue_last=continue_last,
                fork_session=fork_session,
                attached_to_server=True,
                error=f"server_id is not running: {server_id}",
                server_recovered_from_registry=server.recovered_from_registry,
                model=model,
                variant=variant,
                agent=agent,
                show_thinking=show_thinking,
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
        model=model,
        variant=variant,
        agent=agent,
        show_thinking=show_thinking,
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
            continue_last=continue_last,
            fork_session=fork_session,
            attached_to_server=attached_to_server,
            server_recovered_from_registry=server_recovered_from_registry,
            error=f"working_dir does not exist or is not a directory: {resolved_working_dir}",
            model=model,
            variant=variant,
            agent=agent,
            show_thinking=show_thinking,
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
            requested_session_id=session_id,
            continue_last=continue_last,
            fork_session=fork_session,
            attached_to_server=attached_to_server,
            server_recovered_from_registry=server_recovered_from_registry,
            requested_model=model,
            requested_variant=variant,
            requested_agent=agent,
            requested_show_thinking=bool(show_thinking),
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
    model: str | None = DEFAULT_OPENCODE_MODEL,
    variant: str | None = DEFAULT_OPENCODE_VARIANT,
    agent: str | None = None,
    show_thinking: bool = False,
    include_tail: bool = False,
    include_output: bool = False,
    include_delta: bool = False,
    recent_events_limit: int = DEFAULT_RECENT_EVENTS_LIMIT,
    delta_max_chars: int | None = None,
    tail_max_chars: int | None = None,
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
        model,
        variant,
        agent,
        bool(show_thinking),
    )
    if early_result is not None:
        if job is not None:
            return job_to_result(
                job,
                lock_rejected=bool(early_result.get("lock_rejected")),
                new_job_started=bool(early_result.get("new_job_started")),
                summary_override=early_result.get("summary"),
                include_tail=bool(include_tail),
                include_output=bool(include_output),
                include_delta=bool(include_delta),
                recent_events_limit=clamp_recent_events_limit(recent_events_limit),
                delta_max_chars=clamp_delta_max_chars(delta_max_chars),
                tail_max_chars=clamp_tail_max_chars(tail_max_chars),
            )
        return early_result
    if job is None:
        raise RuntimeError("opencode_coder internal error: no job and no result")

    wait_for_job_policy(job, job.wait_policy, job.effective_timeout_seconds)
    return job_to_result(
        job,
        include_tail=bool(include_tail),
        include_output=bool(include_output),
        include_delta=bool(include_delta),
        recent_events_limit=clamp_recent_events_limit(recent_events_limit),
        delta_max_chars=clamp_delta_max_chars(delta_max_chars),
        tail_max_chars=clamp_tail_max_chars(tail_max_chars),
    )


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
    include_tail: bool = False,
    include_output: bool = False,
    include_delta: bool = False,
    recent_events_limit: int = DEFAULT_RECENT_EVENTS_LIMIT,
    delta_max_chars: int | None = None,
    tail_max_chars: int | None = None,
) -> dict:
    """通过 job_id 查询 opencode_coder 后台任务状态；默认返回 compact 结果。"""
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
        include_tail=bool(include_tail),
        include_output=bool(include_output),
        include_delta=bool(include_delta),
        recent_events_limit=clamp_recent_events_limit(recent_events_limit),
        delta_max_chars=clamp_delta_max_chars(delta_max_chars),
        tail_max_chars=clamp_tail_max_chars(tail_max_chars, default=DEFAULT_STATUS_TAIL_MAX_CHARS),
    )


def compact_wait_snapshot(status_result: dict) -> dict:
    return {field: status_result.get(field) for field in WAIT_COMPACT_SNAPSHOT_FIELDS}


def wait_status_refresh_reason(
    status_result: dict,
    wait_info: dict,
    *,
    include_tail: bool,
    include_output: bool,
    include_delta: bool,
) -> str | None:
    wait_reason = wait_info.get("wait_return_reason")
    status = status_result.get("status")
    if status == "not_found":
        return None
    if include_tail or include_output or include_delta:
        return "debug_output_requested"
    if wait_reason == "terminal_status" and status not in {"completed", "failed", "cancelled"}:
        return "wait_reason_status_mismatch"
    if wait_reason == "first_change_seen" and not status_result.get("new_changed_files"):
        return "first_change_without_snapshot_files"
    if status_result.get("policy_violation") and not (
        status_result.get("extra_changed_files") or status_result.get("forbidden_changed_files")
    ):
        return "policy_violation_without_file_details"
    if status_result.get("is_stalled") and not status_result.get("stall_reason"):
        return "stalled_without_reason"
    if status_result.get("no_event_noop_risk") and not status_result.get("diagnostic_note"):
        return "noop_risk_without_diagnostics"
    if wait_reason == "validation_observed" and not status_result.get("observed_validation_summary"):
        return "validation_observed_without_summary"
    return None


def wait_suggested_next_tool(status_result: dict, wait_info: dict, needs_status_refresh: bool) -> str:
    if needs_status_refresh:
        return "opencode_coder_status"

    wait_reason = wait_info.get("wait_return_reason")
    status = status_result.get("status")
    has_changes = bool(status_result.get("new_changed_files") or status_result.get("all_changed_files"))

    if status == "not_found":
        return "none"
    if status_result.get("policy_violation") or wait_reason == "policy_violation":
        return "opencode_coder_diff"
    if status_result.get("no_event_noop_risk"):
        return "opencode_coder"
    if status_result.get("is_stalled"):
        return "opencode_coder_diff" if has_changes else "opencode_coder_cancel"
    if status in {"completed", "failed", "cancelled"}:
        return "opencode_coder_diff" if has_changes else "none"
    return "opencode_coder_wait"


def add_wait_guidance_fields(
    result: dict,
    status_result: dict,
    wait_info: dict,
    *,
    include_tail: bool,
    include_output: bool,
    include_delta: bool,
) -> None:
    refresh_reason = wait_status_refresh_reason(
        status_result,
        wait_info,
        include_tail=include_tail,
        include_output=include_output,
        include_delta=include_delta,
    )
    needs_status_refresh = refresh_reason is not None
    result["needs_status_refresh"] = needs_status_refresh
    result["suggested_next_tool"] = wait_suggested_next_tool(status_result, wait_info, needs_status_refresh)
    result["status_refresh_reason"] = refresh_reason or "compact_snapshot_sufficient"


@mcp.tool()
def opencode_coder_wait(
    job_id: str,
    wait_seconds: float = DEFAULT_WAIT_WAIT_SECONDS,
    return_on: str = "interesting",
    include_status: bool = True,
    include_tail: bool = False,
    include_output: bool = False,
    include_delta: bool = False,
    recent_events_limit: int = DEFAULT_RECENT_EVENTS_LIMIT,
    delta_max_chars: int | None = None,
    tail_max_chars: int | None = None,
) -> dict:
    """长轮询等待 opencode_coder job 出现值得关注的变化，减少频繁 status 查询的 token 浪费。

    这是一次会阻塞主对话的 MCP 工具调用，不是 MCP 主动通知用户。
    在单次工具调用内部等待 job 达到 terminal、首次文件变更、policy violation、
    stalled、no_event_noop_risk、observed validation passed/failed 或 caller_update_recommended，
    或等待超时后再返回。

    wait_seconds 会先被限制在 0..600 秒，再按 MCP 客户端超时预算和安全边距裁剪。
    return_on 默认 \"interesting\" 等待任何值得关注的变化；传 \"terminal\" 只等待 completed/failed/cancelled。
    include_status 默认 true，返回完整的 job 状态快照；传 false 仍返回 compact snapshot 加 wait 引导字段。
    include_tail/include_output/include_delta 是调试开关，默认 false 保持输出紧凑。
    """
    requested_wait, effective_wait, wait_timeout_policy = compute_effective_wait_wait_seconds(wait_seconds)
    cleanup_jobs()
    with _REGISTRY_LOCK:
        job = _JOBS.get(job_id)

    if job is None:
        status_result = make_job_not_found_result(job_id)
        result = status_result if include_status else compact_wait_snapshot(status_result)
        result["wait_return_reason"] = "not_found"
        result["interesting_update"] = True
        result["waited_seconds"] = 0.0
        result["requested_wait_seconds"] = requested_wait
        result["effective_wait_seconds"] = effective_wait
        result["wait_timeout_policy"] = wait_timeout_policy
        add_wait_guidance_fields(
            result,
            status_result,
            result,
            include_tail=bool(include_tail),
            include_output=bool(include_output),
            include_delta=bool(include_delta),
        )
        return result

    wait_info = wait_for_update(job, effective_wait, return_on)
    wait_info["requested_wait_seconds"] = requested_wait
    wait_info["effective_wait_seconds"] = effective_wait
    wait_info["wait_timeout_policy"] = wait_timeout_policy

    status_result = job_to_result(
        job,
        new_job_started=False,
        include_tail=bool(include_tail),
        include_output=bool(include_output),
        include_delta=bool(include_delta),
        recent_events_limit=clamp_recent_events_limit(recent_events_limit),
        delta_max_chars=clamp_delta_max_chars(delta_max_chars),
        tail_max_chars=clamp_tail_max_chars(tail_max_chars, default=DEFAULT_STATUS_TAIL_MAX_CHARS),
    )
    if include_status:
        result = status_result
    else:
        result = compact_wait_snapshot(status_result)
    result.update(wait_info)
    add_wait_guidance_fields(
        result,
        status_result,
        wait_info,
        include_tail=bool(include_tail),
        include_output=bool(include_output),
        include_delta=bool(include_delta),
    )
    return result


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
            "diff_empty_reason": "job_not_found",
            "diff_source_files": [],
            "diff_command_errors": [],
            "diff_truncated": False,
            "max_chars": effective_max_chars,
            "undiffed_files": [],
            "includes_preexisting_dirty_changes": False,
            "review_required": False,
            "incomplete_changes_risk": False,
            "preexisting_dirty_warning": "",
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
        job_git_root = job.git_root
        job_git_root_error = job.git_root_error
        change_risk_fields = build_change_risk_fields(job, status)

    if not git_status_available:
        return {
            "job_id": job_id,
            "status": status,
            "working_dir": working_dir,
            "new_changed_files": new_changed_files,
            "preexisting_changed_files": preexisting_changed_files,
            "diff": "",
            "diff_empty_reason": "git_status_unavailable",
            "diff_source_files": [],
            "diff_command_errors": [git_status_error or "git_status_unavailable"],
            "diff_truncated": False,
            "max_chars": effective_max_chars,
            "undiffed_files": list(new_changed_files),
            "includes_preexisting_dirty_changes": False,
            "review_required": change_risk_fields["review_required"],
            "incomplete_changes_risk": change_risk_fields["incomplete_changes_risk"],
            "preexisting_dirty_warning": change_risk_fields["preexisting_dirty_warning"],
            "git_status_available": False,
            "error": git_status_error or "git_status_unavailable",
            "success": False,
        }

    status_entries, status_error, status_git_root, status_git_root_error = collect_git_status_entry_map(working_dir)
    if status_error is not None:
        return {
            "job_id": job_id,
            "status": status,
            "working_dir": working_dir,
            "new_changed_files": new_changed_files,
            "preexisting_changed_files": preexisting_changed_files,
            "diff": "",
            "diff_empty_reason": "git_status_error",
            "diff_source_files": [],
            "diff_command_errors": [status_error],
            "diff_truncated": False,
            "max_chars": effective_max_chars,
            "undiffed_files": list(new_changed_files),
            "includes_preexisting_dirty_changes": False,
            "review_required": change_risk_fields["review_required"],
            "incomplete_changes_risk": change_risk_fields["incomplete_changes_risk"],
            "preexisting_dirty_warning": change_risk_fields["preexisting_dirty_warning"],
            "git_status_available": False,
            "error": status_error,
            "success": False,
        }

    git_root = status_git_root or job_git_root
    git_root_error = status_git_root_error or job_git_root_error
    diff_base_dir = git_root or working_dir
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
    diff_source_files: list[str] = []
    undiffed_files: list[str] = []
    errors: list[str] = []
    if git_root_error and not git_root:
        errors.append(f"git root unavailable: {git_root_error}")

    unstaged_diff, unstaged_error = run_git_diff(diff_base_dir, tracked_paths, cached=False)
    if unstaged_error:
        errors.append(f"git diff failed: {unstaged_error}")
        undiffed_files.extend(tracked_paths)
    elif unstaged_diff:
        diff_parts.append(unstaged_diff)
        diff_source_files.extend(tracked_paths)

    cached_diff, cached_error = run_git_diff(diff_base_dir, tracked_paths, cached=True)
    if cached_error:
        errors.append(f"git diff --cached failed: {cached_error}")
        for path in tracked_paths:
            if path not in undiffed_files:
                undiffed_files.append(path)
    elif cached_diff:
        diff_parts.append(cached_diff)
        diff_source_files.extend(tracked_paths)

    for path in untracked_paths:
        untracked_diff, untracked_error = make_untracked_file_diff(diff_base_dir, path)
        if untracked_error:
            undiffed_files.append(path)
            errors.append(f"{path}: {untracked_error}")
        elif untracked_diff:
            diff_parts.append(untracked_diff)
            diff_source_files.append(path)

    full_diff = "\n".join(part.rstrip("\n") for part in diff_parts if part)
    diff, diff_truncated = truncate_diff(full_diff, effective_max_chars)
    undiffed_files = unique_sorted_paths(undiffed_files)
    diff_source_files = unique_sorted_paths(diff_source_files)
    diff_empty_reason = None
    if not full_diff:
        if not new_changed_files:
            diff_empty_reason = "no_new_changed_files"
        elif errors:
            diff_empty_reason = "diff_generation_errors"
        elif undiffed_files:
            diff_empty_reason = "job_files_undiffed"
        else:
            diff_empty_reason = "current_worktree_has_no_diff_for_job_files"

    success = not errors and (bool(full_diff) or not new_changed_files)
    error_text = "; ".join(errors) or (diff_empty_reason if not success else None)
    return {
        "job_id": job_id,
        "status": status,
        "working_dir": working_dir,
        "new_changed_files": new_changed_files,
        "preexisting_changed_files": preexisting_changed_files,
        "diff": diff,
        "diff_empty_reason": diff_empty_reason,
        "diff_source_files": diff_source_files,
        "diff_command_errors": list(errors),
        "diff_truncated": diff_truncated,
        "max_chars": effective_max_chars,
        "undiffed_files": undiffed_files,
        "includes_preexisting_dirty_changes": includes_preexisting_dirty_changes,
        "review_required": change_risk_fields["review_required"],
        "incomplete_changes_risk": change_risk_fields["incomplete_changes_risk"],
        "preexisting_dirty_warning": change_risk_fields["preexisting_dirty_warning"],
        "git_status_available": True,
        "error": error_text,
        "success": success,
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
