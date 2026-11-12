"""LODGaZilla: navigator and a future-ready multi-plot workspace."""

from __future__ import annotations

from src.app.startup_profile import mark as startup_mark

startup_mark("module import started")

import argparse
import json
import logging
import multiprocessing
import os
import queue
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """The start options: python -m src.app.lodgazilla [--default-settings] [--no-plugins] [data_folder]."""
    parser = argparse.ArgumentParser(prog="python -m src.app.lodgazilla", description="LODGaZilla")
    parser.add_argument("data_folder", nargs="?", type=Path, default=None)
    parser.add_argument("--default-settings", action="store_true",
                        help="Use the committed app_settings.json instead of a local app_settings_debug.json.")
    parser.add_argument("--no-plugins", action="store_true",
                        help="Start without loading any plugin; their saved settings are kept.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    # Spawned workers import this as __mp_main__; only a real application launch
    # sets the start options, installs missing dependencies and activates pending
    # plugins, before importing potentially updated libraries.
    _arguments = parse_arguments()
    # The application logs to logs/ in the checkout, and so do its worker processes.
    from src.app.log_paths import APPLICATION_LOG_DIR, LOG_DIR_ENV
    os.environ.setdefault(LOG_DIR_ENV, str(APPLICATION_LOG_DIR))
    # Environment variables, so worker processes inherit the options. They must be
    # set before src.app.settings and src.plugins are imported.
    if _arguments.default_settings:
        os.environ["LODGAZILLA_DEFAULT_SETTINGS"] = "1"
    if _arguments.no_plugins:
        os.environ["LODGAZILLA_NO_PLUGINS"] = "1"
    from tools.setup_environment import run as setup_environment
    setup_result = setup_environment([])
    if setup_result:
        raise SystemExit(setup_result)
    # The splash screen, before the imports below; main() closes it once the window shows.
    from src.app.startup import start_application
    start_application()

import h5py
import numpy as np

startup_mark("standard and scientific imports completed")

# QT_FFMPEG_DEBUG=1 makes Qt's FFmpeg backend write a complete media/stream
# dump directly to stderr. That bypasses Qt's message handler and floods the
# PowerShell console. LODGaZilla logs normal Qt Multimedia messages through
# its internal logger instead, so this developer-only direct-output mode must
# remain disabled for the application process.
os.environ.pop("QT_FFMPEG_DEBUG", None)

from PySide6.QtCore import QEvent, QPoint, Qt, QtMsgType, QUrl, Signal, QTimer, qInstallMessageHandler
from PySide6.QtGui import QDesktopServices, QIcon, QImageReader, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QScrollArea,
    QSplitter,
    QWidget,
    QFileDialog,
    QInputDialog,
    QVBoxLayout,
)

startup_mark("Qt imports completed")

from src.app.settings import (
    AI_SECTION,
    DEFAULT_APP_SETTINGS_PATH,
    DEFAULT_PLUGIN_SOURCES,
    load_ai_settings,
    TIME_AXIS_DISPLAY_MODES,
    begin_app_settings_cache,
    end_app_settings_cache,
    flush_app_settings_cache,
    load_hdf5_settings,
    set_app_settings_changed_callback,
    PLUGINS_SECTION,
    PLOT_SECTION,
    LODGAZILLA_SECTION,
    load_settings_section,
    load_lodgazilla_settings,
    save_settings_section,
    sanitize_plot_settings,
    sanitize_plot_heights,
    sanitize_lodgazilla_settings,
)
from src.gui.navigator.widget import (
    DataNavigatorWidget,
    ITEM_PATH_ROLE,
    ITEM_TYPE_ROLE,
    ROW_TYPE_FOLDER,
    VIDEO_MIME_TYPE,
    IMAGE_MIME_TYPE,
)
from src.app.card_modules import CardModuleLoader, LazyAttribute, card_modules_loaded, loaded_class
from src.app.startup import start_application
from src.data.navigator_data import read_h5_navigator_data
from src.gui.plots.navigator import PlotNavigatorEntry, PlotNavigatorWidget
from src.gui.plots.markers import SECOND_MARKER, set_marker_style, set_selection_style, set_sync_marker_color
from src.gui.plots.base import set_series_colors
from src.gui.plots.legend import PlotLegendWidget, set_legends_shown
from src.app.internal_log import InternalLogger, InternalLogPanel, NativeStderrLogCapture
from src.gui.ai.panel import CREATE_PLUGIN, NOT_CONNECTED_REPLY, AIPanel
from src.gui.about_dialog import AboutDialog
from src.plugins.manager import PluginManager
from src.plugins.updates import PluginUpdates
from src.gui.plots.base import PlotBaseWidget, SERIES_MIME_TYPE, series_from_mime, state_entry
from src.gui.plots.workspace_layout import PlotDropController, PlotRow, PlotStackSplitter
from src.gui.plots.data_drop import DataDropController
from src.gui.settings_widget import SettingsDialog
from src.gui.theme import apply_theme, load_theme
from src.data.folder_info import attachment_path, is_info_file, read_info_file, video_attachment_path, write_info_file
from src.data.workspace_file import (
    UMW_FILE_FILTER, UMW_SUFFIX, WorkspaceFileError, read_workspace_file, updated_recent_workspaces,
    write_workspace_file,
)
from src.data.media import (
    AUDIO_FILE_FILTER, audio_output_path, has_audio_track, is_h5_file, is_media_h5, set_audio_offset,
)
from src.jobs.media_import import MediaItem, MediaJobRequest, run_media_process
from src.data.xyz import AxisSource
from src.data.plot_policy import (
    CARD_TITLES, GROUP_WIDTHS, PlotInput, PlotSource, SELECTION_MIME_TYPE, activation_uses_card, compatible_cards,
    plan_cards,
)
from src.gui.plots.open_menu import context_sources, populate_plot_menu
from src.jobs.plot_drop import SelectionInspection
from src.jobs.import_batch import MAX_IMPORT_PROCESSES, PROGRESS_TOTAL, configured_import_worker_count, run_reimport_batch
from src.jobs.import_coordinator import (
    ActiveImport,
    PluginJobRequest,
    ImportJobCoordinator,
    ImportRequest,
    import_scopes_overlap,
    path_is_relative_to,
    request_conflicts_with_active,
    request_operation,
)
from src.jobs.plugin import run_plugin_process
from src.app.branding import configure_application, lodgazilla_asset_path, version_text
from src.app.demo_data import DEMO_DATA_ROOT, DemoDataJob, demo_data_exists, demo_workspace, is_inside, uses_folder
from src.app.log_paths import log_path
from src.app.graphics import configure_graphics, shutdown_web_engine
from src.jobs.process_tree import start_process_tree
from src.jobs.magic_drop import MagicDropJob, SignInCheck
from src.jobs.plugin_chat import PluginChatJob
from src.ai.plugin_builder import check_plugin_id, plugin_id_from
from src.ai.providers import PROVIDERS, AgentProvider, provider_named
from src.jobs.cache_files import is_cache_output

# The plot cards and their helpers load in the background once the window is open
# (src/app/card_modules.py); these stand in for them until then.
LinePlotWidget = LazyAttribute("src.gui.plots.line", "LinePlotWidget")
MultiPlotWidget = LazyAttribute("src.gui.plots.multi", "MultiPlotWidget")
SampleIntervalPlotWidget = LazyAttribute("src.gui.plots.sample_interval", "SampleIntervalPlotWidget")
VideoPlotWidget = LazyAttribute("src.gui.plots.video", "VideoPlotWidget")
folder_start_unix_seconds = LazyAttribute("src.gui.plots.video.widget", "folder_start_unix_seconds")
ImagePlotWidget = LazyAttribute("src.gui.plots.image", "ImagePlotWidget")
GnssPlotWidget = LazyAttribute("src.gui.plots.gnss", "GnssPlotWidget")
Gnss3DPlotWidget = LazyAttribute("src.gui.plots.gnss_3d", "Gnss3DPlotWidget")
EventStatePlotWidget = LazyAttribute("src.gui.plots.event_state", "EventStatePlotWidget")
XYPlotWidget = LazyAttribute("src.gui.plots.xy", "XYPlotWidget")
XYZPlotWidget = LazyAttribute("src.gui.plots.xyz", "XYZPlotWidget")
FrequencyPlotWidget = LazyAttribute("src.gui.plots.frequency", "FrequencyPlotWidget")
set_value_box_opacity = LazyAttribute("src.gui.plots.value_boxes", "set_value_box_opacity")
install_offset_x_link = LazyAttribute("src.gui.plots.time_offset", "install_offset_x_link")
set_view_box_time_offset = LazyAttribute("src.gui.plots.time_offset", "set_view_box_time_offset")
_attribute_text = LazyAttribute("src.gui.plots.series", "_attribute_text")
_canonical_time_values = LazyAttribute("src.gui.plots.series", "_canonical_time_values")
_series_group_for_navigator_name = LazyAttribute("src.gui.plots.series", "_series_group_for_navigator_name")
_time_axis_metadata_from_group = LazyAttribute("src.gui.plots.series", "_time_axis_metadata_from_group")
load_time_axis_metadata = LazyAttribute("src.gui.plots.series", "load_time_axis_metadata")

if QApplication.instance() is None:
    # Before any QApplication, so cards using OpenGL and Qt WebEngine can load later.
    configure_graphics()

startup_mark("application imports completed")


README_PATH = Path(__file__).resolve().parents[2] / "README.md"
# Delay before deferred settings changes are written to disk, so a crash or a
# closed console window does not discard the whole session's changes.
SETTINGS_FLUSH_DELAY_MS = 1_000
SETTINGS_FLUSH_RETRY_MS = 10_000
SERIES_TIME_RANGE_CACHE_LIMIT = 4_096
_SERIES_TIME_RANGE_CACHE: dict[tuple[str, str, int, int], tuple[float, float] | None] = {}



def install_qt_message_logging(internal_logger: InternalLogger) -> None:
    """Send Qt library diagnostics, including Qt Multimedia/FFmpeg, to the app log."""

    def handle_qt_message(message_type, context, message) -> None:  # type: ignore[no-untyped-def]
        category = getattr(context, "category", "qt") or "qt"
        text = f"{category}: {message}"
        if message_type in {QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg}:
            internal_logger.warning(text)
        else:
            internal_logger.info(text)

    # Qt otherwise writes messages such as "qt.multimedia.ffmpeg: ..." to
    # stderr, which is the PowerShell window when the application is launched
    # from the supplied batch file. Keep a Python reference to the callback so
    # it remains installed for the application's lifetime.
    internal_logger._qt_message_handler = handle_qt_message  # type: ignore[attr-defined]
    qInstallMessageHandler(handle_qt_message)


@dataclass
class ProcessProgressState:
    folder: Path
    source_name: str
    started_at: float
    completed: int = 0
    total: int = PROGRESS_TOTAL
    eta_text: str = "..."
    finished: bool = False
    success: bool = False
    operation: str = "H5 cache"


def format_duration(seconds: float) -> str:
    whole_seconds = max(0, round(seconds))
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def duration_text_seconds(text: str) -> int | None:
    parts = text.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        values = [int(part) for part in parts]
    except ValueError:
        return None
    if len(values) == 2:
        return max(0, values[0] * 60 + values[1])
    return max(0, values[0] * 3600 + values[1] * 60 + values[2])


def combined_process_progress(processes: Iterable[tuple[str, int, int, str]]) -> tuple[int, int, str]:
    """Return progress for the active critical path, not summed worker work.

    Jobs in a folder execute concurrently.  Adding their independent 0--100%
    ranges makes the cell look like two serial progress bars.  The wall-clock
    completion time is instead controlled by the worker with the longest
    remaining estimate.  As workers report new estimates, the critical path
    can change without losing the other workers' contribution.
    """
    process_list = list(processes)
    if not process_list:
        return 0, PROGRESS_TOTAL, "..."
    incomplete = [
        item for item in process_list
        if max(0, min(int(item[1]), int(item[2]))) < max(1, int(item[2]))
        and str(item[3]) != "failed"
    ]
    if not incomplete:
        return PROGRESS_TOTAL, PROGRESS_TOTAL, "failed" if any(item[3] == "failed" for item in process_list) else "0:00"

    # A new worker has no throughput estimate until its first progress update.
    # Treat it as the critical path so the cell never overstates completion.
    def critical_path_key(item: tuple[str, int, int, str]) -> tuple[float, float]:
        completed, total = max(0, min(int(item[1]), int(item[2]))), max(1, int(item[2]))
        eta_seconds = duration_text_seconds(str(item[3]))
        return (float("inf") if eta_seconds is None else eta_seconds, 1 - completed / total)

    _source_name, completed, total, eta_text = max(incomplete, key=critical_path_key)
    total = max(1, int(total))
    completed = max(0, min(int(completed), total))
    return completed, total, str(eta_text)


def folder_multi_series_entries(folders: Iterable[Path]) -> tuple[list[tuple[Path, str]], list[str]]:
    """Collect plottable series of each folder's H5 files; runs in a worker thread."""
    entries: list[tuple[Path, str]] = []
    warnings: list[str] = []
    for folder in folders:
        try:
            paths = sorted(Path(folder).glob("*.h5"))
        except OSError as error:
            warnings.append(f"Could not list {folder}: {error}")
            continue
        for path in paths:
            data = read_h5_navigator_data(path)
            if data.error:
                warnings.append(f"Could not read {path}: {data.error}")
            entries.extend((path, series.navigator_name) for series in data.series
                           if series.visible and series.sample_count > 0)
    return entries, warnings


