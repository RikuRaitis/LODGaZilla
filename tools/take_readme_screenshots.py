"""Take the README screenshots from the demo data: the main window, the Plot navigator and one image per card type.

``python tools/take_readme_screenshots.py [--output docs/images]``

Needs the demo data (``demodata/demo.umw``) and a visible desktop: the window is shown
on screen and captured from it, so Map (web view) and 3D (OpenGL) cards look as they
do for a user. Uses the dark theme (``--theme light`` for light), no plugins and a
temporary copy of the settings. Every image is saved at the same width, and no
picture shows the real folder path. Also writes ``main_window_annotated.png``, the
main window with its areas outlined and numbered for the README.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["LODGAZILLA_NO_PLUGINS"] = "1"

from PySide6.QtCore import QPoint, QRectF, QSignalBlocker, Qt  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.app.branding import configure_application  # noqa: E402
from src.app.graphics import configure_graphics  # noqa: E402
from src.data.workspace_file import read_workspace_file  # noqa: E402

# Card type in the workspace file -> screenshot file name and row height.
CARD_FILES = {
    "line": ("card_line", 300), "multi": ("card_multi_series", 420), "event": ("card_event_state", 240),
    "interval": ("card_sample_interval", 300), "frequency": ("card_frequency", 340), "xy": ("card_xy", 380),
    "xyz": ("card_3d_plot", 420), "gnss": ("card_map", 620), "gnss_3d": ("card_3d_map", 560),
    "image": ("card_image", 460), "video": ("card_video", 440),
}
# The Multi-series card in the main window pictures: four series, never a video.
MULTI_SERIES = ("Heart rate/Heart rate", "Activity/Speed", "Barometer/Altitude", "Activity/Cadence")
# The Frequency card shows the 360 video's soundtrack: 48 kHz audio, sampled regularly.
AUDIO_SERIES = "Media/Audio left"
# Shown in the data folder field instead of the real path.
SHORT_FOLDER = "LODGaZilla\\demodata"
# The demo sessions' "dataset" metadata; the Navigator is filtered to it.
DEMO_DATASET = "LODGaZilla demo data (synthetic)"
# The internal log's last line in the main window pictures.
LOG_LINE = "Opened workspace demo.umw"
ANNOTATION_COLOR = QColor("#ffcc00")


def annotated_areas(window) -> list[tuple[str, object, str]]:  # type: ignore[no-untyped-def]
    """Title, widget and the corner for its label: an empty spot, so the label hides nothing.

    The numbers are the README's table numbers.
    """
    controls = window.navigator.controls
    return [
        ("File bar", controls.file_bar, "right"),
        ("Navigator controls", controls.frame, "bottom left"),
        ("Navigator", window.navigator.tree, "bottom left"),
        ("AI Integration", window.ai_panel, "right"),
        ("Plot navigator toolbar", window.plot_navigator.header_widget, "left"),
        ("Plot navigator timeline", window.plot_navigator.canvas, "left"),
        ("Plot workspace", window.plot_workspace, "bottom left"),
        ("Internal log", window.log_panel, "right"),
    ]


def wait(app: QApplication, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)


class NotOnScreen(RuntimeError):
    """The screen shows something else than the window, such as the lock screen."""


def grab_screen(app: QApplication, widget) -> QPixmap:  # type: ignore[no-untyped-def]
    top_left = widget.mapToGlobal(QPoint(0, 0))
    screen = app.screenAt(top_left) or app.primaryScreen()
    origin = screen.geometry().topLeft()
    return screen.grabWindow(0, top_left.x() - origin.x(), top_left.y() - origin.y(),
                             widget.width(), widget.height())


def check_on_screen(app: QApplication, window) -> None:  # type: ignore[no-untyped-def]
    """Compare the Plot navigator toolbar on screen with how Qt draws it; raise if they differ."""
    widget = window.plot_navigator.header_widget
    images = []
    for pixmap in (grab_screen(app, widget), widget.grab()):
        image = pixmap.toImage().scaled(96, 24).convertToFormat(QImage.Format.Format_RGB32)
        images.append(np.frombuffer(bytes(image.constBits()), np.uint8).reshape(24, image.bytesPerLine())[:, :96 * 4])
    if np.abs(images[0].astype(int) - images[1].astype(int)).mean() > 25:
        raise NotOnScreen


def capture(app: QApplication, window, widget) -> QPixmap:  # type: ignore[no-untyped-def]
    """The widget's area from the screen, as drawn, including web and OpenGL views."""
    check_on_screen(app, window)
    pixmap = grab_screen(app, widget)
    check_on_screen(app, window)
    return pixmap


