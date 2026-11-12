"""Plugin boundaries work without any bundled/public/private plugin code."""
import ast
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.plugin_fixtures import write_manifest, sample_importer
from src.plugins.catalog import discover_plugins, requirement_errors
from src.plugins.services import ApplicationPluginServices
from src.plugins.manager import PluginManager
from src.contracts.jobs import PluginJobRequest
from src.contracts.settings import load_plugin_settings, save_plugin_settings
from src.jobs.plugin import run_plugin_process
from src.jobs.import_data import load_import_configurations, load_import_configuration
from src.jobs.import_coordinator import ActiveImport, request_conflicts_with_active
from tools.setup_environment import dependency_files, fingerprint
from tools.run_tests import snapshot


class CatalogTests(unittest.TestCase):
    def test_empty_or_missing_plugin_directory_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "missing"
            self.assertEqual(discover_plugins(root), [])
            self.assertEqual(load_import_configurations(root, enabled_plugins={}), [])

    def test_manifest_version_and_requirements_disable_only_the_affected_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("good", "future", "missing"):
                folder = root / name
                folder.mkdir()
                (folder / "plugin.py").write_text("raise AssertionError('not during discovery')")
                write_manifest(folder, plugin_id=name, api_version=99 if name == "future" else 1)
            (root / "missing/requirements.txt").write_text("nonexistent-test-dependency>=1\n")
            descriptors = {p.plugin_id: p for p in discover_plugins(root)}
            self.assertEqual(descriptors["good"].error, "")
            self.assertIn("Unsupported plugin API", descriptors["future"].error)
            self.assertIn("Missing dependency", descriptors["missing"].error)
            manager = PluginManager(object(), plugin_root=root)
            manager.set_enabled({"future": True, "missing": True})
            self.assertFalse(any(manager.enabled.values()))

    def test_nested_test_manifests_are_not_discovered(self):
        with sample_importer() as root:
            nested = root / "sample/tests/fixture"
            nested.mkdir(parents=True)
            write_manifest(nested, plugin_id="accidental")
            (nested / "plugin.py").write_text("raise AssertionError('test fixture')")
            self.assertEqual([p.plugin_id for p in discover_plugins(root)], ["sample"])
            self.assertEqual(len(load_import_configurations(root, enabled_plugins={"sample": True})), 1)

    def test_worker_reload_uses_manifest_entry_point(self):
        with sample_importer() as root:
            folder = root / "sample"
            (folder / "importer.py").rename(folder / "parser.py")
            write_manifest(folder, capabilities=["importer"], entry_point="parser.py")
            config = load_import_configuration(folder / "importer.json", hdf5_config={"compression": "lzf"})
            self.assertTrue(config.parser_instance.can_parse(Path("sample.csv")))

    def test_broken_importer_does_not_prevent_loading_others(self):
        with sample_importer() as root:
            broken = root / "broken"
            broken.mkdir()
            write_manifest(broken, plugin_id="broken", capabilities=["importer"], entry_point="importer.py")
            (broken / "importer.json").write_text("{}")
            (broken / "importer.py").write_text("raise RuntimeError('broken parser')")
            with self.assertLogs("src.jobs.import_data", level="WARNING") as messages:
                configurations = load_import_configurations(root, enabled_plugins={"broken": True, "sample": True})
            self.assertEqual(len(configurations), 1)
            self.assertIn("broken parser", messages.output[0])

    def test_external_plugin_overrides_host_copy_in_combined_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host, candidate = root / "host", root / "independent-repository"
            installed = host / "plugin/sample"
            installed.mkdir(parents=True)
            candidate.mkdir()
            for folder in (installed, candidate):
                write_manifest(folder)
            (installed / "version.txt").write_text("installed")
            (candidate / "version.txt").write_text("candidate")
            target = snapshot(host, candidate, root / "staged", all_plugins=True)
            self.assertEqual((target / "version.txt").read_text(), "candidate")

    def test_snapshot_skips_local_data_and_logs_only_at_the_host_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "host"
            for folder in ("Data", "demodata", "logs", "src/data", "src/logs"):
                (host / folder).mkdir(parents=True)
                (host / folder / "file.txt").write_text(folder)
            snapshot(host, None, root / "staged")
            staged = root / "staged"
            self.assertFalse((staged / "Data").exists())
            self.assertFalse((staged / "logs").exists())
            self.assertFalse((staged / "demodata").exists())
            self.assertTrue((staged / "src/data/file.txt").is_file())
            self.assertTrue((staged / "src/logs/file.txt").is_file())

    def test_entry_point_cannot_escape_plugin_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "sample"
            folder.mkdir()
            (root / "outside.py").write_text("raise AssertionError")
            write_manifest(folder, entry_point="../outside.py")
            messages = []
            self.assertEqual(discover_plugins(root, log=messages.append), [])
            self.assertIn("invalid plugin file", messages[0])

    def test_dependency_stamp_includes_plugin_list_and_content(self):
        with sample_importer() as plugins:
            root = plugins.parent
            (root / "requirements.txt").write_text("numpy>=1.25\n")
            files = dependency_files(root, plugins)
            first = fingerprint(sum((list(items) for items in files), []), "python")
            (plugins / "sample/requirements.txt").write_text("numpy>=1.26\n")
            second = fingerprint(sum((list(items) for items in dependency_files(root, plugins)), []), "python")
            self.assertNotEqual(first, second)
            with mock.patch("src.plugins.catalog.importlib.metadata.version", return_value="1.0"):
                self.assertIn("installed 1.0", requirement_errors(plugins / "sample/requirements.txt")[0])

    def test_core_has_no_imports_from_installed_plugins(self):
        root = Path(__file__).resolve().parents[1]
        for folder in (root / "src", root / "tests"):
            for path in folder.rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                    names = [node.module or ""] if isinstance(node, ast.ImportFrom) else (
                        [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                    self.assertFalse(any(name == "plugin" or name.startswith("plugin.") for name in names), str(path))


class PluginJobTests(unittest.TestCase):
    def test_a_plugin_folder_cannot_take_the_place_of_an_installed_package(self):
        import queue
        import sys
        import xml
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "xml"
            folder.mkdir()
            (folder / "__init__.py").write_text("")
            worker = folder / "worker.py"
            worker.write_text("def run(request, events, cancel): events.put(('finished', 1, 0, ''))\n")
            events = queue.Queue()
            self.addCleanup(lambda: directory in sys.path and sys.path.remove(directory))
            run_plugin_process(PluginJobRequest(folder, False, 1, "xml", "Run", str(worker)), events)
            finished = events.get(timeout=5)
        # The plugin does not run as the standard library's "xml", which stays itself.
        self.assertEqual(finished[:3], ("finished", 0, 0))
        self.assertTrue(finished[3])
        import xml as again
        self.assertIs(again, xml)
        self.assertNotEqual(sys.path[0], directory)

    def test_real_spawned_worker_can_start_a_nested_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "example_job"
            folder.mkdir()
            (folder / "__init__.py").write_text("")
            worker = folder / "worker.py"
            worker.write_text('''from src.contracts.jobs import run_process_jobs
def init(queue, cancel): pass
def square(value): return value * value
def run(request, events, cancel):
    results = run_process_jobs([2, 3], worker_count=2, worker=square,
        initializer=init, emit_progress=events.put, make_failure_result=lambda job, error: str(error),
        cancel_event=cancel)
    events.put(("finished", sum(results), 0, ""))
''')
            context = multiprocessing.get_context("spawn")
            events, cancel = context.Queue(), context.Event()
            request = PluginJobRequest(folder, False, 2, "example", "Compute", str(worker))
            process = context.Process(target=run_plugin_process, args=(request, events, cancel))
            process.start()
            try:
                self.assertEqual(events.get(timeout=30), ("finished", 13, 0, ""))
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                events.close()
                events.join_thread()

    def test_conflicts_include_plugin_identity_and_folder_scope(self):
        active = ActiveImport(Path("root"), True, 1, None, None, "Convert", plugin_id="first")
        same = PluginJobRequest(Path("root/sub"), False, 1, "first", "Convert")
        other = PluginJobRequest(Path("root/sub"), False, 1, "second", "Convert")
        self.assertTrue(request_conflicts_with_active(same, active))
        self.assertFalse(request_conflicts_with_active(other, active))

    def test_registered_operation_uses_shared_budget_and_private_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "sample"
            folder.mkdir()
            (folder / "plugin.py").write_text("")
            (folder / "worker.py").write_text("")
            write_manifest(folder, operations={"Convert": "worker.py:run"})
            descriptor = discover_plugins(Path(directory))[0]
            window = mock.Mock(max_background_processes=2)
            services = ApplicationPluginServices(window, descriptor)
            services.submit_job("Convert", folder, recursive=True, parameters={"calibration": 2.0})
            request = window.start_import.call_args.args[0]
            self.assertEqual(request.process_slots, 2)
            self.assertEqual(request.parameters, {"calibration": 2.0})
            services.submit_job("Convert", folder)
            self.assertEqual(window.start_import.call_args.args[0].process_slots, 1)
            with self.assertRaises(KeyError):
                services.submit_job("Unregistered", folder)


class PluginSettingsTests(unittest.TestCase):
    def test_local_settings_preserve_defaults_and_failed_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "settings.json").write_text('{"speed": 2}')
            self.assertEqual(load_plugin_settings(folder, {"speed": 1}), {"speed": 2})
            save_plugin_settings(folder, {"speed": 3})
            with mock.patch("src.contracts.settings.os.replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    save_plugin_settings(folder, {"speed": 4})
            self.assertEqual(load_plugin_settings(folder, {}), {"speed": 3})
            self.assertEqual(json.loads((folder / "settings.json").read_text()), {"speed": 2})
            self.assertEqual(list(folder.glob(".settings-*.tmp")), [])
