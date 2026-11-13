"""Repository discovery, staged activation and Settings regression coverage."""
import base64
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.plugins.catalog import validate_manifest
from src.plugins import installation, repositories
from src.plugins.repositories import Candidate


MANIFEST = {"id": "example", "title": "Example", "api_version": 1,
            "capabilities": ["importer"], "entry_point": "importer.py", "version": "1.0"}
URL = "https://github.com/owner/LODGaZilla-example"


class DiscoveryTests(unittest.TestCase):
    def test_new_settings_look_for_plugins_in_the_public_account(self):
        from src.app.settings import DEFAULT_PLUGIN_SOURCES, PLUGINS_SECTION, default_app_settings_data
        self.assertEqual(DEFAULT_PLUGIN_SOURCES, ["https://github.com/RikuRaitis"])
        self.assertEqual(default_app_settings_data()[PLUGINS_SECTION]["sources"], DEFAULT_PLUGIN_SOURCES)
        self.assertEqual(repositories.source_parts(DEFAULT_PLUGIN_SOURCES[0]), ("github.com", "RikuRaitis"))
        committed = json.loads((Path(__file__).resolve().parents[1] / "app_settings.json").read_text(encoding="utf-8"))
        self.assertEqual(committed[PLUGINS_SECTION]["sources"], DEFAULT_PLUGIN_SOURCES)

    def test_only_account_urls_are_accepted(self):
        self.assertEqual(repositories.source_parts("https://bitbucket.org/example-team/"), ("bitbucket.org", "example-team"))
        for url in ("http://github.com/owner", "https://github.com/owner/repo",
                    "https://user:secret@github.com/owner", "https://github.com.evil/owner",
                    "https://github.com/owner?token=secret"):
            with self.assertRaises(ValueError):
                repositories.source_parts(url)

    def test_shared_manifest_validation_rejects_unsafe_paths_and_distinguishes_api(self):
        self.assertEqual(validate_manifest(MANIFEST), "")
        self.assertIn("Unsupported", validate_manifest(dict(MANIFEST, api_version=999)))
        for path in ("../outside.py", "C:/outside.py", "\\outside.py"):
            with self.assertRaises(ValueError):
                validate_manifest(dict(MANIFEST, entry_point=path))
        with self.assertRaises(ValueError):
            validate_manifest(dict(MANIFEST, operations={"run": "../worker.py:run"}))

    def test_github_private_and_public_results_are_filtered_and_deduplicated(self):
        repo = {"name": "LODGaZilla-example", "owner": {"login": "owner"}, "default_branch": "main"}
        other = dict(repo, owner={"login": "someone-else"})
        api = mock.Mock(auth="Bearer secret")
        api.pages.side_effect = [[repo], [repo, other, dict(repo, name="other-tool")]]
        api.get.side_effect = [{"sha": "a" * 40}, {"content": base64.b64encode(json.dumps(MANIFEST).encode()).decode()}]
        with mock.patch.object(repositories, "Api", return_value=api):
            found, errors = repositories.search_sources(["https://github.com/owner"])
        self.assertEqual(errors, [])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].url, URL)
        self.assertIn("/user/repos", api.pages.call_args_list[1].args[0])

    def test_bitbucket_uses_manifest_id_instead_of_repository_name(self):
        api = mock.Mock()
        api.pages.return_value = [{"name": "LODGaZilla-exporter", "slug": "lodgazilla-exporter",
                                  "mainbranch": {"name": "main"}}]
        api.get.side_effect = [{"target": {"hash": "b" * 40}}, dict(MANIFEST, id="export")]
        with mock.patch.object(repositories, "Api", return_value=api):
            found, errors = repositories.search_sources(["https://bitbucket.org/example-team"])
        self.assertFalse(errors)
        self.assertEqual(found[0].plugin_id, "export")
        self.assertIn("/src/" + "b" * 40, api.get.call_args.args[0])

    def test_one_broken_repository_does_not_hide_other_plugins(self):
        api = mock.Mock(auth="")
        repo = {"name": "LODGaZilla-broken", "owner": {"login": "owner"}, "default_branch": "main"}
        api.pages.return_value = [repo, dict(repo, name="LODGaZilla-example")]
        api.get.side_effect = [ValueError("not a plugin"), {"sha": "a" * 40},
                              {"content": base64.b64encode(json.dumps(MANIFEST).encode()).decode()}]
        with mock.patch.object(repositories, "Api", return_value=api):
            found, errors = repositories.search_sources(["https://github.com/owner"])
        self.assertEqual(len(found), 1)
        self.assertEqual(len(errors), 2)
        self.assertIn("public repositories only", errors[0])

    def test_api_refuses_pagination_to_other_hosts_before_sending_credentials(self):
        with mock.patch.object(repositories, "authorization", return_value="Bearer secret"):
            api = repositories.Api("github.com")
        with mock.patch.object(repositories, "build_opener") as opener:
            with self.assertRaises(ValueError):
                api.get("https://attacker.example/next")
        opener.assert_not_called()

    def test_api_pagination_reads_all_pages(self):
        with mock.patch.object(repositories, "authorization", return_value=""):
            api = repositories.Api("github.com")
        with mock.patch.object(api, "get", side_effect=[[1] * 100, [2]]):
            self.assertEqual(len(list(api.pages("/users/owner/repos?per_page=100"))), 101)
        with mock.patch.object(api, "get", side_effect=[{"values": [1], "next": "https://api.github.com/next"}, {"values": [2]}]):
            self.assertEqual(list(api.pages("/repositories/owner?pagelen=100", bitbucket=True)), [1, 2])


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.remote = self.base / "remote"
        self.remote.mkdir()
        self.root = self.base / "plugin"
        repositories.command(["git", "init", "-b", "main", str(self.remote)])
        (self.remote / "plugin.json").write_text(json.dumps(MANIFEST))
        (self.remote / "importer.json").write_text("{}")
        (self.remote / "importer.py").write_text("raise RuntimeError('discovery must not execute me')\n")
        (self.remote / ".gitignore").write_text("settings.local.json\n")
        self.commit()

    def commit(self):
        installation.git(self.remote, "add", ".")
        installation.git(self.remote, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "test")
        self.candidate = Candidate("example", "Example", "1.0", URL, "main",
                                   installation.git(self.remote, "rev-parse", "HEAD"))

    def stage(self):
        actual = repositories.command
        def local_clone(arguments, **kwargs):
            if "clone" in arguments:
                arguments = list(arguments)
                arguments[-2] = str(self.remote)
                result = actual(arguments, **kwargs)
                installation.git(Path(arguments[-1]), "remote", "set-url", "origin", URL)
                return result
            return actual(arguments, **kwargs)
        with mock.patch.object(installation, "command", side_effect=local_clone), \
                mock.patch.object(installation, "authorization", return_value=""):
            installation.stage_install(self.candidate, self.root)

    def install(self):
        self.stage()
        self.assertEqual(installation.apply_pending(self.root), ["Installed Example"])

    def test_install_is_staged_and_activated_without_importing_code(self):
        self.stage()
        self.assertFalse((self.root / "example").exists())
        self.assertEqual(installation.candidate_status(self.candidate, self.root), ("", "Restart required"))
        self.assertEqual(installation.apply_pending(self.root), ["Installed Example"])
        self.assertEqual(installation.git(self.root / "example", "rev-parse", "HEAD"), self.candidate.commit)
        self.assertEqual(installation.candidate_status(self.candidate, self.root), ("", "Up to date"))

    def test_update_preserves_settings_and_can_be_repeated(self):
        self.install()
        installed = self.root / "example"
        (installed / "settings.local.json").write_text('{"keep": true}')
        for value in ("second", "third"):
            (self.remote / "feature.txt").write_text(value)
            self.commit()
            old = installation.git(installed, "rev-parse", "HEAD")
            self.stage()
            self.assertEqual(installation.git(installed, "rev-parse", "HEAD"), old)
            self.assertEqual(installation.apply_pending(self.root), ["Installed Example"])
            self.assertEqual((installed / "feature.txt").read_text(), value)
            self.assertEqual((installed / "settings.local.json").read_text(), '{"keep": true}')

    def test_dirty_checkout_is_not_updated(self):
        self.install()
        (self.root / "example" / "importer.py").write_text("local edit")
        action, message = installation.candidate_status(self.candidate, self.root)
        self.assertFalse(action)
        self.assertIn("Local changes", message)

    def test_edit_after_download_postpones_update(self):
        self.install()
        (self.remote / "feature.txt").write_text("new")
        self.commit()
        self.stage()
        (self.root / "example" / "importer.py").write_text("local edit")
        messages = installation.apply_pending(self.root)
        self.assertIn("postponed", messages[0])
        self.assertFalse((self.root / "example" / "feature.txt").exists())
        self.assertTrue(installation.pending_requests(self.root))

    def test_other_repository_cannot_replace_same_plugin_id(self):
        self.install()
        installation.git(self.root / "example", "remote", "set-url", "origin", "https://github.com/other/LODGaZilla-example")
        action, message = installation.candidate_status(self.candidate, self.root)
        self.assertFalse(action)
        self.assertIn("different repository", message)

    def test_local_commits_are_never_discarded(self):
        self.install()
        installed = self.root / "example"
        (installed / "mine.txt").write_text("local work")
        installation.git(installed, "add", ".")
        installation.git(installed, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "local")
        (self.remote / "feature.txt").write_text("remote work")
        self.commit()
        with self.assertRaises(ValueError):
            self.stage()
        self.assertTrue((installed / "mine.txt").exists())

    def test_incoming_file_cannot_overwrite_ignored_settings(self):
        self.install()
        (self.root / "example" / "settings.local.json").write_text("keep")
        (self.remote / "settings.local.json").write_text("replace")
        installation.git(self.remote, "add", "-f", "settings.local.json")
        self.commit()
        self.stage()
        self.assertIn("overwrite ignored", installation.apply_pending(self.root)[0])
        self.assertEqual((self.root / "example" / "settings.local.json").read_text(), "keep")


class PluginSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_roundtrip_and_results_keep_checkbox_selection(self):
        from src.gui.settings_widget import SettingsEditorWidget
        from src.plugins.updates import PluginUpdates
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "plugin" / "example"
            folder.mkdir(parents=True)
            (folder / "plugin.json").write_text(json.dumps(MANIFEST))
            (folder / "importer.py").write_text("")
            (folder / "importer.json").write_text("{}")
            controller = PluginUpdates(root=root / "plugin")
            editor = SettingsEditorWidget(root / "settings.json", plugin_updates=controller)
            editor.plugin_checkboxes["example"].setChecked(True)
            editor.plugin_sources_edit.setPlainText("https://github.com/owner\nhttps://bitbucket.org/example-team")
            editor.plugin_startup_check.setChecked(False)
            controller.rows = [(Candidate("example", "Example", "2", URL, "main", "a" * 40), "Update", "Available")]
            controller.changed.emit()
            self.assertTrue(editor.plugin_checkboxes["example"].isChecked())
            editor.save()
            saved = json.loads((root / "settings.json").read_text())["plugins"]
            self.assertEqual(len(saved["sources"]), 2)
            self.assertFalse(saved["check_at_startup"])
            self.assertTrue(saved["enabled"]["example"])
            editor.close()
            controller.shutdown()

    def test_spawned_worker_handles_invalid_source_and_shuts_down(self):
        from src.plugins.updates import PluginUpdates
        with tempfile.TemporaryDirectory() as temp:
            controller = PluginUpdates(root=Path(temp))
            self.addCleanup(controller.shutdown)
            controller.check(["invalid-url"])
            deadline = time.monotonic() + 30
            while controller.busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
            self.assertFalse(controller.busy)
            self.assertEqual(len(controller.errors), 1)
            self.assertIsNone(controller.process)


class CheckoutMoveTests(unittest.TestCase):
    def test_a_moment_of_access_denied_is_retried(self):
        # A virus scanner or the indexer may hold a fresh checkout's files for a moment.
        from src.plugins.installation import _rename_with_retry
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "checkout", Path(directory) / "example"
            source.mkdir()
            original = Path.rename
            failures = iter([PermissionError("in use"), PermissionError("in use")])

            def rename(path, destination):
                error = next(failures, None)
                if error is not None:
                    raise error
                return original(path, destination)

            with mock.patch("pathlib.Path.rename", rename):
                _rename_with_retry(source, target)
            self.assertTrue(target.is_dir())
            source.mkdir()
            with mock.patch("pathlib.Path.rename", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    _rename_with_retry(source, target, seconds=0.05)
