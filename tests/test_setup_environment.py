"""Dependency setup uses the selected interpreter without creating a venv."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import setup_environment


class SetupEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.requirements = self.root / "requirements.txt"
        self.requirements.write_text("example>=1\n")
        self.marker = self.root / ".lodgazilla_dependencies.json"

    def run_setup(self, *arguments):
        with mock.patch.object(setup_environment, "ROOT", self.root), \
                mock.patch.object(setup_environment, "dependency_files", return_value=([self.requirements], [], [])), \
                mock.patch.object(sys, "argv", ["setup_environment.py", *arguments]), \
                contextlib.redirect_stdout(io.StringIO()):
            return setup_environment.main()

    def test_install_and_validation_use_selected_python_without_creating_venv(self):
        with mock.patch.object(setup_environment.subprocess, "run") as run:
            self.assertEqual(self.run_setup(), 0)
        self.assertEqual(run.call_count, 3)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(all(command[0] == str(Path(sys.executable).resolve()) for command in commands))
        self.assertEqual(commands[0][1:4], ["-m", "pip", "install"])
        self.assertEqual(commands[1][1:], ["-m", "pip", "check"])
        self.assertTrue(self.marker.is_file())
        self.assertFalse((self.root / ".venv").exists())

    def test_run_reports_a_failed_install_instead_of_raising(self):
        error = io.StringIO()
        with mock.patch.object(setup_environment, "ROOT", self.root), \
                mock.patch.object(setup_environment, "dependency_files", return_value=([self.requirements], [], [])), \
                mock.patch.object(setup_environment.subprocess, "run",
                                  side_effect=subprocess.CalledProcessError(1, "pip")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(error):
            self.assertEqual(setup_environment.run([]), 1)
        self.assertIn("Dependency setup failed", error.getvalue())
        self.assertFalse(self.marker.exists())

    def test_check_without_stamp_does_not_install_or_write_files(self):
        with mock.patch.object(setup_environment.subprocess, "run") as run:
            self.assertEqual(self.run_setup("--check"), 1)
        run.assert_not_called()
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.root / ".venv").exists())

    def test_stamp_skips_reinstall_but_changed_interpreter_invalidates_it(self):
        with mock.patch.object(setup_environment.subprocess, "run") as run:
            self.assertEqual(self.run_setup(), 0)
            run.reset_mock()
            self.assertEqual(self.run_setup(), 0)
            run.assert_not_called()
            with mock.patch.object(sys, "executable", str(self.root / "another-python.exe")):
                self.assertEqual(self.run_setup("--check"), 1)
            run.assert_not_called()

    def test_failed_install_does_not_save_success_stamp(self):
        with mock.patch.object(setup_environment.subprocess, "run",
                               side_effect=subprocess.CalledProcessError(1, "pip")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_setup()
        self.assertFalse(self.marker.exists())

    def test_check_does_not_activate_pending_plugins(self):
        with mock.patch("src.plugins.installation.pending_requests", return_value=["pending"]), \
                mock.patch("src.plugins.installation.apply_pending") as activate, \
                mock.patch.object(setup_environment.subprocess, "run") as run:
            self.assertEqual(self.run_setup("--check"), 1)
        activate.assert_not_called()
        run.assert_not_called()

    def test_activation_precedes_dependency_discovery(self):
        steps = []
        with mock.patch.object(setup_environment, "ROOT", self.root), \
                mock.patch.object(sys, "argv", ["setup_environment.py", "--plugin-root", str(self.root / "plugin")]), \
                mock.patch("src.plugins.installation.apply_pending", side_effect=lambda root: steps.append("activate") or []), \
                mock.patch.object(setup_environment, "dependency_files", side_effect=lambda *args: steps.append("dependencies") or ([self.requirements], [], [])), \
                mock.patch.object(setup_environment.subprocess, "run"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup_environment.main(), 0)
        self.assertEqual(steps, ["activate", "dependencies"])

    def test_custom_plugin_root_matches_runtime_environment(self):
        custom = self.root / "external"
        with mock.patch.dict("os.environ", {"LODGAZILLA_PLUGIN_ROOT": str(custom)}), \
                mock.patch("src.plugins.installation.apply_pending", return_value=[]) as activate, \
                mock.patch.object(setup_environment.subprocess, "run"):
            self.assertEqual(self.run_setup(), 0)
        activate.assert_called_once_with(custom)
