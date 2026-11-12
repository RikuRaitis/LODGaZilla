import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from src.plugins.loader import load_module_from_path  # noqa: E402
from src.plugins.manager import discover_plugins  # noqa: E402
from src.plugins.manager import PluginManager
from tests.plugin_fixtures import write_manifest


class PluginLoaderTests(unittest.TestCase):
    def test_module_is_cached_by_resolved_path_and_namespace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module_path = Path(temp_dir) / "example.py"
            module_path.write_text("value = 42\n", encoding="utf-8")

            first = load_module_from_path(module_path, module_namespace="test_plugin")
            second = load_module_from_path(module_path, module_namespace="test_plugin")

        self.assertIs(first, second)
        self.assertEqual(42, first.value)

    def test_failed_load_does_not_leave_a_cached_module(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module_path = Path(temp_dir) / "broken.py"
            module_path.write_text("raise RuntimeError('broken')\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "broken"):
                load_module_from_path(module_path, module_namespace="test_plugin")

            self.assertFalse(any(
                module is not None and getattr(module, "__file__", None) == str(module_path.resolve())
                for module in sys.modules.values()
            ))

    def test_discovery_logs_a_broken_plugin_and_continues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_path = Path(temp_dir) / "broken" / "plugin.py"
            plugin_path.parent.mkdir()
            plugin_path.write_text("raise RuntimeError('broken plugin')\n", encoding="utf-8")
            write_manifest(plugin_path.parent, plugin_id="broken")
            messages: list[str] = []

            descriptors = discover_plugins(temp_dir, log=messages.append)
            self.assertEqual(len(descriptors), 1)
            self.assertEqual(messages, [])  # Discovery must not execute plugin code.
            manager = PluginManager(object(), plugin_root=temp_dir, log=messages.append)
            manager.set_enabled({"broken": True})

        self.assertFalse(manager.is_enabled("broken"))
        self.assertEqual(len(messages), 1)
        self.assertIn("(RuntimeError): broken plugin", messages[0])


if __name__ == "__main__":
    unittest.main()
