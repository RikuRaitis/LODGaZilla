"""Standalone host window for the reusable series plot widget."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMainWindow

from src.gui.plots.series import SeriesPlotWidget


class SeriesPlotWindow(QMainWindow):
    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setWindowTitle("Series plot")
        self.resize(960, 620)
        self.plot_widget = SeriesPlotWidget(self)
        self.setCentralWidget(self.plot_widget)

    def show_timing_delta(self, h5_path: str | Path, series_name: str) -> None:
        data = self.plot_widget.show_timing_delta(h5_path, series_name)
        self.setWindowTitle(f"Timing delta plot - {data.title if data is not None else series_name}")

    def show_series(self, h5_path: str | Path, series_name: str) -> None:
        data = self.plot_widget.show_series(h5_path, series_name)
        self.setWindowTitle(f"Series plot — {data.title if data is not None else series_name}")
