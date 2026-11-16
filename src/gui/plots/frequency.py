"""Asynchronous, in-memory spectrogram card using the shared time axis."""

from dataclasses import asdict, replace
from pathlib import Path
from threading import Event

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, QSize, QThread, QTimer, Signal
from PySide6.QtGui import QActionGroup, QColor, QFontMetrics
from PySide6.QtWidgets import QColorDialog, QDialog, QInputDialog, QMenu, QToolButton

from src.data.frequency import FFTSettings, FFT_WINDOW_SIZES, colour_values
from src.gui.plots.base import PlotBaseWidget, apply_view_range_state, series_legend_name, view_range_state
from src.gui.plots.card_markers import TimeCardMarkers
from src.gui.plots.frequency_data import FrequencyData, load_frequency_data
from src.gui.plots.frequency_settings import FrequencySettingsDialog
from src.gui.plots.icons import plot_control_icon
from src.gui.plots.interactive import InteractivePlotWidget
from src.gui.plots.legend import LegendEntry, PlotLegendWidget
from src.gui.plots.marker_info import MarkerInfoEntry
from src.gui.plots.markers import FIRST_MARKER, SECOND_MARKER
from src.gui.plots.value_boxes import ValueBoxItem, ValueBoxLayer
from src.gui.plots.selection_info import SelectionDurationInfo
from src.gui.plots.series import TimeAxisMetadata, _display_time_axis_metadata
from src.gui.plots.series_hover import install_series_name_hover
from src.gui.plots.theme import apply_plot_palette


# Cancelled reads may finish after their card closes; retain their threads only.
_LIVE_WORKERS: set[QThread] = set()


def ordinal(number: int) -> str:
    """1st, 2nd, 3rd, 4th, ..., 11th, 12th, 13th, ..., 21st."""
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


class FrequencyLoader(QThread):
    def __init__(self, revision, entry, settings, time_range):
        super().__init__()
        self.revision = revision
        self.entry, self.settings, self.time_range = entry, settings, time_range
        self.cancelled = Event()
        self.result: FrequencyData | None = None
        self.error = ""

    def run(self):
        try:
            self.result = load_frequency_data(*self.entry, self.settings, self.time_range, self.cancelled)
        except Exception as error:  # Return worker failures to the card.
            self.error = str(error)


class FrequencyCanvas(InteractivePlotWidget):
    # Right click places the second marker; the FFT menu is on the card's header button.
    def contextMenuEvent(self, event):
        event.accept()


class FrequencyLegend(PlotLegendWidget):
    """The card's one series, as on other cards; its color is the strongest-frequency line's."""

    def __init__(self, plot: "FrequencyPlotWidget") -> None:
        super().__init__(plot.plot_widget)
        self.plot = plot
        self.color_requested.connect(lambda _key: plot.choose_peak_line_color())
        self.remove_requested.connect(plot.remove_series_at)
        self.move_requested.connect(plot.remove_series_at)

    def show_series(self) -> None:
        plot = self.plot
        self.set_entries([LegendEntry(0, series_legend_name(*plot.series[0]), plot.peak_line_color, plot.series[0])]
                         if plot.series else [])


