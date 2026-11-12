import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import version
from src.data.workspace_file import WorkspaceFileError, read_workspace_file, write_workspace_file


class VersionTests(unittest.TestCase):
    def tearDown(self):
        version.version_text.cache_clear()

    def test_app_version_is_semantic(self):
        self.assertRegex(version.APP_VERSION, rf"^{version.VERSION_PATTERN.pattern}$")
        for text in ("1.0.0", "1.0.0-beta.1", "1.0.0-rc.2", "2.10.3"):
            self.assertTrue(version.VERSION_PATTERN.fullmatch(text), text)
        for text in ("1.0", "v1.0.0", "1.0.0-", "01.0.0", "1.0.0-beta.01"):
            self.assertFalse(version.VERSION_PATTERN.fullmatch(text), text)

    def described(self, output):
        completed = subprocess.CompletedProcess([], 0, stdout=output)
        with mock.patch.object(version.subprocess, "run", return_value=completed):
            version.version_text.cache_clear()
            return version.version_text()

    def test_version_text_shows_git_describe_except_on_the_release_tag(self):
        app = version.APP_VERSION
        self.assertEqual(self.described(f"v{app}\n"), app)
        self.assertEqual(self.described(f"v{app}-5-g9850d27-dirty\n"), f"{app} (v{app}-5-g9850d27-dirty)")
        self.assertEqual(self.described("9850d27\n"), f"{app} (9850d27)")
        self.assertEqual(self.described(""), app)
        with mock.patch.object(version.subprocess, "run", side_effect=OSError("no git")):
            version.version_text.cache_clear()
            self.assertEqual(version.version_text(), app)

    def test_workspace_files_record_the_version_that_saved_them(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_workspace_file(Path(folder) / "saved.umw", {"workspace": {"rows": []}})
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["application_version"], version.APP_VERSION)
            # A newer workspace names the version that saved it.
            payload.update(version=99, application_version="9.0.0")
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(WorkspaceFileError, r"LODGaZilla 9\.0\.0 \(UMW 99\)"):
                read_workspace_file(path)


if __name__ == "__main__":
    unittest.main()
