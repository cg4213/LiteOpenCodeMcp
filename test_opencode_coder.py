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
    elif prompt == "fail":
        code = (
            "import sys\n"
            "sys.stderr.write('boom\\n')\n"
            "sys.stderr.flush()\n"
            "raise SystemExit(7)\n"
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
        self.assertIn("ok", result["stdout_tail"])
        self.assertIsNotNone(result["job_id"])
        self.assertEqual(result["preexisting_changed_files"], [])
        self.assertEqual(result["all_changed_files"], [])
        self.assertEqual(result["new_changed_files"], [])
        self.assertFalse(result["attached_to_server"])
        self.assertIsNone(result["server_id"])
        self.assertIsNone(result["server_url"])
        self.assertIsNone(FAKE_BUILD_CALLS[-1]["server_url"])

    def test_default_completion_wait_policy_keeps_old_behavior(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started_at = time.monotonic()
            result = server.opencode_coder("delayed_output", working_dir=working_dir, timeout_seconds=2)
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

        self.assertEqual(final_status["status"], "completed")
        self.assertIn("done", final_status["stdout_tail"])

    def test_first_output_returns_after_initial_output(self):
        with tempfile.TemporaryDirectory() as working_dir:
            started_at = time.monotonic()
            result = server.opencode_coder(
                "delayed_output",
                working_dir=working_dir,
                timeout_seconds=2,
                wait_policy="first_output",
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

        self.assertLess(elapsed, 0.75)
        self.assertEqual(result["wait_policy"], "first_change")
        self.assertEqual(result["new_changed_files"], ["src/new.txt"])
        self.assertIsNotNone(result["first_change_at"])
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
            status = server.opencode_coder_status(initial["job_id"], wait_seconds=1)
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

    def test_status_returns_output_cursors(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)
            status = server.opencode_coder_status(completed["job_id"])

        self.assertGreater(status["stdout_cursor"], 0)
        self.assertEqual(status["stderr_cursor"], 0)
        self.assertEqual(status["stdout_delta"], "")
        self.assertEqual(status["stderr_delta"], "")
        self.assertIn("ok", status["stdout_tail"])

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
            )
            final_status = wait_for_terminal_job(initial["job_id"])

        self.assertIn("err-done", status["stderr_delta"])
        self.assertNotIn("err-first", status["stderr_delta"])
        self.assertGreater(status["stderr_cursor"], initial["stderr_cursor"])
        self.assertFalse(status["stderr_delta_truncated"])
        self.assertEqual(final_status["status"], "completed")

    def test_status_without_cursor_keeps_legacy_tail_fields(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)
            status = server.opencode_coder_status(completed["job_id"])

        self.assertIn("ok", status["stdout_tail"])
        self.assertEqual(status["stdout_delta"], "")
        self.assertEqual(status["stderr_delta"], "")
        self.assertIn("ok", status["output"])

    def test_status_invalid_or_out_of_range_cursor_is_safe(self):
        with tempfile.TemporaryDirectory() as working_dir:
            completed = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)
            invalid = server.opencode_coder_status(completed["job_id"], stdout_cursor="bad", stderr_cursor=-5)
            too_large = server.opencode_coder_status(completed["job_id"], stdout_cursor=999999)

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
                status = server.opencode_coder_status(completed["job_id"], stdout_cursor=0)
        finally:
            server.MAX_DELTA_BUFFER_CHARS = previous_limit

        self.assertTrue(status["stdout_delta_truncated"])
        self.assertEqual(status["stdout_delta"], "tuvwxyz\n")
        self.assertEqual(status["stdout_cursor"], completed["stdout_cursor"])

    def test_not_found_status_returns_cursor_and_delta_fields(self):
        result = server.opencode_coder_status("missing-job", stdout_cursor=123, stderr_cursor=456)

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["stdout_cursor"], 0)
        self.assertEqual(result["stderr_cursor"], 0)
        self.assertEqual(result["stdout_delta"], "")
        self.assertEqual(result["stderr_delta"], "")
        self.assertFalse(result["stdout_delta_truncated"])
        self.assertFalse(result["stderr_delta_truncated"])

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

    def test_long_task_times_out_and_can_be_queried_until_final_completion(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder("long", working_dir=working_dir, timeout_seconds=0.05)

            self.assertEqual(result["status"], "timed_out")
            self.assertTrue(result["process_running"])
            self.assertIsNotNone(result["pid"])

            job_id = result["job_id"]
            running_status = server.opencode_coder_status(job_id)
            self.assertIn(running_status["status"], {"timed_out", "completed"})

            final_status = wait_for_terminal_job(job_id)

        self.assertEqual(final_status["status"], "completed")
        self.assertEqual(final_status["exit_code"], 0)
        self.assertFalse(final_status["process_running"])
        self.assertIn("begin", final_status["stdout_tail"])
        self.assertIn("done", final_status["stdout_tail"])

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
            result = server.opencode_coder("fail", working_dir=working_dir, timeout_seconds=2)

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["return_code"], 7)
        self.assertIn("boom", result["stderr_tail"])

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
        self.assertEqual(status["status"], "cancelled")
        self.assertFalse(status["process_running"])

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
