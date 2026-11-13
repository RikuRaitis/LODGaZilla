"""Line-plot implementation for LODGaZilla plot cards."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QLabel, QWidget

from src.gui.plots.base import (
    PlotBaseWidget, apply_view_range_state, series_legend_name, state_entry, view_range_state,
)
from src.gui.plots.card_markers import TimeCardMarkers
from src.gui.plots.legend import LegendEntry, PlotLegendWidget
from src.gui.plots.series import SeriesPlotWidget, TimeAxisMetadata


class LinePlotWidget(TimeCardMarkers, PlotBaseWidget):
    """An LOD-aware line plot hosted inside the reusable plot-card frame."""
    help_topic = "line"

    marker_placed = Signal(float)
    marker_cleared = Signal()
    second_marker_placed = Signal(float)
    second_marker_cleared = Signal()
    time_axis_representation_changed = Signal(str)
    plot_status_changed = Signal(str)
    series_plot_class = SeriesPlotWidget
    accepts_series_drop = True
    supports_draw_style = True

    def apply_draw_style(self, style: str) -> None:
        self.series_plot.set_draw_style(style)

    # The series plot keys colors by source, so inserting, removing or
    # reordering series keeps their colors; inserted series get an unused one.

    def _apply_series_order(self, order: list[int]) -> None:
        view = self.plot_view_box()
        ranges = view.viewRange()
        super()._apply_series_order(order)
        self.legend.set_entries(self.series, self.series_plot.series_colors())
        view.setRange(xRange=ranges[0], yRange=ranges[1], padding=0)

    def __init__(
        self, h5_path: Path, series_name: str, parent=None, *, settings_path: str | Path | None = None
    ) -> None:  # type: ignore[no-untyped-def]
        super().__init__(h5_path, series_name, parent)
        self._last_status = ""
        self.series_plot = self.series_plot_class(self, settings_path=settings_path)
        self.series_plot.status_changed.connect(self._on_plot_status_changed)
        self.series_plot.load_failed.connect(self._show_load_error)
        self.series_plot.plot_widget.scroll_requested.connect(
            lambda delta: self.wheel_scrolled.emit(delta)
        )
        self.series_plot.plot_widget.marker_placed.connect(lambda x_value: self.marker_placed.emit(x_value))
        self.series_plot.plot_widget.marker_cleared.connect(lambda: self.marker_cleared.emit())
        self.series_plot.plot_widget.second_marker_placed.connect(lambda x_value: self.second_marker_placed.emit(x_value))
        self.series_plot.plot_widget.second_marker_cleared.connect(lambda: self.second_marker_cleared.emit())
        self.series_plot.plot_widget.time_axis_representation_changed.connect(
            self.time_axis_representation_changed
        )
        self.error_label = QLabel(self)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        self.content_layout.addWidget(self.series_plot, stretch=1)
        self.content_layout.addWidget(self.error_label)
        self.legend = FloatingLegend(self.series_plot.plot_widget, self)
        self.on_series_changed()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if hasattr(self, "legend"):
            self.legend.reposition()

    def apply_theme(self) -> None:
        self.series_plot.apply_theme()

    def on_series_changed(self) -> None:
        """Render the primary line with the established dynamic LOD widget.

        The base card retains all assigned series.  Multiple overlaid line
        rendering is intentionally the next extension of this plot type;
        today the first assigned series is the plotted primary data source.
        """
        if not self.series:
            self.series_plot.show_series_entries([])
            self.error_label.hide()
            self.legend.hide()
            return
        try:
            self.series_plot.show_series_entries(self.series)
        except (OSError, KeyError, ValueError) as error:
            self._show_load_error(str(error))
        else:
            self.error_label.hide()
            self.legend.set_entries(self.series, self.series_plot.series_colors())
            self.legend.updateGeometry()
            self.legend.update()
            self.series_plot.plot_widget.update()
            # A series drop is handled while Qt is still dispatching its drag
            # event. Reposition once that event has completed as well.
            QTimer.singleShot(0, self.legend, self.legend.reposition)

    def _show_load_error(self, error: str) -> None:
        if self.series:
            self.error_label.setText(f"Could not plot {self.series[0][1]}: {error}")
            self.error_label.show()

    def workspace_state(self) -> dict:
        return {**super().workspace_state(), "colors": self.series_plot.series_colors(),
                "view": view_range_state(self.plot_view_box())}

    def restore_workspace_state(self, state: dict) -> None:
        entries = [state_entry(entry) for entry in state.get("series", [])]
        self.series_plot.set_entry_colors(dict(zip(entries, state.get("colors", []))))
        super().restore_workspace_state(state)
        apply_view_range_state(self.plot_view_box(), state.get("view"))

    def _on_plot_status_changed(self, status: str) -> None:
        """Forward only meaningful plot status changes to the application log."""
        if status == self._last_status:
            return
        self._last_status = status
        names = ", ".join(name for _path, name in self.series)
        self.plot_status_changed.emit(f"Plot [{names}]: {status}")

    @property
    def current_status(self) -> str:
        """Most recent status, used when a newly-created plot is connected."""
        return self._last_status

    def plot_view_box(self):  # type: ignore[no-untyped-def]
        return self.series_plot.plot_item.getViewBox()

    def marker_canvas(self):  # type: ignore[no-untyped-def]
        return self.series_plot.plot_widget

    def marker_value_boxes(self):  # type: ignore[no-untyped-def]
        return self.series_plot.value_boxes

    def show_marker_data(self, x_value: float, index: int = 0) -> None:
        self.series_plot.show_marker_values(x_value, index)

    def hide_marker_data(self, index: int | None = None) -> None:
        self.series_plot.hide_marker_values(index)

    def dispose(self) -> None:
        self.series_plot.dispose()

    def set_data_time_range(self, time_range: tuple[float, float] | None) -> None:
        self.series_plot.set_data_time_range(time_range)

    def time_axis_metadata(self) -> TimeAxisMetadata | None:
        return self.series_plot.time_axis_metadata

    def set_time_axis_display_metadata(self, metadata: TimeAxisMetadata) -> None:
        self.series_plot.set_time_axis_display_metadata(metadata)

    def restore_own_time_axis_metadata(self) -> None:
        self.series_plot.restore_own_time_axis_metadata()

    def reload_plot_settings(self) -> None:
        self.series_plot.reload_plot_settings()

class FloatingLegend(PlotLegendWidget):
    """Overlay legend with per-series color controls."""

    def __init__(self, plot_widget: QWidget, line_plot: LinePlotWidget) -> None:
        super().__init__(plot_widget)
        self.line_plot = line_plot
        self.color_requested.connect(self.choose_color)
        self.remove_requested.connect(self.remove_series)
        self.move_requested.connect(self.remove_series)

    def set_entries(self, entries: list[tuple[Path, str]], colors: list[str]) -> None:
        super().set_entries([
            LegendEntry(index, series_legend_name(h5_path, series_name), color, (h5_path, series_name))
            for index, ((h5_path, series_name), color) in enumerate(zip(entries, colors))
        ])

    def choose_color(self, index: int) -> None:
        colors = self.line_plot.series_plot.series_colors()
        if not 0 <= index < len(colors):
            return
        selected = QColorDialog.getColor(QColor(colors[index]), self, "Series color")
        if not selected.isValid():
            return
        self.line_plot.series_plot.set_series_color(index, selected.name())
        self.set_entries(self.line_plot.series, self.line_plot.series_plot.series_colors())

    def remove_series(self, index: int) -> None:
        self.line_plot.remove_series_at(index)
