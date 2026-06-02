import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


MODULE_PATH = Path(__file__).with_name("opencode-coder.py")
SPEC = importlib.util.spec_from_file_location("opencode_coder_server", MODULE_PATH)
server = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = server
SPEC.loader.exec_module(server)

FAKE_BUILD_CALLS = []


def fake_command(
    prompt: str,
    *,
    working_dir: str | None = None,
    server_url: str | None = None,
    session_id: str | None = None,
    continue_last: bool = False,
    fork_session: bool = False,
    title: str | None = None,
    model: str | None = server.DEFAULT_OPENCODE_MODEL,
    variant: str | None = server.DEFAULT_OPENCODE_VARIANT,
    agent: str | None = None,
    show_thinking: bool = False,
) -> list[str]:
    FAKE_BUILD_CALLS.append(
        {
            "prompt": prompt,
            "working_dir": working_dir,
            "server_url": server_url,
            "session_id": session_id,
            "continue_last": continue_last,
            "fork_session": fork_session,
            "title": title,
            "model": model,
            "variant": variant,
            "agent": agent,
            "show_thinking": show_thinking,
        }
    )
    if prompt == "short":
        code = "print('ok', flush=True)"
    elif prompt == "slow_no_output":
        code = (
            "import time\n"
            "time.sleep(0.4)\n"
            "print('done', flush=True)\n"
        )
    elif prompt == "no_output_success":
        code = ""
    elif prompt == "delayed_output":
        code = (
            "import time\n"
            "time.sleep(0.2)\n"
            "print('first', flush=True)\n"
            "time.sleep(0.5)\n"
            "print('done', flush=True)\n"
        )
    elif prompt == "delayed_stderr":
        code = (
            "import sys\n"
            "import time\n"
            "time.sleep(0.2)\n"
            "sys.stderr.write('err-first\\n')\n"
            "sys.stderr.flush()\n"
            "time.sleep(0.5)\n"
            "sys.stderr.write('err-done\\n')\n"
            "sys.stderr.flush()\n"
        )
    elif prompt == "large_stdout":
        code = "print('abcdefghijklmnopqrstuvwxyz', flush=True)"
    elif prompt == "session_json":
        code = "print('{\"sessionID\":\"ses_test_123\"}', flush=True)"
    elif prompt == "opencode_events":
        events = [
            {
                "type": "message.part.updated",
                "timestamp": "2026-06-01T00:00:01Z",
                "sessionID": "ses_evt_123",
                "messageID": "msg_1",
                "part": {"type": "text", "text": "Working on it"},
            },
            {
                "type": "message.part.updated",
                "timestamp": "2026-06-01T00:00:02Z",
                "sessionID": "ses_evt_123",
                "messageID": "msg_1",
                "part": {"type": "tool", "name": "edit", "status": "started"},
            },
            {
                "type": "step",
                "timestamp": "2026-06-01T00:00:03Z",
                "sessionID": "ses_evt_123",
                "messageID": "msg_1",
                "status": "finished",
                "reason": "stop",
            },
        ]
        code = (
            "import json\n"
            f"events = {events!r}\n"
            "for event in events:\n"
            "    print(json.dumps(event), flush=True)\n"
        )
    elif prompt == "read_event_then_sleep":
        event = {
            "type": "message.part.updated",
            "sessionID": "ses_read",
            "messageID": "msg_read",
            "part": {"type": "tool", "name": "read", "status": "started"},
        }
        code = (
            "import json\n"
            "import time\n"
            f"event = {event!r}\n"
            "print(json.dumps(event), flush=True)\n"
            "time.sleep(10)\n"
        )
    elif prompt == "bash_tool_event_then_sleep":
        event = {
            "type": "message.part.updated",
            "sessionID": "ses_bash",
            "messageID": "msg_bash",
            "part": {
                "type": "tool",
                "name": "bash",
                "status": "started",
                "text": "python -m py_compile opencode-coder.py",
            },
        }
        code = (
            "import json\n"
            "import time\n"
            f"event = {event!r}\n"
            "print(json.dumps(event), flush=True)\n"
            "time.sleep(10)\n"
        )
    elif prompt == "validation_words_text_then_sleep":
        event = {
            "type": "message.part.updated",
            "sessionID": "ses_text_validation_words",
            "messageID": "msg_text_validation_words",
            "part": {
                "type": "text",
                "text": (
                    "README says to run python -m py_compile, "
                    "debug_check_compilation, and git diff --check later."
                ),
            },
        }
        code = (
            "import json\n"
            "import time\n"
            f"event = {event!r}\n"
            "print(json.dumps(event), flush=True)\n"
            "time.sleep(10)\n"
        )
    elif prompt == "validation_words_stdout_then_sleep":
        code = (
            "import time\n"
            "print('Docs mention python -m py_compile, debug_check_compilation, and git diff --check', flush=True)\n"
            "time.sleep(10)\n"
        )
    elif prompt == "read_tool_validation_words_then_sleep":
        event = {
            "type": "message.part.updated",
            "sessionID": "ses_read_validation_words",
            "messageID": "msg_read_validation_words",
            "part": {
                "type": "tool",
                "name": "read",
                "status": "completed",
                "text": (
                    "README content mentions python -m py_compile, "
                    "debug_check_compilation, git diff --check, and 0 errors."
                ),
            },
        }
        code = (
            "import json\n"
            "import time\n"
            f"event = {event!r}\n"
            "print(json.dumps(event), flush=True)\n"
            "time.sleep(10)\n"
        )
    elif prompt == "glob_tool_validation_words_then_sleep":
        event = {
            "type": "message.part.updated",
            "sessionID": "ses_glob_validation_words",
            "messageID": "msg_glob_validation_words",
            "part": {
                "type": "tool",
                "name": "glob",
                "status": "completed",
                "text": (
                    "Matched docs that mention python -m py_compile, "
                    "debug_check_compilation, git diff --check, and 0 errors."
                ),
            },
        }
        code = (
            "import json\n"
            "import time\n"
            f"event = {event!r}\n"
            "print(json.dumps(event), flush=True)\n"
            "time.sleep(10)\n"
        )
    elif prompt == "unity_validation_events":
        events = [
            {
                "type": "message.part.updated",
                "sessionID": "ses_unity",
                "messageID": "msg_unity_1",
                "part": {"type": "tool", "name": "unity_skills_debug_force_recompile", "status": "completed"},
            },
            {
                "type": "message.part.updated",
                "sessionID": "ses_unity",
                "messageID": "msg_unity_2",
                "part": {"type": "tool", "name": "unity_skills_debug_check_compilation", "status": "completed"},
            },
            {
                "type": "message.part.updated",
                "sessionID": "ses_unity",
                "messageID": "msg_unity_3",
                "part": {
                    "type": "tool",
                    "name": "unity_skills_console_get_logs",
                    "status": "completed",
                    "text": "Unity Skills compile check observed: 0 errors",
                },
            },
        ]
        code = (
            "import json\n"
            f"events = {events!r}\n"
            "for event in events:\n"
            "    print(json.dumps(event), flush=True)\n"
        )
    elif prompt == "unity_validation_failed_after_pass":
        events = [
            {
                "type": "message.part.updated",
                "sessionID": "ses_unity_failed",
                "messageID": "msg_unity_failed_1",
                "part": {
                    "type": "tool",
                    "name": "unity_skills_console_get_logs",
                    "status": "completed",
                    "text": "Unity Skills compile check observed: 0 errors",
                },
            },
            {
                "type": "message.part.updated",
                "sessionID": "ses_unity_failed",
                "messageID": "msg_unity_failed_2",
                "part": {
                    "type": "tool",
                    "name": "unity_skills_console_get_logs",
                    "status": "completed",
                    "text": "Unity Skills compile check observed: 2 errors; compilation failed",
                },
            },
        ]
        code = (
            "import json\n"
            f"events = {events!r}\n"
            "for event in events:\n"
            "    print(json.dumps(event), flush=True)\n"
        )
    elif prompt == "unity_validation_failed_then_zero":
        events = [
            {
                "type": "message.part.updated",
                "sessionID": "ses_unity_failed_then_zero",
                "messageID": "msg_unity_failed_then_zero_1",
                "part": {
                    "type": "tool",
                    "name": "unity_skills_console_get_logs",
                    "status": "completed",
                    "text": "Unity Skills compile check observed: compilation failed",
                },
            },
            {
                "type": "message.part.updated",
                "sessionID": "ses_unity_failed_then_zero",
                "messageID": "msg_unity_failed_then_zero_2",
                "part": {
                    "type": "tool",
                    "name": "unity_skills_console_get_logs",
                    "status": "completed",
                    "text": "Unity Skills compile check observed: 0 errors",
                },
            },
        ]
        code = (
            "import json\n"
            f"events = {events!r}\n"
            "for event in events:\n"
            "    print(json.dumps(event), flush=True)\n"
        )
    elif prompt == "gap_events":
        events = [
            {
                "type": "message.part.updated",
                "sessionID": "ses_gap",
                "messageID": "msg_gap_1",
                "part": {"type": "text", "text": "Reading context"},
            },
            {
                "type": "message.part.updated",
                "sessionID": "ses_gap",
                "messageID": "msg_gap_2",
                "part": {"type": "tool", "name": "read", "status": "completed"},
            },
            {
                "type": "message.part.updated",
                "sessionID": "ses_gap",
                "messageID": "msg_gap_3",
                "part": {"type": "tool", "name": "bash", "status": "completed"},
            },
            {
                "type": "step",
                "sessionID": "ses_gap",
                "messageID": "msg_gap_4",
                "status": "finished",
                "reason": "stop",
            },
        ]
        code = (
            "import json\n"
            "import time\n"
            f"events = {events!r}\n"
            "for event in events:\n"
            "    print(json.dumps(event), flush=True)\n"
            "    time.sleep(0.04)\n"
        )
    elif prompt == "many_opencode_events":
        code = (
            "import json\n"
            f"limit = {server.RECENT_EVENT_LIMIT + 5!r}\n"
            "for index in range(limit):\n"
            "    print(json.dumps({\n"
            "        'type': 'message.part.updated',\n"
            "        'sessionID': 'ses_many',\n"
            "        'messageID': f'msg_{index}',\n"
            "        'part': {'type': 'text', 'text': f'event {index}'},\n"
            "    }), flush=True)\n"
        )
    elif prompt == "large_json_text":
        large_text = ("x" * 1000) + "FULL_TEXT_SENTINEL"
        event = {
            "type": "message.part.updated",
            "sessionID": "ses_large",
            "messageID": "msg_large",
            "part": {"type": "text", "text": large_text},
        }
        code = (
            "import json\n"
            f"event = {event!r}\n"
            "print(json.dumps(event), flush=True)\n"
        )
    elif prompt == "json_event_then_sleep":
        event = {
            "type": "message.part.updated",
            "sessionID": "ses_sleep",
            "messageID": "msg_sleep",
            "part": {"type": "text", "text": "Waiting after text"},
        }
        code = (
            "import json\n"
            "import time\n"
            f"event = {event!r}\n"
            "print(json.dumps(event), flush=True)\n"
            "time.sleep(10)\n"
        )
    elif prompt == "long":
        code = (
            "import time\n"
            "print('begin', flush=True)\n"
            "time.sleep(0.4)\n"
            "print('done', flush=True)\n"
        )
    elif prompt == "very_long":
        code = (
            "import time\n"
            "print('begin', flush=True)\n"
            "time.sleep(10)\n"
            "print('done', flush=True)\n"
        )
    elif prompt == "no_output_long":
        code = (
            "import time\n"
            "time.sleep(10)\n"
        )
    elif prompt == "fail":
        code = (
            "import sys\n"
            "sys.stderr.write('boom\\n')\n"
            "sys.stderr.flush()\n"
            "raise SystemExit(7)\n"
        )
    elif prompt.startswith("fail_after_write:"):
        path = prompt.split(":", 1)[1]
        code = (
            "import sys\n"
            "from pathlib import Path\n"
            f"path = Path({path!r})\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_text('partial\\n', encoding='utf-8')\n"
            "sys.stderr.write('failed after write\\n')\n"
            "sys.stderr.flush()\n"
            "raise SystemExit(9)\n"
        )
    elif prompt.startswith("write:"):
        path = prompt.split(":", 1)[1]
        code = (
            "from pathlib import Path\n"
            f"path = Path({path!r})\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_text('generated\\n', encoding='utf-8')\n"
            "print(f'wrote {path}', flush=True)\n"
        )
    elif prompt.startswith("write_silent:"):
        path = prompt.split(":", 1)[1]
        code = (
            "from pathlib import Path\n"
            f"path = Path({path!r})\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_text('generated\\n', encoding='utf-8')\n"
        )
    elif prompt.startswith("delete:"):
        path = prompt.split(":", 1)[1]
        code = (
            "from pathlib import Path\n"
            f"path = Path({path!r})\n"
            "path.unlink()\n"
            "print(f'deleted {path}', flush=True)\n"
        )
    elif prompt.startswith("restore_baseline:"):
        path = prompt.split(":", 1)[1]
        code = (
            "from pathlib import Path\n"
            f"path = Path({path!r})\n"
            "path.write_text('baseline\\n', encoding='utf-8')\n"
            "print(f'restored {path}', flush=True)\n"
        )
    elif prompt.startswith("write_binary:"):
        path = prompt.split(":", 1)[1]
        code = (
            "from pathlib import Path\n"
            f"path = Path({path!r})\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_bytes(b'\\x00\\x01\\x02')\n"
            "print(f'wrote binary {path}', flush=True)\n"
        )
    elif prompt.startswith("long_write:"):
        path = prompt.split(":", 1)[1]
        code = (
            "import time\n"
            "from pathlib import Path\n"
            "print('begin', flush=True)\n"
            "time.sleep(0.2)\n"
            f"path = Path({path!r})\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_text('generated\\n', encoding='utf-8')\n"
            "print(f'wrote {path}', flush=True)\n"
        )
    elif prompt.startswith("write_then_sleep:"):
        path = prompt.split(":", 1)[1]
        code = (
            "import time\n"
            "from pathlib import Path\n"
            f"path = Path({path!r})\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_text('generated before cancel\\n', encoding='utf-8')\n"
            "print(f'wrote {path}', flush=True)\n"
            "time.sleep(10)\n"
        )
    elif prompt.startswith("write_silent_then_sleep:"):
        path = prompt.split(":", 1)[1]
        code = (
            "import time\n"
            "from pathlib import Path\n"
            f"path = Path({path!r})\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_text('generated before stall\\n', encoding='utf-8')\n"
            "time.sleep(10)\n"
        )
    elif prompt.startswith("delayed_write:"):
        path = prompt.split(":", 1)[1]
        code = (
            "import time\n"
            "from pathlib import Path\n"
            "time.sleep(0.2)\n"
            f"path = Path({path!r})\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_text('generated\\n', encoding='utf-8')\n"
            "time.sleep(0.5)\n"
            "print(f'done {path}', flush=True)\n"
        )
    elif prompt.startswith("slow_read_then_write:"):
        path = prompt.split(":", 1)[1]
        event = {
            "type": "message.part.updated",
            "sessionID": "ses_slow_read_write",
            "messageID": "msg_slow_read_write",
            "part": {"type": "tool", "name": "read", "status": "completed"},
        }
        code = (
            "import json\n"
            "import time\n"
            "from pathlib import Path\n"
            f"event = {event!r}\n"
            "print(json.dumps(event), flush=True)\n"
            "time.sleep(0.12)\n"
            f"path = Path({path!r})\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_text('generated after slow read\\n', encoding='utf-8')\n"
            "print(f'done {path}', flush=True)\n"
        )
    elif prompt.startswith("double_write_same_file:"):
        path = prompt.split(":", 1)[1]
        code = (
            "import time\n"
            "from pathlib import Path\n"
            "time.sleep(0.15)\n"
            f"path = Path({path!r})\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            "path.write_text('first\\n', encoding='utf-8')\n"
            "time.sleep(0.45)\n"
            "path.write_text('second\\n', encoding='utf-8')\n"
            "time.sleep(1.5)\n"
            "print(f'done {path}', flush=True)\n"
        )
    else:
        raise AssertionError(f"unexpected prompt in fake command: {prompt}")
    return [sys.executable, "-c", code]