def save(pixmap: QPixmap, path: Path, width: int) -> None:
    """Save at the shared width, so every picture shows at the same size in the README."""
    image = pixmap.toImage().scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
    image.save(str(path))
    print(f"{path.name}: {image.width()} x {image.height()}")


def annotate(window, pixmap: QPixmap) -> QPixmap:  # type: ignore[no-untyped-def]
    """Outline and number each area of the main window on a copy of its screenshot."""
    image = QPixmap(pixmap)
    image.setDevicePixelRatio(1.0)
    scale = image.width() / max(1, window.width())
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont(window.font())
    font.setPixelSize(round(15 * scale))
    font.setBold(True)
    painter.setFont(font)
    for number, (title, widget, corner) in enumerate(annotated_areas(window), start=1):
        if not widget.isVisible():
            continue
        top_left = widget.mapTo(window, QPoint(0, 0))
        rect = QRectF(top_left.x() * scale, top_left.y() * scale, widget.width() * scale, widget.height() * scale)
        rect = rect.adjusted(2 * scale, 2 * scale, -2 * scale, -2 * scale)
        painter.setPen(QPen(ANNOTATION_COLOR, 3 * scale))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 6 * scale, 6 * scale)
        label = f" {number}  {title} "
        width = painter.fontMetrics().horizontalAdvance(label) + 8 * scale
        height = painter.fontMetrics().height() + 6 * scale
        # Inside the outline, at its top left; a short area gets its label centered instead.
        badge = QRectF(rect.left() + 6 * scale, rect.top() + 6 * scale, width, height)
        if "right" in corner:
            badge.moveRight(rect.right() - 40 * scale)
        if "bottom" in corner:
            badge.moveBottom(rect.bottom() - 6 * scale)
        if rect.height() < height + 12 * scale:
            badge.moveTop(rect.center().y() - height / 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ANNOTATION_COLOR)
        painter.drawRoundedRect(badge, height / 2, height / 2)
        painter.setPen(QColor("#1c1c1c"))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
    painter.end()
    return image


