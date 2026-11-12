"""A plugin-free application can open an existing cache and close cleanly."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

import h5py
from PySide6.QtWidgets import QApplication, QMenu, QMenuBar, QMessageBox, QStatusBar
from src.app.branding import APP_VERSION
from src.app.settings import load_settings_section
from src.app.lodgazilla import LODGaZillaWindow, parse_arguments
from src.gui.about_dialog import AboutDialog
from src.plugins.manager import PluginManager
from tests.qt_wait import qt_wait_until


class CoreStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_start_open_cache_and_close_without_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            settings = folder / "settings.json"
            settings.write_text(json.dumps({"navigator_controls": {"recent_folders": []}}))
            cache = folder / "example.h5"
            with h5py.File(cache, "w") as handle:
                series = handle.create_group("series/signal")
                series.create_dataset("time_unix", data=[1., 2., 3.])
                series.create_dataset("value", data=[4., 5., 6.])
                series.attrs["time_dataset"] = "time_unix"
            with mock.patch("src.app.lodgazilla.PluginManager",
                            side_effect=lambda window, **kwargs: PluginManager(window, plugin_root=folder / "empty", **kwargs)):
                window = LODGaZillaWindow(folder, settings_path=settings)
            try:
                self.assertEqual(window.plugin_manager.descriptors, [])
                window.show()
                card = window.plot_workspace.add_line_plot(cache, "signal")
                self.app.processEvents()
                self.assertEqual(card.series, [(cache, "signal")])
                self.assertIs(window.plot_workspace.active_plot, card)
                # No menu bar: its commands are on the File bar and the Plot navigator.
                self.assertIsNone(window.findChild(QMenuBar))
                # No status bar either: loading and status messages go to the Internal log panel.
                window.on_navigator_loading_changed(True)
                window.on_navigator_loading_changed(False)
                self.assertIsNone(window.findChild(QStatusBar))
                log_text = window.log_panel.entries.toPlainText()
                self.assertIn(f"Loading data folder {folder.name}…", log_text)
                self.assertIn(f"Loaded data folder {folder.name}", log_text)
                # Without plugins, the plugin actions are listed last and H5 generation is disabled.
                menu = QMenu(window)
                window.add_plugin_folder_actions(menu, [folder, folder / "other"], "batch")
                self.assertEqual([(action.text(), action.isEnabled()) for action in menu.actions()],
                                 [("Plugin: Generate / update H5 files (2 folders)", False)])
                window.batch_menu.aboutToShow.emit()
                self.assertEqual([action.text() for action in window.batch_menu.actions()],
                                 ["Plugin: Generate H5 files for all folders"])
                self.assertTrue(window.plot_navigator.plugin_bar.isHidden())
                # The Batch button sits between Refresh and Sample loss, and asks before it starts.
                controls = window.navigator.controls
                row = controls.icon_row.layout()
                buttons = [row.itemAt(index).widget() for index in range(row.count()) if row.itemAt(index).widget()]
                self.assertEqual(buttons[:3], [controls.refresh_button, controls.batch_button,
                                               controls.sample_loss_button])
                for answer, started in ((QMessageBox.StandardButton.No, False), (QMessageBox.StandardButton.Yes, True)):
                    with mock.patch("src.app.lodgazilla.QMessageBox.question", return_value=answer), \
                            mock.patch.object(window, "start_import") as start_import:
                        window.start_data_folder_cache_batch()
                    self.assertEqual(start_import.called, started)
                # Help shows the About dialog; its button opens README.md in the system's own application.
                with mock.patch("src.app.lodgazilla.QDesktopServices.openUrl", return_value=True) as open_url:
                    window.plot_navigator.help_button.click()
                    self.app.processEvents()
                    open_url.assert_not_called()
                    about = window.about_dialog
                    self.assertTrue(about.isVisible())
                    self.assertIn(f"Version {APP_VERSION}", about.text.text())
                    self.assertIn("Riku Tervonen", about.text.text())
                    about.readme_button.click()
                self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()).name, "README.md")
                window.plot_navigator.help_button.click()
                self.assertEqual(len(window.findChildren(AboutDialog)), 1)
                about.close()
                # The log names the running version, found off the GUI thread.
                logged = []
                window.internal_logger.message_logged.connect(lambda _time, text, _kind: logged.append(text))
                window._log_version()
                self.assertTrue(qt_wait_until(lambda: any(text.startswith(f"LODGaZilla {APP_VERSION}")
                                                          for text in logged)))
                # The AI panel opens below the Navigator at its minimum height, showing the drop boxes.
                window.resize(1200, 800)
                window.size_ai_panel(window.ai_panel.is_expanded)
                self.app.processEvents()
                panel = window.ai_panel
                self.assertTrue(panel.is_expanded)
                self.assertEqual(window.left_splitter.sizes()[1], panel.EXPANDED_MIN_HEIGHT)
                # Minimized, it is one row, and stays so at the next start.
                panel.collapse_button.click()
                self.app.processEvents()
                self.assertEqual(window.left_splitter.sizes()[1], panel.MINI_HEIGHT)
                self.assertFalse(load_settings_section("lodgazilla", settings)["ai_panel_expanded"])
                panel.mini_row.click()
                self.app.processEvents()
                self.assertEqual(window.left_splitter.sizes()[1], panel.EXPANDED_MIN_HEIGHT)
                self.assertTrue(load_settings_section("lodgazilla", settings)["ai_panel_expanded"])
                # Using a drop box opens the AI conversation, which takes half of the left side.
                panel.attach_for_plugin([])
                self.app.processEvents()
                navigator_height, panel_height = window.left_splitter.sizes()
                self.assertLessEqual(abs(navigator_height - panel_height), 2)
            finally:
                window.close()
                self.app.processEvents()

    def test_start_options(self):
        arguments = parse_arguments(["--default-settings", "--no-plugins", "data"])
        self.assertTrue(arguments.default_settings and arguments.no_plugins)
        self.assertEqual(arguments.data_folder, Path("data"))
        plain = parse_arguments([])
        self.assertFalse(plain.default_settings or plain.no_plugins)
        self.assertIsNone(plain.data_folder)
        # The start command parses its options before checking dependencies or importing Qt.
        result = subprocess.run([sys.executable, "-m", "src.app.lodgazilla", "--help"],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--default-settings", result.stdout)
        self.assertIn("--no-plugins", result.stdout)

    def test_new_plugin_defaults_preserve_sources_and_startup_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            settings.write_text(json.dumps({"plugins": {"sources": ["https://github.com/owner"],
                                                       "check_at_startup": False, "enabled": {}}}))
            manager = mock.Mock(descriptors=[SimpleNamespace(plugin_id="new_plugin")])
            window = SimpleNamespace(settings_path=settings, plugin_manager=manager, plugin_updates=mock.Mock())
            LODGaZillaWindow.reload_plugin_settings(window)
            saved = json.loads(settings.read_text())["plugins"]
            self.assertEqual(saved["sources"], ["https://github.com/owner"])
            self.assertFalse(saved["check_at_startup"])
            self.assertFalse(saved["enabled"]["new_plugin"])
            LODGaZillaWindow.check_plugin_updates_at_startup(window)
            window.plugin_updates.check.assert_not_called()
            saved["check_at_startup"] = True
            settings.write_text(json.dumps({"plugins": saved}))
            LODGaZillaWindow.check_plugin_updates_at_startup(window)
            window.plugin_updates.check.assert_called_once_with(["https://github.com/owner"])
