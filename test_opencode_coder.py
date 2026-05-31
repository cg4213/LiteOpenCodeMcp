import importlib.util
from pathlib import Path
import sys
import tempfile
import time
import unittest


MODULE_PATH = Path(__file__).with_name("opencode-coder.py")
SPEC = importlib.util.spec_from_file_location("opencode_coder_server", MODULE_PATH)
server = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = server
SPEC.loader.exec_module(server)


def fake_command(prompt: str) -> list[str]:
    if prompt == "short":
        code = "print('ok', flush=True)"
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
    else:
        raise AssertionError(f"unexpected prompt in fake command: {prompt}")
    return [sys.executable, "-c", code]


class OpenCodeCoderTests(unittest.TestCase):
    def setUp(self):
        server._reset_jobs_for_tests()
        self.original_build_command = server.build_opencode_command
        server.build_opencode_command = fake_command

    def tearDown(self):
        server.build_opencode_command = self.original_build_command
        server._reset_jobs_for_tests()

    def test_short_task_completes_synchronously(self):
        with tempfile.TemporaryDirectory() as working_dir:
            result = server.opencode_coder("short", working_dir=working_dir, timeout_seconds=2)

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["success"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("ok", result["stdout_tail"])
        self.assertIsNotNone(result["job_id"])

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


if __name__ == "__main__":
    unittest.main()
