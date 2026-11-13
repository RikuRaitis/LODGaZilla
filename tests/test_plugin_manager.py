from tests.plugin_fixtures import write_manifest
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from src.plugins.catalog import NO_PLUGINS_ENV  # noqa: E402
from src.plugins.manager import PluginManager  # noqa: E402


class PluginManagerTests(unittest.TestCase):
    def test_disabled_plugins_are_not_called_and_failures_are_isolated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_path = Path(temp_dir) / "sample" / "plugin.py"
            plugin_path.parent.mkdir()
            plugin_path.write_text(
                "PLUGIN_ID = 'sample'\n"
                "PLUGIN_TITLE = 'Sample'\n"
                "class Plugin:\n"
                "    def __init__(self, window, log): self.calls = []\n"
                "    def launch(self): raise RuntimeError('launch failure')\n"
                "    def handle_event(self, name, message): self.calls.append((name, message))\n"
                "PLUGIN_CLASS = Plugin\n",
                encoding="utf-8",
            )
            write_manifest(plugin_path.parent)
            messages: list[str] = []
            manager = PluginManager(object(), plugin_root=temp_dir, log=messages.append)

            manager.dispatch_event("usb_connected", "first")
            # Disabled plugins are not instantiated at all.
            self.assertNotIn("sample", manager.plugins)
            manager.set_enabled({"sample": True})
            manager.dispatch_event("usb_connected", "second")
            manager.launch("sample")

        self.assertEqual(manager.plugins["sample"].calls, [("usb_connected", "second")])
        self.assertTrue(any("launch failed (RuntimeError): launch failure" in item for item in messages))

    def test_a_plugin_added_while_running_can_be_enabled_without_a_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = PluginManager(object(), plugin_root=temp_dir)
            self.assertEqual(manager.descriptors, [])
            # Create plugin writes a new importer while LODGaZilla runs.
            folder = Path(temp_dir) / "new_importer"
            folder.mkdir()
            (folder / "importer.py").write_text("", encoding="utf-8")
            (folder / "importer.json").write_text("{}", encoding="utf-8")
            write_manifest(folder, plugin_id="new_importer", capabilities=["importer"], entry_point="importer.py")
            self.assertEqual(manager.refresh(), ["new_importer"])
            manager.set_enabled({"new_importer": True})
            self.assertTrue(manager.has_enabled_importer())
            # Looking again finds nothing new.
            self.assertEqual(manager.refresh(), [])

    def test_a_running_ui_plugin_keeps_its_descriptor_on_refresh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_path = Path(temp_dir) / "sample" / "plugin.py"
            plugin_path.parent.mkdir()
            plugin_path.write_text("class Plugin:\n    def __init__(self, window, log): pass\nPLUGIN_CLASS = Plugin\n",
                                   encoding="utf-8")
            write_manifest(plugin_path.parent)
            manager = PluginManager(object(), plugin_root=temp_dir)
            manager.set_enabled({"sample": True})
            running = manager.descriptors[0]
            write_manifest(plugin_path.parent, title="Renamed")
            manager.refresh()
            # Its code already runs: the change applies after a restart.
            self.assertIs(manager.descriptors[0], running)

    def test_no_plugins_option_loads_none_even_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_path = Path(temp_dir) / "sample" / "plugin.py"
            plugin_path.parent.mkdir()
            plugin_path.write_text("class Plugin:\n    def __init__(self, window, log): pass\nPLUGIN_CLASS = Plugin\n",
                                   encoding="utf-8")
            write_manifest(plugin_path.parent)
            with mock.patch.dict(os.environ, {NO_PLUGINS_ENV: "1"}):
                manager = PluginManager(object(), plugin_root=temp_dir)
            manager.set_enabled({"sample": True})
        self.assertEqual(manager.descriptors, [])
        self.assertEqual(manager.plugins, {})
        self.assertFalse(manager.is_enabled("sample"))



class PluginLauncherAndOperationTests(unittest.TestCase):
    def _manager(self, directory: str, **manifest):  # type: ignore[no-untyped-def]
        folder = Path(directory) / "sample"
        folder.mkdir()
        (folder / "plugin.py").write_text(
            "class Plugin:\n"
            "    def __init__(self, services, log): pass\n"
            "    def operation_parameters(self, operation): return {'operation': operation}\n"
            "PLUGIN_CLASS = Plugin\n", encoding="utf-8")
        (folder / "worker.py").write_text("def run(request, events, cancel_event): pass\n", encoding="utf-8")
        (folder / "icon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        write_manifest(folder, **manifest)
        window = mock.Mock(max_background_processes=4)
        return PluginManager(window, plugin_root=directory), window

    def test_manifest_launcher_and_operations_are_offered_only_while_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            manager, window = self._manager(directory, launcher={"title": "Sample window", "icon": "icon.svg"}, operations={
                "Plain": "worker.py:run",
                "Export": {"worker": "worker.py:run", "kind": "export", "title": "Sample CSV", "scopes": ["batch"]},
            })
            descriptor = manager.descriptors[0]
            self.assertEqual(descriptor.error, "")
            self.assertEqual(descriptor.launcher.title, "Sample window")
            self.assertEqual(descriptor.launcher.icon, (Path(directory) / "sample" / "icon.svg").resolve())
            self.assertEqual(descriptor.operations, {"Plain": "worker.py:run", "Export": "worker.py:run"})
            self.assertEqual((manager.launchers(), manager.operations("batch")), ([], []))
            manager.set_enabled({"sample": True})
            self.assertEqual(manager.launchers(), [descriptor])
            # The plain form declares only a worker, so the host offers only the declared export.
            self.assertEqual([operation.name for _descriptor, operation in manager.operations("batch")], ["Export"])
            self.assertEqual(manager.operations("folder"), [])
            folder = Path(directory) / "data"
            folder.mkdir()
            manager.run_operation("sample", "Export", folder, recursive=True)
            request = window.start_import.call_args.args[0]
            self.assertEqual((request.operation, request.recursive), ("Export", True))
            self.assertEqual(request.parameters, {"operation": "Export"})

    def test_invalid_launcher_or_operation_rejects_the_manifest(self):
        for manifest in ({"launcher": {"icon": "icon.png"}},
                         {"operations": {"Export": {"worker": "worker.py:run", "kind": "copy", "scopes": ["folder"]}}},
                         {"operations": {"Export": {"worker": "worker.py:run", "kind": "export", "scopes": ["all"]}}}):
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as directory:
                manager, _window = self._manager(directory, **manifest)
                self.assertEqual(manager.descriptors, [])
