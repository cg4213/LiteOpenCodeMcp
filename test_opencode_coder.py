import importlib.util
from pathlib import Path
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
    elif prompt == "session_json":
        code = "print('{\"sessionID\":\"ses_test_123\"}', flush=True)"
    elif prompt == "long":
        code = (
            "import time\n"
            "print('begin', flush=True)\n"
            "time.sleep(0.4)\n"
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


class OpenCodeCoderTests(unittest.TestCase):
    def setUp(self):
        FAKE_BUILD_CALLS.clear()
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
        self.assertEqual(status["status"], "stopped")

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
            )
            server.opencode_server_stop(started["server_id"])

        self.assertEqual(result["session_id"], "ses_test_123")
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

            time.sleep(0.8)
            final_status = server.opencode_coder_status(job_id)

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

            time.sleep(0.8)
            final_status = server.opencode_coder_status(first["job_id"])

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
            time.sleep(0.6)
            final_status = server.opencode_coder_status(initial["job_id"])

        self.assertEqual(final_status["status"], "completed")
        self.assertTrue(final_status["policy_violation"])
        self.assertEqual(final_status["extra_changed_files"], ["docs/outside.txt"])


if __name__ == "__main__":
    unittest.main()
