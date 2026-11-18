import os
import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from src.app.settings import (  # noqa: E402
    DEFAULT_HDF5_SETTINGS,
    DEFAULT_PLOT_SETTINGS,
    PLOT_HEIGHT_SETTINGS,
    NavigatorControlSettings,
    load_hdf5_settings,
    load_navigator_control_settings,
    load_navigator_diagnostics_settings,
    sanitize_navigator_diagnostics_settings,
    load_plot_settings,
    load_lodgazilla_settings,
    sanitize_lodgazilla_settings,
    load_settings_section,
    save_hdf5_settings,
    save_navigator_control_settings,
    save_settings_section,
    begin_app_settings_cache,
    end_app_settings_cache,
    resolve_app_settings_path,
)
from src.gui.settings_widget import SettingsEditorWidget  # noqa: E402
from src.gui.theme import apply_theme  # noqa: E402


class AppSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_local_debug_settings_take_precedence_over_committed_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertEqual(resolve_app_settings_path(root), root / "app_settings.json")
            (root / "app_settings_debug.json").write_text("{}", encoding="utf-8")
            self.assertEqual(resolve_app_settings_path(root), root / "app_settings_debug.json")
            # `--default-settings` tests the committed defaults even when debug settings exist.
            with mock.patch.dict(os.environ, {"LODGAZILLA_DEFAULT_SETTINGS": "1"}):
                self.assertEqual(resolve_app_settings_path(root), root / "app_settings.json")

    def test_defaults_include_hdf5_and_navigator_controls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"

            hdf5 = load_hdf5_settings(path)
            controls = load_navigator_control_settings(path)

        self.assertEqual(hdf5, DEFAULT_HDF5_SETTINGS)
        self.assertEqual(
            controls,
            NavigatorControlSettings(
                align_columns_of_different_type=False,
            ),
        )

    def test_plot_heights_migrate_shared_setting_and_validate_per_card_values(self):
        migrated = sanitize_lodgazilla_settings({"default_plot_height": 350})
        heights = migrated["default_plot_heights"]
        self.assertNotIn("default_plot_height", migrated)
        self.assertEqual(set(heights), set(PLOT_HEIGHT_SETTINGS))
        self.assertEqual(heights["event"], 130)
        # A legacy shared height is clamped to each card's own minimum.
        self.assertTrue(all(height == max(350, PLOT_HEIGHT_SETTINGS[key][2])
                            for key, height in heights.items() if key != "event"))
        validated = sanitize_lodgazilla_settings({
            "default_plot_height": 350,
            "default_plot_heights": {
                "line": 0, "multi": 9_000, "event": 440, "gnss": "bad", "gnss_3d": 80,
            },
        })["default_plot_heights"]
        self.assertEqual(validated["line"], 80)
        self.assertEqual(validated["multi"], 2_000)
        self.assertEqual(validated["event"], 440)
        self.assertEqual(validated["gnss"], 350)
        self.assertEqual(validated["gnss_3d"], PLOT_HEIGHT_SETTINGS["gnss_3d"][2])
        self.assertEqual(validated["video"], 350)
        self.assertEqual(
            sanitize_lodgazilla_settings({"default_plot_heights": None})["default_plot_heights"],
            {key: default for key, (_, default, _) in PLOT_HEIGHT_SETTINGS.items()},
        )

    def test_editor_migrates_and_saves_independent_plot_heights(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"
            path.write_text(json.dumps({"lodgazilla": {"default_plot_height": 360}}), encoding="utf-8")
            editor = SettingsEditorWidget(path)
            self.addCleanup(editor.deleteLater)
            self.assertEqual(editor.default_plot_height_spins["gnss"].value(), 360)
            self.assertEqual(editor.default_plot_height_spins["event"].value(), 130)
            expected = {}
            for index, (key, spin) in enumerate(editor.default_plot_height_spins.items()):
                expected[key] = 400 + index * 25
                spin.setValue(expected[key])
            editor.save()
            self.assertNotIn("default_plot_height", json.loads(path.read_text())["lodgazilla"])
            self.assertEqual(load_lodgazilla_settings(path)["default_plot_heights"], expected)
            self.assertEqual(
                load_lodgazilla_settings(Path(temp_dir) / "fresh.json")["default_plot_heights"],
                {key: default for key, (_, default, _) in PLOT_HEIGHT_SETTINGS.items()},
            )
            reopened = SettingsEditorWidget(path)
            self.addCleanup(reopened.deleteLater)
            self.assertEqual({key: spin.value() for key, spin in reopened.default_plot_height_spins.items()}, expected)

    def test_saving_sections_preserves_unrelated_sections(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"
            save_settings_section(path, "custom", {"keep": "me"})
            save_hdf5_settings(path, {"compression": "lzf", "chunk_rows": 1024})

            custom = load_settings_section("custom", path)
            hdf5 = load_hdf5_settings(path)

        self.assertEqual(custom, {"keep": "me"})
        self.assertEqual(hdf5["compression"], "lzf")
        self.assertEqual(hdf5["chunk_rows"], 1024)
        self.assertEqual(hdf5["lod_bucket_growth"], DEFAULT_HDF5_SETTINGS["lod_bucket_growth"])

    def test_active_cache_defers_the_file_write_until_shutdown_flush(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"
            begin_app_settings_cache(path)
            try:
                save_settings_section(path, "custom", {"deferred": True})
                self.assertFalse(path.exists())
                self.assertEqual(load_settings_section("custom", path), {"deferred": True})
                self.assertTrue(end_app_settings_cache(path))
                self.assertEqual(load_settings_section("custom", path), {"deferred": True})
            finally:
                end_app_settings_cache(path, flush=False)

    def test_navigator_control_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"

            save_navigator_control_settings(
                path,
                NavigatorControlSettings(
                    align_columns_of_different_type=True,
                ),
            )
            controls = load_navigator_control_settings(path)

        self.assertEqual(
            controls,
            NavigatorControlSettings(
                align_columns_of_different_type=True,
            ),
        )

    def test_new_settings_have_no_default_metadata_keys(self):
        from src.app.settings import load_default_info_metadata_settings
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            path.write_text("{}", encoding="utf-8")
            self.assertEqual(load_default_info_metadata_settings(path)["keys"], [])

    def test_committed_settings_are_the_code_defaults(self):
        # AGENTS.md: the committed app_settings.json uses the code defaults and never holds
        # machine- or user-specific values (which are left out, or empty).
        from src.app.settings import default_app_settings_data
        committed = json.loads((ROOT / "app_settings.json").read_text(encoding="utf-8"))
        defaults = default_app_settings_data()
        left_out = {("default_info_metadata",), ("plugins", "enabled"), ("navigator_controls", "recent_folders"),
                    ("lodgazilla", "recent_workspaces"), ("tree_columns",)}
        differences = []

        def compare(saved, default, path=()):
            if path in left_out:
                return
            if isinstance(saved, dict) and isinstance(default, dict):
                for key in set(saved) | set(default):
                    if key in saved and key in default:
                        compare(saved[key], default[key], (*path, key))
                    elif (*path, key) not in left_out:
                        differences.append(".".join((*path, key)))
            elif saved != default:
                differences.append(".".join(path))

        compare(committed, defaults)
        self.assertEqual(sorted(differences), [])

    def test_recent_folders_do_not_persist_elided_display_text(self):
        full_path = r"C:\Users\example1\OneDrive - ExampleCo\Desktop\LOG\SensorRecordings\Data"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"
            save_navigator_control_settings(
                path,
                NavigatorControlSettings(
                    recent_folders=(
                        full_path,
                        r"C:\Users\exam...op\LOG\SensorRecordings\Data",
                    ),
                ),
            )
            controls = load_navigator_control_settings(path)

        self.assertEqual(controls.recent_folders, (full_path,))

    def test_recent_folders_keep_real_paths_with_three_dots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dotted = Path(temp_dir) / "run...2"
            dotted.mkdir()
            path = Path(temp_dir) / "app_settings.json"
            save_navigator_control_settings(path, NavigatorControlSettings(recent_folders=(str(dotted),)))
            controls = load_navigator_control_settings(path)
        self.assertEqual(controls.recent_folders, (str(dotted),))

    def test_settings_editor_uses_typed_controls_and_saves(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"
            editor = SettingsEditorWidget(path)

            editor.navigator_align_columns_checkbox.setChecked(True)
            editor.compression_combo.setCurrentText("none")
            editor.compression_level_spin.setValue(0)
            editor.chunk_rows_spin.setValue(4096)
            editor.lod_target_buckets_spin.setValue(500)
            editor.lod_bucket_growth_spin.setValue(8)
            editor.minimum_diagnostic_width_pixels_spin.setValue(9)
            editor.selected_series_line_width_spin.setValue(7)
            editor.track_selection_opacity_spin.setValue(60)
            editor.track_selection_layer_combo.setCurrentIndex(editor.track_selection_layer_combo.findData("own_trace"))
            editor.save()

            controls = load_navigator_control_settings(path)
            hdf5 = load_hdf5_settings(path)
            plot = load_plot_settings(path)

        self.assertEqual(
            controls,
            NavigatorControlSettings(
                align_columns_of_different_type=True,
            ),
        )
        self.assertEqual(hdf5["compression"], "none")
        self.assertEqual(hdf5["compression_level"], 0)
        self.assertEqual(hdf5["chunk_rows"], 4096)
        self.assertEqual(hdf5["lod_target_buckets"], 500)
        self.assertEqual(hdf5["lod_bucket_growth"], 8)
        self.assertEqual(plot["minimum_diagnostic_width_pixels"], 9)
        self.assertEqual(plot["selected_series_line_width"], 7)
        self.assertEqual(plot["track_selection_opacity_percent"], 60)
        self.assertEqual(plot["track_selection_layer"], "own_trace")

    def test_plot_settings_default_to_five_pixel_diagnostic_width(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = load_plot_settings(Path(temp_dir) / "app_settings.json")

        self.assertEqual(settings, DEFAULT_PLOT_SETTINGS)

    def test_track_selection_settings_are_validated(self):
        from src.app.settings import sanitize_plot_settings
        plot = sanitize_plot_settings({"track_selection_layer": "sideways", "track_selection_opacity_percent": 500,
                                       "track_selection_color": "not a color"})
        self.assertEqual((plot["track_selection_layer"], plot["track_selection_opacity_percent"],
                          plot["track_selection_color"]), ("always_top", 100, "#2196f3"))

    def test_diagnostic_settings_migrate_legacy_switch_before_defaults_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"
            path.write_text(json.dumps({"navigator_diagnostics": {
                "enabled": False,
                "sample_loss": {"color": "#123456", "bold": False, "italic": True},
                "both": {"color": "#abcdef"},
            }}), encoding="utf-8")
            settings = load_navigator_diagnostics_settings(path)
            self.assertFalse(settings["sample_loss"]["enabled"])
            self.assertFalse(settings["clipping"]["enabled"])
            self.assertEqual(settings["sample_loss"]["color"], "#123456")
            self.assertTrue(settings["sample_loss"]["italic"])
            self.assertNotIn("enabled", settings)
            self.assertNotIn("both", settings)
            # Reading customized settings must not change another navigator's defaults.
            defaults = load_navigator_diagnostics_settings(None)
            self.assertTrue(defaults["sample_loss"]["enabled"])
            self.assertEqual(defaults["sample_loss"]["color"], "#806b00")

    def test_diagnostic_priority_ignores_unknown_and_duplicate_entries(self):
        settings = sanitize_navigator_diagnostics_settings({"order": ["clipping", "both", "clipping"]})
        self.assertEqual(settings["order"], ["clipping", "sample_loss"])
        self.assertEqual(sanitize_navigator_diagnostics_settings({"order": None})["order"], ["sample_loss", "clipping"])

    def test_diagnostic_editor_picker_switches_and_priority_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"
            editor = SettingsEditorWidget(path)
            self.addCleanup(editor.close)
            color, bold, italic = editor.diagnostic_style_controls["clipping"]
            bold.setChecked(False)
            italic.setChecked(True)
            with mock.patch("src.gui.settings_widget.QColorDialog.getColor", return_value=QColor("#123456")):
                color.click()
            with mock.patch("src.gui.settings_widget.QColorDialog.getColor", return_value=QColor()):
                color.click()
            self.assertEqual(color.color(), "#123456")
            editor.diagnostic_priority_combo.setCurrentIndex(editor.diagnostic_priority_combo.findData("clipping"))
            editor.save()
            settings = load_navigator_diagnostics_settings(path)
            self.assertEqual(settings["order"], ["clipping", "sample_loss"])
            # On and off are the Navigator's buttons; the dialog keeps the saved state.
            self.assertEqual(settings["clipping"], {"enabled": True, "color": "#123456", "bold": False, "italic": True})
            self.assertTrue(settings["sample_loss"]["enabled"])
            self.assertNotIn("both", json.loads(path.read_text(encoding="utf-8"))["navigator_diagnostics"])
            reloaded = SettingsEditorWidget(path)
            self.addCleanup(reloaded.close)
            self.assertEqual(reloaded.diagnostic_priority_combo.currentData(), "clipping")
            self.assertTrue(reloaded.diagnostic_style_controls["clipping"][2].isChecked())
            self.assertEqual(reloaded.diagnostic_style_controls["clipping"][0].color(), "#123456")

    def test_dialog_has_the_six_tabs_and_keeps_settings_changed_elsewhere(self):
        from src.app.settings import (
            load_lodgazilla_settings,
            load_navigator_diagnostics_settings,
            save_settings_section,
        )
        from src.gui.settings_widget import TAB_TITLES

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"
            # Set with main-window buttons, not in the dialog.
            save_settings_section(path, "lodgazilla", {"show_legends": False, "time_axis_display": "bottom"})
            save_settings_section(path, "navigator_controls", {"hide_irrelevant_series": False})
            save_settings_section(path, "navigator_diagnostics", {"clipping": {"enabled": False}})
            editor = SettingsEditorWidget(path)
            self.addCleanup(editor.close)
            self.assertEqual(tuple(editor.tabs.tabText(i) for i in range(editor.tabs.count())), TAB_TITLES)
            # The AI settings are on their own tab, not on General.
            ai_tab = editor.tabs.widget(TAB_TITLES.index("AI"))
            general_tab = editor.tabs.widget(TAB_TITLES.index("General"))
            self.assertTrue(ai_tab.isAncestorOf(editor.ai_budget_spin))
            self.assertFalse(general_tab.isAncestorOf(editor.ai_budget_spin))
            editor.plot_navigator_color_buttons["frequency"].set_color("#010203")
            editor.legend_width_spin.setValue(40)
            editor.series_name_hover_delay_spin.setValue(1200)
            editor.save()
            lodgazilla = load_lodgazilla_settings(path)
            plot = load_plot_settings(path)
            controls = load_navigator_control_settings(path)
            diagnostics = load_navigator_diagnostics_settings(path)
        self.assertEqual((lodgazilla["show_legends"], lodgazilla["time_axis_display"]), (False, "bottom"))
        self.assertNotIn("show_card_controls", lodgazilla)
        self.assertEqual((plot["legend_width_percent"], plot["series_name_hover_delay_ms"]), (40, 1200))
        self.assertEqual(lodgazilla["plot_navigator_colors"]["frequency"], "#010203")
        self.assertFalse(controls.hide_irrelevant_series)
        self.assertFalse(diagnostics["clipping"]["enabled"])

    def test_consent_given_while_the_dialog_is_open_is_kept(self):
        from src.app.settings import load_ai_settings
        from src.gui.settings_widget import SettingsEditorWidget

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"
            save_settings_section(path, "ai", {**load_ai_settings(path), "codex_magic_drop_consent": True})
            editor = SettingsEditorWidget(path)
            self.addCleanup(editor.close)
            # The AI panel saves Claude's consent; the user unticks Codex's in the dialog.
            save_settings_section(path, "ai", {**load_ai_settings(path), "magic_drop_consent": True})
            editor.codex_magic_drop_consent_checkbox.setChecked(False)
            editor.save()
            saved = load_ai_settings(path)
        self.assertTrue(saved["magic_drop_consent"])
        self.assertFalse(saved["codex_magic_drop_consent"])

    def test_theme_preview_applies_immediately_and_is_saved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app_settings.json"
            editor = SettingsEditorWidget(path)
            editor.theme_combo.setCurrentText("Dark")
            editor.save()
            appearance = load_settings_section("appearance", path)

        self.assertEqual(
            QApplication.palette().color(QPalette.ColorRole.Window),
            QColor(45, 45, 45),
        )
        self.assertEqual(appearance["theme"], "dark")
        apply_theme("os")

    def test_light_theme_uses_dark_text_for_controls_and_placeholders(self):
        existing_widget = QWidget()
        apply_theme("dark")
        apply_theme("light")
        palette = QApplication.palette()

        self.assertEqual(palette.color(QPalette.ColorRole.Window), QColor(245, 245, 245))
        self.assertEqual(palette.color(QPalette.ColorRole.Base), QColor(255, 255, 255))
        self.assertEqual(
            existing_widget.palette().color(QPalette.ColorRole.Window),
            QColor(245, 245, 245),
        )

        for color_group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            for color_role in (
                QPalette.ColorRole.ButtonText,
                QPalette.ColorRole.WindowText,
                QPalette.ColorRole.PlaceholderText,
            ):
                self.assertLess(
                    palette.color(color_group, color_role).lightness(),
                    160,
                )
        stylesheet = QApplication.instance().styleSheet()
        self.assertEqual(stylesheet, "")
        self.assertEqual(QApplication.instance().style().objectName(), "fusion")

        apply_theme("os")


if __name__ == "__main__":
    unittest.main()