def h5_cache_logger() -> logging.Logger:
    """Logger used by spawned cache coordinators (outside the Qt process)."""
    logger = logging.getLogger("lodgazilla.h5_cache")
    if not logger.handlers:
        path = log_path("lodgazilla_h5_cache.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(processName)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    return logger


def run_reimport_process(
    data_folder_text: str,
    events,
    recursive: bool = False,
    process_limit: int = MAX_IMPORT_PROCESSES,
    enabled_plugins: dict[str, bool] | None = None,
    hdf5_settings: dict[str, object] | None = None,
    cancel_event=None,
    start_event=None,
) -> None:  # type: ignore[no-untyped-def]
    """Spawn-safe cache coordinator; it creates the bounded per-file workers."""
    if start_event is not None and not start_event.wait(30):
        return
    try:
        run_reimport_batch(
            Path(data_folder_text), events, recursive=recursive,
            logger=h5_cache_logger(), process_limit=process_limit,
            enabled_plugins=enabled_plugins, hdf5_settings=hdf5_settings,
            cancel_event=cancel_event,
        )
    except Exception as error:  # noqa: BLE001 - return worker failures to the GUI queue.
        h5_cache_logger().exception("H5 cache batch failed")
        events.put(("finished", 0, 0, str(error)))


# Cards with first and second markers and the selection between them.
TWO_MARKER_CARD_TYPES = (
    LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget, XYPlotWidget, GnssPlotWidget, XYZPlotWidget,
    Gnss3DPlotWidget,
)
# Cards with a time axis whose views can be linked.
TIME_CARD_TYPES = (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget)


def _has_empty_axis(card: PlotBaseWidget) -> bool:
    """Whether a grouped card has an unassigned axis in any legend group."""
    if not card.accepts_axis_drop:
        return False
    groups = card.grouped_series
    return any(groups.axis(key) is None for key in range(len(groups.groups) * groups.width))


def _attached_video_offset(folder: Path, video_path: Path, fallback: float) -> float:
    """A video's current sync offset from its folder's info file; it may have changed since saving."""
    try:
        video = next((entry for entry in read_info_file(folder).videos
                      if video_attachment_path(folder, entry) == video_path.resolve()), None)
        return float(video["offset_seconds"]) if video is not None else fallback
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return fallback


class PlotWorkspace(QScrollArea):
    """Scrollable stack of resizable plot cards."""

    plot_status_changed = Signal(str)
    plot_navigator_changed = Signal(object)
    video_offset_saved = Signal(Path)
    global_marker_changed = Signal(object)
    global_second_marker_changed = Signal(object)
    sync_marker_changed = Signal(object)
    plot_layout_changed = Signal()
    # The active card, or None when no card is active.
    active_plot_changed = Signal(object)
    # The linked time cards' visible (start, stop), or None when views are unlinked.
    linked_view_range_changed = Signal(object)

    def __init__(
        self,
        default_plot_heights: dict[str, int] | None = None,
        parent: QWidget | None = None,
        *,
        settings_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings_path = Path(settings_path) if settings_path is not None else None
        self.default_plot_heights = sanitize_plot_heights(default_plot_heights)
        self.link_x_axes = True
        # The first linked time card's view box, followed for the Plot navigator's view box.
        self._linked_view_box = None
        self._linked_view_plot: PlotBaseWidget | None = None
        self.link_time_axis_format = True
        self._synchronizing_time_axis_format = False
        self._y_axis_alignment_timer = QTimer(self)
        self._y_axis_alignment_timer.setSingleShot(True)
        self._y_axis_alignment_timer.setInterval(50)
        self._y_axis_alignment_timer.timeout.connect(self._align_line_plot_y_axes)
        # Video cards' Timeline sliders follow the time cards' plot-area edges.
        self._video_timeline_alignment_timer = QTimer(self)
        self._video_timeline_alignment_timer.setSingleShot(True)
        self._video_timeline_alignment_timer.setInterval(50)
        self._video_timeline_alignment_timer.timeout.connect(self.align_video_timelines)
        self.plot_layout_changed.connect(self._video_timeline_alignment_timer.start)
        self.link_markers = False
        self.snap_vertical_marker_to_nearest_point = False
        self.marker_info_display = "active"
        self.time_axis_display = "all"
        self.legends_shown = True
        self.card_controls_shown = True
        self._synchronizing_vertical_marker = False
        self._global_marker_timestamp: float | None = None
        self._global_second_marker_timestamp: float | None = None
        self._video_marker_info_source: VideoPlotWidget | None = None
        self._video_marker_info_timer = QTimer(self)
        self._video_marker_info_timer.setSingleShot(True)
        # Keep marker lines responsive at 10 Hz, but HDF5 popup reads are
        # intentionally less frequent while a video is playing.
        self._video_marker_info_timer.setInterval(250)
        self._video_marker_info_timer.timeout.connect(self._refresh_video_marker_info_boxes)
        # Sync anchors deliberately survive later video scrubbing.  They are
        # only updated by a marker explicitly placed in a data plot.
        self._last_data_marker_timestamp: float | None = None
        # The data time stored by a Video card's Set data marker, until that sync completes.
        self._sync_marker_timestamp: float | None = None
        self._plot_navigator_ranges: dict[PlotBaseWidget, tuple[float, float]] = {}
        self._plot_navigator_full_ranges: dict[PlotBaseWidget, tuple[float, float]] = {}
        self.active_plot: PlotBaseWidget | None = None
        self._clear_active_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._clear_active_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._clear_active_shortcut.setAutoRepeat(False)
        self._clear_active_shortcut.activated.connect(lambda: self.activate_plot(None))
        # Video cards show both markers and the selection on their Timeline slider.
        self.global_marker_changed.connect(lambda _timestamp: self._update_video_timelines())
        self.global_second_marker_changed.connect(lambda _timestamp: self._update_video_timelines())
        # The content must retain its own height; otherwise QScrollArea grows a
        # lone plot card to fill all available workspace space.
        self.setWidgetResizable(False)
        self.setAcceptDrops(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.viewport().setAcceptDrops(True)
        self.viewport().installEventFilter(self)
        self.splitter = PlotStackSplitter()
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setAcceptDrops(True)
        self.splitter.installEventFilter(self)
        self.splitter.splitterMoved.connect(self._update_workspace_minimum_height)
        self.splitter.resize_plot_requested.connect(self.resize_plot_at_index)
        self.setWidget(self.splitter)
        self.splitter.layout_changed.connect(self.plot_layout_changed)
        self._drop_controller = PlotDropController(self, self.splitter)
        self._drop_controller.drop_requested.connect(self._card_dropped)
        self._data_drop_controller = DataDropController(
            self, load_lodgazilla_settings(self.settings_path)["card_drop_selector_delay_ms"]
        )
        self._data_drop_controller.create_requested.connect(self.create_dropped_card)
        self._data_drop_controller.create_beside_requested.connect(self.create_dropped_card_beside)
        QTimer.singleShot(0, self._sync_content_width)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._sync_content_width()
        self.schedule_y_axis_alignment()
        self.plot_layout_changed.emit()

    def add_new_plot(self, h5_path: Path, series_name: str) -> PlotBaseWidget:
        # A single series always defaults to Multi, without waiting for metadata.
        source = PlotSource("series", Path(h5_path), series_name)
        return self.open_plot_inputs((PlotInput(source, ("multi",)),))[0]

    def open_plot_sources(self, sources, preferred=None, row_index=None, beside=None):
        SelectionInspection(self, sources, lambda items: self.open_plot_inputs(items, preferred, row_index, beside))

    def open_plot_inputs(self, items, preferred=None, row_index=None, beside=None):
        """Open cards at a row boundary, or beside a card as ``(card, "left"/"right")``."""
        anchor = None
        if row_index is not None and row_index < self.splitter.count():
            row = self.splitter.widget(row_index)
            anchor = next((plot for plot in self.plots() if self.splitter.row_for(plot) is row), None)
        plots = [self._open_card_plan(plan) for plan in plan_cards(items, preferred)]
        if anchor is not None:
            for plot in plots:
                self.move_plot(plot, anchor, "above")
        elif beside is not None and beside[0] in self.plots():
            target, position = beside
            neighbour = target
            for plot in plots:
                # Later cards follow the previous one; cards without room open below the row.
                if self.move_plot(plot, neighbour, position):
                    neighbour, position = plot, "right"
                else:
                    self.move_plot(plot, target, "below")
        return plots

    def _open_card_plan(self, plan):
        kind, sources = plan.kind, [item.source for item in plan.items]
        first = sources[0]
        if kind == "image":
            return self.add_image_plot(first.folder or first.path.parent, first.path)
        if kind == "video":
            return self.add_video_plot(first.folder or first.path.parent, first.path, first.offset)
        entries = [source.entry for source in sources if source.kind == "series"]
        if kind == "multi":
            return self.add_multi_plot(entries, [source for source in sources if source.kind == "video"])
        if kind == "interval":
            return self.add_sample_interval_plot(entries)
        if kind in GROUP_WIDTHS:
            width = GROUP_WIDTHS[kind]
            axes = [AxisSource(*entry) for entry in entries]
            initial = tuple(axes[:width]) + (None,) * max(0, width - len(axes))
            if kind == "xy":
                plot = self.add_xy_plot(entries[0], entries[1] if len(entries) > 1 else None)
            elif kind == "xyz":
                plot = self.add_xyz_plot(*(entries[:3] + [None] * max(0, 3 - len(entries))))
            else:
                factory = self.add_gnss_plot if kind == "gnss" else self.add_gnss_3d_plot
                plot = factory(*entries[0], initial_axes=initial)
            if len(axes) > width:
                plot.add_legend_group(axes[width:])
            return plot
        factory = {"line": self.add_line_plot, "event": self.add_event_state_plot,
                   "frequency": self.add_frequency_plot}[kind]
        plot = factory(*entries[0])
        if len(entries) > 1:
            plot.series = entries
            plot.on_series_changed()
        return plot

    def active_card_accepts(self, items):
        card = self.active_plot
        return (card is not None and card in self.plots()
                and (card.accepts_axis_drop or card.accepts_series_drop)
                and card.drop_card_type in compatible_cards(items))

    def plot_in_active_card(self, items):
        if self.active_card_accepts(items):
            self.add_plot_inputs_to_card(items, self.active_plot, fill_empty=True)
        else:
            self.open_plot_inputs(items)

    def activate_series(self, h5_path, series_name):
        source = PlotSource("series", Path(h5_path), series_name)
        SelectionInspection(self, [source], self.plot_activated_series)

    def plot_activated_series(self, items):
        """Use the active card only when it is the default card or awaits an axis."""
        card = self.active_plot
        if (card is not None and card in self.plots()
                and (card.accepts_axis_drop or card.accepts_series_drop)
                and activation_uses_card(items, card.drop_card_type, _has_empty_axis(card))):
            self.add_plot_inputs_to_card(items, card, fill_empty=True)
        else:
            self.open_plot_inputs(items)

    def add_plot_inputs_to_card(self, items, card, index=-1, *, fill_empty=False):
        plans = plan_cards(items, card.drop_card_type)
        if plans and plans[0].kind == card.drop_card_type:
            plan = plans.pop(0)
            entries = [item.source.entry for item in plan.items if item.source.kind != "video"]
            if isinstance(card, MultiPlotWidget):
                self._add_filmstrips(card, [item.source for item in plan.items if item.source.kind == "video"])
            if entries and card.accepts_axis_drop:
                axes = [AxisSource(*entry) for entry in entries]
                if fill_empty:
                    axes = card.grouped_series.fill_empty_axes(axes)
                if axes:
                    card.add_legend_group(axes, None if index < 0 else index)
            elif entries and card.drop_card_type == "frequency":
                card.add_series(*entries[0])
            elif entries:
                # Insert through the card so per-series colors stay attached
                # to their sources; each new source follows the previous one.
                index = len(card.series) if index < 0 else max(0, min(index, len(card.series)))
                for entry in dict.fromkeys(entries):
                    if entry not in card.series:
                        card.insert_series(*entry, index)
                        index += 1
            self._series_drop_finished(card)
        for plan in plans:
            self._open_card_plan(plan)

    def _register_plot(self, plot: PlotBaseWidget, *, plot_type: str) -> PlotBaseWidget:
        """Attach the common card behavior and add a plot to the workspace."""
        plot.drop_card_type = plot_type
        if self._sync_marker_timestamp is not None and hasattr(plot, "set_sync_marker"):
            plot.set_sync_marker(self._sync_marker_value(plot, self._sync_marker_timestamp))
        if hasattr(plot, "second_marker_placed"):
            plot.second_marker_placed.connect(lambda x_value, source=plot: self.on_second_marker_placed(source, x_value))
            plot.second_marker_cleared.connect(lambda source=plot: self.on_second_marker_cleared(source))
        plot.series_inserted.connect(self.insert_series_to_plot)
        plot.legend_changed.connect(self._series_drop_finished)
        plot.activated.connect(self.activate_plot)
        plot.close_requested.connect(self.remove_plot)
        plot.plot_dropped.connect(self.reorder_plot)
        plot.reorder_drag_started.connect(self._drop_controller.start)
        plot.reorder_drag_finished.connect(self._drop_controller.finish)
        plot.height_resize_requested.connect(self.resize_plot)
        plot.wheel_scrolled.connect(self.scroll_workspace)
        plot.time_offset_changed.connect(self.on_time_offset_changed)
        plot.set_header_controls_shown(self.card_controls_shown)
        if isinstance(plot, TIME_CARD_TYPES):
            install_offset_x_link(plot.plot_view_box())
        self.splitter.add_card(plot, self.default_plot_heights[plot_type])
        self._update_workspace_minimum_height()
        self.activate_plot(plot)
        self._emit_plot_navigator_changed()
        return plot

    def add_line_plot(self, h5_path: Path, series_name: str) -> PlotBaseWidget:
        """Always create a regular time-series plot, including GNSS values."""
        plot = LinePlotWidget(h5_path, series_name, settings_path=self.settings_path)
        return self._register_line_plot(plot)

    def add_multi_plot(self, entries: list[tuple[Path, str]], videos=()) -> PlotBaseWidget | None:
        """Open a Multi-series card with these series and video filmstrips."""
        videos = list(videos)
        if not entries and not videos:
            return None
        if entries:
            plot = MultiPlotWidget(*entries[0], settings_path=self.settings_path)
        else:
            # A video-only card has no H5 series; its time axis follows the video's data folder.
            plot = MultiPlotWidget(Path(videos[0].folder or videos[0].path.parent), "", settings_path=self.settings_path)
        if len(entries) > 1:
            plot.series = list(dict.fromkeys(entries))
            plot.on_series_changed()
        plot.filmstrips_changed.connect(self._emit_plot_navigator_changed)
        plot.filmstrip_offset_edited.connect(self.apply_video_offset)
        self._add_filmstrips(plot, videos)
        return self._register_line_plot(plot)

    def _add_filmstrips(self, card: MultiPlotWidget, videos) -> None:  # type: ignore[no-untyped-def]
        for video in videos:
            problem = card.add_filmstrip(video)
            if problem is not None:
                self.plot_status_changed.emit(f"Cannot add video filmstrip: {problem}")

    def add_sample_interval_plot(self, entries: list[tuple[Path, str]]) -> PlotBaseWidget | None:
        """Plot the time between consecutive samples of each series on one card."""
        if not entries:
            return None
        plot = SampleIntervalPlotWidget(*entries[0], settings_path=self.settings_path)
        if len(entries) > 1:
            plot.series = list(dict.fromkeys(entries))
            plot.on_series_changed()
        return self._register_line_plot(plot)

    def _register_line_plot(self, plot: LinePlotWidget) -> PlotBaseWidget:
        plot.series_dropped.connect(self.add_series_to_plot)
        plot.plot_status_changed.connect(self.plot_status_changed)
        if plot.current_status:
            self.plot_status_changed.emit(plot.current_status)
        plot.marker_placed.connect(lambda x_value, source=plot: self.on_marker_placed(source, x_value))
        plot.marker_cleared.connect(lambda source=plot: self.on_marker_cleared(source))
        plot.time_axis_representation_changed.connect(
            lambda representation, source=plot: self.on_time_axis_representation_changed(
                source, representation
            )
        )
        plot.plot_view_box().sigYRangeChanged.connect(
            lambda *_args: self.schedule_y_axis_alignment()
        )
        plot.set_marker_snap_enabled(self.snap_vertical_marker_to_nearest_point)
        plot_type = ("interval" if isinstance(plot, SampleIntervalPlotWidget) else
                     "multi" if isinstance(plot, MultiPlotWidget) else "line")
        self._register_plot(plot, plot_type=plot_type)
        self.update_x_axis_links()
        self.update_time_axis_format_links()
        self.schedule_y_axis_alignment()
        return plot

    def add_series_to_plot(self, plot: PlotBaseWidget, h5_path: Path, series_name: str) -> None:
        plot.add_series(h5_path, series_name)
        self._series_drop_finished(plot)

    def insert_series_to_plot(self, plot: PlotBaseWidget, h5_path: Path, series_name: str, index: int) -> None:
        plot.insert_series(h5_path, series_name, index)
        self._series_drop_finished(plot)

    def _series_drop_finished(self, plot: PlotBaseWidget) -> None:
        if isinstance(plot, (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget)):
            self.update_x_axis_links()
            self.update_time_axis_format_links()
            self.schedule_y_axis_alignment()
        self.activate_plot(plot)
        self._emit_plot_navigator_changed()

    def add_event_state_plot(self, h5_path: Path, series_name: str) -> PlotBaseWidget:
        """Create an optional event/state timeline."""
        plot = EventStatePlotWidget(h5_path, series_name)
        plot.series_dropped.connect(self.add_series_to_plot)
        plot.marker_placed.connect(lambda x_value, source=plot: self.on_marker_placed(source, x_value))
        plot.marker_cleared.connect(lambda source=plot: self.on_marker_cleared(source))
        plot.time_axis_representation_changed.connect(
            lambda representation, source=plot: self.on_time_axis_representation_changed(source, representation)
        )
        plot.set_marker_snap_enabled(self.snap_vertical_marker_to_nearest_point)
        self._register_plot(plot, plot_type="event")
        self.update_x_axis_links()
        self.update_time_axis_format_links()
        self.schedule_y_axis_alignment()
        return plot

    def add_frequency_plot(self, h5_path: Path, series_name: str) -> PlotBaseWidget:
        plot = FrequencyPlotWidget(h5_path, series_name)
        plot.series_dropped.connect(self.add_series_to_plot)
        plot.plot_status_changed.connect(self.plot_status_changed)
        plot.marker_placed.connect(lambda value, source=plot: self.on_marker_placed(source, value))
        plot.marker_cleared.connect(lambda source=plot: self.on_marker_cleared(source))
        plot.time_axis_representation_changed.connect(
            lambda representation, source=plot: self.on_time_axis_representation_changed(source, representation)
        )
        plot.data_ready.connect(self.update_x_axis_links)
        plot.data_ready.connect(self.schedule_y_axis_alignment)
        plot.set_marker_snap_enabled(self.snap_vertical_marker_to_nearest_point)
        self._register_plot(plot, plot_type="frequency")
        self.update_x_axis_links()
        self.update_time_axis_format_links()
        self.schedule_y_axis_alignment()
        return plot

    def add_xy_plot(
        self,
        x_entry: tuple[Path, str],
        y_entry: tuple[Path, str] | None = None,
        time_range: tuple[float, float] | None = None,
    ) -> PlotBaseWidget:
        """Add an X/Y card, optionally leaving Y empty for a later drop."""
        plot = XYPlotWidget(x_entry, y_entry, time_range=time_range)
        plot.marker_placed.connect(lambda x_value, source=plot: self.on_marker_placed(source, x_value))
        plot.marker_cleared.connect(lambda source=plot: self.on_marker_cleared(source))
        return self._register_plot(plot, plot_type="xy")

    def add_gnss_plot(
        self, h5_path: Path, series_name: str, time_range: tuple[float, float] | None = None,
        *, initial_axes=None,
    ) -> PlotBaseWidget:
        plot = GnssPlotWidget(h5_path, series_name, time_range=time_range, initial_axes=initial_axes)
        plot.series_dropped.connect(self.add_series_to_plot)
        plot.marker_placed.connect(lambda x_value, source=plot: self.on_marker_placed(source, x_value))
        plot.marker_cleared.connect(lambda source=plot: self.on_marker_cleared(source))
        return self._register_plot(plot, plot_type="gnss")

    def add_xyz_plot(self, x_entry, y_entry=None, z_entry=None) -> XYZPlotWidget:
        plot = XYZPlotWidget(x_entry, y_entry, z_entry)
        plot.marker_placed.connect(lambda x_value, source=plot: self.on_marker_placed(source, x_value))
        plot.marker_cleared.connect(lambda source=plot: self.on_marker_cleared(source))
        plot.series_changed.connect(self._emit_plot_navigator_changed)
        plot.plot_status_changed.connect(self.plot_status_changed)
        return self._register_plot(plot, plot_type="xyz")

    def add_gnss_3d_plot(
        self,
        h5_path: Path,
        series_name: str,
        *,
        height_series: tuple[Path, str] | None = None,
        time_range: tuple[float, float] | None = None,
        initial_axes=None,
    ) -> PlotBaseWidget:
        """Add the MapLibre/deck.gl 3D GNSS route card."""
        plot = Gnss3DPlotWidget(
            h5_path, series_name, height_series=height_series, time_range=time_range, initial_axes=initial_axes
        )
        plot.marker_placed.connect(lambda x_value, source=plot: self.on_marker_placed(source, x_value))
        plot.marker_cleared.connect(lambda source=plot: self.on_marker_cleared(source))
        return self._register_plot(plot, plot_type="gnss_3d")

    def add_image_plot(self, folder: Path, image_path: Path) -> PlotBaseWidget:
        plot = ImagePlotWidget(folder, image_path)
        self._register_plot(plot, plot_type="image")
        # Images decode in the background; errors arrive after registration.
        plot.plot_status_changed.connect(self.plot_status_changed)
        return plot

    def add_video_plot(self, folder: Path, video_path: Path, offset_seconds: float = 0.0) -> PlotBaseWidget:
        plot = VideoPlotWidget(folder, video_path, offset_seconds)
        plot.marker_timestamp_changed.connect(
            lambda timestamp, source=plot: self.on_video_marker_timestamp(source, timestamp)
        )
        plot.sync_data_requested.connect(self.set_video_sync_data_marker)
        plot.sync_video_requested.connect(self.set_video_sync_video_marker)
        plot.offset_edited.connect(lambda source: self._save_video_offset(source, source.offset_seconds))
        plot.timeline_alignment_requested.connect(self._video_timeline_alignment_timer.start)
        plot.duration_changed.connect(self._emit_plot_navigator_changed)
        plot.set_marker_link_enabled(self.link_markers)
        plot.set_timeline_markers(self._global_marker_timestamp, self._global_second_marker_timestamp)
        return self._register_plot(plot, plot_type="video")

    def _sync_data_marker_timestamp(self) -> float | None:
        """The red marker to sync to: the active card's when unlinked, else the last one placed in data.

        Linked markers also follow video playback, so the last marker placed in a
        data card is used rather than the current shared position.
        """
        active = self.active_plot
        if not self.link_markers and isinstance(active, TWO_MARKER_CARD_TYPES):
            value = active.marker_values()[0]
            if value is not None:
                return self._marker_timestamp(active, value)
        return self._last_data_marker_timestamp

    def set_video_sync_data_marker(self, plot: VideoPlotWidget) -> None:
        """First sync point: store the red marker's time, shown purple on every card."""
        timestamp = self._sync_data_marker_timestamp()
        if timestamp is None:
            plot.show_sync_message("Sync: place a marker in a data plot first")
            self.plot_status_changed.emit("Video sync needs a marker placed in a data plot.")
            return
        plot.capture_sync_anchor(timestamp)
        self.set_sync_marker(timestamp)
        self._complete_video_sync(plot)

    def set_video_sync_video_marker(self, plot: VideoPlotWidget) -> None:
        """Second sync point: the current video position matches the data marker."""
        plot.capture_sync_video_position()
        self._complete_video_sync(plot)

    def request_video_sync(self, plot: VideoPlotWidget) -> None:
        """Set both sync points at once, from the current data marker and video position."""
        self.set_video_sync_data_marker(plot)
        if plot.has_sync_anchor:
            self.set_video_sync_video_marker(plot)

    def set_sync_marker(self, timestamp: float | None) -> None:
        """Show the stored sync data marker on every card and the Plot navigator."""
        self._sync_marker_timestamp = timestamp
        for plot in self.plots():
            if hasattr(plot, "set_sync_marker"):
                plot.set_sync_marker(None if timestamp is None else self._sync_marker_value(plot, timestamp))
        self.sync_marker_changed.emit(timestamp)

    def _sync_marker_value(self, plot: PlotBaseWidget, timestamp: float) -> float:
        # Video cards convert the shared time themselves.
        return self._marker_value_for_plot(plot, timestamp) if isinstance(plot, TWO_MARKER_CARD_TYPES) else timestamp

    def _complete_video_sync(self, plot: VideoPlotWidget) -> None:
        """Once both sync points are set, calculate and save the offset."""
        if not (plot.has_sync_anchor and plot.has_sync_video_position):
            self.plot_status_changed.emit(
                "Video sync: data marker set; seek to the matching frame and set the video marker."
                if plot.has_sync_anchor else
                "Video sync: video marker set; place the data marker and set it.")
            return
        offset = plot.apply_sync_anchor()
        self.set_sync_marker(None)
        if offset is None:
            plot.show_sync_message("Sync: data start time is unavailable")
            self.plot_status_changed.emit("Video sync failed: the attached data has no log start time.")
            return
        self._save_video_offset(plot, offset)

    def apply_video_offset(self, folder: Path, video_path: Path, offset: float) -> None:
        """A video's sync offset set elsewhere, such as on a filmstrip: show it everywhere and save it."""
        for card in self.plots():
            if isinstance(card, VideoPlotWidget) and card.video_path.resolve() == Path(video_path).resolve():
                card.set_offset(offset)
        self._store_video_offset(Path(folder), Path(video_path), offset)

    def _save_video_offset(self, plot: VideoPlotWidget, offset: float) -> None:
        """Apply a video's new sync offset to filmstrips and the Plot navigator, then save it."""
        self._store_video_offset(plot.folder, plot.video_path, offset, plot)

    def _store_video_offset(self, folder: Path, video_path: Path, offset: float,
                            plot: VideoPlotWidget | None = None) -> None:
        for card in self.plots():
            if isinstance(card, MultiPlotWidget):
                card.series_plot.set_filmstrip_offset(video_path, offset)
        # The video's Plot Navigator range includes its offset. Refresh it as
        # soon as the offset is applied, even if metadata persistence fails.
        self._emit_plot_navigator_changed()
        try:
            info = read_info_file(folder)
            video = next(
                (entry for entry in info.videos
                 if video_attachment_path(folder, entry) == Path(video_path).resolve()),
                None,
            )
            if video is None:
                raise ValueError("This video is no longer attached to the data folder.")
            video["offset_seconds"] = f"{offset:.6f}"
            for audio in info.audio:
                if audio.get("video") and attachment_path(folder, {"path": audio["video"]}) == Path(video_path).resolve():
                    audio["offset_seconds"] = f"{offset:.6f}"
                    audio_path = attachment_path(folder, audio)
                    if audio_path.is_file() and not set_audio_offset(audio_path, offset):
                        self.plot_status_changed.emit("The video's audio could not be moved; it is open elsewhere.")
            write_info_file(folder, info)
            self.video_offset_saved.emit(folder)
            self.plot_status_changed.emit(f"Video sync saved: {offset:+.3f} s")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            if plot is not None:
                plot.show_sync_message("Sync: offset could not be saved")
            self.plot_status_changed.emit(f"Video sync offset was not saved: {error}")

    def add_gnss_plot_from_pair(
        self,
        first: tuple[Path, str],
        second: tuple[Path, str],
        *,
        time_range: tuple[float, float],
    ) -> PlotBaseWidget:
        """Create a GNSS map from selected latitude then longitude series."""
        plot = GnssPlotWidget(
            *first,
            time_range=time_range,
            coordinate_pair=(first[0], first[1], second[0], second[1]),
        )
        plot.marker_placed.connect(lambda x_value, source=plot: self.on_marker_placed(source, x_value))
        plot.marker_cleared.connect(lambda source=plot: self.on_marker_cleared(source))
        return self._register_plot(plot, plot_type="gnss")

    def _emit_plot_navigator_changed(self) -> None:
        # Cards were added, removed or moved: the bottom card may have changed.
        self.update_time_axis_visibility()
        entries: list[PlotNavigatorEntry] = []
        for plot in self.plots():
            if not isinstance(plot, PlotBaseWidget):
                continue
            time_range = self._plot_time_range(plot)
            if time_range is None:
                continue
            current_full_range = tuple(sorted(time_range))
            # The outline always represents every assigned source.  Unlike a
            # selected fill range, this must expand (or shrink) immediately
            # when a series is added or removed from an existing plot card.
            self._plot_navigator_full_ranges[plot] = current_full_range
            full_start, full_stop = current_full_range
            start, stop = self._plot_navigator_ranges.get(plot, (full_start, full_stop))
            start = max(full_start, min(full_stop, start))
            stop = max(start, min(full_stop, stop))
            entries.append(PlotNavigatorEntry(
                plot=plot,
                start=start,
                stop=stop,
                source=self._plot_navigator_source(plot),
                plot_type=self._plot_navigator_type(plot),
                full_start=full_start,
                full_stop=full_stop,
                offset=plot.time_offset,
                offset_set=plot.has_time_offset(),
            ))
        self.plot_navigator_changed.emit(entries)
        # Closing or moving the first time card hands the view box to the next one.
        self._track_linked_view_range()
        self.plot_layout_changed.emit()

    @staticmethod
    def _plot_navigator_type(plot: PlotBaseWidget) -> str:
        if isinstance(plot, FrequencyPlotWidget):
            return "frequency"
        if isinstance(plot, MultiPlotWidget):
            return "multi"
        if isinstance(plot, SampleIntervalPlotWidget):
            return "interval"
        if isinstance(plot, LinePlotWidget):
            return "line"
        if isinstance(plot, EventStatePlotWidget):
            return "event"
        if isinstance(plot, XYPlotWidget):
            return "xy"
        if isinstance(plot, XYZPlotWidget):
            return "xyz"
        if isinstance(plot, GnssPlotWidget):
            return "gnss"
        if isinstance(plot, Gnss3DPlotWidget):
            return "gnss"
        if isinstance(plot, VideoPlotWidget):
            return "video"
        return "other"

    @staticmethod
    def _plot_navigator_source(plot: PlotBaseWidget) -> str:
        if isinstance(plot, ImagePlotWidget):
            return str(plot.image_path)
        if isinstance(plot, VideoPlotWidget):
            return str(plot.video_path)
        return "\n".join(f"{path.parent.name}/{name}" for path, name in plot.series) or "Empty plot"

    @staticmethod
    def _series_time_range(h5_path: Path, series_name: str) -> tuple[float, float] | None:
        """Return a series' time range, reopening its H5 file only after it changes.

        Every card add, drop, move and close refreshes the Plot navigator, so
        the range is cached per (path, series, mtime, size) instead of being
        read again for every series of every card.
        """
        try:
            stat = Path(h5_path).stat()
        except OSError:
            return None
        key = (str(h5_path), str(series_name), stat.st_mtime_ns, stat.st_size)
        if key not in _SERIES_TIME_RANGE_CACHE:
            if len(_SERIES_TIME_RANGE_CACHE) >= SERIES_TIME_RANGE_CACHE_LIMIT:
                _SERIES_TIME_RANGE_CACHE.clear()
            _SERIES_TIME_RANGE_CACHE[key] = PlotWorkspace._read_series_time_range(h5_path, series_name)
        return _SERIES_TIME_RANGE_CACHE[key]

    @staticmethod
    def _read_series_time_range(h5_path: Path, series_name: str) -> tuple[float, float] | None:
        """Read only the first and final timestamps; never load samples for the overview."""
        try:
            with h5py.File(h5_path, "r") as h5_file:
                group = _series_group_for_navigator_name(h5_file, series_name)
                dataset = group[_attribute_text(group.attrs.get("time_dataset", "time_unix"))]
                if not len(dataset):
                    return None
                metadata = _time_axis_metadata_from_group(h5_file, group)
                values = _canonical_time_values(np.asarray([dataset[0], dataset[-1]], dtype=float), metadata)
                return float(values[0]), float(values[-1])
        except (OSError, KeyError, ValueError, IndexError):
            return None

    def _plot_time_range(self, plot: PlotBaseWidget) -> tuple[float, float] | None:
        if isinstance(plot, ImagePlotWidget):
            return None
        if isinstance(plot, VideoPlotWidget):
            if plot.data_start is None:
                return None
            return plot.data_start + plot.offset_seconds, plot.data_start + plot.offset_seconds + plot.duration
        ranges = []
        for path, name in plot.series:
            series_range = self._series_time_range(path, name)
            if series_range is not None:
                # Multi-series cards can show one series earlier or later than the rest.
                shift = plot.series_time_offset((path, name))
                ranges.append((series_range[0] + shift, series_range[1] + shift))
        if isinstance(plot, MultiPlotWidget):
            ranges += plot.filmstrip_time_ranges()
        if not ranges:
            return None
        # The Plot navigator shows shared time: the card's own time plus its offset.
        offset = plot.time_offset
        return min(item[0] for item in ranges) + offset, max(item[1] for item in ranges) + offset

    def set_plot_navigator_range(self, plot: PlotBaseWidget, start: float, stop: float) -> None:
        start, stop = sorted((float(start), float(stop)))
        self._plot_navigator_ranges[plot] = (start, stop)
        self._apply_plot_data_range(plot, (start, stop))
        if isinstance(plot, VideoPlotWidget) and plot.data_start is not None:
            plot.seek(start - plot.data_start - plot.offset_seconds)
        self._emit_plot_navigator_changed()

    def reset_plot_navigator_range(self, plot: PlotBaseWidget) -> None:
        """Show the card's full time range again."""
        if self._plot_navigator_ranges.pop(plot, None) is None:
            return
        self._apply_plot_data_range(plot, None)
        self._emit_plot_navigator_changed()

    def _apply_plot_data_range(self, plot: PlotBaseWidget, time_range: tuple[float, float] | None) -> None:
        """Limit a card to a shared-time range, or remove the limit."""
        if time_range is None:
            card_range = None
        else:
            # Cards filter their data in their own time, before the offset.
            offset = plot.time_offset
            card_range = (time_range[0] - offset, time_range[1] - offset)
        if isinstance(plot, (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget, XYPlotWidget, XYZPlotWidget)):
            plot.set_data_time_range(card_range)
        elif isinstance(plot, (GnssPlotWidget, Gnss3DPlotWidget)):
            plot.time_range = card_range
            plot.on_series_changed()
        elif isinstance(plot, VideoPlotWidget):
            plot.set_visible_window(None if time_range is None or plot.data_start is None else
                                    (time_range[0] - plot.data_start - plot.offset_seconds,
                                     time_range[1] - plot.data_start - plot.offset_seconds))

    def shift_plot_time_offset(self, plot: PlotBaseWidget, seconds: float) -> None:
        """Add to a card's time offset, as when its Plot navigator bar is dragged sideways."""
        if plot in self.plots() and plot.supports_time_offset:
            # A Multi-series card with a selected series moves only that series.
            plot.set_edited_time_offset(plot.edited_time_offset() + float(seconds))

    def reset_plot_time_offset(self, plot: PlotBaseWidget) -> None:
        if plot in self.plots():
            plot.reset_time_offset()

    def on_time_offset_changed(self, plot: PlotBaseWidget, previous: float) -> None:
        """Show a card at its new time offset: its view, markers and Plot navigator bar move with it."""
        if plot not in self.plots():
            return
        offset = plot.time_offset
        delta = offset - float(previous)
        if plot in self._plot_navigator_ranges:
            # The trimmed range keeps the same data; it moves with the card.
            start, stop = self._plot_navigator_ranges[plot]
            self._plot_navigator_ranges[plot] = (start + delta, stop + delta)
        if isinstance(plot, TIME_CARD_TYPES):
            view_box = plot.plot_view_box()
            canvas = plot.series_plot.plot_widget if isinstance(plot, LinePlotWidget) else plot.plot_widget
            canvas.time_axis.set_display_offset(offset)
            set_view_box_time_offset(view_box, offset)
            if view_box.linkedView(0) is not None:
                # A linked card follows the shared view; its data moves under it.
                view_box.linkedXChanged()
            else:
                # Keep the same shared time on screen, so the data moves and the axis stays.
                left, right = (float(value) for value in view_box.viewRange()[0])
                view_box.setXRange(left - delta, right - delta, padding=0)
        if isinstance(plot, TWO_MARKER_CARD_TYPES):
            self._show_markers_at_offset(plot)
        if self._sync_marker_timestamp is not None and hasattr(plot, "set_sync_marker"):
            plot.set_sync_marker(self._sync_marker_value(plot, self._sync_marker_timestamp))
        self._emit_plot_navigator_changed()

    def _show_markers_at_offset(self, plot: PlotBaseWidget) -> None:
        """Linked markers stay at their shared time; a card's own markers stay on its data."""
        if self.link_markers:
            self._synchronizing_vertical_marker = True
            try:
                for index, timestamp in enumerate((self._global_marker_timestamp, self._global_second_marker_timestamp)):
                    if timestamp is not None and plot.marker_values()[index] is not None:
                        plot.set_vertical_marker(self._marker_value_for_plot(plot, timestamp), index)
            finally:
                self._synchronizing_vertical_marker = False
            self.refresh_marker_info_boxes(plot)
        elif plot is self.active_plot:
            self._show_card_markers_in_navigator(plot)

    def place_marker_from_plot_navigator(self, timestamp: float) -> None:
        self._place_marker_from_plot_navigator(timestamp, 0)
        self._global_marker_timestamp = float(timestamp)
        self.global_marker_changed.emit(self._global_marker_timestamp)

    def clear_marker_from_plot_navigator(self) -> None:
        self._global_marker_timestamp = None
        for plot in self.plots():
            if isinstance(plot, TWO_MARKER_CARD_TYPES):
                plot.clear_vertical_marker()
        self.global_marker_changed.emit(None)

    def place_second_marker_from_plot_navigator(self, timestamp: float) -> None:
        self._place_marker_from_plot_navigator(timestamp, SECOND_MARKER)
        self._global_second_marker_timestamp = float(timestamp)
        self.global_second_marker_changed.emit(self._global_second_marker_timestamp)

    def _place_marker_from_plot_navigator(self, timestamp: float, index: int) -> None:
        """The active card, or the first marker card, shows the marker as if clicked there."""
        source = (self.active_plot if isinstance(self.active_plot, TWO_MARKER_CARD_TYPES)
                  else next((plot for plot in self.plots() if isinstance(plot, TWO_MARKER_CARD_TYPES)), None))
        if source is None:
            return
        value = self._marker_value_for_plot(source, timestamp)
        source.set_vertical_marker(value, index)
        if not isinstance(source, (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget)):
            # Only time canvases announce markers set in code; share the others' here.
            (self.on_marker_placed if index == 0 else self.on_second_marker_placed)(source, value)

    def clear_second_marker_from_plot_navigator(self) -> None:
        self._set_linked_second_markers(None, None)
        self._global_second_marker_timestamp = None
        self.global_second_marker_changed.emit(None)

    def scroll_workspace(self, delta: int) -> None:
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.value() - delta)

    def set_x_axes_linked(self, linked: bool) -> None:
        self.link_x_axes = bool(linked)
        self.update_x_axis_links()

    def set_time_axis_format_linked(self, linked: bool) -> None:
        self.link_time_axis_format = bool(linked)
        self.update_time_axis_format_links()

    def set_markers_linked(self, linked: bool) -> None:
        self.link_markers = bool(linked)
        for plot in self.plots():
            if isinstance(plot, VideoPlotWidget):
                plot.set_marker_link_enabled(linked)
        self.refresh_marker_info_boxes()

    def set_marker_snap_to_nearest_point(self, enabled: bool) -> None:
        self.snap_vertical_marker_to_nearest_point = bool(enabled)
        for plot in self.plots():
            if isinstance(plot, (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget)):
                plot.set_marker_snap_enabled(enabled)

    def set_time_axis_display(self, mode: str) -> None:
        self.time_axis_display = mode if mode in TIME_AXIS_DISPLAY_MODES else "all"
        self.update_time_axis_visibility()

    def set_legends_shown(self, shown: bool) -> None:
        """Show or hide the legend of every card, including cards opened later."""
        self.legends_shown = bool(shown)
        set_legends_shown(self.legends_shown)
        for plot in self.plots():
            for legend in plot.findChildren(PlotLegendWidget):
                legend.apply_legends_shown()

    def set_card_controls_shown(self, shown: bool) -> None:
        """Show or hide the buttons in every card's Drag strip, including cards opened later."""
        self.card_controls_shown = bool(shown)
        for plot in self.plots():
            plot.set_header_controls_shown(self.card_controls_shown)

    def update_time_axis_visibility(self) -> None:
        """Show the time axis on every time card, only on the lowest one, or on none."""
        time_cards = [plot for plot in self.plots()
                      if isinstance(plot, (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget))]
        for plot in time_cards:
            shown = self.time_axis_display == "all" or (
                self.time_axis_display == "bottom" and plot is time_cards[-1])
            plot_item = plot.series_plot.plot_item if isinstance(plot, LinePlotWidget) else plot.plot_item
            if plot_item.getAxis("bottom").isVisible() != shown:
                plot_item.showAxis("bottom", shown)

    def set_marker_info_display(self, mode: str) -> None:
        self.marker_info_display = mode if mode in {"off", "active", "all"} else "active"
        self.refresh_marker_info_boxes()

    def refresh_marker_info_boxes(self, source: PlotBaseWidget | None = None) -> None:
        """Immediately reconcile visible boxes with the selected display mode."""
        timestamps = (self._global_marker_timestamp, self._global_second_marker_timestamp)
        value_plots = [
            plot
            for plot in self.plots()
            if isinstance(plot, TWO_MARKER_CARD_TYPES)
        ]
        if all(timestamp is None for timestamp in timestamps) or self.marker_info_display == "off":
            for plot in value_plots:
                plot.hide_marker_data()
            return
        # A Video card has no values boxes; when it moves the marker, the active card shows them.
        selected = source if source in value_plots else self.active_plot
        # An information box represents a marker time.  It must never create
        # an implicit cross-plot marker link: when markers are unlinked, only
        # the plot where that marker was placed may show/update its values.
        visible_plots = (
            value_plots
            if self.marker_info_display == "all" and self.link_markers
            else [selected] if selected in value_plots else []
        )
        for plot in value_plots:
            if plot not in visible_plots:
                plot.hide_marker_data()
        for plot in visible_plots:
            if isinstance(plot, TWO_MARKER_CARD_TYPES):
                # Each series gets a values box at each placed marker.
                for index, timestamp in enumerate(timestamps):
                    if timestamp is None:
                        plot.hide_marker_data(index)
                    else:
                        plot.show_marker_data(self._marker_value_for_plot(plot, timestamp), index)
            elif timestamps[0] is None:
                plot.hide_marker_data()
            else:
                # X/Y and Map cards show values for the first marker only.
                plot.show_marker_data(self._marker_value_for_plot(plot, timestamps[0]))

    def reload_plot_settings(self) -> None:
        for plot in self.plots():
            if isinstance(plot, LinePlotWidget):
                plot.reload_plot_settings()

    def apply_marker_settings(self, plot_settings: dict[str, object]) -> None:
        """Apply marker colors and width and the values box opacity to open cards."""
        set_marker_style(str(plot_settings["first_marker_color"]), str(plot_settings["second_marker_color"]),
                         int(plot_settings["marker_width"]), int(plot_settings["marker_opacity_percent"]))
        set_selection_style(str(plot_settings["selection_color"]), int(plot_settings["selection_opacity_percent"]))
        set_sync_marker_color(str(plot_settings["sync_marker_color"]))
        set_value_box_opacity(int(plot_settings["values_box_opacity_percent"]))
        set_series_colors(plot_settings["series_colors"])
        for plot in self.plots():
            if isinstance(plot, TWO_MARKER_CARD_TYPES):
                plot.apply_marker_style()
            elif isinstance(plot, VideoPlotWidget):
                plot.timeline.update()
        if self._sync_marker_timestamp is not None:
            self.set_sync_marker(self._sync_marker_timestamp)

    def apply_theme(self) -> None:
        """Refresh plot libraries and overlays after Qt's palette has changed."""
        for plot in self.plots():
            if isinstance(plot, (LinePlotWidget, XYPlotWidget, XYZPlotWidget, GnssPlotWidget, Gnss3DPlotWidget, VideoPlotWidget, EventStatePlotWidget, FrequencyPlotWidget)):
                plot.apply_theme()

    def on_marker_placed(self, source: PlotBaseWidget, x_value: float) -> None:
        if self._synchronizing_vertical_marker:
            return
        self.activate_plot(source)
        timestamp = self._marker_timestamp(source, x_value)
        self._last_data_marker_timestamp = timestamp
        self._global_marker_timestamp = timestamp
        self.global_marker_changed.emit(timestamp)
        if self.link_markers:
            self._synchronizing_vertical_marker = True
            try:
                for plot in self.plots():
                    if isinstance(plot, TWO_MARKER_CARD_TYPES) and plot is not source:
                        plot.set_vertical_marker(self._marker_value_for_plot(plot, timestamp))
                    elif isinstance(plot, VideoPlotWidget):
                        plot.set_marker_timestamp(timestamp)
            finally:
                self._synchronizing_vertical_marker = False
        self.refresh_marker_info_boxes(source)

    def on_video_marker_timestamp(self, source: VideoPlotWidget, timestamp: float) -> None:
        """Move time-plot markers as an attached video plays or is scrubbed."""
        if not self.link_markers or self._synchronizing_vertical_marker:
            return
        self._global_marker_timestamp = timestamp
        self.global_marker_changed.emit(timestamp)
        self._synchronizing_vertical_marker = True
        try:
            for plot in self.plots():
                if plot is source:
                    continue
                if isinstance(plot, TWO_MARKER_CARD_TYPES):
                    plot.set_vertical_marker(self._marker_value_for_plot(plot, timestamp))
        finally:
            self._synchronizing_vertical_marker = False
        self._schedule_video_marker_info_refresh(source)
        self._pan_linked_time_views_to_marker(timestamp)

    def align_video_timelines(self) -> None:
        """Line up each Video card's Timeline slider with the first time card's plot area."""
        edges = None
        for plot in self.plots():
            if not isinstance(plot, (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget)):
                continue
            try:
                box = plot.plot_view_box()
                views = box.scene().views() if box.scene() is not None else []
                if not views or not views[0].isVisible():
                    continue
                view, bounds = views[0], box.sceneBoundingRect()
                left = view.mapToGlobal(view.mapFromScene(bounds.topLeft())).x()
                right = view.mapToGlobal(view.mapFromScene(bounds.topRight())).x()
            except RuntimeError:
                continue
            if right > left:
                edges = (left, right)
                break
        for plot in self.plots():
            if isinstance(plot, VideoPlotWidget):
                plot.set_timeline_span(*(edges or (None, None)))

    def _update_video_timelines(self) -> None:
        for plot in self.plots():
            if isinstance(plot, VideoPlotWidget):
                plot.set_timeline_markers(self._global_marker_timestamp, self._global_second_marker_timestamp)

    def _schedule_video_marker_info_refresh(self, source: VideoPlotWidget) -> None:
        """Coalesce costly marker popup refreshes during video playback."""
        self._video_marker_info_source = source
        if not self._video_marker_info_timer.isActive():
            self._video_marker_info_timer.start()

    def _refresh_video_marker_info_boxes(self) -> None:
        source = self._video_marker_info_source
        self._video_marker_info_source = None
        if source is not None:
            self.refresh_marker_info_boxes(source)

    def _pan_linked_time_views_to_marker(self, timestamp: float) -> None:
        """Keep a video-driven marker visible without changing the current zoom."""
        if not self.link_x_axes:
            return
        time_plots = [
            plot
            for plot in self.plots()
            if isinstance(plot, (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget))
        ]
        if not time_plots:
            return
        view_box = time_plots[0].plot_view_box() if isinstance(time_plots[0], LinePlotWidget) else time_plots[0].plot_item.getViewBox()
        timestamp -= time_plots[0].time_offset
        try:
            left, right = (float(value) for value in view_box.viewRange()[0])
        except (IndexError, TypeError, ValueError):
            return
        width = right - left
        if not np.isfinite(width) or width <= 0 or left <= timestamp <= right:
            return
        # Leave a small lead/lag margin so continuous playback does not pan
        # for every video timestamp once the marker reaches the edge.
        new_left = timestamp - width * (0.10 if timestamp > right else 0.90)
        view_box.setXRange(new_left, new_left + width, padding=0.0)

    @staticmethod
    def _marker_timestamp(plot: PlotBaseWidget, value: float) -> float:
        """Convert a plot's native time coordinate to a common real-time axis."""
        # Every plot type now exposes a marker in canonical Unix-second
        # coordinates.  In particular, X/Y data are paired using converted
        # timestamps so they can synchronize with line and GNSS plots.
        offset = plot.time_offset
        if isinstance(plot, TWO_MARKER_CARD_TYPES):
            return float(value) + offset
        try:
            metadata = load_time_axis_metadata(*plot.series[0])
        except (OSError, KeyError, ValueError, IndexError):
            return float(value) + offset
        if metadata.is_unix_time:
            return float(value) * metadata.seconds_per_unit + offset
        if metadata.log_start_unix_seconds is not None:
            return (metadata.log_start_unix_seconds + (float(value) - metadata.raw_origin) * metadata.seconds_per_unit
                    + offset)
        return float(value) + offset

    @classmethod
    def _marker_value_for_plot(cls, plot: PlotBaseWidget, timestamp: float) -> float:
        """Convert the common marker timestamp back to a plot's native axis."""
        # The card shows its own time plus its offset.
        timestamp = float(timestamp) - plot.time_offset
        if isinstance(plot, TWO_MARKER_CARD_TYPES):
            return float(timestamp)
        try:
            metadata = load_time_axis_metadata(*plot.series[0])
        except (OSError, KeyError, ValueError, IndexError):
            return float(timestamp)
        if metadata.seconds_per_unit <= 0:
            return float(timestamp)
        if metadata.is_unix_time:
            return float(timestamp) / metadata.seconds_per_unit
        if metadata.log_start_unix_seconds is not None:
            return metadata.raw_origin + (float(timestamp) - metadata.log_start_unix_seconds) / metadata.seconds_per_unit
        return float(timestamp)

    def on_marker_cleared(self, source: PlotBaseWidget) -> None:
        if self._synchronizing_vertical_marker:
            return
        self._global_marker_timestamp = None
        self.global_marker_changed.emit(None)
        self.refresh_marker_info_boxes(source)
        if not self.link_markers:
            return
        self._synchronizing_vertical_marker = True
        try:
            for plot in self.plots():
                if isinstance(plot, TWO_MARKER_CARD_TYPES) and plot is not source:
                    plot.clear_vertical_marker()
        finally:
            self._synchronizing_vertical_marker = False

    def on_second_marker_placed(self, source: PlotBaseWidget, x_value: float) -> None:
        if self._synchronizing_vertical_marker:
            return
        self.activate_plot(source)
        timestamp = self._marker_timestamp(source, x_value)
        self._global_second_marker_timestamp = timestamp
        self.global_second_marker_changed.emit(timestamp)
        if self.link_markers:
            self._set_linked_second_markers(timestamp, source)
        self.refresh_marker_info_boxes(source)

    def on_second_marker_cleared(self, source: PlotBaseWidget) -> None:
        if self._synchronizing_vertical_marker:
            return
        self._global_second_marker_timestamp = None
        self.global_second_marker_changed.emit(None)
        if self.link_markers:
            self._set_linked_second_markers(None, source)
        self.refresh_marker_info_boxes(source)

    def _set_linked_second_markers(self, timestamp: float | None, source: PlotBaseWidget | None) -> None:
        """Place or clear the second marker, and so the selection, on every time card."""
        self._synchronizing_vertical_marker = True
        try:
            for plot in self.plots():
                if isinstance(plot, TWO_MARKER_CARD_TYPES) and plot is not source:
                    if timestamp is None:
                        plot.clear_vertical_marker(SECOND_MARKER)
                    else:
                        plot.set_vertical_marker(self._marker_value_for_plot(plot, timestamp), SECOND_MARKER)
        finally:
            self._synchronizing_vertical_marker = False

    def _track_linked_view_range(self) -> None:
        """Follow the linked time cards' shared zoom for the Plot navigator's view box."""
        view_box = None
        if self.link_x_axes:
            self._linked_view_plot = next((plot for plot in self.plots() if isinstance(plot, TIME_CARD_TYPES)), None)
            view_box = None if self._linked_view_plot is None else self._linked_view_plot.plot_view_box()
        if view_box is not self._linked_view_box:
            if self._linked_view_box is not None:
                try:
                    self._linked_view_box.sigXRangeChanged.disconnect(self._emit_linked_view_range)
                except (RuntimeError, TypeError):
                    pass  # The card, and so its view box, was already deleted.
            self._linked_view_box = view_box
            if view_box is not None:
                view_box.sigXRangeChanged.connect(self._emit_linked_view_range)
        self._emit_linked_view_range()

    def _emit_linked_view_range(self, *_args) -> None:  # type: ignore[no-untyped-def]
        time_range = None
        if self._linked_view_box is not None:
            try:
                left, right = (float(value) for value in self._linked_view_box.viewRange()[0])
                offset = self._linked_view_offset()
                time_range = ((left + offset, right + offset)
                              if np.isfinite(left) and np.isfinite(right) and right > left else None)
            except (RuntimeError, IndexError, TypeError, ValueError):
                time_range = None
        self.linked_view_range_changed.emit(time_range)

    def set_linked_view_range(self, start: float, stop: float) -> None:
        """Zoom every linked time card to a range chosen in the Plot navigator."""
        start, stop = sorted((float(start), float(stop)))
        if self._linked_view_box is not None and stop > start:
            offset = self._linked_view_offset()
            self._linked_view_box.setXRange(start - offset, stop - offset, padding=0.0)

    def _linked_view_offset(self) -> float:
        plot = getattr(self, "_linked_view_plot", None)
        try:
            return plot.time_offset if plot is not None else 0.0
        except RuntimeError:
            return 0.0

    def update_x_axis_links(self) -> None:
        self._track_linked_view_range()
        time_plots = [
            plot
            for plot in self.plots()
            if isinstance(plot, (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget))
        ]
        if not time_plots:
            return
        master_view_box = time_plots[0].plot_view_box()
        master_view_box.setXLink(None)
        master_metadata = time_plots[0].time_axis_metadata
        if callable(master_metadata):
            master_metadata = master_metadata()
        # Line curves are rendered in common real-time coordinates.  Share
        # the elapsed-time origin too, so that the optional elapsed formatter
        # stays visually aligned while X linking is enabled.
        time_plots[0].restore_own_time_axis_metadata()
        for plot in time_plots[1:]:
            plot.plot_view_box().setXLink(master_view_box if self.link_x_axes else None)
            if self.link_x_axes and master_metadata is not None:
                plot.set_time_axis_display_metadata(master_metadata)
            else:
                plot.restore_own_time_axis_metadata()

    def schedule_y_axis_alignment(self) -> None:
        """Coalesce axis/layout changes before reserving a common left margin."""
        self._y_axis_alignment_timer.start()

    def _align_line_plot_y_axes(self) -> None:
        time_plots = [
            plot
            for plot in self.plots()
            if isinstance(plot, (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget))
        ]
        if not time_plots:
            return
        axes = [
            plot.series_plot.plot_item.getAxis("left") if isinstance(plot, LinePlotWidget)
            else plot.plot_item.getAxis("left")
            for plot in time_plots
        ]
        # First release an earlier common width so newly needed tick-label
        # space is measured from each plot's current zoom level.
        for axis in axes:
            axis.setWidth(None)
        # Axis geometry is updated on the next graphics-layout pass. Measuring
        # immediately after setWidth(None) can retain the previous plot's
        # width, leaving different X-axis start positions.
        QTimer.singleShot(0, lambda current_axes=axes, current_plots=time_plots: self._apply_axis_width_and_notify(current_axes, current_plots))

    def _apply_axis_width_and_notify(self, axes, time_plots) -> None:  # type: ignore[no-untyped-def]
        self._apply_common_left_axis_width(axes, time_plots)
        self.plot_layout_changed.emit()

    @staticmethod
    def _apply_common_left_axis_width(axes, time_plots=None) -> None:  # type: ignore[no-untyped-def]
        paired = [
            (axis, plot)
            for axis, plot in zip(axes, time_plots or [])
            if axis.scene() is not None
        ]
        if not paired:
            return
        axes = [axis for axis, _plot in paired]
        common_width = max(
            int(axis.sizeHint(Qt.SizeHint.PreferredSize).width())
            if not isinstance(plot, EventStatePlotWidget)
            else max(
                int(axis.sizeHint(Qt.SizeHint.PreferredSize).width()),
                plot.required_left_axis_width(),
            )
            for axis, plot in paired
        )
        for axis in axes:
            axis.setWidth(common_width)

    def update_time_axis_format_links(self) -> None:
        """Keep date/time versus elapsed-time labels consistent across line plots."""
        if not self.link_time_axis_format:
            return
        time_plots = [
            plot
            for plot in self.plots()
            if isinstance(plot, (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget))
        ]
        if not time_plots:
            return
        self._apply_time_axis_representation(time_plots[0].time_axis_representation())

    def on_time_axis_representation_changed(
        self, source: PlotBaseWidget, representation: str
    ) -> None:
        if not self.link_time_axis_format or self._synchronizing_time_axis_format:
            return
        self._apply_time_axis_representation(representation, source)

    def _apply_time_axis_representation(
        self, representation: str, source: PlotBaseWidget | None = None
    ) -> None:
        self._synchronizing_time_axis_format = True
        try:
            for plot in self.plots():
                if isinstance(plot, (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget)) and plot is not source:
                    plot.set_time_axis_representation(representation)
        finally:
            self._synchronizing_time_axis_format = False

    def plots(self) -> list[PlotBaseWidget]:
        """Cards in visual order: rows top to bottom, cards left to right."""
        return self.splitter.plots()

    def remove_plot(self, plot: PlotBaseWidget) -> None:
        if self.splitter.row_for(plot) is None:
            return
        self._data_drop_controller.clear_preview()
        if self._drop_controller.source is plot:
            self._drop_controller.finish()
        self._plot_navigator_ranges.pop(plot, None)
        self._plot_navigator_full_ranges.pop(plot, None)
        dispose = getattr(plot, "dispose", None)
        if callable(dispose):
            dispose()
        if isinstance(plot, VideoPlotWidget):
            plot.shutdown()
        self.splitter.remove_card(plot)
        if self.active_plot is plot:
            self.activate_plot(next(iter(self.plots()), None))
        plot.deleteLater()
        self.update_x_axis_links()
        self.update_time_axis_format_links()
        self.schedule_y_axis_alignment()
        self._emit_plot_navigator_changed()

    def close_all_plots(self) -> None:
        """Close every card, including cards inside horizontal rows."""
        for inspection in self.findChildren(SelectionInspection):
            inspection.cancel()
        for plot in self.plots():
            self.remove_plot(plot)

    def workspace_state(self) -> dict:
        """Rows of cards with their sizes, the active card and the scroll position, for a .umw file."""
        sizes = self.splitter.sizes()
        rows = []
        for index in range(self.splitter.count()):
            row = self.splitter.widget(index)
            cards = row.plots() if isinstance(row, PlotRow) else [row]
            rows.append({"height": sizes[index], "widths": row.sizes() if isinstance(row, PlotRow) else None,
                         "cards": [self._card_record(card) for card in cards]})
        plots = self.plots()
        return {"rows": rows, "active": plots.index(self.active_plot) if self.active_plot in plots else None,
                "scroll": self.verticalScrollBar().value()}

    @staticmethod
    def _card_record(card: PlotBaseWidget) -> dict:
        record = {"type": card.drop_card_type, **card.workspace_state()}
        if isinstance(card, TWO_MARKER_CARD_TYPES):
            record["markers"] = list(card.marker_values())
        return record

    def restore_workspace_state(self, state: dict) -> list[str]:
        """Replace every card with the saved ones; return what could not be restored."""
        problems: list[str] = []
        self.close_all_plots()
        self.set_sync_marker(None)
        self._global_marker_timestamp = self._global_second_marker_timestamp = None
        self.global_marker_changed.emit(None)
        self.global_second_marker_changed.emit(None)
        created = []
        for row in state.get("rows", []):
            cards = []
            for record in row.get("cards", []):
                title = CARD_TITLES.get(record.get("type"), "Card")
                record = self._usable_card_record(record, problems)
                if record is None:
                    continue
                try:
                    cards.append((self._create_card(record), record))
                except (OSError, KeyError, IndexError, TypeError, ValueError) as error:
                    problems.append(f"{title} card could not be opened: {error}")
            if cards:
                created.append((row, cards))
        for row, cards in created:
            if len(cards) > 1:
                self.splitter.place_in_row([card for card, _ in cards], row.get("widths"))
        heights = [int(row.get("height") or 0) for row, _ in created]
        if len(heights) == self.splitter.count():
            self._set_plot_sizes(heights)
            self._update_workspace_minimum_height()
        restored = [pair for _, cards in created for pair in cards]
        for card, record in restored:
            try:
                card.restore_workspace_state(record)
            except (OSError, KeyError, IndexError, TypeError, ValueError) as error:
                problems.append(f"{CARD_TITLES.get(record.get('type'), 'Card')} card settings were not restored: {error}")
        self.update_x_axis_links()
        self.update_time_axis_format_links()
        # Linked time axes align by pixel, so equal Y axis widths come before the saved time ranges.
        self._y_axis_alignment_timer.stop()
        self._align_line_plot_y_axes()
        self._restore_time_ranges(restored)
        for delay in (0, 150):
            QTimer.singleShot(delay, self, lambda: self._restore_time_ranges(restored))
        self._restore_markers(restored)
        plots = self.plots()
        active = state.get("active")
        self.activate_plot(None)
        self.activate_plot(plots[active] if isinstance(active, int) and 0 <= active < len(plots) else None)
        self._emit_plot_navigator_changed()
        scroll = int(state.get("scroll") or 0)
        # The scroll range is known once the restored rows are laid out.
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(scroll))
        return problems

    @staticmethod
    def _usable_card_record(record: dict, problems: list[str]) -> dict | None:
        """The record without sources whose files are missing, or None when nothing is left."""
        title = CARD_TITLES.get(record.get("type"), "Card")
        if record.get("type") not in CARD_TITLES:
            problems.append(f"Unknown card type: {record.get('type')!r}")
            return None
        missing: list[str] = []

        def exists(state) -> bool:  # type: ignore[no-untyped-def]
            if Path(state["path"]).is_file():
                return True
            missing.append(Path(state["path"]).name)
            return False

        record = dict(record)
        if record["type"] in {"image", "video"}:
            usable = exists(record)
            if usable and record["type"] == "video":
                record["offset"] = _attached_video_offset(Path(record["folder"]), Path(record["path"]),
                                                          float(record.get("offset", 0.0)))
        elif "groups" in record:
            groups = [{**group, "axes": [axis if axis is not None and exists(axis) else None for axis in group["axes"]]}
                      for group in record["groups"]]
            record["groups"] = [group for group in groups if any(axis is not None for axis in group["axes"])]
            usable = bool(record["groups"])
        else:
            series = [entry for entry in record.get("series", []) if exists(entry)]
            filmstrips = [{**strip, "offset": _attached_video_offset(
                Path(strip["folder"]), Path(strip["path"]), float(strip.get("offset", 0.0)))}
                for strip in record.get("filmstrips", []) if exists(strip)]
            if record.get("lanes"):
                record["lanes"] = [lane for entry, lane in zip(record.get("series", []), record["lanes"])
                                   if entry in series]
            if record.get("colors"):
                record["colors"] = [color for entry, color in zip(record.get("series", []), record["colors"])
                                    if entry in series]
            record["series"], record["filmstrips"] = series, filmstrips
            usable = bool(series or filmstrips)
        if missing:
            problems.append(f"{title} card: missing {', '.join(dict.fromkeys(missing))}")
        return record if usable else None

    def _create_card(self, record: dict) -> PlotBaseWidget:
        """Create a card from its first saved source; `restore_workspace_state` adds the rest."""
        kind = record["type"]
        if kind == "image":
            return self.add_image_plot(Path(record["folder"]), Path(record["path"]))
        if kind == "video":
            return self.add_video_plot(Path(record["folder"]), Path(record["path"]), float(record.get("offset", 0.0)))
        if "groups" in record:
            first_group = [None if axis is None else AxisSource(*state_entry(axis), axis.get("component", "value"))
                           for axis in record["groups"][0]["axes"]]
            first = next((axis.path, axis.name) for group in record["groups"] for axis in (
                AxisSource(*state_entry(item)) for item in group["axes"] if item is not None))
            if kind == "xy":
                return self.add_xy_plot(first)
            if kind == "xyz":
                return self.add_xyz_plot(first)
            factory = self.add_gnss_plot if kind == "gnss" else self.add_gnss_3d_plot
            return factory(*first, initial_axes=tuple(first_group))
        entries = [state_entry(entry) for entry in record.get("series", [])]
        if kind == "multi":
            videos = [PlotSource("video", Path(strip["path"]), folder=Path(strip["folder"]), offset=strip["offset"])
                      for strip in record.get("filmstrips", [])]
            return self.add_multi_plot(entries[:1], [] if entries else videos[:1])
        if kind == "interval":
            return self.add_sample_interval_plot(entries[:1])
        factory = {"line": self.add_line_plot, "event": self.add_event_state_plot,
                   "frequency": self.add_frequency_plot}[kind]
        return factory(*entries[0])

    def _restore_time_ranges(self, cards) -> None:  # type: ignore[no-untyped-def]
        """Show each time card's saved time range again once the cards are laid out.

        Linked cards follow the card they are linked to; setting them as well would
        push a range back through the link while card widths still differ.
        """
        for card, record in cards:
            view = record.get("view")
            if (card in self.plots() and isinstance(card, (LinePlotWidget, EventStatePlotWidget, FrequencyPlotWidget))
                    and card.plot_view_box().linkedView(0) is None
                    and isinstance(view, list) and view and len(view[0]) == 2):
                card.plot_view_box().setXRange(float(view[0][0]), float(view[0][1]), padding=0)

    def _restore_markers(self, cards) -> None:  # type: ignore[no-untyped-def]
        """Place saved markers as if clicked: linked markers once, unlinked ones on each card."""
        marked = [(card, record["markers"]) for card, record in cards
                  if isinstance(card, TWO_MARKER_CARD_TYPES) and any(value is not None for value in record.get("markers", []))]
        for card, markers in marked[:1] if self.link_markers else marked:
            for index, value in enumerate(markers[:2]):
                if value is None:
                    continue
                card.set_vertical_marker(float(value), index)
                if index == SECOND_MARKER:
                    self.on_second_marker_placed(card, float(value))
                else:
                    self.on_marker_placed(card, float(value))

    def activate_plot(self, plot: PlotBaseWidget | None) -> None:
        if plot is not None and self.splitter.row_for(plot) is None:
            return
        if self.active_plot is plot:
            return
        if self.active_plot is not None:
            self.active_plot.set_active(False)
        self.active_plot = plot
        if plot is not None:
            plot.set_active(True)
            if not self.link_markers and hasattr(plot, "marker_values"):
                self._show_card_markers_in_navigator(plot)
        self.active_plot_changed.emit(plot)

    def _show_card_markers_in_navigator(self, plot: PlotBaseWidget) -> None:
        """Unlinked markers belong to their card; the Plot navigator follows the active card."""
        first, second = (None if value is None else self._marker_timestamp(plot, value)
                         for value in plot.marker_values())
        self._global_marker_timestamp, self._global_second_marker_timestamp = first, second
        self.global_marker_changed.emit(first)
        self.global_second_marker_changed.emit(second)

    def resize_plot(self, plot: PlotBaseWidget, delta: int) -> None:
        row = self.splitter.row_for(plot)
        if row is not None:
            self.resize_plot_at_index(self.splitter.indexOf(row), delta)

    def resize_plot_at_index(self, index: int, delta: int) -> None:
        if index < 0 or not delta:
            return
        sizes = self.splitter.sizes()
        if index >= len(sizes):
            return
        sizes[index] += delta
        self._set_plot_sizes(sizes)
        self.plot_layout_changed.emit()

    def reorder_plot(self, source: PlotBaseWidget, target: PlotBaseWidget, after: bool) -> None:
        """Navigator reordering always moves a card to its own vertical row."""
        self.move_plot(source, target, "below" if after else "above")

    def _card_dropped(self, source: PlotBaseWidget, target: PlotBaseWidget, position: str) -> None:
        """A card drag either moves the card, or adds a Video card's video to a Multi-series card."""
        if position == "filmstrip":
            self._add_filmstrips(target, [source.filmstrip_source()])
            self.activate_plot(target)
            return
        self.move_plot(source, target, position)

    def move_plot(self, source: PlotBaseWidget, target: PlotBaseWidget, position: str) -> bool:
        if not self.splitter.move_card(source, target, position):
            return False
        self.activate_plot(source)
        self.update_x_axis_links()
        self.update_time_axis_format_links()
        self.schedule_y_axis_alignment()
        self._emit_plot_navigator_changed()
        return True

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if any(event.mimeData().hasFormat(kind) for kind in (SELECTION_MIME_TYPE, SERIES_MIME_TYPE, VIDEO_MIME_TYPE, IMAGE_MIME_TYPE)):
            event.acceptProposedAction()

    def create_dropped_card(self, payload: dict, card_type: str, row_index: int) -> None:
        """Create the selected card and place it at the indicated row boundary."""
        self._open_dropped_payload(payload, card_type or None, row_index=row_index)

    def create_dropped_card_beside(self, payload: dict, target: PlotBaseWidget, position: str) -> None:
        """Create the default card for dropped data and place it beside ``target``."""
        self._open_dropped_payload(payload, None, beside=(target, position))

    def _open_dropped_payload(self, payload: dict, card_type, *, row_index=None, beside=None) -> None:
        if "items" in payload:
            sources = payload["items"]
        elif "series" in payload:
            sources = [PlotSource("series", *payload["series"])]
        elif "image_path" in payload:
            sources = [PlotSource("image", payload["image_path"], folder=payload["folder"])]
        else:
            sources = [PlotSource("video", payload["video_path"], folder=payload["folder"],
                                  offset=payload.get("offset_seconds", 0))]
        if "inspected" in payload:
            self.open_plot_inputs(payload["inspected"], card_type, row_index, beside)
        elif len(sources) == 1:
            # Fast early drops need no metadata to choose the single-item default.
            source = sources[0]
            supported = tuple(CARD_TITLES) if source.kind == "series" else (source.kind,)
            self.open_plot_inputs((PlotInput(source, supported),), card_type, row_index, beside)
        else:
            self.open_plot_sources(sources, card_type, row_index, beside)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasFormat(SELECTION_MIME_TYPE):
            try:
                sources = [PlotSource.from_payload(item) for item in json.loads(
                    bytes(event.mimeData().data(SELECTION_MIME_TYPE)).decode("utf-8"))]
                self.open_plot_sources(sources)
                event.acceptProposedAction()
            except (KeyError, TypeError, ValueError):
                event.ignore()
            return
        if event.mimeData().hasFormat(IMAGE_MIME_TYPE):
            try:
                payload = json.loads(bytes(event.mimeData().data(IMAGE_MIME_TYPE)).decode("utf-8"))
                self.add_image_plot(Path(str(payload["folder"])), Path(str(payload["image_path"])))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                event.ignore()
                return
            event.acceptProposedAction()
            return
        if event.mimeData().hasFormat(VIDEO_MIME_TYPE):
            try:
                payload = json.loads(bytes(event.mimeData().data(VIDEO_MIME_TYPE)).decode("utf-8"))
                self.add_video_plot(
                    Path(str(payload["folder"])),
                    Path(str(payload["video_path"])),
                    float(payload.get("offset_seconds", 0.0)),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                event.ignore()
                return
            event.acceptProposedAction()
            return
        series = series_from_mime(event.mimeData())
        if series is None:
            return
        self.add_new_plot(*series)
        event.acceptProposedAction()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if watched is self.viewport() or watched is self.splitter:
            if (event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton
                    and watched.childAt(event.position().toPoint()) is None):
                # Card clicks can propagate here; only bare background clears
                # the active card, not its canvas, controls, or splitter handles.
                self.activate_plot(None)
            # QScrollArea routes drops over empty space to its viewport.
            if event.type() == QEvent.Type.DragEnter:
                self.dragEnterEvent(event)
                return event.isAccepted()
            if event.type() == QEvent.Type.Drop:
                self.dropEvent(event)
                return event.isAccepted()
        return super().eventFilter(watched, event)

    def _update_workspace_minimum_height(self, *_args) -> None:
        minimum = sum(self.splitter.minimum_row_height(self.splitter.widget(index))
                      for index in range(self.splitter.count()))
        minimum += max(0, self.splitter.count() - 1) * self.splitter.handleWidth()
        self.splitter.setMinimumHeight(minimum)

    def _set_plot_sizes(self, sizes: list[int]) -> None:
        """Keep the stack exactly as high as its rows, not its viewport."""
        self.splitter.set_row_sizes(sizes)

    def _sync_content_width(self) -> None:
        """QScrollArea no longer resizes the content automatically vertically.

        It must nevertheless receive the available viewport width; otherwise a
        fixed-height splitter can retain a zero-width initial size.
        """
        width = max(1, self.viewport().width())
        self.splitter.setFixedWidth(width)


def agent_missing_message(provider: AgentProvider) -> str:
    return (f"The {provider.product} CLI was not found. Install {provider.product}, or set its path "
            "in Settings > General > AI.")


class LODGaZillaWindow(QMainWindow):
    """Main application window hosting the navigator and plot workspace."""

    def __init__(
        self,
        data_folder: Path | None = None,
        *,
        settings_path: str | Path = DEFAULT_APP_SETTINGS_PATH,
    ) -> None:
        configure_graphics()
        super().__init__()
        # The GUI owns one deferred settings session for its lifetime.  The
        # normal entry point starts it first; this also covers embedded use.
        begin_app_settings_cache(settings_path)
        startup_mark("Loading settings and theme…")
        self.settings_path = Path(settings_path)
        self._settings_flush_timer = QTimer(self)
        self._settings_flush_timer.setSingleShot(True)
        self._settings_flush_timer.setInterval(SETTINGS_FLUSH_DELAY_MS)
        self._settings_flush_timer.timeout.connect(self.flush_settings)
        set_app_settings_changed_callback(self.schedule_settings_flush, self.settings_path)
        self.lodgazilla_settings = load_lodgazilla_settings(self.settings_path)
        self.max_background_processes = int(self.lodgazilla_settings["max_background_processes"])
        self._splitter_save_retry_pending = False
        self.import_coordinator = ImportJobCoordinator(self.max_background_processes)
        # Compatibility aliases keep the UI/progress layer read-only with
        # respect to scheduler-owned collections.
        self.active_imports = self.import_coordinator.active_imports
        self.import_queue = self.import_coordinator.queue
        self.process_progress_started_at: dict[str, ProcessProgressState] = {}
        self.completed_processes_pending_refresh: set[str] = set()
        self.tree_refresh_pending = False
        apply_theme(load_theme(self.settings_path))
        self.setWindowTitle("LODGaZilla")
        self.setWindowIcon(QIcon(str(lodgazilla_asset_path(load_theme(self.settings_path), "icon"))))
        self.resize(1200, 760)

        startup_mark("Loading data navigator…")
        self.navigator = DataNavigatorWidget(data_folder, settings_path=self.settings_path, defer_initial_load=True)
        startup_mark("data navigator construction completed")
        startup_mark("Creating plot workspace…")
        startup_mark("plot workspace construction started")
        self.plot_workspace = PlotWorkspace(
            self.lodgazilla_settings["default_plot_heights"], settings_path=self.settings_path
        )
        startup_mark("plot workspace construction completed")
        startup_mark("plot navigator construction started")
        self.plot_navigator = PlotNavigatorWidget()
        self.plot_navigator.settings_requested.connect(self.show_settings)
        startup_mark("plot navigator construction completed")
        self.plot_workspace.plot_navigator_changed.connect(self.plot_navigator.set_entries)
        self.plot_workspace.global_marker_changed.connect(self.plot_navigator.set_marker)
        self.plot_workspace.global_second_marker_changed.connect(self.plot_navigator.set_second_marker)
        self.plot_workspace.sync_marker_changed.connect(self.plot_navigator.set_sync_marker)
        self._plot_navigator_alignment_timer = QTimer(self)
        self._plot_navigator_alignment_timer.setSingleShot(True)
        self._plot_navigator_alignment_timer.setInterval(50)
        self._plot_navigator_alignment_timer.timeout.connect(self.align_plot_navigator_to_plot_area)
        self.plot_workspace.plot_layout_changed.connect(self.schedule_plot_navigator_alignment)
        self.plot_navigator.expanded_changed.connect(self.on_plot_navigator_expanded_changed)
        self.plot_navigator.plot_activated.connect(self.plot_workspace.activate_plot)
        self.plot_workspace.active_plot_changed.connect(self.plot_navigator.set_active_plot)
        self.plot_navigator.range_changed.connect(self.plot_workspace.set_plot_navigator_range)
        self.plot_navigator.range_reset_requested.connect(self.plot_workspace.reset_plot_navigator_range)
        self.plot_navigator.offset_dragged.connect(self.plot_workspace.shift_plot_time_offset)
        self.plot_navigator.offset_reset_requested.connect(self.plot_workspace.reset_plot_time_offset)
        self.plot_workspace.linked_view_range_changed.connect(self.plot_navigator.set_view_range)
        self.plot_navigator.view_range_changed.connect(self.plot_workspace.set_linked_view_range)
        self.plot_navigator.reorder_requested.connect(self.plot_workspace.reorder_plot)
        self.plot_navigator.marker_placed.connect(self.plot_workspace.place_marker_from_plot_navigator)
        self.plot_navigator.marker_cleared.connect(self.plot_workspace.clear_marker_from_plot_navigator)
        self.plot_navigator.second_marker_placed.connect(self.plot_workspace.place_second_marker_from_plot_navigator)
        self.plot_navigator.second_marker_cleared.connect(self.plot_workspace.clear_second_marker_from_plot_navigator)
        self.plot_navigator.close_all_requested.connect(self.plot_workspace.close_all_plots)
        self.plot_navigator.sample_loss_visibility_changed.connect(self.set_sample_loss_area_visibility)
        self.plot_navigator.clipping_visibility_changed.connect(self.set_clipping_area_visibility)
        self.plot_navigator.marker_info_display_changed.connect(self.set_marker_info_display)
        self.plot_navigator.time_axis_display_changed.connect(self.set_time_axis_display)
        self.plot_navigator.legends_visibility_changed.connect(self.set_legends_shown)
        self.plot_navigator.card_controls_visibility_changed.connect(self.set_card_controls_shown)
        self.plot_navigator.snap_to_nearest_changed.connect(self.set_snap_to_nearest)
        self.plot_navigator.marker_link_changed.connect(self.set_markers_linked)
        self.plot_navigator.plot_views_link_changed.connect(self.set_plot_views_linked)
        startup_mark("plot workspace and navigator signal wiring completed")
        startup_mark("Creating application layout…")
        self.plot_area = QWidget()
        plot_area_layout = QVBoxLayout(self.plot_area)
        plot_area_layout.setContentsMargins(0, 0, 0, 0)
        plot_area_layout.setSpacing(0)
        plot_area_layout.addWidget(self.plot_navigator)
        plot_area_layout.addWidget(self.plot_workspace, stretch=1)
        # The AI panel sits below the Navigator; minimized it is one AI Integration row.
        self.ai_panel = AIPanel()
        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.setChildrenCollapsible(False)
        self._left_splitter_handle_width = self.left_splitter.handleWidth()
        self.left_splitter.addWidget(self.navigator)
        self.left_splitter.addWidget(self.ai_panel)
        self.left_splitter.setStretchFactor(0, 1)
        self.left_splitter.setStretchFactor(1, 0)
        self.ai_panel.expanded_changed.connect(self.on_ai_panel_expanded_changed)
        self.ai_panel.conversation_started.connect(self.on_ai_conversation_started)
        # Magic drop: files dropped on the AI panel become H5 files with Claude or Codex.
        self.magic_drop_job: MagicDropJob | None = None
        # Create plugin: the plugin being built (plugin/<id>), and each provider's session in it.
        self.plugin_chat_job: PluginChatJob | None = None
        self.plugin_chat_id: str | None = None
        self._plugin_chat_sessions: dict[str, str] = {}
        self._magic_drop_queue: list[tuple[list[Path], str, AgentProvider]] = []
        self._sign_in_checks: dict[str, SignInCheck] = {}
        for provider in PROVIDERS.values():
            check = SignInCheck(provider, self)
            check.checked.connect(lambda signed_in, name=provider.name: self.ai_panel.set_signed_in(name, signed_in))
            self._sign_in_checks[provider.name] = check
        self.ai_panel.provider_changed.connect(lambda _name: self.check_ai_sign_in())
        self.ai_panel.files_dropped.connect(self.start_magic_drop)
        self.ai_panel.stop_requested.connect(self.stop_ai)
        self.ai_panel.question_submitted.connect(self.on_ai_question)
        self.ai_panel.sign_in_requested.connect(self.sign_in_to_ai)
        # Queued: the panel opens during startup too, and finding the CLI should not delay the window.
        self.ai_panel.expanded_changed.connect(
            lambda expanded: expanded and QTimer.singleShot(0, self.check_ai_sign_in))
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.left_splitter)
        self.main_splitter.addWidget(self.plot_area)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.splitterMoved.connect(self.save_splitter_position)
        startup_mark("main application layout constructed")
        internal_log_path = log_path("lodgazilla.log")
        self.internal_logger = InternalLogger(internal_log_path, self)
        install_qt_message_logging(self.internal_logger)
        self.log_panel = InternalLogPanel(
            self.internal_logger,
            self,
            maximum_lines=int(self.lodgazilla_settings["internal_log_maximum_lines"]),
        )
        self.native_stderr_capture = NativeStderrLogCapture(self.internal_logger)
        self.native_stderr_capture.start()
        self.content_splitter = QSplitter(Qt.Orientation.Vertical)
        self.content_splitter.setChildrenCollapsible(True)
        self._content_splitter_handle_width = self.content_splitter.handleWidth()
        self.content_splitter.addWidget(self.main_splitter)
        self.content_splitter.addWidget(self.log_panel)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 0)
        self.content_splitter.splitterMoved.connect(self.on_content_splitter_moved)
        self.setCentralWidget(self.content_splitter)
        self.log_panel.expanded_changed.connect(self.on_log_panel_expanded_changed)
        startup_mark("internal logging panel constructed")
        self._log_panel_settings_timer = QTimer(self)
        self._log_panel_settings_timer.setSingleShot(True)
        self._log_panel_settings_timer.setInterval(250)
        self._log_panel_settings_timer.timeout.connect(self.save_log_panel_preferences)
        self.navigator.series_activated.connect(self.plot_workspace.activate_series)
        self.navigator.gnss_group_activated.connect(self.plot_workspace.add_gnss_plot)
        self.navigator.video_activated.connect(self.plot_workspace.add_video_plot)
        self.navigator.video_context_menu_requested.connect(self.show_video_context_menu)
        self.navigator.image_activated.connect(self.plot_workspace.add_image_plot)
        self.navigator.image_context_menu_requested.connect(self.show_image_context_menu)
        self.plot_workspace.video_offset_saved.connect(
            lambda _folder: self.navigator.refresh_tree()
        )
        self.navigator.series_context_menu_requested.connect(self.show_series_context_menu)
        self.navigator.series_group_context_menu_requested.connect(self.show_series_group_context_menu)
        self.navigator.folder_import_requested.connect(self.start_folder_cache_rebuild)
        self.navigator.folder_context_menu_requested.connect(self.show_folder_context_menu)
        self.navigator.folders_context_menu_requested.connect(self.show_folders_context_menu)
        self.navigator.metadata_write_failed.connect(self.on_metadata_write_failed)
        self.navigator.navigator_warning.connect(self.on_navigator_warning)
        self.navigator.loading_changed.connect(self.on_navigator_loading_changed)
        # What is still loading in the background, logged in the Internal log panel.
        self._navigator_loading = False
        self._loading_cards = False
        self._loading_logged: dict[str, bool] = {}
        self._card_loader = CardModuleLoader(self)
        self._card_loader.finished.connect(self.on_card_modules_loaded)
        self.plot_workspace.plot_status_changed.connect(self.internal_logger.info)
        self.event_timer = QTimer(self)
        self.event_timer.setInterval(150)
        self.event_timer.timeout.connect(self.poll_import_events)

        startup_mark("Loading plugins…")
        startup_mark("plugin manager construction started")
        self.plugin_manager = PluginManager(self, log=self.internal_logger.info)
        self.plugin_updates = PluginUpdates(self)
        self.plugin_updates.changed.connect(self.report_plugin_updates)
        QTimer.singleShot(0, self.check_plugin_updates_at_startup)
        startup_mark("plugin manager construction completed")

        # No menu bar: workspace files are on the Navigator's File bar, Settings and
        # Help on the Plot navigator.
        # The workspace (.umw) file last opened or saved; Save writes to it again.
        self.workspace_path: Path | None = None
        controls = self.navigator.controls
        controls.workspace_open_requested.connect(self.open_workspace)
        controls.workspace_save_requested.connect(self.save_workspace)
        controls.workspace_save_as_requested.connect(self.save_workspace_as)
        self.batch_menu = controls.batch_menu
        self.batch_menu.aboutToShow.connect(self.populate_batch_menu)
        self.plot_navigator.plugin_launch_requested.connect(self.plugin_manager.launch)
        self.recent_workspaces_menu = controls.recent_workspaces_menu
        self.recent_workspaces_menu.aboutToShow.connect(self.populate_recent_workspaces_menu)
        for keys, slot in (("Ctrl+O", self.open_workspace), ("Ctrl+S", self.save_workspace),
                           ("Ctrl+Shift+S", self.save_workspace_as)):
            QShortcut(QKeySequence(keys), self, slot)
        self.plot_navigator.help_requested.connect(self.show_help)
        self.about_dialog: AboutDialog | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.demo_data_job: DemoDataJob | None = None
        self.demo_data_progress: QProgressDialog | None = None
        self._apply_runtime_settings()
        startup_mark("file bar and runtime settings configured")
        self.internal_logger.info(f"Internal log started: {internal_log_path}")
        self._log_version()
        self.plot_workspace._emit_plot_navigator_changed()
        QTimer.singleShot(0, self.schedule_plot_navigator_alignment)
        QTimer.singleShot(0, self.restore_splitter_position)
        QTimer.singleShot(0, self.restore_log_panel_size)
        QTimer.singleShot(0, lambda: self.size_ai_panel(self.ai_panel.is_expanded))
        startup_mark("LODGaZilla is ready")

    def schedule_settings_flush(self) -> None:
        """Debounce writes of the deferred settings session after each change."""
        self._settings_flush_timer.start(SETTINGS_FLUSH_DELAY_MS)

    def flush_settings(self) -> bool:
        """Write pending settings now; retry later if the file is locked."""
        if flush_app_settings_cache(self.settings_path):
            return True
        self._settings_flush_timer.start(SETTINGS_FLUSH_RETRY_MS)
        return False

    def workspace_state(self) -> dict:
        """The Navigator, Plot navigator and Plot workspace, as saved in a .umw file."""
        workspace = self.plot_workspace
        return {
            "navigator": self.navigator.workspace_state(),
            "plot_navigator": {
                "link_markers": workspace.link_markers,
                "link_plot_x_axes": workspace.link_x_axes,
                "time_axis_display": workspace.time_axis_display,
                "marker_info_display": workspace.marker_info_display,
                "snap_to_nearest": workspace.snap_vertical_marker_to_nearest_point,
                "show_legends": workspace.legends_shown,
                "show_card_controls": workspace.card_controls_shown,
            },
            "workspace": workspace.workspace_state(),
        }

    def apply_workspace_state(self, state: dict) -> list[str]:
        """Reset the workspace to a saved one; return what could not be restored."""
        controls = state.get("plot_navigator", {})
        # Linking first: it decides how saved markers are placed.
        for key, apply, show in (
            ("link_markers", self.set_markers_linked, self.plot_navigator.set_markers_linked),
            ("link_plot_x_axes", self.set_plot_views_linked, self.plot_navigator.set_plot_views_linked),
            ("time_axis_display", self.set_time_axis_display, self.plot_navigator.set_time_axis_display),
            ("marker_info_display", self.set_marker_info_display, self.plot_navigator.set_marker_info_display),
            ("snap_to_nearest", self.set_snap_to_nearest, self.plot_navigator.set_snap_to_nearest),
            ("show_legends", self.set_legends_shown, self.plot_navigator.set_legends_shown),
            ("show_card_controls", self.set_card_controls_shown, self.plot_navigator.set_card_controls_shown),
        ):
            if key in controls:
                apply(controls[key])
                show(controls[key])
        self.navigator.restore_workspace_state(state.get("navigator", {}))
        return self.plot_workspace.restore_workspace_state(state.get("workspace", {}))

    def open_workspace(self) -> None:
        folder = self.workspace_path.parent if self.workspace_path else self.navigator.data_folder()
        selected, _filter = QFileDialog.getOpenFileName(
            self, "Open workspace", str(folder or ""), f"{UMW_FILE_FILTER};;All files (*)")
        if selected:
            self.open_workspace_file(Path(selected))

    def open_workspace_file(self, path: Path) -> bool:
        try:
            state = read_workspace_file(path)
        except WorkspaceFileError as error:
            self.internal_logger.error(str(error))
            QMessageBox.warning(self, "Open workspace", str(error))
            if not Path(path).is_file():
                self._update_lodgazilla_settings(lambda settings: settings.__setitem__("recent_workspaces", [
                    item for item in settings["recent_workspaces"] if Path(item).resolve() != Path(path).resolve()]))
            return False
        problems = self.apply_workspace_state(state)
        self._workspace_file_used(Path(path))
        for problem in problems:
            self.internal_logger.warning(f"Workspace {Path(path).name}: {problem}")
        message = f"Opened workspace {Path(path).name}"
        if problems:
            message += f"; {len(problems)} item(s) could not be restored, see the log"
        self.internal_logger.info(message)
        return True

    def save_workspace(self) -> None:
        if self.workspace_path is None:
            self.save_workspace_as()
        else:
            self.write_workspace(self.workspace_path)

    def save_workspace_as(self) -> None:
        folder = self.workspace_path.parent if self.workspace_path else self.navigator.data_folder()
        name = self.workspace_path.name if self.workspace_path else f"workspace{UMW_SUFFIX}"
        selected, _filter = QFileDialog.getSaveFileName(
            self, "Save workspace", str(Path(folder or Path.home()) / name), UMW_FILE_FILTER)
        if selected:
            self.write_workspace(Path(selected))

    def write_workspace(self, path: Path) -> bool:
        try:
            path = write_workspace_file(path, self.workspace_state())
        except (OSError, TypeError, ValueError) as error:
            message = f"Could not save workspace {Path(path).name}: {error}"
            self.internal_logger.error(message)
            QMessageBox.warning(self, "Save workspace", message)
            return False
        self._workspace_file_used(path)
        self.internal_logger.info(f"Saved workspace {path}")
        return True

    def _workspace_file_used(self, path: Path) -> None:
        self.workspace_path = Path(path).resolve()
        self.setWindowTitle(f"LODGaZilla - {self.workspace_path.name}")
        self._update_lodgazilla_settings(lambda settings: settings.__setitem__(
            "recent_workspaces", updated_recent_workspaces(settings["recent_workspaces"], self.workspace_path)))

    def populate_recent_workspaces_menu(self) -> None:
        menu = self.recent_workspaces_menu
        menu.clear()
        recent = list(self.lodgazilla_settings.get("recent_workspaces", []))
        for item in recent:
            action = menu.addAction(Path(item).name, lambda path=Path(item): self.open_workspace_file(path))
            action.setToolTip(item)
            action.setStatusTip(item)
        if not recent:
            menu.addAction("No recent workspaces").setEnabled(False)

    def _log_version(self) -> None:
        """Log the running version; asking Git takes most of a second, so not on the GUI thread."""
        logger = self.internal_logger

        def run() -> None:
            try:
                logger.info(f"LODGaZilla {version_text()}")
            except RuntimeError:
                pass  # The window closed first.

        threading.Thread(target=run, name="version", daemon=True).start()

    def show_help(self) -> None:
        """The Plot navigator's Help button: the About dialog, whose button opens README.md."""
        if self.about_dialog is None:
            dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
            self.about_dialog = AboutDialog(lodgazilla_asset_path("dark" if dark else "light", "big"),
                                            version_text(), self)
            self.about_dialog.readme_requested.connect(self.open_readme)
        self.about_dialog.show()
        self.about_dialog.raise_()
        self.about_dialog.activateWindow()

    def open_readme(self) -> None:
        """Open README.md in the application the system uses for Markdown files."""
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(README_PATH))):
            self.internal_logger.warning(f"Could not open {README_PATH}: no application opens Markdown files")

    def restore_splitter_position(self) -> None:
        available_width = max(1, self.main_splitter.width())
        navigator_width = round(available_width * float(self.lodgazilla_settings["navigator_width_ratio"]))
        self.main_splitter.setSizes([navigator_width, max(1, available_width - navigator_width)])

    def restore_log_panel_size(self) -> None:
        total_height = max(1, self.content_splitter.height())
        if not self.log_panel.is_expanded:
            self.collapse_log_panel_to_mini_row()
            return
        log_height = min(
            int(self.lodgazilla_settings["internal_log_expanded_height"]),
            max(80, total_height - 80),
        )
        self.content_splitter.setSizes([max(1, total_height - log_height), log_height])

    def on_log_panel_expanded_changed(self, expanded: bool) -> None:
        handle = self.content_splitter.handle(1)
        self.content_splitter.setHandleWidth(
            self._content_splitter_handle_width if expanded else 0
        )
        handle.setVisible(expanded)
        if expanded:
            QTimer.singleShot(0, self.restore_log_panel_size)
        else:
            QTimer.singleShot(0, self.collapse_log_panel_to_mini_row)
        self.schedule_save_log_panel_height()

    def schedule_save_log_panel_height(self, *_args) -> None:  # type: ignore[no-untyped-def]
        if hasattr(self, "_log_panel_settings_timer"):
            self._log_panel_settings_timer.start()

    def on_content_splitter_moved(self, *_args) -> None:  # type: ignore[no-untyped-def]
        """Treat dragging the log pane to its mini-row height as collapse."""
        sizes = self.content_splitter.sizes()
        if (
            self.log_panel.isVisible()
            and self.log_panel.is_expanded
            and len(sizes) > 1
            and sizes[1] <= self.log_panel.MINI_HEIGHT
        ):
            self.log_panel.set_expanded(False)
            return
        self.schedule_save_log_panel_height()

    def save_log_panel_preferences(self) -> None:
        """Persist the mini/expanded state and only the useful expanded height."""
        def update(settings: dict[str, object]) -> None:
            settings["internal_log_expanded"] = self.log_panel.is_expanded
            if self.log_panel.is_expanded:
                settings["internal_log_expanded_height"] = max(80, self.log_panel.height())

        self._update_lodgazilla_settings(update)

    def _update_lodgazilla_settings(self, update: Callable[[dict[str, object]], None]) -> bool:
        """Sanitize, mutate, and persist the LODGaZilla settings section once."""
        settings = sanitize_lodgazilla_settings(load_settings_section(LODGAZILLA_SECTION, self.settings_path))
        update(settings)
        settings = sanitize_lodgazilla_settings(settings)
        if save_settings_section(self.settings_path, LODGAZILLA_SECTION, settings):
            self.lodgazilla_settings = settings
            return True
        return False

    def _update_plot_settings(self, update: Callable[[dict[str, object]], None]) -> bool:
        """Sanitize, mutate, and persist the plot settings section once."""
        settings = sanitize_plot_settings(load_settings_section(PLOT_SECTION, self.settings_path))
        update(settings)
        return save_settings_section(
            self.settings_path, PLOT_SECTION, sanitize_plot_settings(settings)
        )

    def collapse_log_panel_to_mini_row(self) -> None:
        """Remove the unused splitter space and resize handle in mini mode."""
        mini_height = max(self.log_panel.MINI_HEIGHT, self.log_panel.height())
        total_height = max(1, self.content_splitter.height())
        self.content_splitter.setSizes([max(1, total_height - mini_height), mini_height])
        self.content_splitter.setHandleWidth(0)
        self.content_splitter.handle(1).hide()

    def check_plugin_updates_at_startup(self) -> None:
        settings = load_settings_section(PLUGINS_SECTION, self.settings_path)
        sources = settings.get("sources", DEFAULT_PLUGIN_SOURCES)
        if settings.get("check_at_startup", True) and isinstance(sources, list) and sources:
            self.plugin_updates.check(sources)

    def report_plugin_updates(self) -> None:
        message = self.plugin_updates.message
        if message:
            self.internal_logger.info(message + " See Settings > Plugins.")
        for error in self.plugin_updates.errors:
            self.internal_logger.warning(error)

    def reload_plugin_settings(self) -> None:
        plugin_settings = load_settings_section(PLUGINS_SECTION, self.settings_path)
        enabled = plugin_settings.get("enabled", {}) if isinstance(plugin_settings, dict) else {}
        enabled = dict(enabled) if isinstance(enabled, dict) else {}
        changed = False
        for descriptor in self.plugin_manager.descriptors:
            if descriptor.plugin_id not in enabled:
                enabled[descriptor.plugin_id] = False
                changed = True
        if changed:
            save_settings_section(self.settings_path, PLUGINS_SECTION, {**plugin_settings, "enabled": enabled})
        self.plugin_manager.set_enabled(enabled)

    def on_ai_panel_expanded_changed(self, expanded: bool) -> None:
        """Size the AI panel and remember whether it is open for the next start."""
        self.size_ai_panel(expanded)
        # Restoring the saved state at startup changes nothing, so the file is not rewritten.
        if bool(self.lodgazilla_settings["ai_panel_expanded"]) != bool(expanded):
            self._update_lodgazilla_settings(
                lambda settings: settings.__setitem__("ai_panel_expanded", bool(expanded))
            )

    def size_ai_panel(self, expanded: bool) -> None:
        """Expanded, the AI panel opens at its minimum height, or half the left side once its
        AI conversation is open; minimized, it is its one row."""
        self.left_splitter.setHandleWidth(self._left_splitter_handle_width if expanded else 0)
        self.left_splitter.handle(1).setVisible(expanded)
        total = max(1, self.left_splitter.height() - self.left_splitter.handleWidth())
        if not expanded:
            self.left_splitter.setSizes([max(1, total - AIPanel.MINI_HEIGHT), AIPanel.MINI_HEIGHT])
            return
        height = total // 2 if self.ai_panel.mode is not None else AIPanel.EXPANDED_MIN_HEIGHT
        self.left_splitter.setSizes([max(1, total - height), height])

    def on_ai_conversation_started(self) -> None:
        """The AI conversation needs room: grow the AI panel to half the left side, never shrink it."""
        total = max(1, self.left_splitter.height() - self.left_splitter.handleWidth())
        if self.left_splitter.sizes()[1] < total // 2:
            self.left_splitter.setSizes([total - total // 2, total // 2])

    # Magic drop.

    def agent_executable(self, provider: AgentProvider) -> Path | None:
        """The provider's CLI: its path in Settings, or found automatically."""
        return provider.find(load_ai_settings(self.settings_path)[provider.path_setting] or None)

    def check_ai_sign_in(self) -> None:
        """Show in the AI panel whether the chosen provider's CLI is signed in; checked in the background."""
        provider = provider_named(self.ai_panel.provider)
        self._sign_in_checks[provider.name].start(self.agent_executable(provider))

    def sign_in_to_ai(self, name: str) -> None:
        provider = provider_named(name)
        executable = self.agent_executable(provider)
        if executable is None:
            self.ai_panel.append_note(agent_missing_message(provider))
            return
        try:
            provider.start_sign_in(executable)
        except OSError as error:
            self.ai_panel.append_note(f"Could not start the {provider.name} sign-in: {error}")
            return
        self.ai_panel.append_note(f"Sign in to {provider.name} in the window that opened, then drop your files again.")
        # The CLI's sign-in has no completion signal here; check again after a while.
        for delay in (15_000, 45_000, 90_000):
            QTimer.singleShot(delay, self.check_ai_sign_in)

    def start_magic_drop(self, files: list[Path], hints: str) -> None:
        """Turn files dropped on Magic drop into H5 files in a new folder of the data folder,
        with the provider chosen in the AI panel."""
        panel = self.ai_panel
        provider = provider_named(panel.provider)
        data_folder = self.navigator.data_folder()
        if data_folder is None or not Path(data_folder).is_dir():
            panel.append_note("Choose a data folder first; Magic drop creates its folder there.")
            return
        executable = self.agent_executable(provider)
        if executable is None:
            panel.append_note(agent_missing_message(provider))
            return
        if not self.confirm_magic_drop_consent(provider):
            panel.append_note("Magic drop cancelled: nothing was sent.")
            return
        groups = [list(files)]
        if len(files) > 1:
            choice = self.ask_magic_drop_grouping(len(files))
            if choice is None:
                panel.append_note("Magic drop cancelled: nothing was sent.")
                return
            groups = [[path] for path in files] if choice == "each" else groups
        self._magic_drop_queue.extend((group, hints, provider) for group in groups)
        if self.magic_drop_job is None:
            self._start_next_magic_drop(Path(data_folder))
        else:
            panel.append_note("Queued; it starts when the current Magic drop finishes.")

    def confirm_magic_drop_consent(self, provider: AgentProvider) -> bool:
        """Ask once per provider whether dropped files may be sent to it; the answer is saved in Settings."""
        settings = load_ai_settings(self.settings_path)
        if settings[provider.consent_setting]:
            return True
        name, vendor = provider.name, provider.vendor
        web = (f" {name} can also read web pages, such as a file format's specification."
               if settings[provider.web_setting] else "")
        answer = QMessageBox.question(
            self, "AI Integration",
            f"Magic drop and Create plugin send the files you drop here, and your questions, to {name} "
            f"({vendor}). {name} may read the whole files.{web}\n\nDo not drop files with personal data, "
            "such as location or heart rate of real people, unless you are allowed to share them.\n\n"
            f"Allow sending dropped files to {name}? You can withdraw this in Settings.")
        if answer != QMessageBox.StandardButton.Yes:
            return False
        save_settings_section(self.settings_path, AI_SECTION, {**settings, provider.consent_setting: True})
        return True

    def ask_magic_drop_grouping(self, count: int) -> str | None:
        """For several files: "one" folder for all of them, a folder for "each" file, or None to cancel."""
        box = QMessageBox(QMessageBox.Icon.Question, "Magic drop",
                          f"You dropped {count} files. Are they one recording?", parent=self)
        one = box.addButton("One folder for all", QMessageBox.ButtonRole.AcceptRole)
        each = box.addButton("One folder per file", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return {one: "one", each: "each"}.get(box.clickedButton())

    def _start_next_magic_drop(self, data_folder: Path) -> None:
        while self._magic_drop_queue:
            files, hints, provider = self._magic_drop_queue.pop(0)
            executable = self.agent_executable(provider)
            if executable is not None:
                break
            self.ai_panel.append_note(agent_missing_message(provider))
        else:
            self.ai_panel.set_busy(False)
            return
        settings = load_ai_settings(self.settings_path)
        job = MagicDropJob(executable, files, data_folder, hints=hints, max_budget_usd=settings["max_budget_usd"],
                           time_limit_seconds=60.0 * settings["time_limit_minutes"], provider=provider,
                           web=settings[provider.web_setting], parent=self)
        job.event.connect(lambda kind, text: self.on_magic_drop_event(kind, text, provider.name))
        job.finished.connect(lambda ok, message, folder: self.on_magic_drop_finished(
            ok, message, folder, data_folder, provider))
        self.magic_drop_job = job
        self.ai_panel.set_busy(True)
        self.internal_logger.info(f"Magic drop started with {provider.name}: {', '.join(path.name for path in files)}")
        job.start()

    def on_magic_drop_event(self, kind: str, text: str, agent: str = "") -> None:
        if kind in ("text", "result"):
            self.ai_panel.append_agent_message(text, agent)
        else:
            self.ai_panel.append_note(text)

    def on_magic_drop_finished(self, succeeded: bool, message: str, folder: Path | None,
                               data_folder: Path, provider: AgentProvider) -> None:
        job, self.magic_drop_job = self.magic_drop_job, None
        if job is not None:
            job.deleteLater()
        self.ai_panel.append_note(message)
        (self.internal_logger.info if succeeded else self.internal_logger.warning)(f"Magic drop: {message}")
        if succeeded:
            self.ai_panel.set_signed_in(provider.name, True)
            current = self.navigator.data_folder()
            if current is not None and Path(current) == data_folder:
                self.navigator.refresh_tree(rescan=True)
        self._start_next_magic_drop(data_folder)

    def stop_magic_drop(self) -> None:
        self._magic_drop_queue.clear()
        if self.magic_drop_job is not None:
            self.ai_panel.append_note("Stopping…")
            self.magic_drop_job.cancel()

    def stop_ai(self) -> None:
        """The AI panel's Stop button: stop Magic drop and the Create plugin question in progress."""
        self.stop_magic_drop()
        if self.plugin_chat_job is not None:
            self.ai_panel.append_note("Stopping…")
            self.plugin_chat_job.cancel()

    # Create plugin.

    def on_ai_question(self, text: str, files: list) -> None:
        """A question in the AI panel: to the agent while creating a plugin, otherwise a note."""
        panel = self.ai_panel
        if panel.mode != CREATE_PLUGIN:
            panel.append_note(NOT_CONNECTED_REPLY)
            return
        if self.plugin_chat_job is not None or self.magic_drop_job is not None:
            panel.append_note("Wait until the AI has finished; then ask again.")
            return
        provider = provider_named(panel.provider)
        executable = self.agent_executable(provider)
        if executable is None:
            panel.append_note(agent_missing_message(provider))
            return
        if not self.confirm_magic_drop_consent(provider):
            panel.append_note("Cancelled: nothing was sent.")
            return
        if self.plugin_chat_id is None:
            plugin_id = self.ask_plugin_id(files)
            if plugin_id is None:
                panel.append_note("Cancelled: nothing was sent.")
                return
            self.plugin_chat_id = plugin_id
            self._plugin_chat_sessions = {}
        settings = load_ai_settings(self.settings_path)
        job = PluginChatJob(executable, self.plugin_chat_id, text, [Path(path) for path in files],
                            session_id=self._plugin_chat_sessions.get(provider.name, ""), provider=provider,
                            web=settings[provider.web_setting], max_budget_usd=settings["max_budget_usd"],
                            time_limit_seconds=60.0 * settings["time_limit_minutes"], parent=self)
        job.event.connect(lambda kind, event_text: self.on_magic_drop_event(kind, event_text, provider.name))
        job.finished.connect(lambda ok, note, session: self.on_plugin_chat_finished(ok, note, session, provider))
        self.plugin_chat_job = job
        panel.set_busy(True)
        self.internal_logger.info(f"Create plugin: question to {provider.name} for plugin/{self.plugin_chat_id}")
        job.start()

    def ask_plugin_id(self, files: list) -> str | None:
        """The new plugin's ID, which names its folder plugin/<id>; None when cancelled."""
        suggestion = plugin_id_from(Path(files[0]).stem if files else "my_format")
        message = "Plugin ID (lowercase letters, digits and underscores). It becomes the folder plugin/<id>:"
        while True:
            plugin_id, accepted = QInputDialog.getText(self, "Create plugin", message, text=suggestion)
            if not accepted:
                return None
            plugin_id = plugin_id.strip()
            problem = check_plugin_id(plugin_id)
            if not problem:
                return plugin_id
            suggestion = plugin_id
            message = f"{problem}\nPlugin ID:"

    def on_plugin_chat_finished(self, succeeded: bool, note: str, session_id: str, provider: AgentProvider) -> None:
        job, self.plugin_chat_job = self.plugin_chat_job, None
        if job is not None:
            job.deleteLater()
        if session_id:
            self._plugin_chat_sessions[provider.name] = session_id
        if note:
            self.ai_panel.append_note(note)
        if succeeded:
            self.ai_panel.set_signed_in(provider.name, True)
        else:
            self.internal_logger.warning(f"Create plugin: {note}")
        self.ai_panel.set_busy(self.magic_drop_job is not None)

    def on_metadata_write_failed(self, folder: Path, error: str) -> None:
        self.internal_logger.error(f"Could not save folder metadata for {folder.name}: {error}")

    def on_navigator_warning(self, message: str) -> None:
        self.internal_logger.warning(message)

    def start_loading_cards(self) -> None:
        """Load the plot card code in the background once the window is shown."""
        if card_modules_loaded():
            return
        self._loading_cards = True
        self._update_loading_status()
        self._card_loader.start()

    def on_card_modules_loaded(self, error: str) -> None:
        self._loading_cards = False
        startup_mark("plot card modules loaded")
        if error:
            # A card whose module failed imports it again when opened, and reports that error.
            self.internal_logger.warning(f"Plot cards could not all load in the background: {error}")
        self._update_loading_status()

    def on_navigator_loading_changed(self, loading: bool) -> None:
        self._navigator_loading = loading
        if not loading:
            startup_mark("navigator loaded")
        self._update_loading_status()

    def _update_loading_status(self) -> None:
        """Log in the Internal log panel when LODGaZilla's cards and the data folder start and finish loading."""
        folder = self.navigator.data_folder()
        folder_name = folder.name if folder else ""
        states = (
            ("cards", self._loading_cards, "Loading LODGaZilla…", "LODGaZilla loaded"),
            ("navigator", self._navigator_loading,
             f"Loading data folder {folder_name}…", f"Loaded data folder {folder_name}"),
        )
        for key, loading, started, finished in states:
            if loading != self._loading_logged.get(key, False):
                self.internal_logger.info(started if loading else finished)
                self._loading_logged[key] = loading

    def show_folder_context_menu(self, folder: Path, position) -> None:  # type: ignore[no-untyped-def]
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.addAction("Multi-series", lambda: self.plot_folder_in_multi_widget(folder))
        menu.addSeparator()
        attach_menu = menu.addMenu("Attach")
        attach_menu.addAction("Video…", lambda: self.attach_video_to_folder(folder))
        attach_menu.addAction("Images…", lambda: self.attach_images_to_folder(folder))
        attach_menu.addAction("Audio…", lambda: self.attach_audio_to_folder(folder))
        menu.addAction("Show in file manager", lambda: self.show_folder_in_file_manager(folder))
        # Plugin actions come last, each named "Plugin: …".
        menu.addSeparator()
        self.add_plugin_folder_actions(menu, [folder], "folder")
        self.plugin_manager.add_folder_context_actions(menu, folder)
        menu.exec(self.navigator.tree.viewport().mapToGlobal(position))

    def show_folders_context_menu(self, folders: list[Path], position) -> None:  # type: ignore[no-untyped-def]
        """A virtual group or several selected folders: the plugin actions, run on each folder."""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        self.add_plugin_folder_actions(menu, folders, "batch")
        menu.exec(self.navigator.tree.viewport().mapToGlobal(position))

    def add_plugin_folder_actions(self, menu: QMenu, folders: list[Path], scope: str) -> None:
        """Add H5 generation and the plugins' declared operations for ``folders``, each folder on its own."""
        suffix = f" ({len(folders)} folders)" if len(folders) > 1 else ""
        generate = menu.addAction(f"Plugin: Generate / update H5 files{suffix}")
        generate.triggered.connect(lambda: [self.start_folder_cache_rebuild(folder) for folder in folders])
        self._require_importer(generate)
        for descriptor, operation in self.plugin_manager.operations(scope):
            action = menu.addAction(f"Plugin: {operation.kind.capitalize()} {operation.title}{suffix}")
            action.setToolTip(descriptor.title)
            action.triggered.connect(lambda _checked=False, plugin_id=descriptor.plugin_id, name=operation.name: [
                self.plugin_manager.run_operation(plugin_id, name, folder) for folder in folders])

    def _require_importer(self, action) -> None:  # type: ignore[no-untyped-def]
        """H5 generation needs an enabled importer plugin; without one the action is disabled with a hint."""
        if not self.plugin_manager.has_enabled_importer():
            action.setEnabled(False)
            action.setToolTip("Enable an importer plugin in Settings > Plugins")

    def populate_batch_menu(self) -> None:
        """The File bar's Batch menu: the plugin actions for the whole data folder."""
        menu = self.batch_menu
        menu.clear()
        menu.setToolTipsVisible(True)
        generate = menu.addAction("Plugin: Generate H5 files for all folders", self.start_data_folder_cache_batch)
        self._require_importer(generate)
        for descriptor, operation in self.plugin_manager.operations("batch"):
            action = menu.addAction(f"Plugin: {operation.kind.capitalize()} {operation.title} for all folders")
            action.setToolTip(descriptor.title)
            action.triggered.connect(lambda _checked=False, plugin_id=descriptor.plugin_id, name=operation.name:
                                     self.start_data_folder_operation(plugin_id, name))

    def start_data_folder_operation(self, plugin_id: str, operation: str) -> None:
        data_folder = self.navigator.data_folder()
        if data_folder is None:
            self.internal_logger.warning(f"Cannot run {operation}: no data folder is selected.")
            return
        if self.confirm_data_folder_batch(operation, data_folder):
            self.plugin_manager.run_operation(plugin_id, operation, data_folder, recursive=True)

    def confirm_data_folder_batch(self, action: str, data_folder: Path) -> bool:
        """Ask before a Batch menu action runs on every folder under the data folder."""
        answer = QMessageBox.question(
            self, "Batch", f"Run {action} for every folder under {data_folder}?\n\n"
            "This can take a long time on a large data folder.")
        return answer == QMessageBox.StandardButton.Yes

    def refresh_plugin_launchers(self) -> None:
        """Show an icon in the Plot navigator's Plugins group for each enabled plugin with a launcher."""
        self.plot_navigator.set_plugin_launchers([
            (descriptor.plugin_id, descriptor.launcher.title, descriptor.launcher.icon)
            for descriptor in self.plugin_manager.launchers()
        ])

    def plot_folder_in_multi_widget(self, folder: Path) -> None:
        # Reading every H5 file can be slow on OneDrive or network folders, so
        # collect the series in a background job and open the card afterwards.
        workspace, logger = self.plot_workspace, self.internal_logger

        def open_entries(result: tuple[list[tuple[Path, str]], list[str]]) -> None:
            entries, warnings = result
            for warning in warnings:
                logger.warning(warning)
            if entries:
                workspace.add_multi_plot(entries)
            else:
                logger.info(f"No cached data series to plot in {folder}")

        SelectionInspection(workspace, (Path(folder),), open_entries,
                            inspect=folder_multi_series_entries, empty_result=([], [f"Could not read {folder}"]))

    def attach_video_to_folder(self, folder: Path) -> None:
        selected, _filter = QFileDialog.getOpenFileNames(
            self, "Attach videos", str(folder), "Video files (*.insv *.lrv *.mp4 *.mov *.mkv *.avi *.m4v);;All files (*)"
        )
        if not selected:
            return
        try:
            info = read_info_file(folder)
            attached_paths = {video_attachment_path(folder, video) for video in info.videos}
            added_videos = []
            for selected_path in selected:
                video_path = Path(selected_path).resolve()
                if video_path in attached_paths:
                    continue
                try:
                    relative_path = Path(os.path.relpath(video_path, folder.resolve())).as_posix()
                except ValueError:
                    # Windows cannot make a relative path across drive letters.
                    relative_path = video_path.as_posix()
                info.videos.append({"path": relative_path, "offset_seconds": "0"})
                attached_paths.add(video_path)
                added_videos.append(video_path)
            write_info_file(folder, info)
            self.navigator.refresh_tree(rescan=True)
            self.internal_logger.info(f"Video links saved for {folder.name}")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.internal_logger.error(f"Could not attach video to {folder}: {error}")
            return
        # Each video's soundtrack becomes an audio H5 in the process queue.
        self.extract_video_audio(folder, added_videos)

    @staticmethod
    def _relative_link(folder: Path, path: Path) -> str:
        try:
            return Path(os.path.relpath(path, folder.resolve())).as_posix()
        except ValueError:
            # Windows cannot make a relative path across drive letters.
            return path.as_posix()

    def _audio_link(self, folder: Path, source: Path, *, video: Path | None = None) -> tuple[dict, MediaItem]:
        """The info-file link and the conversion job for one audio source."""
        output = audio_output_path(source, folder)
        link = {"path": self._relative_link(folder, output), "source": self._relative_link(folder, source),
                "offset_seconds": "0"}
        if video is not None:
            link["video"] = self._relative_link(folder, video)
            link["offset_seconds"] = next(
                (str(entry.get("offset_seconds", "0")) for entry in read_info_file(folder).videos
                 if video_attachment_path(folder, entry) == video.resolve()), "0")
        offset = float(link["offset_seconds"] or 0)
        return link, MediaItem(str(source), str(output), folder_start_unix_seconds(folder), offset)

    def _video_audio_links(self, folder: Path, video_path: Path, info=None) -> list[dict]:  # type: ignore[no-untyped-def]
        info = info or read_info_file(folder)
        return [audio for audio in info.audio
                if audio.get("video") and attachment_path(folder, {"path": audio["video"]}) == Path(video_path).resolve()]

    def _queue_audio(self, folder: Path, items: list[MediaItem]) -> None:
        if items:
            self.start_import(MediaJobRequest(folder.resolve(), tuple(items)))

    def attach_audio_to_folder(self, folder: Path) -> None:
        """Link audio H5 files directly; convert other audio files in the process queue."""
        selected, _filter = QFileDialog.getOpenFileNames(self, "Attach audio", str(folder),
                                                         f"{AUDIO_FILE_FILTER};;All files (*)")
        if selected:
            self.attach_audio_files(folder, [Path(path) for path in selected])

    def attach_audio_files(self, folder: Path, paths: list[Path]) -> None:
        try:
            info = read_info_file(folder)
            linked = {attachment_path(folder, audio) for audio in info.audio}
            sources = {attachment_path(folder, {"path": audio["source"]}) for audio in info.audio if audio.get("source")}
            items = []
            for path in (Path(path).resolve() for path in paths):
                if path in linked or path in sources:
                    continue
                if is_h5_file(path):
                    info.audio.append({"path": self._relative_link(folder, path), "offset_seconds": "0"})
                else:
                    link, item = self._audio_link(folder, path)
                    info.audio.append(link)
                    items.append(item)
            write_info_file(folder, info)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.on_metadata_write_failed(folder, str(error))
            return
        self.navigator.refresh_tree(rescan=True)
        self._queue_audio(folder, items)

    def extract_video_audio(self, folder: Path, video_paths: list[Path]) -> None:
        """Convert video soundtracks to audio H5 files that follow each video's sync offset."""
        try:
            info = read_info_file(folder)
            items = []
            for video_path in (Path(path).resolve() for path in video_paths):
                # A stale link from an earlier attempt is replaced rather than duplicated.
                info.audio = [audio for audio in info.audio if audio not in self._video_audio_links(folder, video_path, info)]
                if not has_audio_track(video_path):
                    self.internal_logger.info(f"{video_path.name} has no soundtrack to extract")
                    continue
                link, item = self._audio_link(folder, video_path, video=video_path)
                info.audio.append(link)
                items.append(item)
            if items:
                write_info_file(folder, info)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.on_metadata_write_failed(folder, str(error))
            return
        self._queue_audio(folder, items)

    def _tree_folder_at(self, position) -> Path | None:  # type: ignore[no-untyped-def]
        item = self.navigator.tree.itemAt(position)
        while item is not None and item.data(0, ITEM_TYPE_ROLE) != ROW_TYPE_FOLDER:
            item = item.parent()
        return Path(str(item.data(0, ITEM_PATH_ROLE))) if item is not None else None

    def _add_audio_actions(self, menu, folder: Path, h5_path: Path) -> None:  # type: ignore[no-untyped-def]
        link = next((audio for audio in read_info_file(folder).audio
                     if attachment_path(folder, audio) == h5_path.resolve()), None)
        if link is None:
            return
        menu.addSeparator()
        offset_action = menu.addAction("Set audio offset…", lambda: self.set_audio_offset(folder, h5_path))
        if link.get("video"):
            offset_action.setEnabled(False)
            offset_action.setText("Audio offset follows the video's sync")
        menu.addAction("Remove audio link", lambda: self.remove_audio_link(folder, h5_path))

    def set_audio_offset(self, folder: Path, h5_path: Path) -> None:
        """Move an audio file in time relative to the folder's log start."""
        try:
            info = read_info_file(folder)
            link = next(audio for audio in info.audio if attachment_path(folder, audio) == h5_path.resolve())
            current = float(link.get("offset_seconds", "0") or 0)
        except (OSError, ValueError, StopIteration, json.JSONDecodeError) as error:
            self.on_metadata_write_failed(folder, str(error))
            return
        offset, accepted = QInputDialog.getDouble(
            self, "Audio offset", "Seconds from the folder's log start to the audio's start:",
            current, -1e7, 1e7, 3)
        if not accepted:
            return
        if not set_audio_offset(h5_path, offset):
            self.internal_logger.error(f"Could not move {h5_path.name}: the file is open elsewhere.")
            return
        link["offset_seconds"] = f"{offset:.6f}"
        try:
            write_info_file(folder, info)
        except (OSError, ValueError) as error:
            self.on_metadata_write_failed(folder, str(error))
        self.navigator.refresh_tree(rescan=True)
        self.internal_logger.info(f"Audio offset of {h5_path.name}: {offset:+.3f} s")

    def remove_audio_link(self, folder: Path, h5_path: Path) -> None:
        """Remove only the link; the audio H5 and its source stay on disk."""
        try:
            info = read_info_file(folder)
            info.audio = [audio for audio in info.audio if attachment_path(folder, audio) != h5_path.resolve()]
            write_info_file(folder, info)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.on_metadata_write_failed(folder, str(error))
            return
        self.navigator.refresh_tree(rescan=True)
        self.internal_logger.info(f"Removed audio link from {folder.name}: {h5_path.name}")

    def attach_images_to_folder(self, folder: Path) -> None:
        formats = sorted({bytes(fmt).decode("ascii") for fmt in QImageReader.supportedImageFormats()})
        patterns = " ".join(f"*.{fmt}" for fmt in formats)
        selected, _filter = QFileDialog.getOpenFileNames(
            self, "Attach images", str(folder), f"Image files ({patterns});;All files (*)"
        )
        if not selected:
            return
        try:
            info = read_info_file(folder)
            attached_paths = {attachment_path(folder, image) for image in info.images}
            for selected_path in selected:
                image_path = Path(selected_path).resolve()
                if image_path in attached_paths:
                    continue
                try:
                    relative_path = Path(os.path.relpath(image_path, folder.resolve())).as_posix()
                except ValueError:
                    # Windows cannot make a relative path across drive letters.
                    relative_path = image_path.as_posix()
                info.images.append({"path": relative_path})
                attached_paths.add(image_path)
            write_info_file(folder, info)
        except (OSError, ValueError) as error:
            self.on_metadata_write_failed(folder, str(error))
            return
        self.navigator.refresh_tree(rescan=True)
        self.internal_logger.info(f"Image links saved for {folder.name}")

    def show_image_context_menu(self, folder: Path, image_path: Path, position) -> None:  # type: ignore[no-untyped-def]
        menu = QMenu(self)
        populate_plot_menu(menu, self.plot_workspace,
                           context_sources(self.navigator, PlotSource("image", image_path, folder=folder)))
        menu.addAction("Remove link", lambda: self.remove_image_link(folder, image_path))
        menu.exec(self.navigator.tree.viewport().mapToGlobal(position))

    def remove_image_link(self, folder: Path, image_path: Path) -> None:
        """Unlink an image and close its cards, preserving the source file."""
        try:
            info = read_info_file(folder)
            info.images = [image for image in info.images
                           if attachment_path(folder, image) != image_path.resolve()]
            write_info_file(folder, info)
        except (OSError, ValueError) as error:
            self.on_metadata_write_failed(folder, str(error))
            return
        workspace = self.plot_workspace
        for plot in reversed(workspace.plots()):
            if (isinstance(plot, ImagePlotWidget)
                    and plot.folder.resolve() == folder.resolve()
                    and plot.image_path.resolve() == image_path.resolve()):
                workspace.remove_plot(plot)
        self.navigator.refresh_tree(rescan=True)
        self.internal_logger.info(f"Removed image link from {folder.name}: {image_path.name}")

    def show_video_context_menu(self, folder: Path, video_path: Path, position) -> None:  # type: ignore[no-untyped-def]
        menu = QMenu(self)
        populate_plot_menu(menu, self.plot_workspace,
                           context_sources(self.navigator, PlotSource("video", video_path, folder=folder)))
        if not any(attachment_path(folder, audio).is_file() for audio in self._video_audio_links(folder, video_path)):
            menu.addAction("Extract audio", lambda: self.extract_video_audio(folder, [video_path]))
        menu.addAction("Remove link", lambda: self.remove_video_link(folder, video_path))
        menu.exec(self.navigator.tree.viewport().mapToGlobal(position))

    def remove_video_link(self, folder: Path, video_path: Path) -> None:
        """Remove only the metadata link, leaving the video file on disk."""
        try:
            info = read_info_file(folder)
            info.videos = [
                video for video in info.videos
                if video_attachment_path(folder, video) != video_path.resolve()
            ]
            info.audio = [audio for audio in info.audio if audio not in self._video_audio_links(folder, video_path, info)]
            write_info_file(folder, info)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.on_metadata_write_failed(folder, str(error))
            return
        # Close matching cards so they cannot save a sync offset for an unlinked video.
        workspace = self.plot_workspace
        for plot in reversed(workspace.plots()):
            if (isinstance(plot, VideoPlotWidget)
                    and plot.folder.resolve() == folder.resolve()
                    and plot.video_path.resolve() == video_path.resolve()):
                workspace.remove_plot(plot)
        self.navigator.refresh_tree(rescan=True)
        self.internal_logger.info(f"Removed video link from {folder.name}: {video_path.name}")

    def show_series_context_menu(self, h5_path: Path, series_name: str, position) -> None:
        menu = QMenu(self)
        sources = context_sources(self.navigator, PlotSource("series", h5_path, series_name))
        populate_plot_menu(menu, self.plot_workspace, sources)
        folder = self._tree_folder_at(position) if is_media_h5(h5_path) else None
        if folder is not None:
            self._add_audio_actions(menu, folder, Path(h5_path))
        menu.exec(self.navigator.tree.viewport().mapToGlobal(position))

    def show_series_group_context_menu(self, entries, position) -> None:
        menu = QMenu(self)
        sources = [PlotSource("series", path, name) for path, name in entries]
        populate_plot_menu(menu, self.plot_workspace, sources, group=True)
        menu.exec(self.navigator.tree.viewport().mapToGlobal(position))

    def show_folder_in_file_manager(self, folder: Path) -> None:
        if not folder.is_dir():
            self.internal_logger.warning(f"Cannot show missing folder in file manager: {folder}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self.internal_logger.error(f"Could not open file manager for {folder}.")

    def start_data_folder_cache_batch(self) -> None:
        data_folder = self.navigator.data_folder()
        if data_folder is None:
            self.internal_logger.warning("Cannot batch-create H5 cache: no data folder is selected.")
            return
        if self.confirm_data_folder_batch("H5 file generation", data_folder):
            self.start_import(data_folder, recursive=True)

    def start_folder_cache_rebuild(self, folder: Path) -> None:
        if not self.plugin_manager.has_enabled_importer():
            self.internal_logger.warning("Cannot generate H5 cache: no importer plugin is enabled.")
            return
        self.start_import(folder, recursive=False)

    def start_import(self, folder: Path | ImportRequest | PluginJobRequest, *, recursive: bool | None = None) -> None:
        request = (folder if isinstance(folder, (ImportRequest, PluginJobRequest, MediaJobRequest))
                   else self.create_import_request(folder, recursive=bool(recursive)))
        if request is None:
            self.internal_logger.warning(f"Cannot create H5 cache: folder does not exist: {folder}")
            return
        if not request.folder.is_dir():
            self.internal_logger.warning(f"Cannot start job: folder does not exist: {request.folder}")
            return
        if self.can_start_import_now(request):
            self.start_import_process(request)
            self.start_queued_imports()
            return
        self.import_coordinator.enqueue(request)
        operation = request_operation(request)
        self.internal_logger.info(
            f"Queued {operation} for {request.folder}. Pending folders: {len(self.import_queue)}."
        )

    def create_import_request(self, folder: Path, *, recursive: bool) -> ImportRequest | None:
        if not folder.is_dir():
            return None
        # A recursive batch must not walk the whole data tree in the GUI
        # thread merely to size its worker pool.  Reserve the configured upper
        # bound; discovery happens inside the spawned coordinator instead.
        source_count = self.max_background_processes if recursive else self.estimate_source_file_count(folder, recursive=False)
        return ImportRequest(
            folder.resolve(), recursive,
            configured_import_worker_count(max(1, source_count), process_limit=self.max_background_processes),
        )

    def can_start_import_now(self, request: ImportRequest | PluginJobRequest) -> bool:
        return self.import_coordinator.can_start(request)

    def start_import_process(self, request: ImportRequest | PluginJobRequest) -> None:
        import_id = self.import_coordinator.reserve_id()
        is_plugin_job = isinstance(request, PluginJobRequest)
        is_media_job = isinstance(request, MediaJobRequest)
        if not is_plugin_job and not is_media_job:
            self.clear_imported_children(request.folder, recursive=request.recursive)
        context = multiprocessing.get_context("spawn")
        events = context.Queue()
        # Set on application close so the coordinator stops its pool workers
        # and converter subprocesses instead of leaving them orphaned.
        cancel_event = context.Event()
        start_event = context.Event()
        if is_media_job:
            target, args = run_media_process, (request, events, cancel_event, start_event,
                                               load_hdf5_settings(self.settings_path))
        elif is_plugin_job:
            target, args = run_plugin_process, (request, events, cancel_event, start_event)
        else:
            target, args = run_reimport_process, (
                str(request.folder), events, request.recursive, request.process_slots,
                # Settings may not be flushed yet. Spawned processes must
                # receive the GUI's current values instead of rereading disk.
                dict(self.plugin_manager.enabled),
                load_hdf5_settings(self.settings_path),
                cancel_event, start_event,
            )
        process = context.Process(
            target=target, args=args,
            name=f"LODGaZilla{'AudioJob' if is_media_job else 'PluginJob' if is_plugin_job else 'H5Cache'}-{import_id}",
        )
        process_tree = start_process_tree(process, start_event)
        self.import_coordinator.register_active(import_id, ActiveImport(
            request.folder, request.recursive, request.process_slots, process, events,
            request_operation(request), cancel_event, process_tree, getattr(request, "plugin_id", ""),
        ))
        self.event_timer.start()
        used_slots = self.max_background_processes - self.available_import_slots()
        self.internal_logger.info(
            f"Starting {request_operation(request)} in {request.folder}; worker slots: {request.process_slots}/"
            f"{self.max_background_processes}; active folders: {len(self.active_imports)}; "
            f"total reserved slots: {used_slots}/{self.max_background_processes}."
        )

    def start_queued_imports(self) -> None:
        # Do not let a queued request that conflicts with an active recursive
        # batch block independent folders behind it. Start every request that
        # fits the remaining shared worker budget; leave the rest in order.
        while self.import_queue:
            request = self.import_coordinator.pop_next_runnable()
            if request is None:
                return
            self.start_import_process(request)

    def available_import_slots(self) -> int:
        return self.import_coordinator.available_slots()

    def has_active_scope_conflict(self, request: ImportRequest | PluginJobRequest) -> bool:
        return self.import_coordinator.has_conflict(request)

    def estimate_source_file_count(self, folder: Path, *, recursive: bool) -> int:
        candidates = folder.rglob("*") if recursive else folder.iterdir()
        return sum(
            1 for path in candidates
            if path.is_file() and not is_cache_output(path) and not is_info_file(path)
        )

    def poll_import_events(self) -> None:
        if not self.active_imports:
            self.start_queued_imports()
            if not self.active_imports:
                self.event_timer.stop()
            return
        for import_id, active in list(self.active_imports.items()):
            while True:
                try:
                    event = active.events.get_nowait()
                except queue.Empty:
                    break
                if not event:
                    continue
                event_type = event[0]
                if event_type == "process_started":
                    self.start_process_progress(*event[1:], operation=active.operation)
                elif event_type == "process_progress":
                    self.update_process_progress(*event[1:])
                elif event_type == "process_eta":
                    self.update_process_eta(*event[1:])
                elif event_type == "plugin_event" and active.plugin_id:
                    self.plugin_manager.dispatch_job_event(active.plugin_id, *event[1:])
                elif event_type == "process_finished":
                    self.finish_process_progress(*event[1:])
                elif event_type == "message":
                    self.internal_logger.info(str(event[1]))
                elif event_type == "finished":
                    self.finish_reimport(import_id, *event[1:])
                    break
            if import_id in self.active_imports and not active.process.is_alive():
                active.process.join()
                if active.process.exitcode not in {0, None}:
                    self.finish_reimport(import_id, 0, 0, f"{active.operation} process exited with code {active.process.exitcode}.")

    def start_process_progress(self, folder_text: str, process_id: str, source_name: str, total: int, *, operation: str = "H5 cache") -> None:
        folder = Path(folder_text).resolve()
        self.completed_processes_pending_refresh.discard(process_id)
        previous = self.process_progress_started_at.get(process_id)
        if previous is not None and previous.folder != folder:
            self.remove_process_progress(previous.folder, process_id)
        self.process_progress_started_at[process_id] = ProcessProgressState(
            folder, source_name, time.monotonic(), total=max(1, int(total)), operation=operation
        )
        self.update_folder_progress(folder)

    def update_process_progress(self, folder_text: str, process_id: str, source_name: str, completed: int, total: int) -> None:
        folder = Path(folder_text).resolve()
        state = self.process_progress_started_at.get(process_id)
        if state is None or state.folder != folder or state.source_name != source_name:
            self.start_process_progress(folder_text, process_id, source_name, total)
            state = self.process_progress_started_at[process_id]
        total = max(1, int(total))
        completed = max(0, int(completed))
        state.completed, state.total = completed, total
        state.eta_text = "0:00" if completed >= total else self.estimated_time_left_text(state.started_at, completed, total)
        state.finished = state.success = False
        self.update_folder_progress(folder)

    def update_process_eta(self, folder_text: str, process_id: str, source_name: str, remaining_seconds: int) -> None:
        """Apply an operation-provided ETA when exact byte progress is unavailable."""
        folder = Path(folder_text).resolve()
        state = self.process_progress_started_at.get(process_id)
        if state is None or state.folder != folder or state.source_name != source_name:
            return
        state.eta_text = format_duration(max(0, int(remaining_seconds)))
        self.update_folder_progress(folder)

    def finish_process_progress(self, folder_text: str, process_id: str, source_name: str, success: bool) -> None:
        folder = Path(folder_text).resolve()
        state = self.process_progress_started_at.get(process_id)
        if state is None:
            state = ProcessProgressState(folder, source_name, time.monotonic())
            self.process_progress_started_at[process_id] = state
        state.source_name, state.completed, state.total = source_name, PROGRESS_TOTAL, PROGRESS_TOTAL
        state.eta_text, state.finished, state.success = ("0:00" if success else "failed"), True, bool(success)
        self.update_folder_progress(folder)
        if success:
            if state.operation == "H5 cache":
                self.completed_processes_pending_refresh.add(process_id)
                self.schedule_tree_refresh_after_completed_process()
            self.internal_logger.info(f"Completed {state.operation} for {source_name}.")
        else:
            self.internal_logger.error(f"Failed {state.operation} for {source_name}.")

    def update_folder_progress(self, folder: Path, *, exclude_process_id: str | None = None) -> None:
        folder = folder.resolve()
        states = [
            (state.source_name, state.completed, state.total, state.eta_text)
            for process_id, state in self.process_progress_started_at.items()
            if process_id != exclude_process_id and state.folder.resolve() == folder
        ]
        for item in self.navigator_tree_items():
            if item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_FOLDER and Path(str(item.data(0, ITEM_PATH_ROLE))).resolve() == folder:
                if states:
                    completed, total, eta = combined_process_progress(states)
                    self.navigator.set_item_progress(item, completed, total, folder.name, eta)
                else:
                    self.navigator.clear_item_progress(item)
                return

    def remove_process_progress(self, folder: Path, process_id: str) -> None:
        self.update_folder_progress(folder, exclude_process_id=process_id)

    def finish_reimport(self, import_id: int, imported: int, failed: int, error: str) -> None:
        active = self.import_coordinator.finish(import_id)
        if active is not None:
            if active.process_tree is not None:
                active.process_tree.close()
            active.process.join(timeout=0)
            self.remove_progress_states_for_scope(active.folder, recursive=active.recursive)
            if active.operation in {"H5 cache", MediaJobRequest.operation}:
                self.refresh_tree_preserving_progress()
        self.start_queued_imports()
        outcome = error or (
            f"Created {imported} H5 cache file(s); {failed} failed."
            if active is None or active.operation == "H5 cache"
            else f"{active.operation}: {imported} completed; {failed} failed."
        )
        if error or failed:
            self.internal_logger.error(outcome)
        else:
            self.internal_logger.info(outcome)
        if not self.active_imports and not self.import_queue:
            self.event_timer.stop()
            self.completed_processes_pending_refresh.clear()
            self.process_progress_started_at.clear()

    def navigator_tree_items(self):  # type: ignore[no-untyped-def]
        stack = [self.navigator.tree.topLevelItem(index) for index in range(self.navigator.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.child(index) for index in range(item.childCount()))

    def schedule_tree_refresh_after_completed_process(self) -> None:
        if not self.tree_refresh_pending:
            self.tree_refresh_pending = True
            QTimer.singleShot(0, self.refresh_tree_after_completed_processes)

    def refresh_tree_after_completed_processes(self) -> None:
        self.tree_refresh_pending = False
        for process_id in tuple(self.completed_processes_pending_refresh):
            state = self.process_progress_started_at.get(process_id)
            if state is not None and state.finished and state.success:
                self.remove_process_progress(state.folder, process_id)
                self.process_progress_started_at.pop(process_id, None)
        self.completed_processes_pending_refresh.clear()
        self.refresh_tree_preserving_progress()

    def refresh_tree_preserving_progress(self) -> None:
        # Completed imports can create folders/H5 files, so this is one of
        # the explicit moments that must rescan rather than reuse UI metadata.
        self.navigator.refresh_tree(rescan=True)
        for folder in {state.folder.resolve() for state in self.process_progress_started_at.values()}:
            self.update_folder_progress(folder)

    def remove_progress_states_for_scope(self, folder: Path, *, recursive: bool) -> None:
        target = folder.resolve()
        removed: set[str] = set()
        for process_id, state in list(self.process_progress_started_at.items()):
            if state.folder.resolve() == target or (recursive and path_is_relative_to(state.folder.resolve(), target)):
                self.remove_process_progress(state.folder, process_id)
                self.process_progress_started_at.pop(process_id, None)
                removed.add(process_id)
        self.completed_processes_pending_refresh.difference_update(removed)

    def clear_imported_children(self, folder: Path, *, recursive: bool) -> None:
        target = folder.resolve()
        for item in list(self.navigator_tree_items()):
            item_path = item.data(0, ITEM_PATH_ROLE)
            if not item_path:
                continue
            item_folder = Path(str(item_path)).resolve()
            if item_folder == target or (recursive and path_is_relative_to(item_folder, target)):
                item.takeChildren()

    @staticmethod
    def estimated_time_left_text(started_at: float, completed: int, total: int) -> str:
        if completed <= 0 or total <= 0:
            return "..."
        return format_duration((time.monotonic() - started_at) * (total - completed) / completed)

    def save_splitter_position(self, *_args) -> None:  # type: ignore[no-untyped-def]
        sizes = self.main_splitter.sizes()
        total = sum(sizes)
        if total <= 0:
            return
        if self._update_lodgazilla_settings(
            lambda settings: settings.__setitem__("navigator_width_ratio", sizes[0] / total)
        ):
            self._splitter_save_retry_pending = False
            return
        if not self._splitter_save_retry_pending:
            self._splitter_save_retry_pending = True
            QTimer.singleShot(1_000, self.retry_save_splitter_position)

    def retry_save_splitter_position(self) -> None:
        self._splitter_save_retry_pending = False
        self.save_splitter_position()

    def schedule_plot_navigator_alignment(self) -> None:
        """Coalesce plot/layout changes before mapping the ViewBox edges."""
        self._plot_navigator_alignment_timer.start()

    def align_plot_navigator_to_plot_area(self) -> None:
        """Use a line plot's real ViewBox edges, including dynamic Y-axis width."""
        line_plot = next(
            (
                plot
                for plot in self.plot_workspace.plots()
                if isinstance(plot, LinePlotWidget)
            ),
            None,
        )
        if not isinstance(line_plot, LinePlotWidget) or not self.plot_navigator.canvas.isVisible():
            return
        try:
            plot_widget = line_plot.series_plot.plot_widget
            bounds = line_plot.plot_view_box().sceneBoundingRect()
            left_local = plot_widget.mapFromScene(bounds.topLeft())
            right_local = plot_widget.mapFromScene(bounds.topRight())
            left_global = plot_widget.mapToGlobal(QPoint(left_local.x(), 0))
            right_global = plot_widget.mapToGlobal(QPoint(right_local.x(), 0))
            left = self.plot_navigator.canvas.mapFromGlobal(left_global).x()
            right_edge = self.plot_navigator.canvas.mapFromGlobal(right_global).x()
        except RuntimeError:
            return
        if right_edge <= left:
            return
        self.plot_navigator.set_plot_area_margins(
            left,
            self.plot_navigator.canvas.width() - right_edge,
        )

    def show_settings(self) -> None:
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self.settings_path, self)
            self.settings_dialog.setModal(False)
            self.settings_dialog.settings_applied.connect(self.apply_settings)
            self.settings_dialog.demo_data_regenerate_requested.connect(self.regenerate_demo_data)
            self.settings_dialog.finished.connect(lambda _result: setattr(self, "settings_dialog", None))
            self.settings_dialog.finished.connect(self.settings_dialog.deleteLater)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def apply_settings(self) -> None:
        self.lodgazilla_settings = load_lodgazilla_settings(self.settings_path)
        self._apply_runtime_settings()

    # Demo data

    def offer_demo_data(self) -> None:
        """At startup: offer to create the demo data when it is missing."""
        if demo_data_exists(DEMO_DATA_ROOT) or not self.lodgazilla_settings["offer_demo_data"] or self.demo_data_job is not None:
            return
        box = QMessageBox(
            QMessageBox.Icon.Question, "Demo data",
            "LODGaZilla has no demo data yet. Create it now?\n\n"
            "The demo data is synthetic: four running workouts with sensor data, two videos with sound "
            f"and a route picture, in {DEMO_DATA_ROOT}. It takes about 40 minutes and 1.7 GB; "
            "you can keep working meanwhile.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self,
        )
        dont_ask = QCheckBox("Don't ask again")
        box.setCheckBox(dont_ask)
        answer = box.exec()
        if dont_ask.isChecked():
            self._update_lodgazilla_settings(lambda settings: settings.__setitem__("offer_demo_data", False))
        if answer == QMessageBox.StandardButton.Yes:
            self.start_demo_data(replace=False)

    def regenerate_demo_data(self) -> None:
        """Settings > General > Re-generate demo data: delete the folder and create it again."""
        if self.demo_data_job is not None:
            self.show_demo_data_progress()
            return
        answer = QMessageBox.warning(
            self.settings_dialog or self, "Re-generate demo data",
            f"This deletes {DEMO_DATA_ROOT} with any changes you have made to its files, metadata and "
            "workspace, and creates the demo data again. Cards showing demo data are closed. "
            "It takes about 40 minutes.\n\nRe-generate the demo data?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        # Release the files before deleting them.
        for plot in self.plot_workspace.plots():
            if uses_folder(plot.workspace_state(), DEMO_DATA_ROOT):
                self.plot_workspace.remove_plot(plot)
        self.navigator.cancel_background_refresh()
        self.start_demo_data(replace=True)

    def start_demo_data(self, replace: bool) -> None:
        job = DemoDataJob(DEMO_DATA_ROOT, replace=replace, parent=self)
        job.progress.connect(self.on_demo_data_progress)
        job.message.connect(self.on_demo_data_message)
        job.finished.connect(self.on_demo_data_finished)
        self.demo_data_job = job
        dialog = QProgressDialog("Starting…", "Cancel", 0, 1000, self)
        dialog.setWindowTitle("Creating demo data")
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(460)
        dialog.canceled.connect(job.cancel)
        self.demo_data_progress = dialog
        self.internal_logger.info(f"{'Re-generating' if replace else 'Creating'} demo data in {DEMO_DATA_ROOT}")
        job.start()
        self.show_demo_data_progress()

    def show_demo_data_progress(self) -> None:
        if self.demo_data_progress is not None:
            self.demo_data_progress.show()
            self.demo_data_progress.raise_()
            self.demo_data_progress.activateWindow()

    def on_demo_data_progress(self, fraction: float) -> None:
        # A cancelled dialog stays hidden until the generator has stopped.
        if self.demo_data_progress is not None and not self.demo_data_progress.wasCanceled():
            self.demo_data_progress.setValue(round(fraction * 1000))

    def on_demo_data_message(self, message: str) -> None:
        self.internal_logger.info(f"Demo data: {message.strip()}")
        if self.demo_data_progress is not None and not self.demo_data_progress.wasCanceled():
            self.demo_data_progress.setLabelText(message.strip())

    def on_demo_data_finished(self, succeeded: bool, error: str) -> None:
        if self.demo_data_progress is not None:
            self.demo_data_progress.canceled.disconnect()
            self.demo_data_progress.close()
            self.demo_data_progress.deleteLater()
        self.demo_data_progress = None
        if self.demo_data_job is not None:
            self.demo_data_job.deleteLater()
        self.demo_data_job = None
        folder = self.navigator.data_folder()
        if folder is not None and is_inside(Path(folder), DEMO_DATA_ROOT):
            self.navigator.refresh_tree(rescan=True)
        if not succeeded:
            message = f"Demo data could not be created:\n{error}" if error else "Demo data creation was cancelled."
            (self.internal_logger.error if error else self.internal_logger.info)(message)
            if error:
                QMessageBox.warning(self, "Demo data", message)
            return
        self.internal_logger.info(f"Demo data ready in {DEMO_DATA_ROOT}")
        answer = QMessageBox.question(
            self, "Demo data", f"The demo data is ready in {DEMO_DATA_ROOT}.\n\nOpen the demo workspace now?")
        if answer == QMessageBox.StandardButton.Yes:
            self.open_workspace_file(demo_workspace(DEMO_DATA_ROOT))

    def _apply_runtime_settings(self) -> None:
        """Apply the already-loaded settings once all dependent widgets exist."""
        self.max_background_processes = int(self.lodgazilla_settings["max_background_processes"])
        self.import_coordinator.set_maximum_processes(self.max_background_processes)
        self.plot_workspace.default_plot_heights = dict(self.lodgazilla_settings["default_plot_heights"])
        self.plot_workspace._data_drop_controller.delay_ms = self.lodgazilla_settings["card_drop_selector_delay_ms"]
        self.plot_workspace.set_x_axes_linked(bool(self.lodgazilla_settings["link_plot_x_axes"]))
        self.plot_workspace.set_time_axis_format_linked(
            bool(self.lodgazilla_settings["link_plot_time_format"])
        )
        self.plot_workspace.set_markers_linked(bool(self.lodgazilla_settings["link_markers"]))
        self.plot_workspace.set_marker_snap_to_nearest_point(
            bool(self.lodgazilla_settings["snap_vertical_marker_to_nearest_point"])
        )
        self.plot_workspace.set_marker_info_display(str(self.lodgazilla_settings["marker_info_display"]))
        self.plot_workspace.set_time_axis_display(str(self.lodgazilla_settings["time_axis_display"]))
        self.plot_workspace.set_legends_shown(bool(self.lodgazilla_settings["show_legends"]))
        self.plot_workspace.set_card_controls_shown(bool(self.lodgazilla_settings["show_card_controls"]))
        self.plot_navigator.set_legends_shown(bool(self.lodgazilla_settings["show_legends"]))
        self.plot_navigator.set_card_controls_shown(bool(self.lodgazilla_settings["show_card_controls"]))
        self.plot_navigator.set_icon_size(int(self.lodgazilla_settings["plot_navigator_icon_size"]))
        self.plot_navigator.set_colors(dict(self.lodgazilla_settings["plot_navigator_colors"]))
        self.plot_navigator.set_show_time_row(bool(self.lodgazilla_settings["show_plot_navigator_time_row"]))
        self.plot_navigator.set_expanded(bool(self.lodgazilla_settings["plot_navigator_expanded"]))
        self.plot_navigator.set_marker_info_display(str(self.lodgazilla_settings["marker_info_display"]))
        self.plot_navigator.set_time_axis_display(str(self.lodgazilla_settings["time_axis_display"]))
        self.plot_navigator.set_markers_linked(bool(self.lodgazilla_settings["link_markers"]))
        self.plot_navigator.set_plot_views_linked(bool(self.lodgazilla_settings["link_plot_x_axes"]))
        self.plot_navigator.set_snap_to_nearest(
            bool(self.lodgazilla_settings["snap_vertical_marker_to_nearest_point"])
        )
        plot_settings = sanitize_plot_settings(load_settings_section(PLOT_SECTION, self.settings_path))
        self.plot_workspace.apply_marker_settings(plot_settings)
        self.plot_navigator.canvas.update()
        self.plot_navigator.set_diagnostic_area_visibility(
            bool(plot_settings["show_sample_loss_areas"]),
            bool(plot_settings["show_clipping_areas"]),
        )
        self.ai_panel.set_expanded(bool(self.lodgazilla_settings["ai_panel_expanded"]))
        self.log_panel.set_expanded(bool(self.lodgazilla_settings["internal_log_expanded"]))
        if self.log_panel.is_expanded:
            QTimer.singleShot(0, self.restore_log_panel_size)
        apply_theme(load_theme(self.settings_path))
        self.plot_workspace.apply_theme()
        theme = load_theme(self.settings_path)
        configure_application(QApplication.instance(), theme)
        self.setWindowIcon(QIcon(str(lodgazilla_asset_path(theme, 'icon'))))
        self.navigator.reload_navigator_control_settings()
        self.reload_plugin_settings()
        self.refresh_plugin_launchers()

    def on_plot_navigator_expanded_changed(self, expanded: bool) -> None:
        self.schedule_plot_navigator_alignment()
        self._update_lodgazilla_settings(
            lambda settings: settings.__setitem__("plot_navigator_expanded", bool(expanded))
        )

    def _set_diagnostic_area_visibility(self, *, sample_loss: bool | None = None, clipping: bool | None = None) -> None:
        def update(settings: dict[str, object]) -> None:
            if sample_loss is not None:
                settings["show_sample_loss_areas"] = bool(sample_loss)
            if clipping is not None:
                settings["show_clipping_areas"] = bool(clipping)

        if self._update_plot_settings(update):
            self.plot_workspace.reload_plot_settings()

    def set_sample_loss_area_visibility(self, visible: bool) -> None:
        self._set_diagnostic_area_visibility(sample_loss=visible)

    def set_clipping_area_visibility(self, visible: bool) -> None:
        self._set_diagnostic_area_visibility(clipping=visible)

    def set_marker_info_display(self, mode: str) -> None:
        mode = mode if mode in {"off", "active", "all"} else "active"
        if self._update_lodgazilla_settings(
            lambda settings: settings.__setitem__("marker_info_display", mode)
        ):
            self.plot_workspace.set_marker_info_display(mode)

    def set_time_axis_display(self, mode: str) -> None:
        mode = mode if mode in TIME_AXIS_DISPLAY_MODES else "all"
        if self._update_lodgazilla_settings(
            lambda settings: settings.__setitem__("time_axis_display", mode)
        ):
            self.plot_workspace.set_time_axis_display(mode)

    def set_legends_shown(self, shown: bool) -> None:
        if self._update_lodgazilla_settings(lambda settings: settings.__setitem__("show_legends", bool(shown))):
            self.plot_workspace.set_legends_shown(bool(shown))

    def set_card_controls_shown(self, shown: bool) -> None:
        if self._update_lodgazilla_settings(
            lambda settings: settings.__setitem__("show_card_controls", bool(shown))
        ):
            self.plot_workspace.set_card_controls_shown(bool(shown))

    def set_snap_to_nearest(self, enabled: bool) -> None:
        if self._update_lodgazilla_settings(
            lambda settings: settings.__setitem__("snap_vertical_marker_to_nearest_point", bool(enabled))
        ):
            self.plot_workspace.set_marker_snap_to_nearest_point(bool(enabled))

    def set_markers_linked(self, enabled: bool) -> None:
        if self._update_lodgazilla_settings(
            lambda settings: settings.__setitem__("link_markers", bool(enabled))
        ):
            self.plot_workspace.set_markers_linked(bool(enabled))

    def set_plot_views_linked(self, enabled: bool) -> None:
        if self._update_lodgazilla_settings(
            lambda settings: settings.__setitem__("link_plot_x_axes", bool(enabled))
        ):
            self.plot_workspace.set_x_axes_linked(bool(enabled))

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # Persist settings before any teardown step that could raise.
        self._settings_flush_timer.stop()
        if not flush_app_settings_cache(self.settings_path):
            self.internal_logger.error("Could not save app settings before shutdown")
        try:
            self._magic_drop_queue.clear()
            if self.magic_drop_job is not None:
                # A stopped Magic drop removes the folder it created.
                self.magic_drop_job.cancel()
                self.magic_drop_job.wait(10.0)
            if self.plugin_chat_job is not None:
                self.plugin_chat_job.cancel()
                self.plugin_chat_job.wait(10.0)
            if self.demo_data_job is not None:
                # An unfinished set has no demo.umw, so the next start offers it again.
                self.demo_data_job.cancel()
                self.demo_data_job.wait(5.0)
            self.plugin_updates.shutdown()
            self.plugin_manager.shutdown()
            self.navigator.cancel_background_refresh()
            video_class = loaded_class(VideoPlotWidget)
            for video in self.plot_workspace.findChildren(video_class) if video_class is not None else ():
                video.shutdown()
            # Stop every card's loaders and web views before WebEngine shutdown.
            for card in self.plot_workspace.findChildren(PlotBaseWidget):
                dispose = getattr(card, "dispose", None)
                if callable(dispose):
                    try:
                        dispose()
                    except Exception as error:  # noqa: BLE001 - keep closing the other cards.
                        self.internal_logger.warning(
                            f"Could not dispose {type(card).__name__} during shutdown: {error}"
                        )
            shutdown_web_engine(self)
        finally:
            # Import ownership must be released even if a plugin or graphics
            # shutdown raises before reaching this point.
            self.import_coordinator.clear_queue()
            self.stop_active_imports()
            self.internal_logger.info("LODGaZilla closing")
            set_app_settings_changed_callback(None, self.settings_path)
            if not end_app_settings_cache(self.settings_path):
                self.internal_logger.error("Could not save app settings during shutdown")
            self.native_stderr_capture.stop()
            qInstallMessageHandler(None)
            self.internal_logger.close()
            super().closeEvent(event)

    def stop_active_imports(self, grace_seconds: float = 5.0) -> None:
        """Cancel background jobs cooperatively, then terminate as a fallback."""
        actives = list(self.active_imports.values())
        for active in actives:
            if active.cancel_event is not None:
                active.cancel_event.set()
        deadline = time.monotonic() + grace_seconds
        for active in actives:
            if active.cancel_event is not None and active.process.is_alive():
                active.process.join(max(0.0, deadline - time.monotonic()))
        for active in actives:
            tree = getattr(active, "process_tree", None)
            if tree is not None:
                try:
                    tree.close()
                except OSError as error:
                    self.internal_logger.error(f"Could not verify {active.operation} process-tree shutdown: {error}")
            if active.process.is_alive():
                self.internal_logger.warning(
                    f"{active.operation} worker did not stop after cancel; terminating it."
                )
                active.process.terminate()
                active.process.join(3)
            if active.process.is_alive():
                self.internal_logger.warning(
                    f"{active.operation} worker did not stop after terminate; forcing shutdown."
                )
                kill = getattr(active.process, "kill", None)
                if callable(kill):
                    kill()
                    active.process.join(1)
            if active.process.is_alive():
                self.internal_logger.error(
                    f"{active.operation} worker is still alive after forced shutdown."
                )


def main() -> int:
    startup_mark("main entered")
    arguments = parse_arguments()
    startup_mark("arguments parsed")
    # Already done before the imports when started with `python -m src.app.lodgazilla`.
    app, splash = start_application()
    # The window opens without the plot card code; it loads in the background
    # and, with the data folder, reports its progress in the Internal log panel.
    window = LODGaZillaWindow(arguments.data_folder)
    startup_mark("main window constructed")
    if arguments.default_settings:
        window.internal_logger.info(f"--default-settings: using {DEFAULT_APP_SETTINGS_PATH}")
    if arguments.no_plugins:
        window.internal_logger.info("--no-plugins: no plugin is loaded in this session")
    window.show()
    if splash is not None:
        splash.finish(window)
    startup_mark("main window shown; entering Qt event loop")
    # After the window's first paint.
    QTimer.singleShot(50, window.start_loading_cards)
    QTimer.singleShot(500, window.offer_demo_data)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