def fake_server_command(hostname: str, port: int) -> list[str]:
    code = (
        "import socket\n"
        "import sys\n"
        "import time\n"
        "hostname = sys.argv[1]\n"
        "port = int(sys.argv[2])\n"
        "sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "sock.bind((hostname, port))\n"
        "sock.listen()\n"
        "print(f'ready {hostname}:{port}', flush=True)\n"
        "try:\n"
        "    while True:\n"
        "        sock.settimeout(0.2)\n"
        "        try:\n"
        "            conn, _addr = sock.accept()\n"
        "            conn.close()\n"
        "        except socket.timeout:\n"
        "            pass\n"
        "except BaseException:\n"
        "    pass\n"
        "finally:\n"
        "    sock.close()\n"
    )
    return [sys.executable, "-c", code, hostname, str(port)]


def init_git_repo(working_dir: str) -> None:
    subprocess.run(
        ["git", "init"],
        cwd=working_dir,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def commit_all(working_dir: str, message: str = "test commit") -> None:
    subprocess.run(
        ["git", "add", "."],
        cwd=working_dir,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            message,
        ],
        cwd=working_dir,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


def wait_for_terminal_job(job_id: str, total_wait_seconds: float = 3.0) -> dict:
    deadline = time.monotonic() + total_wait_seconds
    status = server.opencode_coder_status(job_id)
    while status["status"] not in TERMINAL_JOB_STATUSES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return status
        status = server.opencode_coder_status(job_id, wait_seconds=min(0.25, remaining))
    return status


def wait_for_job_change(job_id: str, total_wait_seconds: float = 3.0) -> dict:
    deadline = time.monotonic() + total_wait_seconds
    status = server.opencode_coder_status(job_id)
    while not status["new_changed_files"] and status["status"] not in TERMINAL_JOB_STATUSES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return status
        status = server.opencode_coder_status(job_id, wait_seconds=min(0.25, remaining))
    return status


def read_test_registry() -> dict:
    path = Path(server.get_server_registry_path())
    if not path.exists():
        return {"version": server.SERVER_REGISTRY_VERSION, "servers": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def write_test_registry(data: dict) -> None:
    path = Path(server.get_server_registry_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def terminate_process(process) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        server.process_tree_kill(process, timeout_seconds=2)


class OpenCodeCoderTests(unittest.TestCase):
    def setUp(self):
        FAKE_BUILD_CALLS.clear()
        self.original_registry_path = os.environ.get(server.SERVER_REGISTRY_ENV_VAR)
        self.registry_tempdir = tempfile.TemporaryDirectory()
        os.environ[server.SERVER_REGISTRY_ENV_VAR] = str(
            Path(self.registry_tempdir.name) / "opencode_coder_registry.json"
        )
        server._reset_jobs_for_tests()
        server._reset_servers_for_tests()
        self.original_build_command = server.build_opencode_command
        self.original_build_server_command = server.build_opencode_server_command
        server.build_opencode_command = fake_command
        server.build_opencode_server_command = fake_server_command

    def tearDown(self):
        server.build_opencode_command = self.original_build_command
        server.build_opencode_server_command = self.original_build_server_command
        server._reset_jobs_for_tests()
        server._reset_servers_for_tests()
        if self.original_registry_path is None:
            os.environ.pop(server.SERVER_REGISTRY_ENV_VAR, None)
        else:
            os.environ[server.SERVER_REGISTRY_ENV_VAR] = self.original_registry_path
        self.registry_tempdir.cleanup()

    def test_short_task_completes_synchronously(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["success"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout_tail"], "")
        self.assertEqual(result["stderr_tail"], "")
        self.assertEqual(result["stdout_delta"], "")
        self.assertEqual(result["stderr_delta"], "")
        self.assertEqual(result["output"], "")
        self.assertGreater(result["stdout_cursor"], 0)
        self.assertFalse(result["stdout_delta_response_truncated"])
        self.assertFalse(result["stderr_delta_response_truncated"])
        self.assertFalse(result["is_stalled"])
        self.assertIsNone(result["stall_reason"])
        self.assertGreaterEqual(result["runtime_seconds"], 0)
        self.assertGreaterEqual(result["idle_seconds"], 0)
        self.assertFalse(result["review_required"])
        self.assertFalse(result["incomplete_changes_risk"])
        self.assertFalse(result["potential_incomplete_changes_risk"])
        self.assertEqual(result["preexisting_dirty_warning"], "")
        self.assertEqual(result["validation_status"], "not_run_by_wrapper")
        self.assertIn("does not run validation", result["validation_note"])
        self.assertIsNotNone(result["job_id"])
        self.assertEqual(result["preexisting_changed_files"], [])
        self.assertEqual(result["all_changed_files"], [])
        self.assertEqual(result["new_changed_files"], [])
        self.assertFalse(result["attached_to_server"])
        self.assertIsNone(result["server_id"])
        self.assertIsNone(result["server_url"])
        self.assertEqual(result["requested_model"], server.DEFAULT_OPENCODE_MODEL)
        self.assertEqual(result["requested_variant"], server.DEFAULT_OPENCODE_VARIANT)
        self.assertIsNone(result["requested_agent"])
        self.assertFalse(result["requested_show_thinking"])
        self.assertIsNone(FAKE_BUILD_CALLS[-1]["server_url"])
        self.assertEqual(FAKE_BUILD_CALLS[-1]["model"], server.DEFAULT_OPENCODE_MODEL)
        self.assertEqual(FAKE_BUILD_CALLS[-1]["variant"], server.DEFAULT_OPENCODE_VARIANT)
        self.assertIsNone(FAKE_BUILD_CALLS[-1]["agent"])
        self.assertFalse(FAKE_BUILD_CALLS[-1]["show_thinking"])

    def test_coder_records_explicit_model_variant_agent_and_thinking(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "short",
                working_dir=working_dir,
                timeout_seconds=2,
                model="openai/gpt-5",
                variant="fast",
                agent="coder",
                show_thinking=True,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["requested_model"], "openai/gpt-5")
        self.assertEqual(result["requested_variant"], "fast")
        self.assertEqual(result["requested_agent"], "coder")
        self.assertTrue(result["requested_show_thinking"])
        self.assertEqual(FAKE_BUILD_CALLS[-1]["model"], "openai/gpt-5")
        self.assertEqual(FAKE_BUILD_CALLS[-1]["variant"], "fast")
        self.assertEqual(FAKE_BUILD_CALLS[-1]["agent"], "coder")
        self.assertTrue(FAKE_BUILD_CALLS[-1]["show_thinking"])

    def test_coder_include_tail_and_output_returns_debug_fields(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "short",
                working_dir=working_dir,
                timeout_seconds=2,
                include_tail=True,
                include_output=True,
            )

        self.assertEqual(result["status"], "completed")
        self.assertIn("ok", result["stdout_tail"])
        self.assertEqual(result["stderr_tail"], "")
        self.assertIn("ok", result["output"])
        self.assertEqual(result["stdout_delta"], "")

    def test_default_completion_wait_policy_keeps_old_behavior(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started_at = time.monotonic()
            result = server.opencode_coder(
                "delayed_output",
                working_dir=working_dir,
                timeout_seconds=2,
                include_tail=True,
            )
            elapsed = time.monotonic() - started_at

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["wait_policy"], "completion")
        self.assertGreaterEqual(elapsed, 0.6)
        self.assertIn("done", result["stdout_tail"])

    def test_start_only_returns_before_background_completion(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started_at = time.monotonic()
            result = server.opencode_coder(
                "slow_no_output",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            elapsed = time.monotonic() - started_at

            self.assertLess(elapsed, 0.35)
            self.assertEqual(result["wait_policy"], "start_only")
            self.assertIn(result["status"], {"running", "timed_out", "completed"})
            job_id = result["job_id"]
            final_status = wait_for_terminal_job(job_id)
            final_output = server.opencode_coder_status(job_id, include_tail=True)

        self.assertEqual(final_status["status"], "completed")
        self.assertIn("done", final_output["stdout_tail"])

    def test_first_output_returns_after_initial_output(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started_at = time.monotonic()
            result = server.opencode_coder(
                "delayed_output",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_output",
                include_tail=True,
            )
            elapsed = time.monotonic() - started_at
            final_status = wait_for_terminal_job(result["job_id"])

        self.assertLess(elapsed, 0.65)
        self.assertEqual(result["wait_policy"], "first_output")
        self.assertIsNotNone(result["first_output_at"])
        self.assertIsNotNone(result["last_activity_at"])
        self.assertIn("first", result["stdout_tail"])
        self.assertEqual(final_status["status"], "completed")

    def test_first_change_returns_after_new_changed_file(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            started_at = time.monotonic()
            result = server.opencode_coder(
                "delayed_write:src/new.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_change",
                allowed_paths=["src"],
            )
            elapsed = time.monotonic() - started_at
            final_status = wait_for_terminal_job(result["job_id"])

        self.assertLess(elapsed, 1.0)
        self.assertEqual(result["wait_policy"], "first_change")
        self.assertEqual(result["new_changed_files"], ["src/new.txt"])
        self.assertIsNotNone(result["first_change_at"])
        self.assertFalse(result["is_stalled"])
        self.assertFalse(result["potential_incomplete_changes_risk"])
        self.assertFalse(result["policy_violation"])
        self.assertEqual(final_status["status"], "completed")

    def test_status_wait_seconds_waits_for_activity_without_new_job(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "delayed_output",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            build_call_count = len(FAKE_BUILD_CALLS)
            started_at = time.monotonic()
            status = server.opencode_coder_status(initial["job_id"], wait_seconds=1, include_tail=True)
            elapsed = time.monotonic() - started_at
            final_status = wait_for_terminal_job(initial["job_id"])

        self.assertLess(elapsed, 0.6)
        self.assertEqual(len(FAKE_BUILD_CALLS), build_call_count)
        self.assertIn(status["status"], {"running", "timed_out", "completed"})
        self.assertIn("first", status["stdout_tail"])
        self.assertEqual(final_status["status"], "completed")

    def test_status_wait_seconds_wakes_on_same_file_second_write(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            initial = server.opencode_coder(
                "double_write_same_file:src/a.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_change",
            )
            first_activity_at = initial["last_activity_at"]

            started_at = time.monotonic()
            second_status = server.opencode_coder_status(initial["job_id"], wait_seconds=1.2)
            elapsed = time.monotonic() - started_at
            final_status = wait_for_terminal_job(initial["job_id"])

        self.assertLess(elapsed, 1.0)
        self.assertEqual(second_status["new_changed_files"], ["src/a.txt"])
        self.assertNotEqual(second_status["last_activity_at"], first_activity_at)
        self.assertEqual(final_status["status"], "completed")

    def test_status_wait_seconds_clamp_helper(self):
        self.assertEqual(server.clamp_wait_seconds(-1), 0.0)
        self.assertEqual(server.clamp_wait_seconds(31), 30.0)
        self.assertEqual(server.clamp_wait_seconds("not-a-number"), 0.0)

    def test_not_found_status_with_wait_seconds_returns_quickly(self):
        started_at = time.monotonic()
        result = server.opencode_coder_status("missing-job", wait_seconds=30)
        elapsed = time.monotonic() - started_at

        self.assertLess(elapsed, 0.2)
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["recent_events"], [])
        self.assertEqual(result["recent_event_count"], 0)
        self.assertIsNone(result["last_event_type"])
        self.assertIsNone(result["last_text_output"])
        self.assertEqual(result["diagnostic_phase"], "no_event_seen")
        self.assertIn("not found", result["diagnostic_note"])
        self.assertFalse(result["no_event_noop_risk"])
        self.assertIsNone(result["no_event_noop_reason"])

    def test_status_returns_output_cursors(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)
            status = server.opencode_coder_status(completed["job_id"])

        self.assertGreater(status["stdout_cursor"], 0)
        self.assertEqual(status["stderr_cursor"], 0)
        self.assertEqual(status["stdout_delta"], "")
        self.assertEqual(status["stderr_delta"], "")
        self.assertEqual(status["stdout_tail"], "")
        self.assertEqual(status["stderr_tail"], "")
        self.assertEqual(status["output"], "")

    def test_status_default_omits_delta_even_with_cursor_but_advances_cursor(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "delayed_output",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_output",
            )
            status = server.opencode_coder_status(
                initial["job_id"],
                wait_seconds=2,
                stdout_cursor=initial["stdout_cursor"],
            )
            final_status = wait_for_terminal_job(initial["job_id"])

        self.assertEqual(status["stdout_delta"], "")
        self.assertEqual(status["stderr_delta"], "")
        self.assertEqual(status["stdout_tail"], "")
        self.assertEqual(status["output"], "")
        self.assertGreater(status["stdout_cursor"], initial["stdout_cursor"])
        self.assertFalse(status["stdout_delta_response_truncated"])
        self.assertEqual(final_status["status"], "completed")

    def test_status_stdout_delta_from_previous_cursor(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "delayed_output",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_output",
            )
            status = server.opencode_coder_status(
                initial["job_id"],
                wait_seconds=2,
                stdout_cursor=initial["stdout_cursor"],
                include_delta=True,
            )
            final_status = wait_for_terminal_job(initial["job_id"])

        self.assertIn("done", status["stdout_delta"])
        self.assertNotIn("first", status["stdout_delta"])
        self.assertGreater(status["stdout_cursor"], initial["stdout_cursor"])
        self.assertFalse(status["stdout_delta_truncated"])
        self.assertEqual(final_status["status"], "completed")

    def test_status_existing_unread_stdout_delta_returns_without_waiting(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "delayed_output",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_output",
            )
            started_at = time.monotonic()
            status = server.opencode_coder_status(
                initial["job_id"],
                wait_seconds=0.7,
                stdout_cursor=0,
                include_delta=True,
            )
            elapsed = time.monotonic() - started_at
            final_status = wait_for_terminal_job(initial["job_id"])

        self.assertLess(elapsed, 0.25)
        self.assertIn("first", status["stdout_delta"])
        self.assertGreater(status["stdout_cursor"], 0)
        self.assertEqual(final_status["status"], "completed")

    def test_status_stderr_delta_from_previous_cursor(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "delayed_stderr",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_output",
            )
            status = server.opencode_coder_status(
                initial["job_id"],
                wait_seconds=2,
                stderr_cursor=initial["stderr_cursor"],
                include_delta=True,
            )
            final_status = wait_for_terminal_job(initial["job_id"])

        self.assertIn("err-done", status["stderr_delta"])
        self.assertNotIn("err-first", status["stderr_delta"])
        self.assertGreater(status["stderr_cursor"], initial["stderr_cursor"])
        self.assertFalse(status["stderr_delta_truncated"])
        self.assertEqual(final_status["status"], "completed")

    def test_status_include_tail_and_output_returns_legacy_fields(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)
            status = server.opencode_coder_status(completed["job_id"], include_tail=True, include_output=True)

        self.assertIn("ok", status["stdout_tail"])
        self.assertEqual(status["stdout_delta"], "")
        self.assertEqual(status["stderr_delta"], "")
        self.assertIn("ok", status["output"])

    def test_status_include_tail_respects_tail_max_chars(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("large_stdout", working_dir=working_dir, timeout_seconds=2)
            status = server.opencode_coder_status(
                completed["job_id"],
                include_tail=True,
                include_output=True,
                tail_max_chars=6,
            )

        self.assertLessEqual(len(status["stdout_tail"]), 6)
        self.assertTrue(status["stdout_tail"].endswith("vwxyz\n"))
        self.assertEqual(status["output"], status["stdout_tail"].strip())

    def test_status_invalid_or_out_of_range_cursor_is_safe(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)
            invalid = server.opencode_coder_status(
                completed["job_id"],
                stdout_cursor="bad",
                stderr_cursor=-5,
                include_delta=True,
            )
            too_large = server.opencode_coder_status(
                completed["job_id"],
                stdout_cursor=999999,
                include_delta=True,
            )

        self.assertIn("ok", invalid["stdout_delta"])
        self.assertEqual(invalid["stderr_delta"], "")
        self.assertEqual(too_large["stdout_delta"], "")
        self.assertEqual(too_large["stdout_cursor"], completed["stdout_cursor"])

    def test_status_old_cursor_reports_delta_truncation(self):
        previous_limit = server.MAX_DELTA_BUFFER_CHARS
        server.MAX_DELTA_BUFFER_CHARS = 8
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                completed = server.opencode_coder("large_stdout", working_dir=working_dir, timeout_seconds=2)
                status = server.opencode_coder_status(completed["job_id"], stdout_cursor=0, include_delta=True)
        finally:
            server.MAX_DELTA_BUFFER_CHARS = previous_limit

        self.assertTrue(status["stdout_delta_truncated"])
        self.assertEqual(status["stdout_delta"], "tuvwxyz\n")
        self.assertEqual(status["stdout_cursor"], completed["stdout_cursor"])
        self.assertFalse(status["stdout_delta_response_truncated"])

    def test_status_delta_max_chars_truncates_response_and_advances_cursor(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("large_stdout", working_dir=working_dir, timeout_seconds=2)
            status = server.opencode_coder_status(
                completed["job_id"],
                stdout_cursor=0,
                include_delta=True,
                delta_max_chars=6,
            )
            next_status = server.opencode_coder_status(
                completed["job_id"],
                stdout_cursor=status["stdout_cursor"],
                include_delta=True,
            )

        self.assertEqual(status["stdout_delta"], "vwxyz\n")
        self.assertFalse(status["stdout_delta_truncated"])
        self.assertTrue(status["stdout_delta_response_truncated"])
        self.assertEqual(status["stdout_cursor"], completed["stdout_cursor"])
        self.assertEqual(next_status["stdout_delta"], "")

    def test_not_found_status_returns_cursor_and_delta_fields(self):
        result = server.opencode_coder_status("missing-job", stdout_cursor=123, stderr_cursor=456)

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["stdout_cursor"], 0)
        self.assertEqual(result["stderr_cursor"], 0)
        self.assertEqual(result["stdout_delta"], "")
        self.assertEqual(result["stderr_delta"], "")
        self.assertFalse(result["stdout_delta_truncated"])
        self.assertFalse(result["stderr_delta_truncated"])
        self.assertFalse(result["stdout_delta_response_truncated"])
        self.assertFalse(result["stderr_delta_response_truncated"])

    def test_server_start_registers_running_server_and_status(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_server_start(working_dir=working_dir, port=0)
            status = server.opencode_server_status(result["server_id"])
            server.opencode_server_stop(result["server_id"])

        self.assertEqual(result["status"], "running")
        self.assertTrue(result["process_running"])
        self.assertIsNotNone(result["server_id"])
        self.assertTrue(result["url"].startswith("http://127.0.0.1:"))
        self.assertGreater(result["port"], 0)
        self.assertEqual(status["status"], "running")

    def test_server_stop_updates_status(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            stopped = server.opencode_server_stop(started["server_id"])
            status = server.opencode_server_status(started["server_id"])

        self.assertEqual(stopped["status"], "stopped")
        self.assertFalse(stopped["process_running"])
        self.assertIn("process_tree_kill_attempted", stopped)
        self.assertIn("process_tree_kill_succeeded", stopped)
        self.assertIn("process_tree_kill_error", stopped)
        self.assertFalse(stopped["process_tree_kill_attempted"])
        self.assertFalse(stopped["process_tree_kill_succeeded"])
        self.assertIsNone(stopped["process_tree_kill_error"])
        self.assertEqual(status["status"], "stopped")

    def test_server_start_writes_registry_record(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            try:
                registry = read_test_registry()
                record = registry["servers"][started["server_id"]]
            finally:
                server.opencode_server_stop(started["server_id"])

        self.assertEqual(record["server_id"], started["server_id"])
        self.assertEqual(record["url"], started["url"])
        self.assertEqual(record["hostname"], started["hostname"])
        self.assertEqual(record["port"], started["port"])
        self.assertEqual(record["working_dir"], str(Path(working_dir).resolve(strict=False)))
        self.assertEqual(record["pid"], started["pid"])
        self.assertIn("command", record)
        self.assertEqual(started["registry_path"], server.get_server_registry_path())
        self.assertIsNone(started["registry_error"])

    def test_server_stop_removes_registry_record(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            stopped = server.opencode_server_stop(started["server_id"])
            registry = read_test_registry()

        self.assertEqual(stopped["status"], "stopped")
        self.assertNotIn(started["server_id"], registry["servers"])

    def test_server_status_recovers_running_server_from_registry(self):
        process = None
        with tempfile.TemporaryDirectory() as working_dir:
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            process = server._SERVERS[started["server_id"]].process
            with server._SERVER_REGISTRY_LOCK:
                server._SERVERS.clear()

            try:
                status = server.opencode_server_status(started["server_id"])
            finally:
                terminate_process(process)
                server.opencode_server_status(started["server_id"])
                with server._SERVER_REGISTRY_LOCK:
                    server._SERVERS.clear()

        self.assertEqual(status["status"], "running")
        self.assertTrue(status["success"])
        self.assertTrue(status["process_running"])
        self.assertTrue(status["recovered_from_registry"])
        self.assertEqual(status["server_id"], started["server_id"])
        self.assertEqual(status["url"], started["url"])
        self.assertEqual(status["stdout_tail"], "")
        self.assertEqual(status["stderr_tail"], "")

    def test_recovered_server_can_be_used_for_attached_coder(self):
        process = None
        with tempfile.TemporaryDirectory() as working_dir:
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            process = server._SERVERS[started["server_id"]].process
            with server._SERVER_REGISTRY_LOCK:
                server._SERVERS.clear()

            try:
                result = server.opencode_coder(
                    "session_json",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    server_id=started["server_id"],
                )
            finally:
                terminate_process(process)
                server.opencode_server_status(started["server_id"])
                with server._SERVER_REGISTRY_LOCK:
                    server._SERVERS.clear()

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["attached_to_server"])
        self.assertTrue(result["server_recovered_from_registry"])
        self.assertEqual(result["server_id"], started["server_id"])
        self.assertEqual(result["server_url"], started["url"])
        self.assertEqual(FAKE_BUILD_CALLS[-1]["server_url"], started["url"])

    def test_recovered_server_stop_limitation_does_not_pollute_status(self):
        process = None
        with tempfile.TemporaryDirectory() as working_dir:
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            process = server._SERVERS[started["server_id"]].process
            with server._SERVER_REGISTRY_LOCK:
                server._SERVERS.clear()

            try:
                stopped = server.opencode_server_stop(started["server_id"])
                status = server.opencode_server_status(started["server_id"])
            finally:
                terminate_process(process)
                server.opencode_server_status(started["server_id"])
                with server._SERVER_REGISTRY_LOCK:
                    server._SERVERS.clear()

        self.assertEqual(stopped["status"], "running")
        self.assertFalse(stopped["success"])
        self.assertIn("will not blind-kill", stopped["error"])
        self.assertEqual(status["status"], "running")
        self.assertTrue(status["success"])
        self.assertIsNone(status["error"])
        self.assertTrue(status["recovered_from_registry"])

    def test_server_list_includes_memory_server(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            try:
                result = server.opencode_server_list()
            finally:
                server.opencode_server_stop(started["server_id"])

        servers_by_id = {item["server_id"]: item for item in result["servers"]}
        self.assertIn(started["server_id"], servers_by_id)
        self.assertEqual(servers_by_id[started["server_id"]]["status"], "running")
        self.assertTrue(servers_by_id[started["server_id"]]["process_running"])
        self.assertEqual(result["registry_path"], server.get_server_registry_path())
        self.assertGreaterEqual(result["count"], 1)

    def test_server_list_recovers_server_from_registry(self):
        process = None
        with tempfile.TemporaryDirectory() as working_dir:
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            process = server._SERVERS[started["server_id"]].process
            with server._SERVER_REGISTRY_LOCK:
                server._SERVERS.clear()

            try:
                result = server.opencode_server_list()
            finally:
                terminate_process(process)
                server.opencode_server_status(started["server_id"])
                with server._SERVER_REGISTRY_LOCK:
                    server._SERVERS.clear()

        servers_by_id = {item["server_id"]: item for item in result["servers"]}
        self.assertIn(started["server_id"], servers_by_id)
        self.assertTrue(servers_by_id[started["server_id"]]["recovered_from_registry"])
        self.assertEqual(servers_by_id[started["server_id"]]["status"], "running")

    def test_server_list_filters_by_working_dir(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = server.opencode_server_start(working_dir=first_dir, port=0)
            second = server.opencode_server_start(working_dir=second_dir, port=0)
            try:
                result = server.opencode_server_list(working_dir=str(Path(first_dir).resolve(strict=False)))
            finally:
                server.opencode_server_stop(first["server_id"])
                server.opencode_server_stop(second["server_id"])

        server_ids = {item["server_id"] for item in result["servers"]}
        self.assertIn(first["server_id"], server_ids)
        self.assertNotIn(second["server_id"], server_ids)

    def test_server_list_handles_stale_registry_records(self):
        stale_id = "stale-server-list"
        stale_port = server.choose_free_port("127.0.0.1")
        stale_record = {
            "server_id": stale_id,
            "url": f"http://127.0.0.1:{stale_port}",
            "hostname": "127.0.0.1",
            "port": stale_port,
            "working_dir": str(Path.cwd()),
            "pid": os.getpid(),
            "started_at": "2026-01-01T00:00:00.000Z",
            "command": ["opencode", "serve"],
        }
        write_test_registry({"version": server.SERVER_REGISTRY_VERSION, "servers": {stale_id: stale_record}})

        hidden = server.opencode_server_list(include_lost=False)
        hidden_registry = read_test_registry()
        write_test_registry({"version": server.SERVER_REGISTRY_VERSION, "servers": {stale_id: stale_record}})
        visible = server.opencode_server_list(include_lost=True)

        self.assertEqual(hidden["servers"], [])
        self.assertIn("registry_stale", hidden["registry_error"])
        self.assertNotIn(stale_id, hidden_registry["servers"])
        self.assertEqual(visible["count"], 1)
        self.assertEqual(visible["servers"][0]["status"], "lost")
        self.assertEqual(visible["servers"][0]["server_id"], stale_id)
        self.assertIn("server_not_reachable", visible["servers"][0]["registry_error"])

    def test_stale_registry_server_returns_lost_and_cleans_record(self):
        stale_id = "stale-server"
        stale_port = server.choose_free_port("127.0.0.1")
        write_test_registry(
            {
                "version": server.SERVER_REGISTRY_VERSION,
                "servers": {
                    stale_id: {
                        "server_id": stale_id,
                        "url": f"http://127.0.0.1:{stale_port}",
                        "hostname": "127.0.0.1",
                        "port": stale_port,
                        "working_dir": str(Path.cwd()),
                        "pid": os.getpid(),
                        "started_at": "2026-01-01T00:00:00.000Z",
                        "command": ["opencode", "serve"],
                    }
                },
            }
        )

        result = server.opencode_server_status(stale_id)
        registry = read_test_registry()

        self.assertEqual(result["status"], "lost")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "server_lost")
        self.assertIn("server_not_reachable", result["registry_error"])
        self.assertNotIn(stale_id, registry["servers"])

    def test_corrupt_registry_file_does_not_crash_status(self):
        Path(server.get_server_registry_path()).parent.mkdir(parents=True, exist_ok=True)
        Path(server.get_server_registry_path()).write_text("{not-json", encoding="utf-8")

        result = server.opencode_server_status("missing-server")

        self.assertEqual(result["status"], "not_found")
        self.assertFalse(result["success"])
        self.assertIn("failed to read registry", result["registry_error"])

    def test_job_status_is_not_restored_from_registry(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)
            with server._REGISTRY_LOCK:
                server._JOBS.clear()
                server._CWD_ACTIVE_JOBS.clear()
            result = server.opencode_coder_status(completed["job_id"])

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["error"], "job_not_found")

    def test_process_tree_kill_helper_uses_platform_cleanup_path(self):
        class DummyProcess:
            pid = 4321

            def __init__(self):
                self.returncode = None

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.returncode = -9
                return self.returncode

        process = DummyProcess()
        if os.name == "nt":
            calls = []
            original_run = server.subprocess.run

            class DummyCompletedProcess:
                returncode = 0
                stdout = ""
                stderr = ""

            def fake_run(args, **kwargs):
                calls.append((args, kwargs))
                return DummyCompletedProcess()

            server.subprocess.run = fake_run
            try:
                result = server.process_tree_kill(process)
            finally:
                server.subprocess.run = original_run

            self.assertEqual(calls[0][0], ["taskkill", "/PID", "4321", "/T", "/F"])
            self.assertFalse(calls[0][1].get("shell", False))
        else:
            calls = []
            original_killpg = server.os.killpg

            def fake_killpg(pgid, sig):
                calls.append((pgid, sig))

            server.os.killpg = fake_killpg
            try:
                result = server.process_tree_kill(process)
            finally:
                server.os.killpg = original_killpg

            self.assertEqual(calls, [(4321, server.signal.SIGKILL)])

        self.assertTrue(result.attempted)
        self.assertTrue(result.succeeded)
        self.assertIsNone(result.error)

    def test_popen_platform_kwargs_keep_hidden_window_or_session(self):
        kwargs = server.popen_platform_kwargs()
        if os.name == "nt":
            self.assertEqual(kwargs.get("creationflags"), getattr(server.subprocess, "CREATE_NO_WINDOW", 0))
        else:
            self.assertTrue(kwargs["start_new_session"])

    def test_build_command_uses_default_model_and_variant(self):
        command = self.original_build_command("prompt")

        self.assertIn("--model", command)
        self.assertEqual(command[command.index("--model") + 1], "deepseek/deepseek-v4-pro")
        self.assertIn("--variant", command)
        self.assertEqual(command[command.index("--variant") + 1], "max")
        self.assertNotIn("--agent", command)
        self.assertNotIn("--show" + "-thinking", command)
        self.assertNotIn("--thinking", command)

    def test_build_command_supports_explicit_model_variant_agent_and_thinking(self):
        command = self.original_build_command(
            "prompt",
            model="openai/gpt-5",
            variant="fast",
            agent="coder",
            show_thinking=True,
        )

        self.assertEqual(command[command.index("--model") + 1], "openai/gpt-5")
        self.assertEqual(command[command.index("--variant") + 1], "fast")
        self.assertEqual(command[command.index("--agent") + 1], "coder")
        self.assertIn("--thinking", command)
        self.assertNotIn("--show" + "-thinking", command)

    def test_build_command_skips_empty_model_variant_agent_and_thinking(self):
        command = self.original_build_command(
            "prompt",
            model="",
            variant=None,
            agent="",
            show_thinking=False,
        )

        self.assertNotIn("--model", command)
        self.assertNotIn("--variant", command)
        self.assertNotIn("--agent", command)
        self.assertNotIn("--show" + "-thinking", command)
        self.assertNotIn("--thinking", command)

    def test_build_command_supports_attach_session_and_title_flags(self):
        command = self.original_build_command(
            "prompt",
            working_dir="C:/repo",
            server_url="http://127.0.0.1:12345",
            session_id="ses_existing",
            continue_last=True,
            fork_session=True,
            title="Task Title",
        )

        self.assertIn("run", command)
        self.assertIn("--attach", command)
        self.assertIn("http://127.0.0.1:12345", command)
        self.assertIn("--dir", command)
        self.assertIn("C:/repo", command)
        self.assertIn("--session", command)
        self.assertIn("ses_existing", command)
        self.assertIn("--continue", command)
        self.assertIn("--fork", command)
        self.assertIn("--title", command)
        self.assertIn("Task Title", command)

    def test_effective_timeout_uses_client_timeout_margin_env(self):
        original_max_wait = os.environ.pop("OPENCODE_CODER_MAX_WAIT_SECONDS", None)
        original_client_timeout = os.environ.get("OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS")
        original_margin = os.environ.get("OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS")
        os.environ["OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS"] = "240"
        os.environ["OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS"] = "30"
        try:
            requested, effective, policy = server.compute_effective_timeout(500)
        finally:
            if original_max_wait is not None:
                os.environ["OPENCODE_CODER_MAX_WAIT_SECONDS"] = original_max_wait
            if original_client_timeout is None:
                os.environ.pop("OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS", None)
            else:
                os.environ["OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS"] = original_client_timeout
            if original_margin is None:
                os.environ.pop("OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS", None)
            else:
                os.environ["OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS"] = original_margin

        self.assertEqual(requested, 500)
        self.assertEqual(effective, 210)
        self.assertIn("capped_by_wrapper", policy)

    def test_effective_wait_caps_explicit_wait_by_default_mcp_budget(self):
        original_max_wait = os.environ.pop("OPENCODE_CODER_MAX_WAIT_SECONDS", None)
        original_client_timeout = os.environ.get("OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS")
        original_margin = os.environ.get("OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS")
        os.environ.pop("OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS", None)
        os.environ.pop("OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS", None)
        try:
            requested, effective, policy = server.compute_effective_wait_wait_seconds(600)
        finally:
            if original_max_wait is not None:
                os.environ["OPENCODE_CODER_MAX_WAIT_SECONDS"] = original_max_wait
            if original_client_timeout is not None:
                os.environ["OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS"] = original_client_timeout
            if original_margin is not None:
                os.environ["OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS"] = original_margin

        self.assertEqual(requested, 600)
        self.assertEqual(effective, 215)
        self.assertIn("capped_by_wrapper", policy)

    def test_effective_wait_uses_explicit_max_wait_as_final_cap(self):
        original_max_wait = os.environ.get("OPENCODE_CODER_MAX_WAIT_SECONDS")
        original_client_timeout = os.environ.get("OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS")
        original_margin = os.environ.get("OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS")
        os.environ["OPENCODE_CODER_MAX_WAIT_SECONDS"] = "0.15"
        os.environ["OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS"] = "240"
        os.environ["OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS"] = "25"
        try:
            requested, effective, policy = server.compute_effective_wait_wait_seconds(600)
        finally:
            if original_max_wait is None:
                os.environ.pop("OPENCODE_CODER_MAX_WAIT_SECONDS", None)
            else:
                os.environ["OPENCODE_CODER_MAX_WAIT_SECONDS"] = original_max_wait
            if original_client_timeout is None:
                os.environ.pop("OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS", None)
            else:
                os.environ["OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS"] = original_client_timeout
            if original_margin is None:
                os.environ.pop("OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS", None)
            else:
                os.environ["OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS"] = original_margin

        self.assertEqual(requested, 600)
        self.assertEqual(effective, 0.15)
        self.assertIn("capped_by_wrapper", policy)

    def test_attached_coder_passes_server_url_and_parses_session_id(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            result = server.opencode_coder(
                "session_json",
                working_dir=working_dir,
                timeout_seconds=2,
                server_id=started["server_id"],
            )
            job_status = server.opencode_coder_status(result["job_id"])
            server.opencode_server_stop(started["server_id"])

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["attached_to_server"])
        self.assertEqual(result["server_id"], started["server_id"])
        self.assertEqual(result["server_url"], started["url"])
        self.assertEqual(result["session_id"], "ses_test_123")
        self.assertEqual(job_status["session_id"], "ses_test_123")
        self.assertEqual(job_status["server_id"], started["server_id"])
        self.assertTrue(job_status["attached_to_server"])
        self.assertEqual(FAKE_BUILD_CALLS[-1]["server_url"], started["url"])
        self.assertEqual(FAKE_BUILD_CALLS[-1]["working_dir"], str(Path(working_dir).resolve(strict=False)))

    def test_attached_coder_passes_session_options(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            result = server.opencode_coder(
                "session_json",
                working_dir=working_dir,
                timeout_seconds=2,
                server_id=started["server_id"],
                session_id="ses_existing",
                continue_last=True,
                fork_session=True,
                title="Reuse Session",
                wait_policy="first_output",
            )
            server.opencode_server_stop(started["server_id"])

        self.assertEqual(result["session_id"], "ses_test_123")
        self.assertEqual(result["wait_policy"], "first_output")
        self.assertEqual(FAKE_BUILD_CALLS[-1]["session_id"], "ses_existing")
        self.assertTrue(FAKE_BUILD_CALLS[-1]["continue_last"])
        self.assertTrue(FAKE_BUILD_CALLS[-1]["fork_session"])
        self.assertEqual(FAKE_BUILD_CALLS[-1]["title"], "Reuse Session")

    def test_attached_session_no_event_no_change_no_output_reports_noop_risk(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            try:
                result = server.opencode_coder(
                    "no_output_success",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    server_id=started["server_id"],
                    session_id="ses_existing",
                )
                status = server.opencode_coder_status(result["job_id"])
            finally:
                server.opencode_server_stop(started["server_id"])

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["success"])
        self.assertTrue(result["attached_to_server"])
        self.assertEqual(result["recent_event_count"], 0)
        self.assertEqual(result["new_changed_files"], [])
        self.assertEqual(result["stdout_tail"], "")
        self.assertEqual(result["stderr_tail"], "")
        self.assertTrue(result["no_event_noop_risk"])
        self.assertEqual(
            result["no_event_noop_reason"],
            "completed_attached_session_reuse_without_events_changes_or_output",
        )
        self.assertEqual(result["suggested_action"], "check_session_or_retry_without_session")
        self.assertTrue(result["review_required"])
        self.assertEqual(result["progress_phase"], "no_event_noop_risk")
        self.assertTrue(result["caller_update_recommended"])
        self.assertEqual(result["caller_update_reason"], "no_event_noop_risk")
        self.assertTrue(result["session_reuse_risk"])
        self.assertEqual(result["session_reuse_note"], "no_event_noop_risk")
        self.assertEqual(result["root_cause_guess"], "no_event_noop")
        self.assertIn(
            "completed with no stdout JSON events and no job-scoped changes",
            result["diagnostic_note"],
        )
        self.assertIn("session reuse may have no-oped", result["diagnostic_note"])
        self.assertTrue(status["no_event_noop_risk"])
        self.assertEqual(status["suggested_action"], "check_session_or_retry_without_session")
        self.assertEqual(status["progress_phase"], "no_event_noop_risk")

    def test_attached_session_json_event_without_changes_is_not_noop_risk(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            try:
                result = server.opencode_coder(
                    "opencode_events",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    server_id=started["server_id"],
                    session_id="ses_existing",
                )
            finally:
                server.opencode_server_stop(started["server_id"])

        self.assertEqual(result["status"], "completed")
        self.assertGreater(result["recent_event_count"], 0)
        self.assertEqual(result["new_changed_files"], [])
        self.assertFalse(result["no_event_noop_risk"])
        self.assertIsNone(result["no_event_noop_reason"])
        self.assertEqual(result["suggested_action"], "review_result")
        self.assertFalse(result["review_required"])

    def test_attached_session_file_change_without_events_is_not_noop_risk(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            started = server.opencode_server_start(working_dir=working_dir, port=0)
            try:
                result = server.opencode_coder(
                    "write_silent:src/changed.txt",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    server_id=started["server_id"],
                    session_id="ses_existing",
                )
            finally:
                server.opencode_server_stop(started["server_id"])

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["recent_event_count"], 0)
        self.assertEqual(result["new_changed_files"], ["src/changed.txt"])
        self.assertEqual(result["stdout_tail"], "")
        self.assertFalse(result["no_event_noop_risk"])
        self.assertIsNone(result["no_event_noop_reason"])
        self.assertFalse(result["review_required"])

    def test_direct_no_event_no_change_no_output_is_not_noop_risk(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder("no_output_success", working_dir=working_dir, timeout_seconds=2)

        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["attached_to_server"])
        self.assertEqual(result["recent_event_count"], 0)
        self.assertEqual(result["new_changed_files"], [])
        self.assertFalse(result["no_event_noop_risk"])
        self.assertIsNone(result["no_event_noop_reason"])
        self.assertEqual(result["suggested_action"], "review_result")
        self.assertFalse(result["review_required"])

    def test_opencode_json_events_record_diagnostics_and_session_id(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder("opencode_events", working_dir=working_dir, timeout_seconds=2)
            status = server.opencode_coder_status(result["job_id"])

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["session_id"], "ses_evt_123")
        self.assertEqual(result["last_session_id"], "ses_evt_123")
        self.assertEqual(result["last_event_type"], "step")
        self.assertEqual(result["last_event_at"], "2026-06-01T00:00:03Z")
        self.assertEqual(result["recent_event_count"], 3)
        self.assertEqual(len(result["recent_events"]), 3)
        self.assertEqual(result["last_text_output"], "Working on it")
        self.assertEqual(result["work_summary_text"], "Working on it")
        self.assertEqual(result["assistant_last_text"], "Working on it")
        self.assertEqual(result["last_tool_name"], "edit")
        self.assertEqual(result["last_tool_event"]["part_type"], "tool")
        self.assertEqual(result["last_step_status"], "finished")
        self.assertEqual(result["last_step_reason"], "stop")
        self.assertEqual(result["diagnostic_phase"], "process_finished")
        self.assertIn("step_finished", result["diagnostic_note"])
        self.assertEqual(status["recent_event_count"], 3)
        self.assertEqual(status["last_event_summary"]["type"], "step")

    def test_recent_opencode_events_keep_recent_limit(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder("many_opencode_events", working_dir=working_dir, timeout_seconds=2)
            verbose_status = server.opencode_coder_status(result["job_id"], recent_events_limit=20)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["recent_event_count"], server.RECENT_EVENT_LIMIT + 5)
        self.assertEqual(len(result["recent_events"]), server.DEFAULT_RECENT_EVENTS_LIMIT)
        self.assertEqual(result["recent_events"][0]["messageID"], "msg_20")
        self.assertEqual(result["recent_events"][-1]["messageID"], "msg_24")
        self.assertEqual(result["last_text_output"], "event 24")
        self.assertEqual(result["work_summary_text"], "event 24")
        self.assertEqual(len(verbose_status["recent_events"]), server.RECENT_EVENT_LIMIT)
        self.assertEqual(verbose_status["recent_events"][0]["messageID"], "msg_5")
        self.assertEqual(verbose_status["recent_events"][-1]["messageID"], "msg_24")

    def test_large_text_event_is_stored_as_preview_only(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder("large_json_text", working_dir=working_dir, timeout_seconds=2)

        self.assertEqual(result["status"], "completed")
        self.assertLess(len(result["last_text_output"]), 400)
        self.assertLess(len(result["recent_events"][0]["text_preview"]), 400)
        self.assertNotIn("FULL_TEXT_SENTINEL", result["last_text_output"])
        self.assertNotIn("FULL_TEXT_SENTINEL", result["work_summary_text"])
        self.assertTrue(result["last_text_output"].endswith("...[truncated]"))

    def test_non_json_stdout_keeps_output_and_empty_event_diagnostics(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "short",
                working_dir=working_dir,
                timeout_seconds=2,
                include_tail=True,
            )
            status = server.opencode_coder_status(
                result["job_id"],
                stdout_cursor=0,
                include_tail=True,
                include_delta=True,
            )

        self.assertEqual(result["status"], "completed")
        self.assertIn("ok", result["stdout_tail"])
        self.assertIn("ok", status["stdout_delta"])
        self.assertEqual(result["recent_events"], [])
        self.assertEqual(result["recent_event_count"], 0)
        self.assertIsNone(result["last_event_type"])
        self.assertIsNone(result["last_text_output"])
        self.assertIsNone(result["work_summary_text"])
        self.assertEqual(result["diagnostic_phase"], "process_finished")

    def test_progress_waiting_first_output_without_output(self):
        original_startup = server.PROGRESS_STARTUP_SECONDS
        server.PROGRESS_STARTUP_SECONDS = 0.0
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                result = server.opencode_coder(
                    "no_output_long",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    wait_policy="start_only",
                )
                status = server.opencode_coder_status(result["job_id"])
                server.opencode_coder_cancel(result["job_id"])
        finally:
            server.PROGRESS_STARTUP_SECONDS = original_startup

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertEqual(status["progress_phase"], "waiting_first_output")
        self.assertFalse(status["caller_update_recommended"])
        self.assertEqual(status["caller_update_reason"], "continue_silent_poll")
        self.assertIsNone(status["time_to_first_output_seconds"])
        self.assertIsNone(status["time_to_first_event_seconds"])
        self.assertIsNone(status["time_to_first_tool_seconds"])
        self.assertIsNone(status["time_to_first_change_seconds"])

    def test_progress_long_context_or_planning_after_read_without_change(self):
        original_budget = server.NO_FIRST_CHANGE_BUDGET_SECONDS
        original_no_activity = server.STALL_NO_ACTIVITY_SECONDS
        original_no_output = server.STALL_NO_OUTPUT_SECONDS
        server.NO_FIRST_CHANGE_BUDGET_SECONDS = 0.05
        server.STALL_NO_ACTIVITY_SECONDS = 100.0
        server.STALL_NO_OUTPUT_SECONDS = 100.0
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                result = server.opencode_coder(
                    "read_event_then_sleep",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    wait_policy="first_output",
                )
                time.sleep(0.08)
                status = server.opencode_coder_status(result["job_id"])
                server.opencode_coder_cancel(result["job_id"])
        finally:
            server.NO_FIRST_CHANGE_BUDGET_SECONDS = original_budget
            server.STALL_NO_ACTIVITY_SECONDS = original_no_activity
            server.STALL_NO_OUTPUT_SECONDS = original_no_output

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertEqual(status["progress_phase"], "long_context_or_planning")
        self.assertEqual(status["caller_update_reason"], "no_first_change_after_budget")
        self.assertEqual(status["root_cause_guess"], "slow_context_reading")
        self.assertGreaterEqual(status["tool_activity_summary"]["read"], 1)
        self.assertIsNotNone(status["time_to_first_event_seconds"])
        self.assertIsNotNone(status["time_to_first_tool_seconds"])
        self.assertIsNone(status["time_to_first_change_seconds"])

    def test_first_change_progress_and_time_fields(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder(
                "delayed_write:src/changed.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_change",
            )
            final_status = wait_for_terminal_job(result["job_id"])

        self.assertEqual(result["progress_phase"], "editing")
        self.assertEqual(result["new_changed_files"], ["src/changed.txt"])
        self.assertIsNotNone(result["time_to_first_change_seconds"])
        self.assertIsNotNone(result["seconds_since_last_change"])
        self.assertEqual(result["caller_update_reason"], "first_change_seen")
        self.assertEqual(final_status["status"], "completed")

    def test_completed_slow_before_first_change_keeps_specific_root_cause(self):
        original_budget = server.NO_FIRST_CHANGE_BUDGET_SECONDS
        server.NO_FIRST_CHANGE_BUDGET_SECONDS = 0.05
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                init_git_repo(working_dir)
                result = server.opencode_coder(
                    "slow_read_then_write:src/slow.txt",
                    working_dir=working_dir,
                    timeout_seconds=2,
                )
        finally:
            server.NO_FIRST_CHANGE_BUDGET_SECONDS = original_budget

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["progress_phase"], "completed")
        self.assertEqual(result["new_changed_files"], ["src/slow.txt"])
        self.assertGreaterEqual(result["time_to_first_change_seconds"], 0.05)
        self.assertEqual(result["root_cause_guess"], "slow_context_reading")
        self.assertNotEqual(result["root_cause_guess"], "completed_normally")

    def test_validation_words_in_plain_text_do_not_count_as_observed_validation(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "validation_words_text_then_sleep",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_output",
            )
            status = server.opencode_coder_status(result["job_id"])
            server.opencode_coder_cancel(result["job_id"])

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertEqual(status["observed_validation_tools"], [])
        self.assertEqual(status["observed_validation_result"], "none")
        self.assertNotEqual(status["progress_phase"], "validating")
        self.assertEqual(status["progress_phase"], "planning_or_reasoning")

    def test_validation_words_in_plain_stdout_do_not_count_as_observed_validation(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "validation_words_stdout_then_sleep",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_output",
            )
            status = server.opencode_coder_status(result["job_id"])
            server.opencode_coder_cancel(result["job_id"])

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertEqual(status["recent_event_count"], 0)
        self.assertEqual(status["observed_validation_tools"], [])
        self.assertEqual(status["observed_validation_result"], "none")
        self.assertNotEqual(status["progress_phase"], "validating")

    def test_read_tool_content_with_validation_words_is_not_observed_validation(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "read_tool_validation_words_then_sleep",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_output",
            )
            status = server.opencode_coder_status(result["job_id"])
            server.opencode_coder_cancel(result["job_id"])

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertGreaterEqual(status["tool_activity_summary"]["read"], 1)
        self.assertEqual(status["tool_activity_summary"]["unity"], 0)
        self.assertEqual(status["observed_validation_tools"], [])
        self.assertEqual(status["observed_validation_result"], "none")
        self.assertNotEqual(status["progress_phase"], "validating")

    def test_list_tool_content_with_validation_words_is_not_observed_validation(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "glob_tool_validation_words_then_sleep",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_output",
            )
            status = server.opencode_coder_status(result["job_id"])
            server.opencode_coder_cancel(result["job_id"])

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertGreaterEqual(status["tool_activity_summary"]["list"], 1)
        self.assertEqual(status["tool_activity_summary"]["unity"], 0)
        self.assertEqual(status["observed_validation_tools"], [])
        self.assertEqual(status["observed_validation_result"], "none")
        self.assertNotEqual(status["progress_phase"], "validating")

    def test_bash_tool_activity_summary_and_validation_phase(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "bash_tool_event_then_sleep",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_output",
            )
            status = server.opencode_coder_status(result["job_id"])
            server.opencode_coder_cancel(result["job_id"])

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertEqual(status["progress_phase"], "validating")
        self.assertGreaterEqual(status["tool_activity_summary"]["bash"], 1)
        self.assertIn("py_compile", status["observed_validation_tools"])
        self.assertEqual(status["observed_validation_result"], "inconclusive")

    def test_unity_validation_observation_extracts_passed_summary(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder("unity_validation_events", working_dir=working_dir, timeout_seconds=2)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["progress_phase"], "completed")
        self.assertGreaterEqual(result["tool_activity_summary"]["unity"], 3)
        self.assertEqual(
            result["observed_validation_tools"],
            [
                "unity_skills_debug_force_recompile",
                "unity_skills_debug_check_compilation",
                "unity_skills_console_get_logs",
            ],
        )
        self.assertEqual(result["observed_validation_result"], "passed")
        self.assertEqual(result["observed_validation_errors_count"], 0)
        self.assertIn("0 error", result["observed_validation_summary"])

    def test_observed_validation_failed_signal_wins_over_passed_signal(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "unity_validation_failed_after_pass",
                working_dir=working_dir,
                timeout_seconds=2,
            )

        self.assertEqual(result["status"], "completed")
        self.assertIn("unity_skills_console_get_logs", result["observed_validation_tools"])
        self.assertEqual(result["observed_validation_result"], "failed")
        self.assertEqual(result["observed_validation_errors_count"], 2)
        self.assertNotEqual(result["observed_validation_result"], "passed")
        self.assertNotIn("0 error(s)", result["observed_validation_summary"])
        self.assertIn("failing", result["observed_validation_summary"])

    def test_observed_validation_failure_marker_wins_over_later_zero_errors(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "unity_validation_failed_then_zero",
                working_dir=working_dir,
                timeout_seconds=2,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["observed_validation_errors_count"], 0)
        self.assertEqual(result["observed_validation_result"], "failed")
        self.assertNotEqual(result["observed_validation_result"], "passed")
        self.assertNotIn("0 error(s)", result["observed_validation_summary"])
        self.assertIn("failing", result["observed_validation_summary"])

    def test_terminal_statuses_recommend_caller_update(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)
            failed = server.opencode_coder("fail", working_dir=working_dir, timeout_seconds=2)
            timed_out = server.opencode_coder("no_output_long", working_dir=working_dir, timeout_seconds=0)
            try:
                cancellable = server.opencode_coder(
                    "very_long",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    wait_policy="start_only",
                    allow_concurrent=True,
                )
                cancelled = server.opencode_coder_cancel(cancellable["job_id"])
            finally:
                server.opencode_coder_cancel(timed_out["job_id"])

        for result in (completed, failed, timed_out, cancelled):
            self.assertTrue(result["caller_update_recommended"])
            self.assertEqual(result["caller_update_reason"], "terminal_status")
        self.assertEqual(completed["progress_phase"], "completed")
        self.assertEqual(failed["progress_phase"], "failed")
        self.assertEqual(timed_out["progress_phase"], "timed_out")
        self.assertEqual(cancelled["progress_phase"], "cancelled")

    def test_status_not_found_has_progress_fields(self):
        result = server.opencode_coder_status("missing-job")

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["progress_phase"], "not_found")
        self.assertTrue(result["caller_update_recommended"])
        self.assertEqual(result["next_poll_after_seconds"], 0)
        self.assertEqual(result["root_cause_guess"], "unknown")
        self.assertEqual(result["observed_validation_result"], "none")

    def test_session_reuse_diagnostics_for_consecutive_jobs(self):
        with tempfile.TemporaryDirectory() as working_dir:
            first = server.opencode_coder(
                "session_json",
                working_dir=working_dir,
                timeout_seconds=2,
                session_id="ses_existing",
            )
            second = server.opencode_coder(
                "session_json",
                working_dir=working_dir,
                timeout_seconds=2,
                session_id="ses_existing",
            )

        self.assertTrue(first["session_reuse_detected"])
        self.assertEqual(first["session_reuse_mode"], "explicit_session")
        self.assertEqual(first["same_session_recent_job_count"], 1)
        self.assertTrue(second["session_reuse_detected"])
        self.assertEqual(second["same_session_recent_job_count"], 2)
        self.assertEqual(second["same_session_last_job_status"], "completed")
        self.assertFalse(second["session_reuse_risk"])
        self.assertEqual(second["session_reuse_note"], "same_working_dir_recent_session")

    def test_likely_preexisting_from_same_session_positive_and_negative(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            first = server.opencode_coder(
                "write:src/shared.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                session_id="ses_same",
            )
            second = server.opencode_coder(
                "write:src/second.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                session_id="ses_same",
            )
            third = server.opencode_coder(
                "write:src/third.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                session_id="ses_other",
            )

        self.assertEqual(first["new_changed_files"], ["src/shared.txt"])
        self.assertTrue(second["likely_preexisting_from_same_session"])
        self.assertEqual(second["likely_preexisting_same_session_files"], ["src/shared.txt"])
        self.assertFalse(third["likely_preexisting_from_same_session"])
        self.assertEqual(third["likely_preexisting_same_session_files"], [])

    def test_long_gap_segments_are_bounded_and_compact(self):
        original_gap = server.LONG_GAP_MIN_SECONDS
        server.LONG_GAP_MIN_SECONDS = 0.01
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                result = server.opencode_coder("gap_events", working_dir=working_dir, timeout_seconds=2)
        finally:
            server.LONG_GAP_MIN_SECONDS = original_gap

        self.assertLessEqual(len(result["long_gap_segments"]), server.MAX_LONG_GAP_SEGMENTS)
        self.assertGreater(len(result["long_gap_segments"]), 0)
        for segment in result["long_gap_segments"]:
            self.assertLessEqual(len(segment["after"]), server.LONG_GAP_LABEL_MAX_CHARS + 15)
            self.assertLessEqual(len(segment["before"]), server.LONG_GAP_LABEL_MAX_CHARS + 15)
            self.assertIn("duration_seconds", segment)
            self.assertIn("phase_guess", segment)

    def test_compact_diagnostics_do_not_return_large_stdout_or_raw_event_text(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder("large_json_text", working_dir=working_dir, timeout_seconds=2)

        self.assertEqual(result["stdout_tail"], "")
        self.assertEqual(result["stderr_tail"], "")
        self.assertEqual(result["stdout_delta"], "")
        self.assertEqual(result["stderr_delta"], "")
        self.assertLess(len(result["progress_message"]), 300)
        self.assertNotIn("FULL_TEXT_SENTINEL", result["progress_message"])
        self.assertNotIn("FULL_TEXT_SENTINEL", result["observed_validation_summary"])
        self.assertNotIn("FULL_TEXT_SENTINEL", json.dumps(result["long_gap_segments"]))

    def test_missing_server_id_returns_structured_failure(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "short",
                working_dir=working_dir,
                timeout_seconds=2,
                server_id="missing-server",
            )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["success"])
        self.assertFalse(result["attached_to_server"])
        self.assertEqual(result["server_id"], "missing-server")
        self.assertIn("server_id not found", result["error"])
        self.assertFalse(result["no_event_noop_risk"])
        self.assertIsNone(result["no_event_noop_reason"])

    def test_long_task_times_out_and_can_be_queried_until_final_completion(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder("long", working_dir=working_dir, timeout_seconds=0.05)

            self.assertEqual(result["status"], "timed_out")
            self.assertTrue(result["process_running"])
            self.assertIsNotNone(result["pid"])
            self.assertEqual(result["suggested_action"], "continue_polling_or_consider_cancel")
            self.assertIn("not completed", result["validation_note"])

            job_id = result["job_id"]
            running_status = server.opencode_coder_status(job_id)
            self.assertIn(running_status["status"], {"timed_out", "completed"})

            final_status = wait_for_terminal_job(job_id)
            final_output = server.opencode_coder_status(job_id, include_tail=True)

        self.assertEqual(final_status["status"], "completed")
        self.assertEqual(final_status["exit_code"], 0)
        self.assertFalse(final_status["process_running"])
        self.assertIn("begin", final_output["stdout_tail"])
        self.assertIn("done", final_output["stdout_tail"])

    def test_running_job_just_started_is_not_stalled(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "no_output_long",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            status = server.opencode_coder_status(result["job_id"])
            server.opencode_coder_cancel(result["job_id"])

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertFalse(status["is_stalled"])
        self.assertIsNone(status["stall_reason"])
        self.assertEqual(status["suggested_action"], "continue_polling")
        self.assertFalse(status["potential_incomplete_changes_risk"])

    def test_no_output_running_job_reports_stalled_after_threshold(self):
        original_no_output = server.STALL_NO_OUTPUT_SECONDS
        server.STALL_NO_OUTPUT_SECONDS = 0.1
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                result = server.opencode_coder(
                    "no_output_long",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    wait_policy="start_only",
                )
                time.sleep(0.15)
                status = server.opencode_coder_status(result["job_id"])
                server.opencode_coder_cancel(result["job_id"])
        finally:
            server.STALL_NO_OUTPUT_SECONDS = original_no_output

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertTrue(status["is_stalled"])
        self.assertEqual(status["stall_reason"], "no_output_no_change_after_start")
        self.assertEqual(status["suggested_action"], "consider_cancel")
        self.assertEqual(status["new_changed_files"], [])
        self.assertFalse(status["incomplete_changes_risk"])
        self.assertFalse(status["potential_incomplete_changes_risk"])

    def test_running_job_with_changed_files_reports_stalled_after_idle_threshold(self):
        original_changed_files = server.STALL_CHANGED_FILES_NO_ACTIVITY_SECONDS
        original_no_activity = server.STALL_NO_ACTIVITY_SECONDS
        original_no_output = server.STALL_NO_OUTPUT_SECONDS
        server.STALL_CHANGED_FILES_NO_ACTIVITY_SECONDS = 0.1
        server.STALL_NO_ACTIVITY_SECONDS = 100.0
        server.STALL_NO_OUTPUT_SECONDS = 100.0
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                init_git_repo(working_dir)
                result = server.opencode_coder(
                    "write_silent_then_sleep:src/pending.txt",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    wait_policy="first_change",
                    allowed_paths=["src"],
                )
                time.sleep(0.15)
                status = server.opencode_coder_status(result["job_id"])
                server.opencode_coder_cancel(result["job_id"])
        finally:
            server.STALL_CHANGED_FILES_NO_ACTIVITY_SECONDS = original_changed_files
            server.STALL_NO_ACTIVITY_SECONDS = original_no_activity
            server.STALL_NO_OUTPUT_SECONDS = original_no_output

        self.assertEqual(status["status"], "running")
        self.assertEqual(status["new_changed_files"], ["src/pending.txt"])
        self.assertTrue(status["is_stalled"])
        self.assertEqual(status["stall_reason"], "changed_files_no_recent_activity")
        self.assertEqual(status["suggested_action"], "review_diff_then_consider_cancel")
        self.assertTrue(status["review_required"])
        self.assertFalse(status["incomplete_changes_risk"])
        self.assertTrue(status["potential_incomplete_changes_risk"])

    def test_delayed_first_status_after_start_only_change_reports_stalled(self):
        original_changed_files = server.STALL_CHANGED_FILES_NO_ACTIVITY_SECONDS
        original_no_activity = server.STALL_NO_ACTIVITY_SECONDS
        original_no_output = server.STALL_NO_OUTPUT_SECONDS
        server.STALL_CHANGED_FILES_NO_ACTIVITY_SECONDS = 0.1
        server.STALL_NO_ACTIVITY_SECONDS = 100.0
        server.STALL_NO_OUTPUT_SECONDS = 100.0
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                init_git_repo(working_dir)
                result = server.opencode_coder(
                    "write_silent_then_sleep:src/delayed.txt",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    wait_policy="start_only",
                    allowed_paths=["src"],
                )
                time.sleep(0.2)
                status = server.opencode_coder_status(result["job_id"])
                server.opencode_coder_cancel(result["job_id"])
        finally:
            server.STALL_CHANGED_FILES_NO_ACTIVITY_SECONDS = original_changed_files
            server.STALL_NO_ACTIVITY_SECONDS = original_no_activity
            server.STALL_NO_OUTPUT_SECONDS = original_no_output

        self.assertEqual(status["status"], "running")
        self.assertEqual(status["new_changed_files"], ["src/delayed.txt"])
        self.assertGreaterEqual(status["idle_seconds"], 0.1)
        self.assertTrue(status["is_stalled"])
        self.assertEqual(status["stall_reason"], "changed_files_no_recent_activity")
        self.assertEqual(status["suggested_action"], "review_diff_then_consider_cancel")
        self.assertTrue(status["review_required"])
        self.assertFalse(status["incomplete_changes_risk"])
        self.assertTrue(status["potential_incomplete_changes_risk"])

    def test_running_job_with_output_reports_stalled_after_idle_threshold(self):
        original_no_activity = server.STALL_NO_ACTIVITY_SECONDS
        original_no_output = server.STALL_NO_OUTPUT_SECONDS
        server.STALL_NO_ACTIVITY_SECONDS = 0.1
        server.STALL_NO_OUTPUT_SECONDS = 100.0
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                result = server.opencode_coder(
                    "very_long",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    wait_policy="first_output",
                )
                time.sleep(0.15)
                status = server.opencode_coder_status(result["job_id"])
                server.opencode_coder_cancel(result["job_id"])
        finally:
            server.STALL_NO_ACTIVITY_SECONDS = original_no_activity
            server.STALL_NO_OUTPUT_SECONDS = original_no_output

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertTrue(status["is_stalled"])
        self.assertEqual(status["stall_reason"], "no_recent_activity")
        self.assertEqual(status["suggested_action"], "consider_cancel")
        self.assertFalse(status["potential_incomplete_changes_risk"])

    def test_stalled_job_reports_last_event_phase_in_diagnostic_note(self):
        original_no_activity = server.STALL_NO_ACTIVITY_SECONDS
        original_no_output = server.STALL_NO_OUTPUT_SECONDS
        server.STALL_NO_ACTIVITY_SECONDS = 0.1
        server.STALL_NO_OUTPUT_SECONDS = 100.0
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                result = server.opencode_coder(
                    "json_event_then_sleep",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    wait_policy="first_output",
                )
                time.sleep(0.15)
                status = server.opencode_coder_status(result["job_id"])
                server.opencode_coder_cancel(result["job_id"])
        finally:
            server.STALL_NO_ACTIVITY_SECONDS = original_no_activity
            server.STALL_NO_OUTPUT_SECONDS = original_no_output

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertTrue(status["is_stalled"])
        self.assertEqual(status["diagnostic_phase"], "process_running_no_recent_event")
        self.assertEqual(status["last_event_type"], "message.part.updated")
        self.assertEqual(status["last_text_output"], "Waiting after text")
        self.assertIn("stalled", status["diagnostic_note"])
        self.assertIn("model_text", status["diagnostic_note"])

    def test_status_wait_seconds_recomputes_stall_after_wait(self):
        original_no_output = server.STALL_NO_OUTPUT_SECONDS
        server.STALL_NO_OUTPUT_SECONDS = 0.1
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                result = server.opencode_coder(
                    "no_output_long",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    wait_policy="start_only",
                )
                status = server.opencode_coder_status(result["job_id"], wait_seconds=0.25)
                server.opencode_coder_cancel(result["job_id"])
        finally:
            server.STALL_NO_OUTPUT_SECONDS = original_no_output

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertTrue(status["is_stalled"])
        self.assertEqual(status["stall_reason"], "no_output_no_change_after_start")
        self.assertEqual(status["suggested_action"], "consider_cancel")

    def test_timed_out_running_job_has_clear_suggested_action(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder("no_output_long", working_dir=working_dir, timeout_seconds=0)
            try:
                status = server.opencode_coder_status(result["job_id"])
            finally:
                server.opencode_coder_cancel(result["job_id"])

        self.assertEqual(status["status"], "timed_out")
        self.assertTrue(status["process_running"])
        self.assertEqual(status["suggested_action"], "continue_polling_or_consider_cancel")
        self.assertFalse(status["is_stalled"])
        self.assertFalse(status["potential_incomplete_changes_risk"])

    def test_same_cwd_running_job_rejects_second_default_call(self):
        with tempfile.TemporaryDirectory() as working_dir:
            first = server.opencode_coder("long", working_dir=working_dir, timeout_seconds=0)
            second = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=0)

            self.assertEqual(second["job_id"], first["job_id"])
            self.assertTrue(second["lock_rejected"])
            self.assertFalse(second["new_job_started"])
            self.assertIn(second["status"], {"running", "timed_out"})

            final_status = wait_for_terminal_job(first["job_id"])

        self.assertEqual(final_status["status"], "completed")

    def test_failed_task_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "fail",
                working_dir=working_dir,
                timeout_seconds=2,
                include_tail=True,
            )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["return_code"], 7)
        self.assertIn("boom", result["stderr_tail"])
        self.assertFalse(result["is_stalled"])
        self.assertFalse(result["review_required"])
        self.assertFalse(result["incomplete_changes_risk"])
        self.assertFalse(result["potential_incomplete_changes_risk"])
        self.assertIn("not completed", result["validation_note"])

    def test_failed_job_with_changes_reports_incomplete_risk(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder("fail_after_write:src/partial.txt", working_dir=working_dir, timeout_seconds=2)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["new_changed_files"], ["src/partial.txt"])
        self.assertTrue(result["review_required"])
        self.assertTrue(result["incomplete_changes_risk"])
        self.assertFalse(result["potential_incomplete_changes_risk"])
        self.assertEqual(result["suggested_action"], "review_diff_or_git_status")

    def test_timed_out_job_with_changes_reports_review_required(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder("write_then_sleep:src/pending.txt", working_dir=working_dir, timeout_seconds=0)
            try:
                status = wait_for_job_change(result["job_id"])
            finally:
                server.opencode_coder_cancel(result["job_id"])

        self.assertEqual(status["status"], "timed_out")
        self.assertEqual(status["new_changed_files"], ["src/pending.txt"])
        self.assertTrue(status["review_required"])
        self.assertTrue(status["incomplete_changes_risk"])
        self.assertFalse(status["potential_incomplete_changes_risk"])

    def test_cancelled_job_with_changes_reports_incomplete_risk(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder(
                "write_then_sleep:src/cancelled.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_change",
            )
            self.assertIn(result["status"], {"running", "timed_out"})
            self.assertTrue(result["review_required"])
            cancelled = server.opencode_coder_cancel(result["job_id"])

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["new_changed_files"], ["src/cancelled.txt"])
        self.assertTrue(cancelled["review_required"])
        self.assertTrue(cancelled["incomplete_changes_risk"])
        self.assertFalse(cancelled["potential_incomplete_changes_risk"])
        self.assertEqual(cancelled["suggested_action"], "review_diff_or_git_status")

    def test_new_file_enters_new_and_all_changed_files(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder("write:src/new.txt", working_dir=working_dir, timeout_seconds=2)

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["git_status_available"])
        self.assertEqual(result["preexisting_changed_files"], [])
        self.assertEqual(result["new_changed_files"], ["src/new.txt"])
        self.assertEqual(result["all_changed_files"], ["src/new.txt"])
        self.assertEqual(result["changed_files"], result["all_changed_files"])
        self.assertFalse(result["review_required"])
        self.assertFalse(result["incomplete_changes_risk"])
        self.assertFalse(result["potential_incomplete_changes_risk"])

    def test_preexisting_dirty_file_is_not_counted_as_new(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            Path(working_dir, "old.txt").write_text("existing dirty\n", encoding="utf-8")
            result = server.opencode_coder(
                "write:src/new.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                allowed_paths=["src"],
            )

        self.assertEqual(result["preexisting_changed_files"], ["old.txt"])
        self.assertEqual(result["new_changed_files"], ["src/new.txt"])
        self.assertEqual(result["all_changed_files"], ["old.txt", "src/new.txt"])
        self.assertFalse(result["policy_violation"])
        self.assertIn("preexisting dirty", result["preexisting_dirty_warning"])

    def test_preexisting_dirty_file_modified_again_triggers_forbidden_policy(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            old_file = Path(working_dir, "old.txt")
            old_file.write_text("baseline\n", encoding="utf-8")
            commit_all(working_dir)
            old_file.write_text("preexisting dirty\n", encoding="utf-8")
            result = server.opencode_coder(
                "write:old.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                forbidden_paths=["old.txt"],
            )

        self.assertEqual(result["preexisting_changed_files"], ["old.txt"])
        self.assertEqual(result["all_changed_files"], ["old.txt"])
        self.assertEqual(result["new_changed_files"], ["old.txt"])
        self.assertTrue(result["policy_violation"])
        self.assertEqual(result["forbidden_changed_files"], ["old.txt"])
        self.assertEqual(result["extra_changed_files"], [])

    def test_coder_diff_missing_job_returns_not_found(self):
        result = server.opencode_coder_diff("missing-job")

        self.assertEqual(result["status"], "not_found")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "job_not_found")
        self.assertEqual(result["diff"], "")
        self.assertEqual(result["undiffed_files"], [])

    def test_coder_diff_returns_tracked_file_diff(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            tracked_file = Path(working_dir, "tracked.txt")
            tracked_file.write_text("baseline\n", encoding="utf-8")
            commit_all(working_dir)
            job = server.opencode_coder("write:tracked.txt", working_dir=working_dir, timeout_seconds=2)
            diff = server.opencode_coder_diff(job["job_id"])

        self.assertTrue(diff["success"])
        self.assertEqual(diff["new_changed_files"], ["tracked.txt"])
        self.assertFalse(diff["includes_preexisting_dirty_changes"])
        self.assertEqual(diff["undiffed_files"], [])
        self.assertIn("diff --git", diff["diff"])
        self.assertIn("-baseline", diff["diff"])
        self.assertIn("+generated", diff["diff"])

    def test_coder_diff_returns_untracked_file_diff(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            job = server.opencode_coder("write:src/new.txt", working_dir=working_dir, timeout_seconds=2)
            diff = server.opencode_coder_diff(job["job_id"])

        self.assertTrue(diff["success"])
        self.assertEqual(diff["new_changed_files"], ["src/new.txt"])
        self.assertEqual(diff["undiffed_files"], [])
        self.assertIn("new file mode", diff["diff"])
        self.assertIn("+++ b/src/new.txt", diff["diff"])
        self.assertIn("+generated", diff["diff"])
        self.assertEqual(diff["diff_source_files"], ["src/new.txt"])
        self.assertIsNone(diff["diff_empty_reason"])
        self.assertFalse(diff["review_required"])
        self.assertFalse(diff["incomplete_changes_risk"])
        self.assertEqual(diff["preexisting_dirty_warning"], "")

    def test_coder_diff_failed_job_with_changes_reports_review_risk(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            job = server.opencode_coder("fail_after_write:src/partial.txt", working_dir=working_dir, timeout_seconds=2)
            diff = server.opencode_coder_diff(job["job_id"])

        self.assertEqual(diff["status"], "failed")
        self.assertEqual(diff["new_changed_files"], ["src/partial.txt"])
        self.assertTrue(diff["success"])
        self.assertTrue(diff["review_required"])
        self.assertTrue(diff["incomplete_changes_risk"])
        self.assertEqual(diff["preexisting_dirty_warning"], "")
        self.assertIn("+partial", diff["diff"])

    def test_coder_diff_timed_out_job_with_changes_reports_review_risk(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            job = server.opencode_coder("write_then_sleep:src/pending.txt", working_dir=working_dir, timeout_seconds=0)
            try:
                status = wait_for_job_change(job["job_id"])
                diff = server.opencode_coder_diff(job["job_id"])
            finally:
                server.opencode_coder_cancel(job["job_id"])

        self.assertEqual(status["status"], "timed_out")
        self.assertEqual(diff["status"], "timed_out")
        self.assertEqual(diff["new_changed_files"], ["src/pending.txt"])
        self.assertTrue(diff["review_required"])
        self.assertTrue(diff["incomplete_changes_risk"])
        self.assertIn("+generated before cancel", diff["diff"])

    def test_coder_diff_returns_deleted_tracked_file_diff(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            tracked_file = Path(working_dir, "tracked.txt")
            tracked_file.write_text("baseline\n", encoding="utf-8")
            commit_all(working_dir)
            job = server.opencode_coder("delete:tracked.txt", working_dir=working_dir, timeout_seconds=2)
            diff = server.opencode_coder_diff(job["job_id"])

        self.assertTrue(diff["success"])
        self.assertEqual(diff["new_changed_files"], ["tracked.txt"])
        self.assertEqual(diff["undiffed_files"], [])
        self.assertIn("deleted file mode", diff["diff"])
        self.assertIn("-baseline", diff["diff"])
        self.assertEqual(diff["diff_source_files"], ["tracked.txt"])
        self.assertIsNone(diff["diff_empty_reason"])

    def test_coder_diff_non_git_working_dir_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as working_dir:
            job = server.opencode_coder("write:src/new.txt", working_dir=working_dir, timeout_seconds=2)
            diff = server.opencode_coder_diff(job["job_id"])

        self.assertFalse(diff["success"])
        self.assertFalse(diff["git_status_available"])
        self.assertEqual(diff["diff"], "")
        self.assertEqual(diff["undiffed_files"], [])
        self.assertIsNotNone(diff["error"])

    def test_coder_diff_truncates_large_response(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            job = server.opencode_coder("write:src/new.txt", working_dir=working_dir, timeout_seconds=2)
            diff = server.opencode_coder_diff(job["job_id"], max_chars=40)

        self.assertTrue(diff["success"])
        self.assertTrue(diff["diff_truncated"])
        self.assertEqual(len(diff["diff"]), 40)
        self.assertEqual(diff["max_chars"], 40)

    def test_coder_diff_marks_preexisting_dirty_changes(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            old_file = Path(working_dir, "old.txt")
            old_file.write_text("baseline\n", encoding="utf-8")
            commit_all(working_dir)
            old_file.write_text("preexisting dirty\n", encoding="utf-8")
            job = server.opencode_coder("write:old.txt", working_dir=working_dir, timeout_seconds=2)
            diff = server.opencode_coder_diff(job["job_id"])

        self.assertTrue(diff["success"])
        self.assertEqual(diff["new_changed_files"], ["old.txt"])
        self.assertEqual(diff["preexisting_changed_files"], ["old.txt"])
        self.assertTrue(diff["includes_preexisting_dirty_changes"])
        self.assertIn("+generated", diff["diff"])
        self.assertIn("preexisting dirty", diff["preexisting_dirty_warning"])

    def test_coder_diff_reports_empty_reason_when_job_reverts_dirty_file(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            tracked_file = Path(working_dir, "tracked.txt")
            tracked_file.write_text("baseline\n", encoding="utf-8")
            commit_all(working_dir)
            tracked_file.write_text("dirty\n", encoding="utf-8")
            job = server.opencode_coder("restore_baseline:tracked.txt", working_dir=working_dir, timeout_seconds=2)
            diff = server.opencode_coder_diff(job["job_id"])

        self.assertEqual(job["new_changed_files"], ["tracked.txt"])
        self.assertEqual(job["all_changed_files"], [])
        self.assertFalse(diff["success"])
        self.assertEqual(diff["diff"], "")
        self.assertEqual(diff["diff_empty_reason"], "current_worktree_has_no_diff_for_job_files")
        self.assertEqual(diff["diff_source_files"], [])
        self.assertEqual(diff["diff_command_errors"], [])
        self.assertTrue(diff["includes_preexisting_dirty_changes"])
        self.assertEqual(diff["error"], "current_worktree_has_no_diff_for_job_files")

    def test_coder_diff_reports_undiffed_untracked_binary_file(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            job = server.opencode_coder("write_binary:src/blob.bin", working_dir=working_dir, timeout_seconds=2)
            diff = server.opencode_coder_diff(job["job_id"])

        self.assertFalse(diff["success"])
        self.assertEqual(diff["diff"], "")
        self.assertEqual(diff["diff_empty_reason"], "diff_generation_errors")
        self.assertEqual(diff["undiffed_files"], ["src/blob.bin"])
        self.assertIn("binary_file", "; ".join(diff["diff_command_errors"]))

    def test_allowed_paths_allows_in_scope_changes(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder(
                "write:src/ok.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                allowed_paths=["src"],
            )

        self.assertFalse(result["policy_violation"])
        self.assertEqual(result["extra_changed_files"], [])
        self.assertEqual(result["forbidden_changed_files"], [])

    def test_allowed_paths_reports_extra_changed_files(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder(
                "write:docs/outside.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                allowed_paths=["src"],
            )

        self.assertTrue(result["policy_violation"])
        self.assertEqual(result["extra_changed_files"], ["docs/outside.txt"])
        self.assertEqual(result["forbidden_changed_files"], [])
        self.assertTrue(result["review_required"])
        self.assertFalse(result["incomplete_changes_risk"])

    def test_forbidden_paths_reports_forbidden_changed_files(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder(
                "write:src/secret/out.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                allowed_paths=["src"],
                forbidden_paths=["src/secret"],
            )

        self.assertTrue(result["policy_violation"])
        self.assertEqual(result["forbidden_changed_files"], ["src/secret/out.txt"])
        self.assertEqual(result["extra_changed_files"], [])

    def test_allowed_paths_allows_assets_when_working_dir_is_git_root(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder(
                "write:Assets/_SRPG/Scripts/UI/RogueSystemMapPanel.cs",
                working_dir=working_dir,
                timeout_seconds=2,
                allowed_paths=["Assets/_SRPG/Scripts/UI"],
            )

        self.assertFalse(result["policy_violation"])
        self.assertEqual(result["extra_changed_files"], [])
        self.assertEqual(result["new_changed_files"], ["Assets/_SRPG/Scripts/UI/RogueSystemMapPanel.cs"])
        self.assertEqual(result["path_policy"]["git_root"], str(Path(working_dir).resolve()))

    def test_allowed_paths_allows_subdir_prefix_from_repo_root(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder(
                "write:CFantacy-TurnBasedStrategy/Assets/_SRPG/Scripts/UI/RogueSystemMapPanel.cs",
                working_dir=working_dir,
                timeout_seconds=2,
                allowed_paths=["CFantacy-TurnBasedStrategy/Assets/_SRPG/Scripts/UI"],
            )

        self.assertFalse(result["policy_violation"])
        self.assertEqual(result["extra_changed_files"], [])
        self.assertEqual(
            result["new_changed_files"],
            ["CFantacy-TurnBasedStrategy/Assets/_SRPG/Scripts/UI/RogueSystemMapPanel.cs"],
        )

    def test_allowed_paths_allows_unity_assets_suffix_from_repo_root(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder(
                "write:CFantacy-TurnBasedStrategy/Assets/_SRPG/Scripts/UI/RogueSystemMapPanel.cs",
                working_dir=working_dir,
                timeout_seconds=2,
                allowed_paths=["Assets/_SRPG/Scripts/UI"],
            )

        self.assertFalse(result["policy_violation"])
        self.assertEqual(result["extra_changed_files"], [])
        file_match = result["path_policy"]["file_matches"][0]
        self.assertEqual(file_match["verdict"], "allowed")
        self.assertEqual(file_match["allowed_by"]["basis"], "git_relative_suffix")

    def test_allowed_paths_allows_project_relative_path_when_working_dir_is_subdir(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            init_git_repo(repo_dir)
            project_dir = Path(repo_dir, "CFantacy-TurnBasedStrategy")
            project_dir.mkdir()
            result = server.opencode_coder(
                "write:Assets/_SRPG/Scripts/UI/RogueSystemMapPanel.cs",
                working_dir=str(project_dir),
                timeout_seconds=2,
                allowed_paths=["Assets/_SRPG/Scripts/UI"],
            )

        self.assertFalse(result["policy_violation"])
        self.assertEqual(result["extra_changed_files"], [])
        self.assertEqual(
            result["new_changed_files"],
            ["CFantacy-TurnBasedStrategy/Assets/_SRPG/Scripts/UI/RogueSystemMapPanel.cs"],
        )
        file_match = result["path_policy"]["file_matches"][0]
        self.assertEqual(file_match["allowed_by"]["basis"], "working_dir")

    def test_forbidden_paths_keep_priority_with_windows_style_paths(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder(
                "write:Assets/_SRPG/Scripts/UI/Secret/out.cs",
                working_dir=working_dir,
                timeout_seconds=2,
                allowed_paths=["Assets\\_SRPG\\Scripts\\UI"],
                forbidden_paths=["Assets\\_SRPG\\Scripts\\UI\\Secret"],
            )

        self.assertTrue(result["policy_violation"])
        self.assertEqual(result["forbidden_changed_files"], ["Assets/_SRPG/Scripts/UI/Secret/out.cs"])
        self.assertEqual(result["extra_changed_files"], [])
        file_match = result["path_policy"]["file_matches"][0]
        self.assertEqual(file_match["verdict"], "forbidden")
        self.assertIsNotNone(file_match["allowed_by"])
        self.assertIsNotNone(file_match["forbidden_by"])

    def test_non_git_working_dir_returns_structured_snapshot_fields(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "write:src/new.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                allowed_paths=["src"],
            )

        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["git_status_available"])
        self.assertIsNotNone(result["git_status_error"])
        self.assertEqual(result["preexisting_changed_files"], [])
        self.assertEqual(result["all_changed_files"], [])
        self.assertEqual(result["new_changed_files"], [])
        self.assertFalse(result["policy_violation"])

    def test_status_returns_final_policy_result_for_timed_out_job(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            initial = server.opencode_coder(
                "long_write:docs/outside.txt",
                working_dir=working_dir,
                timeout_seconds=0,
                allowed_paths=["src"],
            )
            self.assertEqual(initial["status"], "timed_out")
            final_status = wait_for_terminal_job(initial["job_id"])

        self.assertEqual(final_status["status"], "completed")
        self.assertTrue(final_status["policy_violation"])
        self.assertEqual(final_status["extra_changed_files"], ["docs/outside.txt"])

    def test_cancel_running_job_returns_cancelled_and_not_running(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "very_long",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            result = server.opencode_coder_cancel(initial["job_id"])
            status = server.opencode_coder_status(initial["job_id"])

        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["success"])
        self.assertTrue(result["cancel_requested"])
        self.assertTrue(result["cancel_signal_sent"] or result["cancel_kill_sent"])
        self.assertIn("process_tree_kill_attempted", result)
        self.assertIn("process_tree_kill_succeeded", result)
        self.assertIn("process_tree_kill_error", result)
        if not result["process_tree_kill_attempted"]:
            self.assertFalse(result["process_tree_kill_succeeded"])
            self.assertIsNone(result["process_tree_kill_error"])
        self.assertFalse(result["process_running"])
        self.assertFalse(result["potential_incomplete_changes_risk"])
        self.assertEqual(status["status"], "cancelled")
        self.assertFalse(status["process_running"])
        self.assertFalse(status["potential_incomplete_changes_risk"])

    def test_cancel_releases_cwd_lock_for_next_job(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "very_long",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            cancelled = server.opencode_coder_cancel(initial["job_id"])
            next_result = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertFalse(cancelled["process_running"])
        self.assertEqual(next_result["status"], "completed")
        self.assertFalse(next_result["lock_rejected"])

    def test_cancel_completed_job_is_idempotent_no_signal(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)
            result = server.opencode_coder_cancel(completed["job_id"])

        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["cancel_requested"])
        self.assertFalse(result["cancel_signal_sent"])
        self.assertFalse(result["cancel_kill_sent"])
        self.assertFalse(result["process_tree_kill_attempted"])
        self.assertFalse(result["process_tree_kill_succeeded"])
        self.assertIsNone(result["process_tree_kill_error"])
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["potential_incomplete_changes_risk"])

    def test_cancel_missing_job_returns_structured_not_found(self):
        result = server.opencode_coder_cancel("missing-job")

        self.assertEqual(result["status"], "not_found")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "job_not_found")
        self.assertFalse(result["cancel_requested"])
        self.assertFalse(result["cancel_signal_sent"])
        self.assertFalse(result["cancel_kill_sent"])
        self.assertFalse(result["process_tree_kill_attempted"])
        self.assertFalse(result["process_tree_kill_succeeded"])
        self.assertIsNone(result["process_tree_kill_error"])

    def test_cancel_is_safe_when_called_multiple_times(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "very_long",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            first = server.opencode_coder_cancel(initial["job_id"])
            second = server.opencode_coder_cancel(initial["job_id"])

        self.assertEqual(first["status"], "cancelled")
        self.assertEqual(second["status"], "cancelled")
        self.assertFalse(second["process_running"])
        self.assertTrue(second["cancel_requested"])
        self.assertIn("process_tree_kill_attempted", second)
        self.assertIn("process_tree_kill_succeeded", second)
        self.assertIn("process_tree_kill_error", second)

    def test_cancelled_job_preserves_snapshot_and_path_policy(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            initial = server.opencode_coder(
                "write_then_sleep:src/cancelled.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_change",
                allowed_paths=["src"],
            )
            result = server.opencode_coder_cancel(initial["job_id"])

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["new_changed_files"], ["src/cancelled.txt"])
        self.assertEqual(result["all_changed_files"], ["src/cancelled.txt"])
        self.assertFalse(result["policy_violation"])
        self.assertEqual(result["extra_changed_files"], [])
        self.assertEqual(result["forbidden_changed_files"], [])
        self.assertFalse(result["potential_incomplete_changes_risk"])

    def test_wait_not_found_returns_immediately(self):
        started_at = time.monotonic()
        result = server.opencode_coder_wait("missing-job", wait_seconds=300)
        elapsed = time.monotonic() - started_at

        self.assertLess(elapsed, 0.2)
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["wait_return_reason"], "not_found")
        self.assertTrue(result["interesting_update"])
        self.assertEqual(result["waited_seconds"], 0.0)
        self.assertFalse(result["needs_status_refresh"])
        self.assertEqual(result["suggested_next_tool"], "none")
        self.assertEqual(result["status_refresh_reason"], "compact_snapshot_sufficient")

    def test_wait_running_no_change_times_out_compact(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "no_output_long",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            started_at = time.monotonic()
            result = server.opencode_coder_wait(
                initial["job_id"],
                wait_seconds=0.15,
                return_on="interesting",
                include_status=True,
            )
            elapsed = time.monotonic() - started_at
            server.opencode_coder_cancel(initial["job_id"])

        self.assertLess(elapsed, 1.0)
        self.assertEqual(result["wait_return_reason"], "wait_timeout")
        self.assertFalse(result["interesting_update"])
        self.assertGreater(result["waited_seconds"], 0.0)
        self.assertIn(result["status"], {"running", "timed_out"})
        self.assertIn("working_dir", result)
        self.assertIn("suggested_action", result)
        self.assertFalse(result["needs_status_refresh"])
        self.assertEqual(result["suggested_next_tool"], "opencode_coder_wait")
        self.assertEqual(result["stdout_tail"], "")
        self.assertEqual(result["stderr_tail"], "")
        self.assertEqual(result["stdout_delta"], "")
        self.assertEqual(result["stderr_delta"], "")
        self.assertEqual(result["output"], "")

    def test_wait_caps_explicit_long_wait_by_mcp_margin(self):
        original_max_wait = os.environ.pop("OPENCODE_CODER_MAX_WAIT_SECONDS", None)
        original_client_timeout = os.environ.get("OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS")
        original_margin = os.environ.get("OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS")
        os.environ["OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS"] = "0.3"
        os.environ["OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS"] = "0.1"
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "no_output_long",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            try:
                started_at = time.monotonic()
                result = server.opencode_coder_wait(
                    initial["job_id"],
                    wait_seconds=600,
                    return_on="interesting",
                    include_status=False,
                )
                elapsed = time.monotonic() - started_at
            finally:
                server.opencode_coder_cancel(initial["job_id"])
                if original_max_wait is not None:
                    os.environ["OPENCODE_CODER_MAX_WAIT_SECONDS"] = original_max_wait
                if original_client_timeout is None:
                    os.environ.pop("OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS", None)
                else:
                    os.environ["OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS"] = original_client_timeout
                if original_margin is None:
                    os.environ.pop("OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS", None)
                else:
                    os.environ["OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS"] = original_margin

        self.assertLess(elapsed, 1.0)
        self.assertEqual(result["wait_return_reason"], "wait_timeout")
        self.assertEqual(result["requested_wait_seconds"], 600)
        self.assertAlmostEqual(result["effective_wait_seconds"], 0.2)
        self.assertIn("capped_by_wrapper", result["wait_timeout_policy"])
        self.assertGreaterEqual(result["waited_seconds"], 0.15)

    def test_wait_terminal_returns_on_completion(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "delayed_output",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            started_at = time.monotonic()
            result = server.opencode_coder_wait(
                initial["job_id"],
                wait_seconds=5,
                return_on="terminal",
            )
            elapsed = time.monotonic() - started_at

        self.assertLess(elapsed, 4.0)
        self.assertEqual(result["wait_return_reason"], "terminal_status")
        self.assertTrue(result["interesting_update"])
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["needs_status_refresh"])
        self.assertEqual(result["suggested_next_tool"], "none")
        self.assertEqual(result["status_refresh_reason"], "compact_snapshot_sufficient")

    def test_wait_interesting_returns_on_completion(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "delayed_output",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            started_at = time.monotonic()
            result = server.opencode_coder_wait(
                initial["job_id"],
                wait_seconds=5,
                return_on="interesting",
            )
            elapsed = time.monotonic() - started_at

        self.assertLess(elapsed, 4.0)
        self.assertEqual(result["wait_return_reason"], "terminal_status")
        self.assertTrue(result["interesting_update"])
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["needs_status_refresh"])
        self.assertEqual(result["suggested_next_tool"], "none")

    def test_wait_first_change_returns_immediately(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            initial = server.opencode_coder(
                "double_write_same_file:src/changed.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
                allowed_paths=["src"],
            )
            started_at = time.monotonic()
            result = server.opencode_coder_wait(
                initial["job_id"],
                wait_seconds=5,
                return_on="interesting",
            )
            elapsed = time.monotonic() - started_at
            server.opencode_coder_cancel(initial["job_id"])

        self.assertLess(elapsed, 3.0)
        self.assertEqual(result["wait_return_reason"], "first_change_seen")
        self.assertTrue(result["interesting_update"])
        self.assertEqual(result["new_changed_files"], ["src/changed.txt"])
        self.assertFalse(result["needs_status_refresh"])
        expected_next = "opencode_coder_diff" if result["status"] == "completed" else "opencode_coder_wait"
        self.assertEqual(result["suggested_next_tool"], expected_next)

    def test_wait_terminal_without_completion_times_out(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "very_long",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            started_at = time.monotonic()
            result = server.opencode_coder_wait(
                initial["job_id"],
                wait_seconds=0.15,
                return_on="terminal",
            )
            elapsed = time.monotonic() - started_at
            server.opencode_coder_cancel(initial["job_id"])

        self.assertLess(elapsed, 1.0)
        self.assertEqual(result["wait_return_reason"], "wait_timeout")
        self.assertFalse(result["interesting_update"])
        self.assertGreater(result["waited_seconds"], 0.0)
        self.assertFalse(result["needs_status_refresh"])
        self.assertEqual(result["suggested_next_tool"], "opencode_coder_wait")

    def test_wait_include_status_false_returns_compact_snapshot(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "no_output_long",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            result = server.opencode_coder_wait(
                initial["job_id"],
                wait_seconds=0.05,
                return_on="interesting",
                include_status=False,
            )
            server.opencode_coder_cancel(initial["job_id"])

        self.assertIn(result["status"], {"running", "timed_out"})
        required_fields = [
            "status",
            "success",
            "error",
            "job_id",
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
            "wait_return_reason",
            "interesting_update",
            "waited_seconds",
            "requested_wait_seconds",
            "effective_wait_seconds",
            "wait_timeout_policy",
            "needs_status_refresh",
            "suggested_next_tool",
            "status_refresh_reason",
        ]
        for field in required_fields:
            self.assertIn(field, result)
        self.assertNotIn("stdout_tail", result)
        self.assertNotIn("stderr_tail", result)
        self.assertNotIn("output", result)

    def test_wait_clamp_helper(self):
        self.assertEqual(server.clamp_wait_wait_seconds(-1), 0.0)
        self.assertEqual(server.clamp_wait_wait_seconds(0), 0.0)
        self.assertEqual(server.clamp_wait_wait_seconds(700), server.MAX_WAIT_WAIT_SECONDS)
        self.assertEqual(server.clamp_wait_wait_seconds("not-a-number"), server.DEFAULT_WAIT_WAIT_SECONDS)
        self.assertEqual(server.clamp_wait_wait_seconds(None), server.DEFAULT_WAIT_WAIT_SECONDS)
        self.assertEqual(server.DEFAULT_WAIT_WAIT_SECONDS, 215.0)

    def test_next_poll_after_seconds_no_short_poll_for_running_phases(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder(
                "delayed_output",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            status = server.opencode_coder_status(result["job_id"])
            server.opencode_coder_cancel(result["job_id"])

        self.assertIn(status["status"], {"running", "timed_out"})
        self.assertIn(status["progress_phase"], {"starting", "waiting_first_output", "reading_context",
                                                   "planning_or_reasoning", "editing", "validating",
                                                   "finalizing", "long_context_or_planning"})
        self.assertGreaterEqual(status["next_poll_after_seconds"], 120)

    def test_next_poll_after_seconds_zero_for_terminal_and_not_found(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)
            failed = server.opencode_coder("fail", working_dir=working_dir, timeout_seconds=2)
            not_found = server.opencode_coder_status("missing-job")

        self.assertEqual(completed["next_poll_after_seconds"], 0)
        self.assertEqual(failed["next_poll_after_seconds"], 0)
        self.assertEqual(not_found["next_poll_after_seconds"], 0)

    def test_next_poll_after_seconds_zero_for_timed_out(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder("no_output_long", working_dir=working_dir, timeout_seconds=0)
            try:
                self.assertEqual(result["next_poll_after_seconds"], 0)
            finally:
                server.opencode_coder_cancel(result["job_id"])

    def test_next_poll_after_seconds_zero_for_stalled(self):
        original_no_output = server.STALL_NO_OUTPUT_SECONDS
        server.STALL_NO_OUTPUT_SECONDS = 0.1
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                result = server.opencode_coder(
                    "no_output_long",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    wait_policy="start_only",
                )
                time.sleep(0.2)
                status = server.opencode_coder_status(result["job_id"])
                server.opencode_coder_cancel(result["job_id"])
        finally:
            server.STALL_NO_OUTPUT_SECONDS = original_no_output

        self.assertTrue(status["is_stalled"])
        self.assertEqual(status["next_poll_after_seconds"], 0)

    def test_next_poll_after_seconds_zero_for_policy_violation_during_non_terminal_phase(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder(
                "write_then_sleep:forbidden.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_change",
                forbidden_paths=["forbidden.txt"],
            )
            try:
                self.assertTrue(result["policy_violation"])
                self.assertEqual(result["caller_update_reason"], "policy_violation")
                self.assertIn(result["progress_phase"], {"editing", "validating"})
                self.assertEqual(result["next_poll_after_seconds"], 0)
            finally:
                server.opencode_coder_cancel(result["job_id"])

    def test_next_poll_after_seconds_120_for_first_change_seen(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            result = server.opencode_coder(
                "delayed_write:src/changed.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_change",
                allowed_paths=["src"],
            )
            final_status = wait_for_terminal_job(result["job_id"])

        self.assertEqual(result["caller_update_reason"], "first_change_seen")
        self.assertEqual(result["next_poll_after_seconds"], 120)
        self.assertEqual(final_status["status"], "completed")

    def test_wait_does_not_break_existing_status(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)
            status = server.opencode_coder_status(completed["job_id"])

        self.assertEqual(status["status"], "completed")
        self.assertTrue(status["success"])
        self.assertEqual(status["exit_code"], 0)

    def test_wait_not_found_with_include_status_false_returns_compact_snapshot(self):
        result = server.opencode_coder_wait("missing-job", wait_seconds=5, include_status=False)

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["wait_return_reason"], "not_found")
        self.assertTrue(result["interesting_update"])
        self.assertEqual(result["waited_seconds"], 0.0)
        self.assertIn("working_dir", result)
        self.assertIsNone(result["working_dir"])
        self.assertFalse(result["needs_status_refresh"])
        self.assertEqual(result["suggested_next_tool"], "none")

    def test_wait_does_not_return_stale_first_change_seen(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            initial = server.opencode_coder(
                "write_then_sleep:src/file.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_change",
                allowed_paths=["src"],
            )
            self.assertEqual(initial["caller_update_reason"], "first_change_seen")
            self.assertIn(initial["status"], {"running", "timed_out"})
            started_at = time.monotonic()
            result = server.opencode_coder_wait(
                initial["job_id"],
                wait_seconds=0.5,
                return_on="interesting",
                include_status=False,
            )
            elapsed = time.monotonic() - started_at
            server.opencode_coder_cancel(initial["job_id"])

        self.assertGreaterEqual(elapsed, 0.35)
        self.assertEqual(result["wait_return_reason"], "wait_timeout")
        self.assertFalse(result["interesting_update"])

    def test_wait_returns_for_new_change_during_wait(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            initial = server.opencode_coder(
                "double_write_same_file:src/a.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_change",
                allowed_paths=["src"],
            )
            self.assertEqual(initial["caller_update_reason"], "first_change_seen")
            self.assertIn(initial["status"], {"running", "timed_out"})
            started_at = time.monotonic()
            result = server.opencode_coder_wait(
                initial["job_id"],
                wait_seconds=5,
                return_on="interesting",
                include_status=False,
            )
            elapsed = time.monotonic() - started_at
            server.opencode_coder_cancel(initial["job_id"])

        self.assertLess(elapsed, 4.0)
        self.assertEqual(result["wait_return_reason"], "first_change_seen")
        self.assertTrue(result["interesting_update"])

    def test_wait_interesting_returns_for_terminal(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "delayed_output",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            started_at = time.monotonic()
            result = server.opencode_coder_wait(
                initial["job_id"],
                wait_seconds=2,
                return_on="interesting",
                include_status=False,
            )
            elapsed = time.monotonic() - started_at

        self.assertLess(elapsed, 1.5)
        self.assertEqual(result["wait_return_reason"], "terminal_status")
        self.assertTrue(result["interesting_update"])
        self.assertFalse(result["needs_status_refresh"])

    def test_wait_policy_violation_guidance_prefers_diff_not_status(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)
            initial = server.opencode_coder(
                "write_then_sleep:forbidden.txt",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
                forbidden_paths=["forbidden.txt"],
            )
            result = server.opencode_coder_wait(
                initial["job_id"],
                wait_seconds=2,
                return_on="interesting",
                include_status=False,
            )
            server.opencode_coder_cancel(initial["job_id"])

        self.assertEqual(result["wait_return_reason"], "policy_violation")
        self.assertTrue(result["policy_violation"])
        self.assertEqual(result["forbidden_changed_files"], ["forbidden.txt"])
        self.assertFalse(result["needs_status_refresh"])
        self.assertEqual(result["suggested_next_tool"], "opencode_coder_diff")
        self.assertEqual(result["status_refresh_reason"], "compact_snapshot_sufficient")

    def test_wait_stalled_guidance_prefers_cancel_without_status_refresh(self):
        original_no_output = server.STALL_NO_OUTPUT_SECONDS
        server.STALL_NO_OUTPUT_SECONDS = 0.1
        try:
            with tempfile.TemporaryDirectory() as working_dir:
                initial = server.opencode_coder(
                    "no_output_long",
                    working_dir=working_dir,
                    timeout_seconds=2,
                    wait_policy="start_only",
                )
                result = server.opencode_coder_wait(
                    initial["job_id"],
                    wait_seconds=1,
                    return_on="interesting",
                    include_status=False,
                )
                server.opencode_coder_cancel(initial["job_id"])
        finally:
            server.STALL_NO_OUTPUT_SECONDS = original_no_output

        self.assertEqual(result["wait_return_reason"], "stalled")
        self.assertTrue(result["is_stalled"])
        self.assertEqual(result["stall_reason"], "no_output_no_change_after_start")
        self.assertFalse(result["needs_status_refresh"])
        self.assertEqual(result["suggested_next_tool"], "opencode_coder_cancel")

    def test_wait_debug_output_requests_status_refresh(self):
        with tempfile.TemporaryDirectory() as working_dir:
            initial = server.opencode_coder(
                "delayed_output",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="start_only",
            )
            result = server.opencode_coder_wait(
                initial["job_id"],
                wait_seconds=2,
                return_on="interesting",
                include_status=False,
                include_tail=True,
            )

        self.assertEqual(result["wait_return_reason"], "terminal_status")
        self.assertTrue(result["needs_status_refresh"])
        self.assertEqual(result["suggested_next_tool"], "opencode_coder_status")
        self.assertEqual(result["status_refresh_reason"], "debug_output_requested")


class OpenCodeCoderIntegrationTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("OPENCODE_CODER_RUN_INTEGRATION") != "1":
            self.skipTest("set OPENCODE_CODER_RUN_INTEGRATION=1 to run real opencode integration tests")
        if shutil.which("opencode") is None:
            self.skipTest("opencode CLI was not found on PATH")
        server._reset_jobs_for_tests()
        server._reset_servers_for_tests()
        self.server_id = None

    def tearDown(self):
        if self.server_id is not None:
            try:
                server.opencode_server_stop(self.server_id)
            finally:
                self.server_id = None
        server._reset_jobs_for_tests()
        server._reset_servers_for_tests()

    def integration_total_wait_seconds(self) -> float:
        try:
            return float(os.environ.get("OPENCODE_CODER_INTEGRATION_TOTAL_WAIT_SECONDS", "180"))
        except ValueError:
            return 180.0

    def wait_for_terminal_job(self, job_id: str) -> dict:
        deadline = time.monotonic() + self.integration_total_wait_seconds()
        last_status = server.opencode_coder_status(job_id)
        while last_status["status"] not in {"completed", "failed"}:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return last_status
            last_status = server.opencode_coder_status(job_id, wait_seconds=min(10, remaining))
        return last_status

    def diagnostic(self, *results: dict) -> str:
        parts = []
        for result in results:
            parts.append(
                "\n".join(
                    [
                        f"status={result.get('status')}",
                        f"success={result.get('success')}",
                        f"exit_code={result.get('exit_code')}",
                        f"error={result.get('error')}",
                        f"summary={result.get('summary')}",
                        f"stdout_tail={result.get('stdout_tail')}",
                        f"stderr_tail={result.get('stderr_tail')}",
                    ]
                )
            )
        return "\n---\n".join(parts)

    def test_managed_server_attach_wait_policy_status_git_snapshot(self):
        with tempfile.TemporaryDirectory() as working_dir:
            init_git_repo(working_dir)

            started = server.opencode_server_start(working_dir=working_dir, port=0)
            self.server_id = started.get("server_id")
            self.assertEqual(started["status"], "running", self.diagnostic(started))
            self.assertTrue(started["success"], self.diagnostic(started))
            self.assertTrue(started["process_running"], self.diagnostic(started))

            running_status = server.opencode_server_status(self.server_id)
            self.assertEqual(running_status["status"], "running", self.diagnostic(running_status))

            prompt = (
                "This is an isolated temporary git repo for a smoke test. "
                "Create or overwrite exactly one file named smoke_result.txt in the current directory. "
                "Write exactly this text into the file: LiteOpenCodeMcp integration smoke\n"
                "Do not create, edit, delete, or rename any other files. Finish after writing the file."
            )
            initial = server.opencode_coder(
                prompt,
                working_dir=working_dir,
                timeout_seconds=30,
                wait_policy="first_output",
                server_id=self.server_id,
                title="LiteOpenCodeMcp integration smoke",
                allowed_paths=["smoke_result.txt"],
            )
            self.assertTrue(initial["attached_to_server"], self.diagnostic(initial))
            self.assertEqual(initial["server_id"], self.server_id, self.diagnostic(initial))
            self.assertEqual(initial["server_url"], started["url"], self.diagnostic(initial))

            final_status = self.wait_for_terminal_job(initial["job_id"])
            if not final_status.get("session_id"):
                print("integration note: session_id was not observed in stdout JSON events")

            result_path = Path(working_dir, "smoke_result.txt")
            self.assertEqual(final_status["status"], "completed", self.diagnostic(initial, final_status))
            self.assertEqual(final_status["exit_code"], 0, self.diagnostic(initial, final_status))
            self.assertTrue(result_path.exists(), self.diagnostic(initial, final_status))
            self.assertIn(
                "smoke_result.txt",
                set(final_status["new_changed_files"]) | set(final_status["all_changed_files"]),
                self.diagnostic(initial, final_status),
            )

            stopped = server.opencode_server_stop(self.server_id)
            self.server_id = None
            self.assertFalse(stopped["process_running"], self.diagnostic(stopped))
            self.assertEqual(stopped["status"], "stopped", self.diagnostic(stopped))


if __name__ == "__main__":
    unittest.main()
