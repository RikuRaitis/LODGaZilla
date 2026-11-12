"""Test runs log to a temporary folder, not to the application's logs/ folder."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from src.app import log_paths
from src.app.log_paths import APPLICATION_LOG_DIR, LOG_DIR_ENV, TEST_LOG_DIR, log_directory, log_path

ROOT = Path(__file__).resolve().parents[1]


class LogPathTests(unittest.TestCase):
    def test_a_test_run_logs_to_the_temporary_folder_and_workers_inherit_it(self):
        environment = {key: value for key, value in os.environ.items() if key != LOG_DIR_ENV}
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(log_directory(), TEST_LOG_DIR)
            self.assertEqual(os.environ[LOG_DIR_ENV], str(TEST_LOG_DIR))
        self.assertNotEqual(TEST_LOG_DIR, APPLICATION_LOG_DIR)

    def test_the_environment_variable_overrides_the_folder(self):
        with mock.patch.dict(os.environ, {LOG_DIR_ENV: str(ROOT / "custom_logs")}):
            self.assertEqual(log_path("startup.log"), ROOT / "custom_logs" / "startup.log")

    def test_the_application_logs_to_its_checkout(self):
        code = ("import sys; from src.app.log_paths import log_directory; "
                "print('unittest' in sys.modules, log_directory())")
        environment = {key: value for key, value in os.environ.items() if key != LOG_DIR_ENV}
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=environment,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(maxsplit=1), ["False", f"{log_paths.APPLICATION_LOG_DIR}\n"])


if __name__ == "__main__":
    unittest.main()
