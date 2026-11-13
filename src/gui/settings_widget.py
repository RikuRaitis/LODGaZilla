"""Reusable editor for application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDoubleSpinBox,
    QLineEdit,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from src.app.settings import (
    AI_SECTION,
    APPEARANCE_SECTION,
    DEFAULT_APP_SETTINGS_PATH,
    DEFAULT_PLUGIN_SOURCES,
    DEFAULT_INFO_METADATA_SECTION,
    DEFAULT_HDF5_SETTINGS,
    HDF5_SECTION,
    NAVIGATOR_CONTROLS_SECTION,
    NAVIGATOR_DIAGNOSTICS_SECTION,
    PLOT_SECTION,
    PLOT_HEIGHT_SETTINGS,
    PLUGINS_SECTION,
    LODGAZILLA_SECTION,
    load_app_settings_data,
    sanitize_ai_settings,
    sanitize_default_info_metadata_settings,
    sanitize_hdf5_settings,
    sanitize_navigator_diagnostics_settings,
    sanitize_plot_settings,
    sanitize_lodgazilla_settings,
    save_app_settings_data,
)
from src.gui.plots.icons import plot_control_icon
from src.gui.theme import apply_theme, load_theme
from src.plugins.manager import discover_plugins
from src.plugins.installation import pending_requests, pending_status
from src.plugins.updates import PluginUpdates

TAB_TITLES = ("General", "Navigator", "Plot navigator", "Plot", "Plugins", "HDF5 Cache")
DEFAULT_METADATA_HELP = (
    "Metadata keys offered on every data folder, one per line, for example diver or place. "
    "Each appears as an editable entry under the folder's Info row and can be used in Group by, "
    "filters and columns. A value is written to the folder's _info.json only once you enter one."
)
DIAGNOSTIC_LABELS = (("sample_loss", "Sample loss"), ("clipping", "Clipping"))
# Plot navigator bar colors: setting key -> card type name.
PLOT_NAVIGATOR_BAR_LABELS = (
    ("line", "Line"), ("multi", "Multi-series"), ("event", "Event/state"), ("xy", "X/Y"),
    ("xyz", "3D plot"), ("gnss", "Map"), ("frequency", "Frequency"), ("interval", "Sample interval"),
    ("video", "Video"), ("other", "Other"),
)


class ColorButton(QPushButton):
    """A color swatch that opens a color picker when clicked."""

    color_changed = Signal(str)

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self._color = "#808080"
        self.setFixedHeight(22)
        self.setMinimumWidth(40)
        self.setAccessibleName(f"{name} color")
        self.clicked.connect(self.pick)
        self.set_color(self._color)

    def color(self) -> str:
        return self._color

    def set_color(self, value: str) -> None:
        color = QColor(value)
        if not color.isValid():
            return
        self._color = color.name(QColor.NameFormat.HexRgb)
        self.setToolTip(f"{self.name} color")
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self._color}; border: 1px solid palette(mid); border-radius: 3px; }}"
        )
        self.color_changed.emit(self._color)

    def pick(self) -> None:
        selected = QColorDialog.getColor(QColor(self._color), self, f"{self.name} color")
        if selected.isValid():
            self.set_color(selected.name(QColor.NameFormat.HexRgb))


class SettingsEditorWidget(QWidget):
    """Tabbed settings editor that maps known types to familiar controls."""

    # The Demo data group's Re-generate button; the main window confirms and runs it.
    demo_data_regenerate_requested = Signal()

    def __init__(
        self,
        settings_path: str | Path = DEFAULT_APP_SETTINGS_PATH,
        parent: QWidget | None = None,
        plugin_updates: PluginUpdates | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings_path = Path(settings_path)
        self.settings_data = load_app_settings_data(self.settings_path)
        self.plugin_updates = plugin_updates or PluginUpdates(self)
        if plugin_updates is None:
            self.destroyed.connect(self.plugin_updates.shutdown)

        self.navigator_align_columns_checkbox = QCheckBox()
        self.diagnostic_style_controls: dict[str, tuple[ColorButton, QCheckBox, QCheckBox]] = {}
        self.diagnostic_order = ["sample_loss", "clipping"]
        # Which highlight wins when a row has both sample loss and clipping.
        self.diagnostic_priority_combo = QComboBox()
        for name, label in DIAGNOSTIC_LABELS:
            self.diagnostic_priority_combo.addItem(label, name)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["OS Setting", "Light", "Dark"])
        self.theme_combo.currentTextChanged.connect(lambda _text: apply_theme(self.selected_theme()))

        self.compression_combo = QComboBox()
        self.compression_combo.addItems(["gzip", "lzf", "none"])
        self.compression_level_spin = integer_spinbox(0, 9)
        self.chunk_rows_spin = integer_spinbox(1, 10_000_000)
        self.chunk_rows_spin.setToolTip("Rows per HDF5 chunk")
        self.lod_target_buckets_spin = integer_spinbox(1, 1_000_000)
        self.lod_target_buckets_spin.setToolTip("Buckets in the coarsest LOD level")
        self.lod_bucket_growth_spin = integer_spinbox(2, 128)
        self.lod_bucket_growth_spin.setToolTip("Factor between LOD levels")
        self.minimum_diagnostic_width_pixels_spin = integer_spinbox(1, 100, " px")
        self.multi_selected_line_width_spin = integer_spinbox(1, 20, " px")
        self.multi_series_lane_height_spin = integer_spinbox(2, 50, " %")
        self.multi_filmstrip_height_spin = integer_spinbox(15, 240, " px")
        self.first_marker_color_button = ColorButton("First marker")
        self.second_marker_color_button = ColorButton("Second marker")
        self.selection_color_button = ColorButton("Selection")
        self.sync_marker_color_button = ColorButton("Video sync marker")
        self.marker_width_spin = integer_spinbox(1, 10, " px")
        self.marker_opacity_spin = integer_spinbox(10, 100, " %")
        self.selection_opacity_spin = integer_spinbox(5, 100, " %")
        # Default colors for new series, tracks and lanes, in the order they are used.
        self.series_color_buttons = [ColorButton(f"Series {number}") for number in range(1, 7)]
        self.values_box_opacity_spin = integer_spinbox(0, 100, " %")
        self.default_plot_height_spins = {
            key: integer_spinbox(minimum, 2_000, " px")
            for key, (_, _, minimum) in PLOT_HEIGHT_SETTINGS.items()
        }
        self.link_plot_time_format_checkbox = QCheckBox()
        self.show_plot_navigator_time_row_checkbox = QCheckBox()
        self.internal_log_maximum_lines_spin = integer_spinbox(100, 100_000)
        self.internal_log_maximum_lines_spin.setSingleStep(1000)
        self.plot_navigator_icon_size_spin = integer_spinbox(16, 48, " px")
        self.card_drop_selector_delay_spin = integer_spinbox(0, 10_000, " ms")
        self.card_drop_selector_delay_spin.setSingleStep(100)
        self.plot_navigator_color_buttons = {key: ColorButton(label) for key, label in PLOT_NAVIGATOR_BAR_LABELS}
        self.max_background_processes_spin = integer_spinbox(1, 8)
        self.default_info_metadata_edit = QPlainTextEdit()
        self.default_info_metadata_edit.setPlaceholderText("One metadata key per line")
        self.default_info_metadata_edit.setFixedHeight(96)
        self.plugin_checkboxes: dict[str, QCheckBox] = {}
        self.claude_path_edit = QLineEdit()
        self.claude_path_edit.setPlaceholderText("Found automatically on PATH or in VS Code")
        self.magic_drop_consent_checkbox = QCheckBox()
        self.codex_path_edit = QLineEdit()
        self.codex_path_edit.setPlaceholderText("Found automatically on PATH or in VS Code")
        self.codex_magic_drop_consent_checkbox = QCheckBox()
        self.claude_web_access_checkbox = QCheckBox()
        self.codex_web_access_checkbox = QCheckBox()
        self.ai_budget_spin = QDoubleSpinBox()
        self.ai_budget_spin.setRange(0.1, 100.0)
        self.ai_budget_spin.setDecimals(2)
        self.ai_budget_spin.setPrefix("$ ")
        self.ai_time_limit_spin = integer_spinbox(1, 120, " min")

        self.tabs = QTabWidget(self)
        for title, groups in (
            ("General", (self.appearance_group(), self.default_info_metadata_group(), self.application_group(),
                         self.ai_group(), self.demo_data_group())),
            ("Navigator", (self.navigator_group(), *self.navigator_diagnostics_groups())),
            ("Plot navigator", (self.plot_navigator_group(), self.plot_navigator_colors_group())),
            ("Plot", (self.markers_group(), self.series_colors_group(), self.plot_group(),
                      self.plot_heights_group())),
            ("Plugins", (self.plugins_group(),)),
            ("HDF5 Cache", (self.hdf5_group(),)),
        ):
            self.tabs.addTab(scrolling_page(groups), title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.load_values()

    # General

    def appearance_group(self) -> QGroupBox:
        group, layout = form_group("Appearance")
        layout.addRow("Theme", self.theme_combo)
        return group

    def default_info_metadata_group(self) -> QGroupBox:
        group, layout = form_group("Default metadata fields")
        field = QWidget()
        field_layout = QHBoxLayout(field)
        field_layout.setContentsMargins(0, 0, 0, 0)
        field_layout.addWidget(self.default_info_metadata_edit, 1)
        self.default_info_metadata_help = QToolButton()
        self.default_info_metadata_help.setAutoRaise(True)
        self.default_info_metadata_help.setIcon(plot_control_icon(self, "help"))
        self.default_info_metadata_help.setIconSize(QSize(18, 18))
        self.default_info_metadata_help.setToolTip("What are default metadata fields?")
        self.default_info_metadata_help.clicked.connect(self.show_default_metadata_help)
        field_layout.addWidget(self.default_info_metadata_help, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addRow("Keys (one per line)", field)
        return group

    def show_default_metadata_help(self) -> None:
        button = self.default_info_metadata_help
        QToolTip.showText(button.mapToGlobal(QPoint(0, button.height())), DEFAULT_METADATA_HELP, button)

    def application_group(self) -> QGroupBox:
        group, layout = form_group("Application")
        layout.addRow("Internal log length (lines)", self.internal_log_maximum_lines_spin)
        layout.addRow("Background processes (max)", self.max_background_processes_spin)
        return group

    def ai_group(self) -> QGroupBox:
        group, layout = form_group("AI")
        layout.addRow("Claude Code CLI", self.claude_path_edit)
        layout.addRow("Magic drop may send dropped files to Claude", self.magic_drop_consent_checkbox)
        layout.addRow("Claude may read web pages", self.claude_web_access_checkbox)
        layout.addRow("Codex CLI", self.codex_path_edit)
        layout.addRow("Magic drop may send dropped files to Codex", self.codex_magic_drop_consent_checkbox)
        layout.addRow("Codex may read web pages", self.codex_web_access_checkbox)
        layout.addRow("Magic drop cost limit (Claude)", self.ai_budget_spin)
        layout.addRow("Magic drop time limit", self.ai_time_limit_spin)
        return group

    def demo_data_group(self) -> QGroupBox:
        group, layout = form_group("Demo data")
        note = QLabel("Synthetic running workouts with videos, in the demodata folder. Re-generating "
                      "deletes the folder, including any changes made to it, and creates it again.")
        note.setWordWrap(True)
        layout.addRow(note)
        self.demo_data_regenerate_button = QPushButton("Re-generate demo data")
        self.demo_data_regenerate_button.clicked.connect(self.demo_data_regenerate_requested)
        layout.addRow(self.demo_data_regenerate_button)
        return group

    # Navigator

    def navigator_group(self) -> QGroupBox:
        group, layout = form_group("Navigator")
        layout.addRow("Align columns across row types", self.navigator_align_columns_checkbox)
        return group

    def navigator_diagnostics_groups(self) -> list[QGroupBox]:
        """How rows with sample loss or clipping are highlighted; on and off are Navigator buttons."""
        groups = []
        for name, label in DIAGNOSTIC_LABELS:
            group, layout = form_group(f"{label} highlight")
            color = ColorButton(label)
            bold = QCheckBox()
            italic = QCheckBox()
            layout.addRow("Text color", color)
            layout.addRow("Bold", bold)
            layout.addRow("Italic", italic)
            self.diagnostic_style_controls[name] = (color, bold, italic)
            groups.append(group)
        group, layout = form_group("Both on one row")
        layout.addRow("Highlight shown", self.diagnostic_priority_combo)
        groups.append(group)
        return groups

    # Plot navigator

    def plot_navigator_group(self) -> QGroupBox:
        group, layout = form_group("Plot navigator")
        layout.addRow("Show time row", self.show_plot_navigator_time_row_checkbox)
        layout.addRow("Icon size", self.plot_navigator_icon_size_spin)
        layout.addRow("Card drop selector delay", self.card_drop_selector_delay_spin)
        return group

    def plot_navigator_colors_group(self) -> QGroupBox:
        group, layout = form_group("Bar colors")
        for key, label in PLOT_NAVIGATOR_BAR_LABELS:
            layout.addRow(label, self.plot_navigator_color_buttons[key])
        return group

    # Plot

    def markers_group(self) -> QGroupBox:
        group, layout = form_group("Markers and selection")
        layout.addRow("First marker", self.first_marker_color_button)
        layout.addRow("Second marker", self.second_marker_color_button)
        layout.addRow("Marker width", self.marker_width_spin)
        layout.addRow("Marker opacity", self.marker_opacity_spin)
        layout.addRow("Selection", self.selection_color_button)
        layout.addRow("Selection opacity", self.selection_opacity_spin)
        layout.addRow("Values box opacity", self.values_box_opacity_spin)
        layout.addRow("Video sync marker", self.sync_marker_color_button)
        return group

    def series_colors_group(self) -> QGroupBox:
        group, layout = form_group("Series colors")
        note = QLabel("Used in this order for new series, tracks and lanes.")
        note.setWordWrap(True)
        layout.addRow(note)
        for number, button in enumerate(self.series_color_buttons, start=1):
            layout.addRow(f"Series {number}", button)
        return group

    def plot_group(self) -> QGroupBox:
        group, layout = form_group("Cards")
        layout.addRow("Same time format on all time cards", self.link_plot_time_format_checkbox)
        layout.addRow("Minimum warning band width", self.minimum_diagnostic_width_pixels_spin)
        layout.addRow("Multi-series: selected trace width", self.multi_selected_line_width_spin)
        layout.addRow("Multi-series: lane height", self.multi_series_lane_height_spin)
        layout.addRow("Multi-series: filmstrip height", self.multi_filmstrip_height_spin)
        return group

    def plot_heights_group(self) -> QGroupBox:
        group, layout = form_group("Default card heights")
        note = QLabel("For new cards; open cards keep their size.")
        note.setWordWrap(True)
        layout.addRow(note)
        for key, (label, _, _) in PLOT_HEIGHT_SETTINGS.items():
            spin = self.default_plot_height_spins[key]
            spin.setAccessibleName(f"{label} default height")
            layout.addRow(label, spin)
        return group

    # Plugins

    def plugins_group(self) -> QGroupBox:
        group = QGroupBox("Plugins")
        layout = QVBoxLayout(group)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.plugin_sources_edit = QPlainTextEdit()
        self.plugin_sources_edit.setPlaceholderText(
            "https://github.com/OWNER\nhttps://bitbucket.org/WORKSPACE"
        )
        self.plugin_sources_edit.setFixedHeight(70)
        self.plugin_sources_edit.setToolTip("GitHub account and Bitbucket workspace URLs, one per line")
        form.addRow("Plugin sources", self.plugin_sources_edit)
        self.plugin_startup_check = QCheckBox("Check for new plugins and updates at startup")
        form.addRow(self.plugin_startup_check)
        self.plugin_check_button = QPushButton("Check now")
        self.plugin_check_button.clicked.connect(lambda: self.plugin_updates.check(
            [line.strip() for line in self.plugin_sources_edit.toPlainText().splitlines() if line.strip()]
        ))
        form.addRow(self.plugin_check_button)
        layout.addLayout(form)
        note = QLabel("Only use sources you trust. Private repositories use your Git credentials. "
                      "Install and Update take effect after restarting LODGaZilla, and do not depend on "
                      "Apply or Cancel; Apply saves the sources and the enabled plugins. A plugin "
                      "added to the plugin folder meanwhile, such as one made with Create plugin, works "
                      "as soon as it is enabled; one that needs new Python packages gets them when "
                      "LODGaZilla next starts.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.plugin_rows_layout = QVBoxLayout()
        layout.addLayout(self.plugin_rows_layout)
        self.plugin_status = QLabel()
        self.plugin_status.setWordWrap(True)
        self.plugin_errors = QPlainTextEdit()
        self.plugin_errors.setReadOnly(True)
        self.plugin_errors.setMaximumHeight(100)
        layout.addWidget(self.plugin_status)
        layout.addWidget(self.plugin_errors)
        self.plugin_descriptors = discover_plugins(self.plugin_updates.root)
        self.plugin_updates.changed.connect(self.refresh_plugin_rows)
        self.refresh_plugin_rows()
        return group

    def refresh_plugin_rows(self):
        enabled = {key: checkbox.isChecked() for key, checkbox in self.plugin_checkboxes.items()}
        while self.plugin_rows_layout.count():
            item = self.plugin_rows_layout.takeAt(0)
            item.widget().deleteLater()
        self.plugin_checkboxes.clear()
        installed_rows = {}
        pending = {path.parent.name: pending_status(path) for path in pending_requests(self.plugin_updates.root)}
        for descriptor in self.plugin_descriptors:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            checkbox = QCheckBox(descriptor.title)
            checkbox.setChecked(enabled.get(descriptor.plugin_id, False))
            if descriptor.error:
                checkbox.setEnabled(False)
                checkbox.setToolTip(descriptor.error)
            self.plugin_checkboxes[descriptor.plugin_id] = checkbox
            row_layout.addWidget(checkbox)
            row_layout.addStretch(1)
            status = QLabel(pending[descriptor.plugin_id] if descriptor.plugin_id in pending else
                            descriptor.error or f"Installed {descriptor.version}")
            status.setWordWrap(True)
            row_layout.addWidget(status)
            installed_rows[descriptor.plugin_id] = (row_layout, status)
            self.plugin_rows_layout.addWidget(row)
        for candidate, action, message in self.plugin_updates.rows:
            if candidate.plugin_id in installed_rows:
                row_layout, status = installed_rows.pop(candidate.plugin_id)
                status.setText(message)
            else:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(QLabel(candidate.title))
                row_layout.addStretch(1)
                status = QLabel(message)
                status.setWordWrap(True)
                row_layout.addWidget(status)
                self.plugin_rows_layout.addWidget(row)
            status.setToolTip(candidate.url + "\n" + candidate.commit)
            if action:
                button = QPushButton(action)
                button.setToolTip(candidate.url)
                button.setEnabled(not self.plugin_updates.busy)
                button.clicked.connect(lambda _checked=False, c=candidate: self.plugin_updates.install(c))
                row_layout.addWidget(button)
        if not self.plugin_descriptors and not self.plugin_updates.rows:
            self.plugin_rows_layout.addWidget(QLabel("No plugins found"))
        self.plugin_status.setText(self.plugin_updates.message)
        self.plugin_errors.setPlainText("\n".join(self.plugin_updates.errors))
        self.plugin_errors.setVisible(bool(self.plugin_updates.errors))
        self.plugin_check_button.setEnabled(not self.plugin_updates.busy)

    # HDF5 Cache

    def hdf5_group(self) -> QGroupBox:
        group, layout = form_group("HDF5 Cache")
        layout.addRow("Compression", self.compression_combo)
        layout.addRow("Compression level", self.compression_level_spin)
        layout.addRow("Chunk rows", self.chunk_rows_spin)
        layout.addRow("LOD target buckets", self.lod_target_buckets_spin)
        layout.addRow("LOD bucket growth", self.lod_bucket_growth_spin)
        return group

    # Loading and saving

    def load_values(self) -> None:
        self.theme_combo.setCurrentText(theme_label(load_theme(self.settings_path)))
        navigator_controls = section_mapping(self.settings_data, NAVIGATOR_CONTROLS_SECTION)
        self.navigator_align_columns_checkbox.setChecked(
            bool(navigator_controls.get("align_columns_of_different_type", False))
        )
        diagnostic_settings = sanitize_navigator_diagnostics_settings(
            section_mapping(self.settings_data, NAVIGATOR_DIAGNOSTICS_SECTION)
        )
        self.diagnostic_order = list(diagnostic_settings["order"])
        self.diagnostic_priority_combo.setCurrentIndex(
            self.diagnostic_priority_combo.findData(self.diagnostic_order[0])
        )
        for name, (color, bold, italic) in self.diagnostic_style_controls.items():
            style = diagnostic_settings[name]
            color.set_color(str(style["color"]))
            bold.setChecked(bool(style["bold"]))
            italic.setChecked(bool(style["italic"]))

        ai_settings = sanitize_ai_settings(section_mapping(self.settings_data, AI_SECTION))
        self.claude_path_edit.setText(ai_settings["claude_path"])
        self.magic_drop_consent_checkbox.setChecked(ai_settings["magic_drop_consent"])
        self.codex_path_edit.setText(ai_settings["codex_path"])
        self.codex_magic_drop_consent_checkbox.setChecked(ai_settings["codex_magic_drop_consent"])
        self.claude_web_access_checkbox.setChecked(ai_settings["claude_web_access"])
        self.codex_web_access_checkbox.setChecked(ai_settings["codex_web_access"])
        self.ai_budget_spin.setValue(ai_settings["max_budget_usd"])
        self.ai_time_limit_spin.setValue(ai_settings["time_limit_minutes"])
        hdf5_settings = sanitize_hdf5_settings(section_mapping(self.settings_data, HDF5_SECTION))
        self.compression_combo.setCurrentText(str(hdf5_settings["compression"]))
        self.compression_level_spin.setValue(int(hdf5_settings["compression_level"]))
        self.chunk_rows_spin.setValue(int(hdf5_settings["chunk_rows"]))
        self.lod_target_buckets_spin.setValue(int(hdf5_settings["lod_target_buckets"]))
        self.lod_bucket_growth_spin.setValue(int(hdf5_settings["lod_bucket_growth"]))
        plot_settings = sanitize_plot_settings(section_mapping(self.settings_data, PLOT_SECTION))
        self.multi_selected_line_width_spin.setValue(int(plot_settings["multi_selected_line_width"]))
        self.multi_series_lane_height_spin.setValue(int(plot_settings["multi_series_lane_height_percent"]))
        self.multi_filmstrip_height_spin.setValue(int(plot_settings["multi_filmstrip_height_pixels"]))
        self.first_marker_color_button.set_color(str(plot_settings["first_marker_color"]))
        self.second_marker_color_button.set_color(str(plot_settings["second_marker_color"]))
        self.marker_width_spin.setValue(int(plot_settings["marker_width"]))
        self.marker_opacity_spin.setValue(int(plot_settings["marker_opacity_percent"]))
        self.selection_color_button.set_color(str(plot_settings["selection_color"]))
        self.sync_marker_color_button.set_color(str(plot_settings["sync_marker_color"]))
        self.selection_opacity_spin.setValue(int(plot_settings["selection_opacity_percent"]))
        self.values_box_opacity_spin.setValue(int(plot_settings["values_box_opacity_percent"]))
        colors = plot_settings["series_colors"]
        for index, button in enumerate(self.series_color_buttons):
            if index < len(colors):
                button.set_color(colors[index])
        self.minimum_diagnostic_width_pixels_spin.setValue(
            int(plot_settings["minimum_diagnostic_width_pixels"])
        )
        lodgazilla_settings = sanitize_lodgazilla_settings(
            section_mapping(self.settings_data, LODGAZILLA_SECTION)
        )
        for key, spin in self.default_plot_height_spins.items():
            spin.setValue(lodgazilla_settings["default_plot_heights"][key])
        self.link_plot_time_format_checkbox.setChecked(
            bool(lodgazilla_settings["link_plot_time_format"])
        )
        self.show_plot_navigator_time_row_checkbox.setChecked(
            bool(lodgazilla_settings["show_plot_navigator_time_row"])
        )
        self.internal_log_maximum_lines_spin.setValue(
            int(lodgazilla_settings["internal_log_maximum_lines"])
        )
        self.plot_navigator_icon_size_spin.setValue(int(lodgazilla_settings["plot_navigator_icon_size"]))
        self.card_drop_selector_delay_spin.setValue(int(lodgazilla_settings["card_drop_selector_delay_ms"]))
        for name, button in self.plot_navigator_color_buttons.items():
            button.set_color(str(lodgazilla_settings["plot_navigator_colors"][name]))
        self.max_background_processes_spin.setValue(
            int(lodgazilla_settings["max_background_processes"])
        )
        default_info_metadata = sanitize_default_info_metadata_settings(
            section_mapping(self.settings_data, DEFAULT_INFO_METADATA_SECTION)
        )
        self.default_info_metadata_edit.setPlainText("\n".join(default_info_metadata["keys"]))
        plugin_settings = section_mapping(self.settings_data, PLUGINS_SECTION)
        sources = plugin_settings.get("sources", DEFAULT_PLUGIN_SOURCES)
        self.plugin_sources_edit.setPlainText("\n".join(s for s in sources if isinstance(s, str)) if isinstance(sources, list) else "")
        self.plugin_startup_check.setChecked(bool(plugin_settings.get("check_at_startup", True)))
        enabled_plugins = plugin_settings.get("enabled", {})
        enabled_plugins = enabled_plugins if isinstance(enabled_plugins, dict) else {}
        for plugin_id, checkbox in self.plugin_checkboxes.items():
            checkbox.setChecked(bool(enabled_plugins.get(plugin_id, False)))

    def settings_from_ui(self) -> dict[str, Any]:
        """The saved settings with this dialog's fields applied.

        Each section starts from its saved values, so settings changed only in the
        main window (Navigator and Plot navigator buttons, layout state) are kept.
        """
        data = load_app_settings_data(self.settings_path)
        data[APPEARANCE_SECTION] = {"theme": self.selected_theme()}
        data[NAVIGATOR_CONTROLS_SECTION] = {
            **section_mapping(data, NAVIGATOR_CONTROLS_SECTION),
            "align_columns_of_different_type": self.navigator_align_columns_checkbox.isChecked(),
        }
        saved_diagnostics = sanitize_navigator_diagnostics_settings(section_mapping(data, NAVIGATOR_DIAGNOSTICS_SECTION))
        first = self.diagnostic_priority_combo.currentData()
        data[NAVIGATOR_DIAGNOSTICS_SECTION] = sanitize_navigator_diagnostics_settings({
            "order": [first, *(name for name in self.diagnostic_order if name != first)],
            **{
                name: {
                    # Switched on and off with the Navigator's Sample loss and Clipping buttons.
                    "enabled": saved_diagnostics[name]["enabled"],
                    "color": color.color(),
                    "bold": bold.isChecked(),
                    "italic": italic.isChecked(),
                }
                for name, (color, bold, italic) in self.diagnostic_style_controls.items()
            },
        })
        # A consent the AI panel saved while this dialog was open is kept unless changed here.
        shown_ai = sanitize_ai_settings(section_mapping(self.settings_data, AI_SECTION))
        saved_ai = sanitize_ai_settings(section_mapping(data, AI_SECTION))

        def consent(name: str, checkbox: QCheckBox) -> bool:
            checked = checkbox.isChecked()
            return saved_ai[name] if checked == shown_ai[name] else checked

        data[AI_SECTION] = sanitize_ai_settings({
            "claude_path": self.claude_path_edit.text(),
            "magic_drop_consent": consent("magic_drop_consent", self.magic_drop_consent_checkbox),
            "codex_path": self.codex_path_edit.text(),
            "codex_magic_drop_consent": consent("codex_magic_drop_consent", self.codex_magic_drop_consent_checkbox),
            "claude_web_access": self.claude_web_access_checkbox.isChecked(),
            "codex_web_access": self.codex_web_access_checkbox.isChecked(),
            "max_budget_usd": self.ai_budget_spin.value(),
            "time_limit_minutes": self.ai_time_limit_spin.value(),
        })
        data[HDF5_SECTION] = sanitize_hdf5_settings({
            "compression": self.compression_combo.currentText(),
            "compression_level": self.compression_level_spin.value(),
            "chunk_rows": self.chunk_rows_spin.value(),
            "lod_target_buckets": self.lod_target_buckets_spin.value(),
            "lod_bucket_growth": self.lod_bucket_growth_spin.value(),
        })
        data[PLOT_SECTION] = sanitize_plot_settings({
            **section_mapping(data, PLOT_SECTION),
            "minimum_diagnostic_width_pixels": self.minimum_diagnostic_width_pixels_spin.value(),
            "multi_selected_line_width": self.multi_selected_line_width_spin.value(),
            "multi_series_lane_height_percent": self.multi_series_lane_height_spin.value(),
            "multi_filmstrip_height_pixels": self.multi_filmstrip_height_spin.value(),
            "first_marker_color": self.first_marker_color_button.color(),
            "second_marker_color": self.second_marker_color_button.color(),
            "marker_width": self.marker_width_spin.value(),
            "marker_opacity_percent": self.marker_opacity_spin.value(),
            "selection_color": self.selection_color_button.color(),
            "sync_marker_color": self.sync_marker_color_button.color(),
            "selection_opacity_percent": self.selection_opacity_spin.value(),
            "values_box_opacity_percent": self.values_box_opacity_spin.value(),
            "series_colors": [button.color() for button in self.series_color_buttons],
        })
        data[LODGAZILLA_SECTION] = sanitize_lodgazilla_settings({
            **section_mapping(data, LODGAZILLA_SECTION),
            "default_plot_heights": {
                key: spin.value() for key, spin in self.default_plot_height_spins.items()
            },
            "link_plot_time_format": self.link_plot_time_format_checkbox.isChecked(),
            "show_plot_navigator_time_row": self.show_plot_navigator_time_row_checkbox.isChecked(),
            "internal_log_maximum_lines": self.internal_log_maximum_lines_spin.value(),
            "plot_navigator_icon_size": self.plot_navigator_icon_size_spin.value(),
            "card_drop_selector_delay_ms": self.card_drop_selector_delay_spin.value(),
            "plot_navigator_colors": {
                name: button.color() for name, button in self.plot_navigator_color_buttons.items()
            },
            "max_background_processes": self.max_background_processes_spin.value(),
        })
        data[DEFAULT_INFO_METADATA_SECTION] = sanitize_default_info_metadata_settings({
            "keys": self.default_info_metadata_edit.toPlainText().splitlines(),
        })
        previous_plugins = section_mapping(data, PLUGINS_SECTION)
        previous_enabled = previous_plugins.get("enabled", {})
        enabled = dict(previous_enabled) if isinstance(previous_enabled, dict) else {}
        enabled.update({
            plugin_id: checkbox.isChecked()
            for plugin_id, checkbox in self.plugin_checkboxes.items()
        })
        data[PLUGINS_SECTION] = {
            **previous_plugins,
            "enabled": enabled,
            "sources": [line.strip() for line in self.plugin_sources_edit.toPlainText().splitlines() if line.strip()],
            "check_at_startup": self.plugin_startup_check.isChecked(),
        }
        return data

    def selected_theme(self) -> str:
        return {"OS Setting": "os", "Light": "light", "Dark": "dark"}.get(
            self.theme_combo.currentText(), "os"
        )

    def save(self) -> None:
        save_app_settings_data(self.settings_path, self.settings_from_ui())


class SettingsDialog(QDialog):
    """Dialog wrapper for the settings editor widget."""

    settings_applied = Signal()
    demo_data_regenerate_requested = Signal()

    def __init__(
        self,
        settings_path: str | Path = DEFAULT_APP_SETTINGS_PATH,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._original_theme = load_theme(settings_path)
        self.editor = SettingsEditorWidget(settings_path, self, getattr(parent, "plugin_updates", None))
        self.editor.demo_data_regenerate_requested.connect(self.demo_data_regenerate_requested)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        if apply_button is not None:
            apply_button.clicked.connect(self.save_settings)

        layout = QVBoxLayout(self)
        layout.addWidget(self.editor, 1)
        layout.addWidget(buttons)
        # Just wide enough to show every tab.
        margins = layout.contentsMargins()
        tab_bar = self.editor.tabs.tabBar().sizeHint().width()
        self.resize(tab_bar + margins.left() + margins.right() + 24, 640)

    def accept(self) -> None:
        self.save_settings()
        super().accept()

    def save_settings(self) -> None:
        self.editor.save()
        # Cancel after Apply must restore the theme that is now saved.
        self._original_theme = self.editor.selected_theme()
        self.settings_applied.emit()

    def reject(self) -> None:
        apply_theme(self._original_theme)
        super().reject()


def integer_spinbox(minimum: int, maximum: int, suffix: str = "") -> QSpinBox:
    spinbox = QSpinBox()
    spinbox.setRange(minimum, maximum)
    spinbox.setSingleStep(1)
    spinbox.setSuffix(suffix)
    spinbox.setMinimumWidth(90)
    return spinbox


def form_group(title: str) -> tuple[QGroupBox, QFormLayout]:
    """A group box of label/field rows whose fields fill the width, with left-aligned labels."""
    group = QGroupBox(title)
    layout = QFormLayout(group)
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    return group, layout


def align_form_labels(page: QWidget) -> None:
    """Give every form label on a tab the same width, so all fields start in one column."""
    labels = []
    for form in page.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is not None and isinstance(item.widget(), QLabel):
                labels.append(item.widget())
    width = max((label.sizeHint().width() for label in labels), default=0)
    for label in labels:
        label.setFixedWidth(width)


def scrolling_page(groups) -> QScrollArea:  # type: ignore[no-untyped-def]
    page = QWidget()
    layout = QVBoxLayout(page)
    for group in groups:
        layout.addWidget(group)
    layout.addStretch(1)
    align_form_labels(page)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(page)
    return scroll


def section_mapping(data: dict[str, Any], section: str) -> dict[str, Any]:
    raw_section = data.get(section, {})
    if not isinstance(raw_section, dict):
        return dict(DEFAULT_HDF5_SETTINGS) if section == HDF5_SECTION else {}
    return raw_section


def theme_label(theme: str) -> str:
    return {"os": "OS Setting", "light": "Light", "dark": "Dark"}.get(theme, "OS Setting")