class Screenshots:
    """The demo workspace's cards rearranged for each picture."""

    def __init__(self, app: QApplication, window, workspace: Path, output: Path, width: int,  # type: ignore[no-untyped-def]
                 only: tuple[str, ...] = ()) -> None:
        self.app, self.window, self.output, self.width = app, window, output, width
        # Picture names (without .png) to take; empty takes them all.
        self.only = set(only)
        state = read_workspace_file(workspace)
        # Grouped by date and filtered to the demo sessions, so a user's own folders in demodata stay hidden.
        self.navigator_state = {"folder": str(workspace.parent), "group_by": "Year/Month/Day",
                                "filter_key": "dataset", "filter_value": DEMO_DATASET}
        self.records: dict[str, dict] = {}
        for row in state["workspace"]["rows"]:
            for record in row["cards"]:
                self.records.setdefault(record["type"], record)
        watch = Path(self.records["multi"]["series"][0]["path"])
        self.records["multi_main"] = {"type": "multi", "filmstrips": [],
                                      "series": [{"path": str(watch), "name": name} for name in MULTI_SERIES]}
        audio = sorted(watch.parent.glob("VID_*.audio.h5"))
        if audio:
            self.records["frequency"] = {"type": "frequency", "series": [{"path": str(audio[0]), "name": AUDIO_SERIES}]}

    def show(self, rows: list[tuple[int, list[str]]], *, legends: bool, card_controls: bool,
             plot_navigator: bool) -> None:
        """Replace the cards with ``rows`` of (height, card types), then place both markers."""
        workspace_rows = [{"height": height, "widths": None,
                           "cards": [copy.deepcopy(self.records[kind]) for kind in kinds if kind in self.records]}
                          for height, kinds in rows]
        state = {"navigator": self.navigator_state,
                 "workspace": {"rows": [row for row in workspace_rows if row["cards"]], "active": 0, "scroll": 0}}
        for problem in self.window.apply_workspace_state(state):
            print("Not restored:", problem)
        self.set_toggles(legends=legends, card_controls=card_controls, plot_navigator=plot_navigator)
        wait(self.app, 12)
        # Both markers and the selection between them, a third of the way into the run.
        entries = [entry for entry in self.window.plot_navigator.entries if entry.stop > entry.start]
        if entries:
            start = min(entry.start for entry in entries)
            stop = max(entry.stop for entry in entries)
            self.window.plot_workspace.place_marker_from_plot_navigator(start + 0.35 * (stop - start))
            self.window.plot_workspace.place_second_marker_from_plot_navigator(start + 0.5 * (stop - start))
        wait(self.app, 4)
        self.fit_maps(self.window.plot_workspace.plots())

    def fit_maps(self, cards) -> None:  # type: ignore[no-untyped-def]
        """Fit maps to their tracks again: they fit when created, before the layout and markers settle.

        Leaflet animates the zoom and then loads the tiles, so wait for both.
        """
        maps = [card for card in cards if hasattr(card, "_push_tracks")]
        has_tracks = "typeof map !== 'undefined' && typeof tracks !== 'undefined' && tracks.length > 0"
        for card in maps:
            page = card.map_view.page()
            if hasattr(card, "center_view"):
                # The 3D Map: wait for its track, style, terrain and tiles, then frame the track
                # with Center view; redrawing the tracks would repeat its Auto.
                self.wait_for_page(page, f"{has_tracks} && map.loaded()")
                card.center_view()
                continue
            # The Map: once its tracks are in, Leaflet learns the card's settled size before the fit.
            self.wait_for_page(page, has_tracks)
            page.runJavaScript("map.invalidateSize(false);")
            card._push_tracks(True)
            # Redrawing the tracks drops the markers; draw them again, as the card does after a load.
            card._marker_revision += 1
            card._flush_marker()
        if maps:
            wait(self.app, 3)
            # Then wait for the tiles of the new view.
            for card in maps:
                self.wait_for_page(card.map_view.page(), "map.loaded()" if hasattr(card, "center_view") else
                                   "[street, satellite].every(layer => !map.hasLayer(layer) || !layer.isLoading())")
            # The web view keeps showing its old picture until it repaints; a resize makes it.
            size = self.window.size()
            self.window.resize(size.width() + 1, size.height())
            wait(self.app, 1)
            self.window.resize(size)
            wait(self.app, 4)

    def wait_for_page(self, page, condition: str, timeout: float = 90) -> None:  # type: ignore[no-untyped-def]
        """Wait until a web page's JavaScript ``condition`` is true, or the timeout passes."""
        result: list[bool] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result.clear()
            page.runJavaScript(condition, 0, lambda value: result.append(bool(value)))
            wait(self.app, 1)
            if result and result[0]:
                return
        print(f"The page did not get ready in {timeout:g} s: {condition}", file=sys.stderr)

    def set_toggles(self, *, legends: bool, card_controls: bool, plot_navigator: bool) -> None:
        navigator = self.window.plot_navigator
        navigator.legends_button.setChecked(legends)
        navigator.navigator_button.setChecked(plot_navigator)
        # Each card's Card controls button opens its controls; open or close them on every card.
        for card in self.window.plot_workspace.plots():
            card.set_header_controls_shown(card_controls)

    def set_navigator_shown(self, shown: bool) -> None:
        """Give the Plot area the whole width, so card pictures are as wide as the window."""
        splitter = self.window.main_splitter
        total = sum(splitter.sizes())
        splitter.setSizes([round(total * 0.3), total - round(total * 0.3)] if shown else [0, total])
        wait(self.app, 2)

    def spread_multi_series_lanes(self) -> None:
        """Fill the Multi-series card's height with its lanes, slightly overlapping, and select one."""
        for card in self.window.plot_workspace.plots():
            plot = getattr(card, "series_plot", None)
            if plot is None or not hasattr(plot, "transforms") or not card.series:
                continue
            count = len(card.series)
            lane = 1.1 / count
            ranges = {key: (float(data.y[data.y == data.y].min()), float(data.y[data.y == data.y].max()))
                      for key, _, _, data in plot.items() if data is not None and len(data.y)}
            for index, entry in enumerate(card.series):
                low, high = plot.value_ranges.get(entry) or ranges.get(entry, (0.0, 1.0))
                gain = lane / (high - low) if high > low else lane
                midpoint = (low + high) / 2
                plot.value_midpoints[entry] = midpoint
                # Lane centres from half a lane above the bottom to half a lane below the top.
                center = lane / 2 + index * (1 - lane) / max(1, count - 1)
                plot.transforms[entry] = (gain, center - midpoint * gain)
            plot.apply_transforms()
            plot.select_series(card.series[-1])
        wait(self.app, 2)


    def capture_main_window(self, widget) -> QPixmap:  # type: ignore[no-untyped-def]
        """Capture with the short folder path shown, then show the real one again for later refreshes."""
        edit = self.window.navigator.controls.folder_edit
        with QSignalBlocker(self.window.navigator.controls.folder_combo), QSignalBlocker(edit):
            real = edit.text()
            edit.setText(SHORT_FOLDER)
            edit.setCursorPosition(0)
            wait(self.app, 1)
            try:
                return capture(self.app, self.window, widget)
            finally:
                edit.setText(real)

    def prepare_main_window(self) -> None:
        """The dated sessions expanded and a neutral last log line."""
        tree = self.window.navigator.tree
        for index in range(tree.topLevelItemCount()):
            year = tree.topLevelItem(index)
            if year.text(0).isdigit():
                year.setExpanded(True)
                for child in range(year.childCount()):
                    year.child(child).setExpanded(True)
        tree.clearSelection()
        self.window.internal_logger.info(LOG_LINE)
        self.window.plot_workspace.verticalScrollBar().setValue(0)
        wait(self.app, 4)

    def cards(self) -> None:
        self.set_navigator_shown(False)
        kinds = list(CARD_FILES)
        self.show([(CARD_FILES[kind][1], [kind]) for kind in kinds], legends=True, card_controls=True,
                  plot_navigator=True)
        workspace = self.window.plot_workspace
        for card, kind in zip(workspace.plots(), [kind for kind in kinds if kind in self.records]):
            name = CARD_FILES[kind][0]
            if not self.wanted(name):
                continue
            workspace.ensureWidgetVisible(card, 0, 0)
            wait(self.app, 3)
            # A map out of view cannot fit its track; fit it again now that it shows.
            self.fit_maps([card])
            if kind == "multi" and card.series:
                # A selected series shows its Y axis and label. The axis spans the whole card,
                # so pick a series whose values stay believable there: altitude, in metres.
                altitude = next((entry for entry in card.series if entry[1] == "Barometer/Altitude"), card.series[0])
                card.series_plot.select_series(altitude)
                wait(self.app, 2)
            save(capture(self.app, self.window, card), self.output / f"{name}.png", self.width)

    def wanted(self, name: str) -> bool:
        return not self.only or name in self.only

    def main_window(self) -> None:
        self.set_navigator_shown(True)
        self.show([(210, ["line"]), (300, ["multi_main"]), (150, ["event"]), (330, ["video", "xyz", "gnss"])],
                  legends=False, card_controls=False, plot_navigator=False)
        self.spread_multi_series_lanes()
        self.prepare_main_window()
        save(self.capture_main_window(self.window), self.output / "main_window.png", self.width)

    def annotated_main_window(self) -> None:
        self.show([(220, ["line"]), (300, ["multi_main"]), (160, ["event"]), (200, ["interval"])],
                  legends=True, card_controls=False, plot_navigator=True)
        self.spread_multi_series_lanes()
        self.prepare_main_window()
        if self.wanted("main_window_annotated"):
            save(annotate(self.window, self.capture_main_window(self.window)),
                 self.output / "main_window_annotated.png", self.width)

    def plot_navigator(self) -> None:
        """The toolbar and timeline at full width, zoomed in so the view box shows."""
        self.set_navigator_shown(False)
        entries = [entry for entry in self.window.plot_navigator.entries if entry.stop > entry.start]
        line = next((card for card in self.window.plot_workspace.plots() if hasattr(card, "plot_view_box")), None)
        if entries and line is not None:
            start = min(entry.start for entry in entries)
            stop = max(entry.stop for entry in entries)
            line.plot_view_box().setXRange(start + 0.2 * (stop - start), start + 0.7 * (stop - start), padding=0)
        wait(self.app, 4)
        if self.wanted("plot_navigator"):
            save(capture(self.app, self.window, self.window.plot_navigator), self.output / "plot_navigator.png",
                 self.width)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "images")
    parser.add_argument("--width", type=int, default=1600, help="Window width in screen pixels.")
    parser.add_argument("--height", type=int, default=1000, help="Window height in screen pixels.")
    parser.add_argument("--image-width", type=int, default=2000, help="Width of every saved picture.")
    parser.add_argument("--theme", choices=("dark", "light"), default="dark")
    parser.add_argument("--only", nargs="+", default=[], metavar="NAME",
                        help="Take only these pictures, by file name without .png, such as card_multi_series.")
    arguments = parser.parse_args()
    workspace = ROOT / "demodata" / "demo.umw"
    if not workspace.is_file():
        print("No demo data: start LODGaZilla once and let it create demodata/.", file=sys.stderr)
        return 1
    arguments.output.mkdir(parents=True, exist_ok=True)

    settings_folder = Path(tempfile.mkdtemp(prefix="lodgazilla-screenshots-"))
    settings_path = settings_folder / "app_settings.json"
    settings = json.loads((ROOT / "app_settings.json").read_text(encoding="utf-8"))
    settings.setdefault("appearance", {})["theme"] = arguments.theme
    settings.setdefault("lodgazilla", {})["offer_demo_data"] = False
    # Linked markers and views, with the time axis on the bottom card (Plot navigator preferences).
    settings["lodgazilla"].update(link_markers=True, link_plot_x_axes=True, time_axis_display="bottom")
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    configure_graphics()
    app = QApplication(sys.argv)
    configure_application(app, arguments.theme)
    from src.app.lodgazilla import LODGaZillaWindow

    window = LODGaZillaWindow(ROOT / "demodata", settings_path=settings_path)
    window.resize(arguments.width, arguments.height)
    window.move(20, 20)
    # The capture reads the screen: keep every other window, such as a meeting, out of it.
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    window.show()
    window.raise_()
    window.activateWindow()
    window.start_loading_cards()
    wait(app, 8)

    pictures = Screenshots(app, window, workspace, arguments.output, arguments.image_width, tuple(arguments.only))
    result = 0
    try:
        if any(pictures.wanted(name) for name, _ in CARD_FILES.values()):
            pictures.cards()
        if pictures.wanted("main_window"):
            pictures.main_window()
        if pictures.wanted("main_window_annotated") or pictures.wanted("plot_navigator"):
            pictures.annotated_main_window()
            pictures.plot_navigator()
    except NotOnScreen:
        print("The window was not on screen (locked or covered); the pictures taken so far are kept. "
              "Keep the screen unlocked and run this again.", file=sys.stderr)
        result = 1

    window.close()
    wait(app, 2)
    shutil.rmtree(settings_folder, ignore_errors=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
