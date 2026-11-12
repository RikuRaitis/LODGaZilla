"""Sample interval cards: the time between consecutive samples of each series."""

from __future__ import annotations

from pathlib import Path

from src.gui.plots.line import LinePlotWidget
from src.gui.plots.series import PlotSeriesData, SeriesPlotWidget, load_sample_interval_series


class SampleIntervalSeriesPlot(SeriesPlotWidget):
    """Plot every assigned series' sample intervals, in seconds, on a shared time axis."""

    value_axis_label = "Sample interval"
    statistics_quantity = "interval"
    # Marker values describe sample values, not intervals; the marker line still links views.
    shows_marker_values = False
    primary_plot_mode = "timing_delta"

    def show_series(self, h5_path: str | Path, series_name: str) -> PlotSeriesData | None:
        return self.show_timing_delta(h5_path, series_name)

    def _entry_loader(self, *, primary: bool):  # type: ignore[no-untyped-def]
        return load_sample_interval_series


class SampleIntervalPlotWidget(LinePlotWidget):
    """A Line-style card whose Y axis is the interval since each series' previous sample."""
    help_topic = "interval"

    series_plot_class = SampleIntervalSeriesPlot
