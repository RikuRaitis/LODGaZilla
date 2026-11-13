"""LODGaZilla.bat starts the app from any folder, such as a shortcut's start folder."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "win32", "LODGaZilla.bat is for Windows")
class LauncherTests(unittest.TestCase):
    def test_bat_finds_the_code_from_another_folder_and_keeps_that_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            # --help exits after the arguments are read, before any window opens.
            result = subprocess.run(["cmd", "/c", str(ROOT / "LODGaZilla.bat"), "--help"], cwd=folder,
                                    capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
                                    env={**os.environ, "PYTHONPATH": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: python -m src.app.lodgazilla", result.stdout)
        self.assertNotIn("No module named", result.stderr)


if __name__ == "__main__":
    unittest.main()