class FrequencyPlotWidget(TimeCardMarkers, PlotBaseWidget):
    help_topic = "frequency"
    accepts_series_drop = True

    marker_placed = Signal(float)
    marker_cleared = Signal()
    second_marker_placed = Signal(float)
    second_marker_cleared = Signal()
    time_axis_representation_changed = Signal(str)
    plot_status_changed = Signal(str)
    data_ready = Signal()

    def __init__(self, h5_path: Path, series_name: str, parent=None):
        super().__init__(h5_path, series_name, parent)
        self.settings = FFTSettings()
        self.show_peak_line = True
        self.peak_line_color = "#ffffff"
        self.peak_line_threshold = 0.0
        self._peak_times = self._peak_frequencies = self._peak_amplitudes = np.empty(0)
        self._marker_info_x = {FIRST_MARKER: None, SECOND_MARKER: None}
        self.time_axis_metadata: TimeAxisMetadata | None = None
        self._display_metadata: TimeAxisMetadata | None = None
        self.data: FrequencyData | None = None
        self.data_time_range = None
        self._full_time_range = None
        self._nyquist = None
        self._worker: FrequencyLoader | None = None
        self._pending = None
        self._revision = 0
        self._disposed = self._applying = False
        self._initial = True
        self._reset_frequency_view = True
        self.plot_widget = FrequencyCanvas(self)
        self.plot_item = self.plot_widget.getPlotItem()
        self.plot_item.setLabel("left", "Frequency", units="Hz")
        # The time axis' ticks name the time, as on Line cards; a label would add an SI multiplier.
        self.plot_item.setLabel("bottom", "")
        self.plot_item.disableAutoRange()
        self.image = pg.ImageItem(axisOrder="row-major")
        self.plot_item.addItem(self.image)
        self.peak_line = pg.PlotDataItem(pen=pg.mkPen(self.peak_line_color, width=2), connect="finite")
        self.peak_line.setZValue(self.image.zValue() + 1)
        self.plot_item.addItem(self.peak_line, ignoreBounds=True)
        self.colour_bar = pg.ColorBarItem(values=(-80, 0), colorMap=pg.colormap.get("viridis"), interactive=False)
        self.colour_bar.setImageItem(self.image, insert_in=self.plot_item)
        # The colour bar's label, in full and short, and the one that fits its height.
        self._colour_bar_labels = ("Amplitude", "")
        self.colour_bar.axis.geometryChanged.connect(self._fit_colour_bar_label)
        # The last message sent to the app log, so a zoom that changes nothing logs nothing.
        self._logged_status: str | None = None
        self.value_boxes = ValueBoxLayer(self.plot_widget)
        self.selection_info = SelectionDurationInfo(self.plot_widget, self.value_boxes, lambda: self.time_axis_metadata)
        self.content_layout.addWidget(self.plot_widget, stretch=1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._request_data)
        self.plot_view_box().sigXRangeChanged.connect(self._range_changed)
        self.plot_widget.scroll_requested.connect(self.wheel_scrolled)
        self.plot_widget.marker_placed.connect(self.marker_placed)
        self.plot_widget.marker_cleared.connect(self.marker_cleared)
        self.plot_widget.time_axis_representation_changed.connect(self.time_axis_representation_changed)
        self.fft_settings_button = QToolButton(self)
        self.fft_settings_button.setToolTip("FFT settings")
        self.fft_settings_button.setAccessibleName("FFT settings")
        self.fft_settings_button.setAutoRaise(True)
        self.fft_settings_button.setFixedSize(self.close_button.size())
        self.fft_settings_button.setIconSize(QSize(18, 18))
        self.fft_settings_button.clicked.connect(
            lambda: self.show_fft_menu(self.fft_settings_button.mapToGlobal(self.fft_settings_button.rect().bottomLeft()))
        )
        self.header_controls.append(self.fft_settings_button)
        self.plot_widget.second_marker_placed.connect(self.second_marker_placed)
        self.plot_widget.second_marker_cleared.connect(self.second_marker_cleared)
        self.plot_widget.marker_snap_resolver = self.nearest_time
        # The series is named in the Legend; the plot area starts right below the header row.
        self.legend = FrequencyLegend(self)
        install_series_name_hover(self.plot_widget.viewport(), self.series_name_at)
        self.apply_theme()
        self.on_series_changed()

    def series_name_at(self, position):
        """The series under a canvas position, inside the plot area, for the hover name."""
        if not self.series or self.data is None:
            return None
        scene = self.plot_widget.mapToScene(position)
        return series_legend_name(*self.series[0]) if self.plot_view_box().sceneBoundingRect().contains(scene) else None

    def on_series_changed(self):
        if not self.series:
            return
        self.legend.show_series()
        self._initial = True
        self._reset_frequency_view = True
        self.data = None
        self._full_time_range = None
        self._nyquist = None
        self._display_metadata = None
        self.time_axis_metadata = None
        self.hide_marker_data()
        self.image.clear()
        self.peak_line.clear()
        self._range_changed()

    def workspace_state(self):
        return {**super().workspace_state(), "fft_settings": asdict(self.settings),
                "peak_line": {"visible": self.show_peak_line, "color": self.peak_line_color,
                              "threshold": self.peak_line_threshold},
                "view": view_range_state(self.plot_view_box())}

    def restore_workspace_state(self, state):
        try:
            self.settings = FFTSettings(**state.get("fft_settings", {}))
        except (TypeError, ValueError):
            pass
        peak = state.get("peak_line", {})
        self.set_peak_line_visible(peak.get("visible", self.show_peak_line))
        self.peak_line_color = str(peak.get("color", self.peak_line_color))
        self.peak_line.setPen(pg.mkPen(self.peak_line_color, width=2))
        self.legend.show_series()
        try:
            self.set_peak_line_threshold(peak.get("threshold", self.peak_line_threshold))
        except (TypeError, ValueError):
            pass
        super().restore_workspace_state(state)
        if state.get("view"):
            # The saved view replaces the first load's fit to the whole recording.
            self._initial = self._reset_frequency_view = False
            apply_view_range_state(self.plot_view_box(), state["view"])

    def add_series(self, h5_path, series_name):
        # One signal per amplitude heatmap; a dropped signal replaces its source.
        self.series = [(Path(h5_path), str(series_name))]
        self.on_series_changed()

    def insert_series(self, h5_path, series_name, index):
        # A drop on the Legend replaces the source too.
        self.add_series(h5_path, series_name)

    def _range_changed(self, *_args):
        if self._disposed or self._applying:
            return
        self._revision += 1
        self._pending = None
        if self._worker is not None:
            self._worker.cancelled.set()
        self._timer.start()

    def _request_data(self):
        if self._disposed or not self.series:
            return
        time_range = None if self._initial else tuple(self.plot_view_box().viewRange()[0])
        if self.data_time_range is not None:
            time_range = self.data_time_range if time_range is None else (
                max(time_range[0], self.data_time_range[0]), min(time_range[1], self.data_time_range[1]),
            )
            if time_range[1] <= time_range[0]:
                self.image.clear()
                self.peak_line.clear()
                self.data = None
                self.hide_marker_data()
                self._log_status("The visible range is outside the selected data range.")
                return
        self._pending = (self._revision, self.series[0], self.settings, time_range)
        if self._worker is None:
            self._start_pending()

    def _start_pending(self):
        if self._disposed or self._pending is None:
            return
        worker = FrequencyLoader(*self._pending)
        self._pending = None
        self._worker = worker
        _LIVE_WORKERS.add(worker)
        worker.finished.connect(self._worker_finished)
        worker.finished.connect(lambda current=worker: _LIVE_WORKERS.discard(current))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _worker_finished(self):
        worker = self._worker
        self._worker = None
        if worker is not None and not self._disposed and worker.revision == self._revision:
            if worker.error:
                self.image.clear()
                self.peak_line.clear()
                self.data = None
                self.hide_marker_data()
                self._log_status(worker.error)
            elif worker.result is not None:
                self._show_data(worker.result)
        self._start_pending()

    def _show_data(self, data):
        self._applying = True
        first_load = self._initial
        try:
            self.data = data
            self._peak_times, self._peak_frequencies, self._peak_amplitudes = data.strongest_signal()
            self._render_peak_line()
            self._full_time_range = data.full_time_range
            self._nyquist = data.sample_rate / 2
            self.time_axis_metadata = data.metadata
            self.set_time_axis_display_metadata(self._display_metadata or data.metadata)
            self._render_amplitude()
            band = data.frequency_range or (0, data.sample_rate / 2)
            self.plot_view_box().setLimits(yMin=band[0], yMax=band[1])
            if self._initial:
                self.plot_view_box().setRange(xRange=data.full_time_range, yRange=band, padding=0)
                self._initial = False
            elif self._reset_frequency_view:
                self.plot_view_box().setYRange(*band, padding=0)
            self._reset_frequency_view = False
        finally:
            self._applying = False
        for index, x_value in self._marker_info_x.items():
            if x_value is not None:
                self.show_marker_data(x_value, index)
        if first_load:
            self.data_ready.emit()

    def _render_amplitude(self):
        if self.data is None:
            return
        data = self.data
        pixels, levels = colour_values(data.amplitude, self.settings)
        self.image.setImage(pixels, autoLevels=False, levels=levels)
        df = data.sample_rate / data.window_size
        self.image.setRect(QRectF(data.time_edges[0], data.first_frequency_hz - df / 2,
                                 data.time_edges[1] - data.time_edges[0], data.amplitude.shape[0] * df))
        self.colour_bar.setLevels(levels)
        unit = data.unit or "unit"
        self._colour_bar_labels = ((f"Amplitude (dB re 1 {unit})", "dB") if self.settings.decibels
                                   else (f"Amplitude ({unit})", unit))
        self._fit_colour_bar_label()
        status = (f"{data.sample_rate:g} samples/s · FFT {data.window_size} · Bin spacing {df:g} Hz"
                  f" · Window {1000 * data.window_size / data.sample_rate:g} ms")
        if self.settings.target_bin_hz is not None and df > self.settings.target_bin_hz * (1 + 1e-9):
            status += f" · Target {self.settings.target_bin_hz:g} Hz not reached (available samples or FFT size limit)"
        if data.window_stride > 1:
            status += f" · Overview: every {ordinal(data.window_stride)} window; zoom for more detail"
        if data.skipped_windows:
            status += f" · {data.skipped_windows} windows with gaps or irregular timing omitted"
        self._log_status(status)

    def _log_status(self, status: str) -> None:
        """Send the card's FFT details or problem to the app log, once per change."""
        if status == self._logged_status:
            return
        self._logged_status = status
        name = series_legend_name(*self.series[0]) if self.series else "Frequency"
        self.plot_status_changed.emit(f"Frequency — {name}: {status}")

    def _fit_colour_bar_label(self):
        """The full label when it fits along the colour bar, otherwise the short one, so a low card's
        label doesn't run into the header."""
        full, short = self._colour_bar_labels
        axis = self.colour_bar.axis
        fits = QFontMetrics(axis.label.font()).horizontalAdvance(full) <= axis.geometry().height()
        text = full if fits else short
        if axis.labelText != text:
            axis.setLabel(text)

    def set_fft_settings(self, settings):
        old = self.settings
        self.settings = settings
        if (old.frequency_min_hz, old.frequency_max_hz) != (settings.frequency_min_hz, settings.frequency_max_hz):
            self._reset_frequency_view = True
        if replace(old, decibels=settings.decibels, dynamic_range_db=settings.dynamic_range_db) == settings:
            self._render_amplitude()
        else:
            self._range_changed()

    def build_fft_menu(self):
        menu = QMenu(self)
        menu.fft_submenus = []
        band_action = menu.addAction("Frequency range and resolution…", self.show_frequency_settings)
        band_action.setEnabled(self._nyquist is not None)
        def choices(title, field, options):
            submenu = menu.addMenu(title)
            group = QActionGroup(submenu)
            menu.fft_submenus.append((submenu, group))
            group.setExclusive(True)
            for label, value in options:
                action = submenu.addAction(label)
                action.setCheckable(True)
                action.setChecked(getattr(self.settings, field) == value and
                                  (field != "window_size" or self.settings.target_bin_hz is None))
                group.addAction(action)
                action.triggered.connect(lambda _checked=False, field=field, value=value: self._set_fft_option(field, value))
        choices("FFT window size", "window_size", [(str(size), size) for size in FFT_WINDOW_SIZES])
        choices("Overlap", "overlap", [(f"{value * 100:g}%", value) for value in (0.0, 0.5, 0.75, 0.875)])
        choices("Window function", "window", [(name, name) for name in ("Hann", "Hamming", "Rectangular")])
        choices("Amplitude scale", "decibels", [("Decibels", True), ("Linear", False)])
        choices("Colour range", "dynamic_range_db", [(f"{value} dB", value) for value in (40, 60, 80, 100, 120)])
        remove_mean = menu.addAction("Remove mean (DC)")
        remove_mean.setCheckable(True)
        remove_mean.setChecked(self.settings.remove_mean)
        remove_mean.toggled.connect(lambda checked: self.set_fft_settings(replace(self.settings, remove_mean=checked)))
        menu.addSeparator()
        peak = menu.addAction("Show strongest frequency")
        peak.setCheckable(True)
        peak.setChecked(self.show_peak_line)
        peak.toggled.connect(self.set_peak_line_visible)
        menu.addAction("Strongest-frequency line colour…", self.choose_peak_line_color)
        unit = self.data.unit if self.data is not None else ""
        threshold = menu.addAction(
            f"Strongest-frequency line threshold ({self.peak_line_threshold:g} {unit})…",
            self.choose_peak_line_threshold,
        )
        threshold.setEnabled(self.data is not None)
        menu.addSeparator()
        menu.addAction("Full frequency range", self.full_frequency_range)
        menu.addAction("Fit time and frequency", self.fit_view)
        # Retain QAction wrappers too: PySide can otherwise destroy a submenu
        # when a temporary menu.actions() list is released by a caller.
        menu.fft_actions = menu.actions()
        return menu

    def set_peak_line_visible(self, visible):
        self.show_peak_line = bool(visible)
        self.peak_line.setVisible(self.show_peak_line)

    def choose_peak_line_color(self):
        color = QColorDialog.getColor(QColor(self.peak_line_color), self, "Strongest-frequency line colour")
        if color.isValid():
            self.peak_line_color = color.name()
            self.peak_line.setPen(pg.mkPen(color, width=2))
            self.legend.show_series()

    def choose_peak_line_threshold(self):
        if self.data is None:
            return
        unit = self.data.unit or "source units"
        value, accepted = QInputDialog.getDouble(
            self, "Strongest-frequency line threshold",
            f"Minimum peak amplitude ({unit}).\n0 disables the threshold; applies in linear units even on a dB plot.",
            self.peak_line_threshold, 0.0, 1e15, 9,
        )
        if accepted:
            self.set_peak_line_threshold(value)

    def set_peak_line_threshold(self, value):
        value = float(value)
        if not np.isfinite(value) or value < 0:
            raise ValueError("Peak-line threshold must be finite and non-negative.")
        self.peak_line_threshold = value
        self._render_peak_line()

    def _render_peak_line(self):
        if self.data is None:
            self.peak_line.clear()
            return
        # Filter only the overlay; keep the peak values available for markers.
        frequencies = np.where(self._peak_amplitudes >= self.peak_line_threshold,
                               self._peak_frequencies, np.nan)
        # Extend to the outer pixel edges so even one FFT window has a trace.
        # NaNs break connections at missing or below-threshold windows.
        self.peak_line.setData(
            np.r_[self.data.time_edges[0], self._peak_times, self.data.time_edges[1]],
            np.r_[frequencies[0], frequencies, frequencies[-1]],
        )

    def _set_fft_option(self, field, value):
        changes = {field: value}
        if field == "window_size":
            changes["target_bin_hz"] = None
        self.set_fft_settings(replace(self.settings, **changes))

    def full_frequency_range(self):
        already_full = self.settings.frequency_min_hz == 0 and self.settings.frequency_max_hz is None
        self.set_fft_settings(replace(self.settings, frequency_min_hz=0.0, frequency_max_hz=None))
        if already_full and self._nyquist is not None:
            self.plot_view_box().setYRange(0, self._nyquist, padding=0)

    def show_frequency_settings(self):
        if self._nyquist is None:
            return
        dialog = FrequencySettingsDialog(self.settings, self._nyquist * 2, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_fft_settings(dialog.selected_settings())
        dialog.deleteLater()

    def show_fft_menu(self, position):
        menu = self.build_fft_menu()
        menu.exec(position)
        menu.deleteLater()

    def fit_view(self):
        if self._full_time_range is not None:
            band = (self.settings.frequency_min_hz, min(self.settings.frequency_max_hz or self._nyquist, self._nyquist))
            self.plot_view_box().setRange(xRange=self._full_time_range, yRange=band, padding=0)

    def plot_view_box(self):
        return self.plot_item.getViewBox()

    def set_time_axis_display_metadata(self, metadata):
        self._display_metadata = metadata
        self.plot_widget.configure_time_axis(**_display_time_axis_metadata(metadata).__dict__)

    def restore_own_time_axis_metadata(self):
        if self.time_axis_metadata is not None:
            self.set_time_axis_display_metadata(self.time_axis_metadata)

    def set_data_time_range(self, time_range):
        self.data_time_range = tuple(sorted(time_range)) if time_range is not None else None
        self._range_changed()

    def clear_vertical_marker(self, index=FIRST_MARKER):
        super().clear_vertical_marker(index)
        self.hide_marker_data(index)

    def nearest_time(self, x_value):
        if self.data is None:
            return x_value
        start, end = self.data.time_edges
        step = (end - start) / self.data.amplitude.shape[1]
        index = np.clip(round((x_value - start) / step - 0.5), 0, self.data.amplitude.shape[1] - 1)
        return start + (index + 0.5) * step

    def show_marker_data(self, x_value, index=FIRST_MARKER):
        self._marker_info_x[index] = x_value
        if self.data is None or not self.data.time_edges[0] <= x_value < self.data.time_edges[1]:
            self.value_boxes.clear(index)
            return
        data = self.data
        column = min(data.amplitude.shape[1] - 1, int((x_value - data.time_edges[0]) / (data.time_edges[1] - data.time_edges[0]) * data.amplitude.shape[1]))
        frequency = self._peak_frequencies[column]
        if not np.isfinite(frequency):
            self.value_boxes.clear(index)
            return
        amplitude = self._peak_amplitudes[column]
        text_color = self.plot_widget.palette().color(self.plot_widget.palette().ColorRole.Text).name()
        entry = MarkerInfoEntry(
            "Strongest frequency", text_color, f"{frequency:.9g} Hz",
            (f"Amplitude: {amplitude:.9g} {data.unit}".rstrip(),
             f"FFT bin spacing: {data.sample_rate / data.window_size:.9g} Hz",
             f"FFT window: {1000 * data.window_size / data.sample_rate:.9g} ms",
             "Peak within the selected frequency band."),
            full_name=series_legend_name(*self.series[0]) if self.series else "",
        )
        # The box sits where the strongest-frequency line crosses the marker.
        self.value_boxes.set_items(index, x_value, [ValueBoxItem("peak", entry, float(frequency))])

    def hide_marker_data(self, index=None):
        for marker in (FIRST_MARKER, SECOND_MARKER) if index is None else (index,):
            self._marker_info_x[marker] = None
            self.value_boxes.clear(marker)

    def apply_theme(self):
        apply_plot_palette(self.plot_widget)
        self.fft_settings_button.setIcon(plot_control_icon(self, "fft-settings"))
        palette = self.plot_widget.palette()
        self.colour_bar.axis.setTextPen(palette.color(palette.ColorRole.Text))
        self.value_boxes.apply_style()
        for index, x_value in self._marker_info_x.items():
            if x_value is not None:
                self.show_marker_data(x_value, index)

    def dispose(self):
        self._disposed = True
        self._timer.stop()
        self._pending = None
        if self._worker is not None:
            self._worker.cancelled.set()
            self._worker.finished.disconnect(self._worker_finished)
            self._worker = None

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)
