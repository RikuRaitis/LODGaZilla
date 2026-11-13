"""Record the demo video's scenes from the demo data, with real mouse input (see docs/VIDEO_DEMO.md).

``python tools/demo_video/record.py [--scenes 2 3] [--output build/demo_video]``

Starts LODGaZilla with the demo data in a window whose contents are 1920 x 1080 screen
pixels, then plays each scene: the scene is set up off camera, a separate recorder
process captures the window from the screen, and the real cursor moves and clicks while
every event is logged. Each scene leaves ``scene<N>.h264``, ``scene<N>.frames.json`` and
``scene<N>.events.json`` for ``edit.py``.

Needs the demo data, an unlocked screen and no one touching the mouse or keyboard while
it runs. Uses a temporary copy of the settings, no plugins and the dark theme.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import Future
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CLEAN_ENV = dict(os.environ)  # Before Qt adds its own FFmpeg libraries to PATH; for the recorder.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ["LODGAZILLA_NO_PLUGINS"] = "1"

import numpy as np  # noqa: E402
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyle  # noqa: E402

import take_readme_screenshots as shots  # noqa: E402
from demo_video.explorer import ExplorerWindow  # noqa: E402
from src.gui.navigator.widget import GROUPING_NONE  # noqa: E402
from demo_video.mouse import Mouse  # noqa: E402
from src.data.workspace_file import read_workspace_file  # noqa: E402

WIDTH, HEIGHT = 1920, 1080
SESSION = "20261103_071500"
LONG_SESSION = "20261109_060000"
# The scenes' data folder: a staged copy in the shared Public folder, so the Data folder field and
# Magic drop's messages show no user name. (A fake text in the field breaks the Navigator's
# refreshes and Magic drop, and the app resolves drive letters and links to the real path.)
PUBLIC_STAGE = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "LODGaZilla_demo"
STAGE = PUBLIC_STAGE / "demodata"


def stage_demo_data() -> None:
    """Hard-link the demo data into STAGE (same drive: no copying, no space), but copy the
    info files and audio H5 files, which the app writes to: edits never reach the real
    demo data, and each run starts from its saved state."""
    source = ROOT / "demodata"
    for path in source.rglob("*"):
        if path.is_dir() or path.suffix == ".lock" or path.name == "demo.umw":
            continue
        target = STAGE / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        written = path.name.endswith(("_info.json", ".audio.h5"))
        if written or not target.exists():
            target.unlink(missing_ok=True)
            if written:
                shutil.copy2(path, target)
            else:
                os.link(path, target)


def remove_tree(folder: Path) -> None:
    """Delete a folder this script made, including Git's read-only files; say if any stays."""
    def writable_again(function, path, _error):  # type: ignore[no-untyped-def]
        os.chmod(path, stat.S_IWRITE)
        function(path)
    if folder.exists():
        shutil.rmtree(folder, onerror=writable_again)
    if folder.exists():
        print(f"Could not remove {folder}", file=sys.stderr)


def stage_paths(value):  # type: ignore[no-untyped-def]
    """A workspace record with its demo data paths moved to STAGE."""
    real, staged = json.dumps(str(ROOT / "demodata"))[1:-1], json.dumps(str(STAGE))[1:-1]
    return json.loads(json.dumps(value).replace(real, staged))
# A screen point outside the window, where the cursor waits when a scene doesn't use it.
OFF_WINDOW = (2450.0, 1350.0)


class Bridge(QObject):
    """Runs a function on the GUI thread for the worker thread and returns its result."""

    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def _run(job) -> None:  # type: ignore[no-untyped-def]
        function, future = job
        try:
            future.set_result(function())
        except BaseException as error:  # noqa: BLE001 - handed to the worker thread
            future.set_exception(error)

    def __call__(self, function, timeout: float = 120):  # type: ignore[no-untyped-def]
        future: Future = Future()
        self.call.emit((function, future))
        return future.result(timeout)


class Director:
    """Sets up each scene, records it and plays its mouse actions (worker thread)."""

    def __init__(self, app: QApplication, window, output: Path) -> None:  # type: ignore[no-untyped-def]
        self.app, self.window, self.output = app, window, output
        self.gui = Bridge()
        self.mouse = Mouse()
        self.dpr = app.primaryScreen().devicePixelRatio()
        stage_demo_data()
        shutil.rmtree(STAGE / "bike_ride_power", ignore_errors=True)  # A Magic drop take's folder.
        state = stage_paths(read_workspace_file(ROOT / "demodata" / "demo.umw"))
        self.records: dict[str, dict] = {}
        for row in state["workspace"]["rows"]:
            for record in row["cards"]:
                self.records.setdefault(record["type"], record)
        self.workspace_cards = [card for row in state["workspace"]["rows"] for card in row["cards"]]
        watch = Path(self.records["multi"]["series"][0]["path"])
        self.watch = watch
        self.records["multi_main"] = {"type": "multi", "filmstrips": [], "series": [
            {"path": str(watch), "name": name} for name in shots.MULTI_SERIES]}
        audio = sorted(watch.parent.glob("VID_*.audio.h5"))
        self.records["frequency"] = {"type": "frequency", "series": [{"path": str(audio[0]), "name": shots.AUDIO_SERIES}]}
        long_watch = STAGE / LONG_SESSION / f"watch_{LONG_SESSION}.h5"
        self.records["long_line"] = {"type": "line", "series": [{"path": str(long_watch), "name": "Accelerometer/X"}]}
        self.navigator_state = {"folder": str(STAGE), "group_by": "Year/Month/Day",
                                "filter_key": "dataset", "filter_value": shots.DEMO_DATASET}

    # --- Geometry, on the GUI thread -------------------------------------------------------
    def point(self, widget, local=None) -> tuple[float, float]:  # type: ignore[no-untyped-def]
        """A widget's point (default: centre) in physical screen pixels."""
        def compute():
            spot = widget.rect().center() if local is None else local
            spot = spot.toPoint() if isinstance(spot, QPointF) else spot
            glob = widget.mapToGlobal(spot)
            return glob.x() * self.dpr, glob.y() * self.dpr
        return self.gui(compute)

    def capture_rect(self) -> list[int]:
        def compute():
            top_left = self.window.mapToGlobal(QPoint(0, 0))
            return [round(top_left.x() * self.dpr), round(top_left.y() * self.dpr),
                    round(self.window.width() * self.dpr) // 2 * 2, round(self.window.height() * self.dpr) // 2 * 2]
        return self.gui(compute)

    def tree_item(self, *names: str):  # type: ignore[no-untyped-def]
        """The Navigator tree item at a path of names from the top level, or None."""
        def find():
            tree = self.window.navigator.tree
            items = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
            item = None
            for name in names:
                item = next((candidate for candidate in items if candidate.text(0) == name), None)
                if item is None:
                    return None
                items = [item.child(index) for index in range(item.childCount())]
            return item
        return self.gui(find)

    def item_point(self, *names: str) -> tuple[float, float]:
        """The middle of an item's name in the Navigator tree, in physical screen pixels."""
        item = self.wait_for(lambda: self.tree_item(*names), f"tree item {'/'.join(names)}")

        def compute():
            tree = self.window.navigator.tree
            tree.scrollToItem(item)
            rect = tree.visualItemRect(item)
            width = tree.fontMetrics().horizontalAdvance(item.text(0))
            local = QPoint(rect.left() + min(width // 2 + 4, max(4, tree.columnWidth(0) - rect.left() - 8)),
                           rect.center().y())
            glob = tree.viewport().mapToGlobal(local)
            return glob.x() * self.dpr, glob.y() * self.dpr
        return self.gui(compute)

    def cards(self) -> list:
        return self.gui(lambda: list(self.window.plot_workspace.plots()))

    def canvas_point(self, card, fraction_x: float, fraction_y: float = 0.5) -> tuple[float, float]:  # type: ignore[no-untyped-def]
        """A point on a time card's plot area, by fractions of its visible width and height."""
        def compute():
            plot = getattr(card, "series_plot", card)
            view_box = plot.plot_widget.getPlotItem().getViewBox()
            rect = view_box.sceneBoundingRect()
            scene_point = QPointF(rect.left() + fraction_x * rect.width(), rect.top() + fraction_y * rect.height())
            local = plot.plot_widget.mapFromScene(scene_point)
            glob = plot.plot_widget.viewport().mapToGlobal(local)
            return glob.x() * self.dpr, glob.y() * self.dpr
        return self.gui(compute)

    # --- Waiting --------------------------------------------------------------------------
    def wait_for(self, condition, what: str, timeout: float = 60):  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.1)
        raise TimeoutError(f"Waited {timeout} s for {what}")

    # --- Setting up -------------------------------------------------------------------------
    def apply(self, rows: list[tuple[int, list[str]]], *, navigator: bool, legends: bool = False,
              markers: tuple[float, float | None] | None = None, expand: tuple[tuple[str, ...], ...] = ()) -> None:
        """Replace the cards with ``rows`` and settle the window for a scene, off camera."""
        def setup():
            window = self.window
            workspace_rows = [{"height": height, "widths": None,
                               "cards": [copy.deepcopy(self.records[kind]) for kind in kinds]}
                              for height, kinds in rows]
            state = {"navigator": self.navigator_state,
                     "workspace": {"rows": workspace_rows, "active": 0, "scroll": 0}}
            for problem in window.apply_workspace_state(state):
                self.say(f"Not restored: {problem}")
            navigator_buttons = window.plot_navigator
            navigator_buttons.legends_button.setChecked(legends)
            navigator_buttons.card_controls_button.setChecked(True)
            navigator_buttons.navigator_button.setChecked(True)
            navigator_buttons.sample_loss_button.setChecked(True)
            navigator_buttons.clipping_button.setChecked(True)
            splitter = window.main_splitter
            total = sum(splitter.sizes())
            splitter.setSizes([round(total * 0.3), total - round(total * 0.3)] if navigator else [0, total])
            window.ai_panel.set_expanded(False)
        self.gui(setup)
        time.sleep(8)
        self.gui(lambda: self.collapse_tree(expand))
        if markers is not None:
            def place():
                entries = [entry for entry in self.window.plot_navigator.entries if entry.stop > entry.start]
                start = min(entry.start for entry in entries)
                stop = max(entry.stop for entry in entries)
                self.window.plot_workspace.place_marker_from_plot_navigator(start + markers[0] * (stop - start))
                if markers[1] is not None:
                    self.window.plot_workspace.place_second_marker_from_plot_navigator(start + markers[1] * (stop - start))
            self.gui(place)
        time.sleep(3)
        self.fit_maps()
        self.gui(self.neutral_window)
        self.gui(self.show_whole_range)
        time.sleep(1)

    def show_whole_range(self) -> None:
        """Zoom the linked time cards out to all their data (a card can open on a narrow range)."""
        entries = [entry for entry in self.window.plot_navigator.entries if entry.stop > entry.start]
        if entries:
            self.window.plot_workspace.set_linked_view_range(min(entry.start for entry in entries),
                                                             max(entry.stop for entry in entries))

    def collapse_tree(self, expand: tuple[tuple[str, ...], ...]) -> None:
        """Collapse the Navigator tree, then expand each path in ``expand``."""
        tree = self.window.navigator.tree
        tree.collapseAll()
        tree.clearSelection()
        # Wide enough for a session folder's name, such as 20261103_071500.
        tree.setColumnWidth(0, 205)
        for path in expand:
            items = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
            for name in path:
                item = next((candidate for candidate in items if candidate.text(0) == name), None)
                if item is None:
                    break
                item.setExpanded(True)
                items = [item.child(index) for index in range(item.childCount())]
        tree.scrollToTop()

    def neutral_window(self) -> None:
        """The folder path from its start, a neutral log line and the workspace at its top."""
        self.window.navigator.controls.folder_edit.setCursorPosition(0)
        self.window.internal_logger.info("Opened the demo data")
        self.window.plot_workspace.verticalScrollBar().setValue(0)

    def fit_maps(self) -> None:
        maps = [card for card in self.cards() if hasattr(card, "_push_tracks")]
        for card in maps:
            if hasattr(card, "center_view"):
                self.gui(card.center_view)
                continue
            self.gui(lambda card=card: card.map_view.page().runJavaScript("map.invalidateSize(false);"))
            self.gui(lambda card=card: (card._push_tracks(True), setattr(card, "_marker_revision", card._marker_revision + 1),
                                        card._flush_marker()))
        if maps:
            time.sleep(4)

    # --- Recording --------------------------------------------------------------------------
    def record(self, name: str, actions, lead: float = 0.8, tail: float = 1.0) -> None:  # type: ignore[no-untyped-def]
        """Record the window while ``actions`` runs, with ``lead`` and ``tail`` seconds around it."""
        rect = self.capture_rect()
        base = self.output / name
        recorder = subprocess.Popen(
            [sys.executable, str(HERE / "recorder.py"), *map(str, rect), str(base)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=CLEAN_ENV)
        if recorder.stdout.readline().strip() != "recording":
            raise RuntimeError("The recorder did not start")
        self.mouse.events = []
        self.mouse._log("start")
        started = time.perf_counter()
        time.sleep(lead)
        action_start = time.perf_counter()
        actions()
        action_end = time.perf_counter()
        time.sleep(tail)
        recorder.stdin.write("stop\n")
        recorder.stdin.flush()
        summary = recorder.stdout.read().strip().splitlines()[-1]
        recorder.wait(30)
        (base.parent / (base.name + ".events.json")).write_text(json.dumps({
            "capture_rect": rect, "device_pixel_ratio": self.dpr, "started": started,
            "action_start": action_start, "action_end": action_end, "events": self.mouse.events,
        }), encoding="utf-8")
        self.say(f"{name}: {summary}, actions {action_end - action_start:.1f} s")

    def rest(self, seconds: float) -> None:
        time.sleep(seconds)

    def say(self, message: str) -> None:
        """Report to record.log: the app sends the process's standard output to its own log."""
        with open(self.output / "record.log", "a", encoding="utf-8") as log:
            log.write(f"{time.strftime('%H:%M:%S')} {message}\n")

    # --- Scenes -----------------------------------------------------------------------------
    def scene2(self) -> None:
        """The Navigator: recordings by date; a double-click plots a series."""
        self.apply([], navigator=True)
        start = self.point(self.window.plot_workspace)
        self.mouse.jump(*start)

        def actions():
            for path in (("2026",), ("2026", "November"), ("2026", "November", "03"),
                         ("2026", "November", "03", SESSION), ("2026", "November", "03", SESSION, "Heart rate")):
                self.mouse.move(*self.item_point(*path))
                self.rest(0.15)
                self.mouse.double_click()
                self.rest(0.35)
            self.mouse.move(*self.item_point("2026", "November", "03", SESSION, "Heart rate", "Heart rate"))
            self.rest(0.2)
            self.mouse.double_click()
            self.wait_for(lambda: self.cards(), "the Line card")
            self.rest(0.4)
            self.mouse.move(*self.point(self.window.plot_workspace), seconds=0.8)
            self.rest(1.5)
        self.record("scene2", actions)

    def scene3(self) -> None:
        """Drag data into a card, show the legend, place both markers and read a values box."""
        heart_rate = ("2026", "November", "03", SESSION, "Heart rate")
        activity = ("2026", "November", "03", SESSION, "Activity")
        # Multi-series, so Speed gets its own lane instead of a flat line on the heart rate's axis.
        self.apply([(545, ["heart_multi"])], navigator=True, expand=(heart_rate, activity))
        card = self.cards()[0]
        self.mouse.jump(*self.canvas_point(card, 0.5, 0.5))
        legends = self.window.plot_navigator.legends_button

        def actions():
            self.mouse.move(*self.point(legends))
            self.rest(0.1)
            self.mouse.click()
            self.rest(0.4)
            self.mouse.move(*self.item_point(*activity, "Speed"))
            self.rest(0.15)
            self.mouse.drag(*self.point(card.legend), seconds=0.9, hold=0.5)
            self.wait_for(lambda: self.gui(lambda: len(card.series) >= 2), "the drop")
            self.rest(0.5)
            self.mouse.move(*self.canvas_point(card, 0.38, 0.6))
            self.rest(0.1)
            self.mouse.click("left")
            self.rest(0.5)
            self.mouse.move(*self.canvas_point(card, 0.62, 0.6))
            self.rest(0.1)
            self.mouse.click("right")
            self.rest(0.6)
            boxes = self.gui(lambda: card.series_plot.value_boxes.visible_boxes())
            if boxes:
                self.mouse.move(*self.point(boxes[0]))
                self.rest(1.9)
            self.mouse.move(*self.point(legends))
            self.rest(0.1)
            self.mouse.click()
            self.rest(0.4)
        self.record("scene3", actions)

    def scene1(self) -> None:
        """The opening: the main window full of demo cards, the video playing."""
        self.apply([(165, ["multi_main"]), (165, ["accel_line"]), (215, ["video", "gnss_3d", "gnss"])],
                   navigator=False, markers=(0.05, None))
        self.mouse.jump(*OFF_WINDOW)
        video = self.video_card()
        self.gui(lambda: video.set_playing(True))
        self.rest(1.0)
        self.record("scene1", lambda: self.rest(6.0), lead=0.3, tail=0.3)
        self.gui(lambda: video.set_playing(False))

    def scene4(self) -> None:
        """The video plays in sync with the data, the soundtrack's spectrogram and the map."""
        self.apply([(165, ["multi_main"]), (165, ["frequency"]), (215, ["video", "xyz", "gnss"])],
                   navigator=True, markers=(0.04, None),
                   expand=(("2026", "November", "03", SESSION, "Media"),))
        video = self.video_card()
        resting = self.gui(lambda: QPoint(video.width() // 2, video.height() // 3))
        self.mouse.jump(*self.point(video, resting))

        def actions():
            self.mouse.move(*self.point(video.play_button))
            self.rest(0.15)
            self.mouse.click()
            self.rest(0.6)
            self.mouse.move(*self.point(video, resting), seconds=0.7)
            self.rest(7.5)
        self.record("scene4", actions)
        self.gui(lambda: video.set_playing(False))

    def scene5(self) -> None:
        """Ctrl+wheel zooms the two-hour run's accelerometer from hours down to single samples."""
        self.apply([(545, ["long_line"])], navigator=False)
        card = self.cards()[0]
        self.mouse.jump(*self.canvas_point(card, 0.75, 0.75))

        def actions():
            self.mouse.move(*self.canvas_point(card, 0.5, 0.5), seconds=0.6)
            self.rest(0.2)
            self.mouse.key("ctrl", True)
            self.rest(0.15)
            # Down to about 50 ms, where the 1000 Hz samples show as separate points.
            self.mouse.wheel(72, 6.5)
            self.rest(0.15)
            self.mouse.key("ctrl", False)
            self.rest(2.2)
        self.record("scene5", actions)

    def scene6(self) -> None:
        """Magic drop: a CSV dropped from Explorer; the AI reads it, writes a parser and the new
        folder is plotted. A real run with the AI panel's provider; the edit shortens the wait."""
        data = STAGE
        made = data / "bike_ride_power"  # Magic drop's folder for the CSV; only this one is removed.
        shutil.rmtree(made, ignore_errors=True)
        # Not grouped by date: the CSV's date is unknown until the AI reads it.
        self.navigator_state = {"folder": str(data), "group_by": GROUPING_NONE, "filter_key": "", "filter_value": ""}
        self.apply([], navigator=True)
        self.gui(lambda: self.window.ai_panel.set_expanded(True))
        time.sleep(1.5)
        drop_zone = self.window.ai_panel.magic_drop_zone
        explorer = ExplorerWindow(self.output / "drop")
        try:
            explorer.place(-390, 150, 1100, 480)
            left, top, _right, _bottom = explorer.rect()
            self.mouse.jump(left + 760, top + 330)

            def actions():
                self.mouse.move(left + 610, top + 272)
                self.rest(0.2)
                self.mouse.drag(*self.point(drop_zone), seconds=1.0, hold=0.6)
                self.mouse._log("mark", name="dropped")
                explorer.close()
                self.wait_for(lambda: self.gui(lambda: self.window.magic_drop_job is not None), "Magic drop to start")
                self.mouse.move(*self.point(self.window.ai_panel), seconds=0.8)
                self.wait_for(lambda: self.gui(lambda: self.window.magic_drop_job is None), "the AI", timeout=1200)
                self.mouse._log("mark", name="ai_done")
                folder = self.wait_for(lambda: made if (made / "bike_ride_power.h5").is_file() else None, "the H5 file")
                self.say(f"Magic drop made {folder.name}: {sorted(p.name for p in folder.iterdir())}")
                path = self.wait_for(lambda: self.tree_path(folder.name), "the new folder in the Navigator")
                self.rest(1.0)
                for depth in range(1, len(path) + 1):
                    item = self.tree_item(*path[:depth])
                    if not self.gui(item.isExpanded):
                        self.mouse.move(*self.item_point(*path[:depth]))
                        self.rest(0.15)
                        self.mouse.double_click()
                        self.rest(0.4)
                series = self.wait_for(lambda: self.first_series(path), "a series in the new folder")
                for depth in range(len(path) + 1, len(series)):
                    self.mouse.move(*self.item_point(*series[:depth]))
                    self.rest(0.15)
                    self.mouse.double_click()
                    self.rest(0.4)
                self.mouse.move(*self.item_point(*series))
                self.rest(0.2)
                self.mouse.double_click()
                self.wait_for(lambda: self.cards(), "the new card")
                self.mouse._log("mark", name="plotted")
                self.rest(0.5)
                self.mouse.move(*self.point(self.window.plot_workspace), seconds=0.8)
                self.rest(2.0)
            self.record("scene6", actions)
        finally:
            explorer.close()

    def tree_path(self, name: str) -> tuple[str, ...] | None:
        """The names from the top of the Navigator tree down to the item called ``name``."""
        def find():
            tree = self.window.navigator.tree
            stack = [(tree.topLevelItem(index), ()) for index in range(tree.topLevelItemCount())]
            while stack:
                item, parents = stack.pop()
                path = (*parents, item.text(0))
                if item.text(0) == name:
                    return path
                stack.extend((item.child(index), path) for index in range(item.childCount()))
            return None
        return self.gui(find)

    def first_series(self, folder_path: tuple[str, ...]) -> tuple[str, ...] | None:
        """The path to the new folder's most telling data series: power if there is one."""
        folder = self.tree_item(*folder_path)  # Asks the GUI thread itself, so not inside find().

        def find():
            found = []
            stack = [(folder.child(index), folder_path) for index in range(folder.childCount())]
            while stack:
                item, parents = stack.pop(0)
                path = (*parents, item.text(0))
                if item.data(0, Qt.ItemDataRole.UserRole) == "series":
                    found.append(path)
                stack.extend((item.child(index), path) for index in range(item.childCount()))
            power = [path for path in found if "power" in path[-1].lower()]
            return (power or found or [None])[0]
        return self.gui(find)

    # --- Helpers for the closer look ---------------------------------------------------------
    def label(self, text: str) -> None:
        """An on-screen label naming the action that follows (drawn by edit.py)."""
        self.mouse._log("label", text=text)

    def click_at(self, point: tuple[float, float], button: str = "left", pause: float = 0.15) -> None:
        self.mouse.move(*point)
        self.rest(pause)
        self.mouse.click(button)

    def popup_point(self, *texts: str) -> tuple[float, float]:
        """The centre of a menu action in the open popup menu; several texts walk into submenus."""
        def compute():
            menu = QApplication.activePopupWidget()
            # An open submenu is the active popup itself: then look for the last text in it.
            path = texts[-1:] if any(action.text().replace("&", "") == texts[-1] for action in menu.actions()) else texts
            for depth, text in enumerate(path):
                action = next((action for action in menu.actions()
                               if action.text().replace("&", "") == text), None)
                if action is None:
                    raise LookupError(f"No menu action {text!r} in {[a.text() for a in menu.actions()]}")
                if depth < len(path) - 1:
                    menu = action.menu()
                    continue
                glob = menu.mapToGlobal(menu.actionGeometry(action).center())
                return glob.x() * self.dpr, glob.y() * self.dpr
        return self.wait_for(lambda: self._try(compute), f"menu action {texts}", timeout=10)

    def _try(self, compute):  # type: ignore[no-untyped-def]
        try:
            return self.gui(compute)
        except (LookupError, AttributeError):
            return None

    def pick(self, combo, text: str, data=None) -> None:  # type: ignore[no-untyped-def]
        """Open a combo box with a click and click its item ``text`` (or the item holding ``data``)."""
        # An editable combo box opens from its arrow; a click on its text only edits it.
        arrow = self.gui(lambda: QPoint(combo.width() - 9, combo.height() // 2)) if self.gui(combo.isEditable) else None
        self.click_at(self.point(combo, arrow))
        self.rest(0.35)

        def compute():
            index = (combo.count() - 1 if text == "last" else
                     combo.findText(text) if data is None else combo.findData(data))
            if index < 0:
                raise LookupError(f"No item {text!r}")
            view = combo.view()
            view.scrollTo(view.model().index(index, 0))  # A long list scrolls; bring the item into view.
            rect = view.visualRect(view.model().index(index, 0))
            glob = view.viewport().mapToGlobal(rect.center())
            return glob.x() * self.dpr, glob.y() * self.dpr
        self.click_at(self.wait_for(lambda: self._try(compute), f"combo item {text}", timeout=10))

    def cell_point(self, item, column: int) -> tuple[float, float]:  # type: ignore[no-untyped-def]
        def compute():
            tree = self.window.navigator.tree
            rect = tree.visualRect(tree.indexFromItem(item, column))
            glob = tree.viewport().mapToGlobal(QPoint(rect.left() + min(60, rect.width() // 2), rect.center().y()))
            return glob.x() * self.dpr, glob.y() * self.dpr
        return self.gui(compute)

    def time_point(self, card, seconds: float, fraction_y: float = 0.5, axis: bool = False):  # type: ignore[no-untyped-def]
        """The point at ``seconds`` after the demo run's start on a time card (or its left axis)."""
        def compute():
            plot = getattr(card, "series_plot", card)
            widget = plot.plot_widget
            view_box = widget.getPlotItem().getViewBox()
            (left, right), (bottom, top) = view_box.viewRange()
            start = self.run_start
            scene = view_box.mapViewToScene(QPointF(start + seconds, bottom + fraction_y * (top - bottom)))
            local = widget.mapFromScene(scene)
            if axis:
                local.setX(round(widget.mapFromScene(view_box.sceneBoundingRect().topLeft()).x()) - 18)
            glob = widget.viewport().mapToGlobal(local)
            return glob.x() * self.dpr, glob.y() * self.dpr
        return self.gui(compute)

    @property
    def run_start(self) -> float:
        entries = [entry for entry in self.window.plot_navigator.entries if entry.stop > entry.start]
        return min(entry.start for entry in entries)

    def zoom_to(self, start: float, stop: float) -> None:
        """Zoom the linked time cards to seconds ``start``–``stop`` of the run, off camera."""
        def apply():
            origin = self.run_start
            self.window.plot_workspace.set_linked_view_range(origin + start, origin + stop)
        self.gui(apply)
        time.sleep(1.5)

    def slider_point(self, slider, value: float) -> tuple[float, float]:  # type: ignore[no-untyped-def]
        """The point on a horizontal slider for ``value``."""
        def compute():
            handle = slider.style().pixelMetric(QStyle.PixelMetric.PM_SliderLength, None, slider)
            span = max(1, slider.width() - handle)
            position = QStyle.sliderPositionFromValue(slider.minimum(), slider.maximum(), round(value), span)
            glob = slider.mapToGlobal(QPoint(position + handle // 2, slider.height() // 2))
            return glob.x() * self.dpr, glob.y() * self.dpr
        return self.gui(compute)

    def keep_info(self, session: str):  # type: ignore[no-untyped-def]
        """Back up a demo run's info file; the returned function restores it (metadata and sync
        offsets are written to it on camera)."""
        path = STAGE / session / f"{session}_info.json"
        saved = path.read_bytes()

        def restore() -> None:
            path.write_bytes(saved)
        return restore

    def find_item(self, *names: str):  # type: ignore[no-untyped-def]
        return self.wait_for(lambda: self.tree_item(*names), f"tree item {'/'.join(names)}")

    def series_record(self, kind: str, names: list[str], path: Path | None = None) -> dict:
        return {"type": kind, "filmstrips": [], "series": [{"path": str(path or self.watch), "name": name}
                                                         for name in names]}

    # --- A closer look ---------------------------------------------------------------------
    def scene7(self) -> None:
        """C1, Navigator: sample loss and clipping highlights; add, delete, group and filter metadata."""
        restore = self.keep_info(SESSION)
        info = ("2026", "November", "03", SESSION, "Info")
        imu = ("2026", "November", "03", SESSION, "Accelerometer")
        self.apply([], navigator=True, expand=(info, imu))
        controls = self.window.navigator.controls
        self.gui(lambda: [button.setChecked(False) for button in (controls.sample_loss_button, controls.clipping_button)])
        self.mouse.jump(*self.point(self.window.plot_workspace))

        def actions():
            self.label("Highlight sample loss")
            self.click_at(self.point(controls.sample_loss_button))
            self.rest(0.8)
            self.label("Highlight clipping")
            self.click_at(self.point(controls.clipping_button))
            self.rest(0.9)
            self.label("Add a metadata key")
            self.mouse.move(*self.item_point(*info, "[ADD NEW KEY]"))
            self.rest(0.15)
            self.mouse.double_click()
            self.rest(0.4)
            self.mouse.type("shoes")
            self.mouse.tap("enter")
            self.rest(0.6)
            row = self.find_item(*info, "shoes")
            self.mouse.move(*self.cell_point(row, 1))
            self.rest(0.15)
            self.mouse.double_click()
            self.rest(0.4)
            self.mouse.type("Trail shoes")
            self.mouse.tap("enter")
            self.rest(0.9)
            self.label("Delete a key")
            self.click_at(self.item_point(*info, "shoes"), "right")
            self.rest(0.4)
            self.click_at(self.popup_point("Delete key/value pair"))
            self.rest(0.8)
            self.label("Group by a key")
            self.pick(controls.group_by_combo, "runner")
            self.rest(1.2)
            self.label("Filter by a value")
            self.pick(controls.filter_key_combo, "sport")
            self.rest(0.4)
            self.pick(controls.filter_value_combo, "Trail running")
            self.rest(1.6)
        try:
            self.record("scene7", actions)
        finally:
            restore()

    def scene8(self) -> None:
        """C2, Line: sample loss and clipping areas, pan, zoom, and Auto / Lines / Points."""
        self.apply([(545, ["accel_line"])], navigator=False)
        card = self.cards()[0]
        self.mouse.jump(*self.time_point(card, 30, 0.75))

        def actions():
            # Clipping at 29.02-29.03 s and sample loss at 29.05-29.11 s sit side by side (demo data).
            self.label("Ctrl + wheel · zoom")
            self.mouse.move(*self.time_point(card, 29.06, 0.5), seconds=0.7)
            self.mouse.key("ctrl", True)
            self.mouse.wheel(31, 2.6)
            self.mouse.key("ctrl", False)
            self.rest(0.4)
            self.label("Shaded: clipping, then sample loss")
            self.mouse.move(*self.time_point(card, 29.025, 0.2), seconds=0.6)
            self.rest(0.8)
            self.mouse.move(*self.time_point(card, 29.08, 0.2), seconds=0.5)
            self.rest(0.9)
            self.label("Drag · pan")
            start = self.time_point(card, 29.15, 0.75)
            self.mouse.move(*start)
            self.mouse.drag(start[0] - 160, start[1], seconds=0.8, hold=0.1, nudge=False)
            self.rest(0.4)
            for style, text in (("points", "Points"), ("lines", "Lines"), ("auto", "Auto")):
                self.label(text)
                self.click_at(self.point(card.draw_style_buttons[style]))
                self.rest(0.9)
        self.record("scene8", actions)

    def scene9(self) -> None:
        """C3, Multi-series: select a trace, move its lane, scale it, add a filmstrip."""
        media = ("2026", "November", "03", SESSION, "Media")
        self.apply([(545, ["multi_main"])], navigator=True, legends=True, expand=(media,))
        card = self.cards()[0]
        self.mouse.jump(*self.point(self.window.plot_workspace))

        def trace_point(name: str, seconds: float):  # type: ignore[no-untyped-def]
            def compute():
                plot = card.series_plot
                for (path, series), _curve, _markers, data in plot.items():
                    if series == name and data is not None and len(data.x):
                        index = int(np.argmin(np.abs(np.asarray(data.x) - (self.run_start + seconds))))
                        gain, offset = plot.transforms.get((path, series), (1.0, 0.0))
                        view_box = plot.plot_widget.getPlotItem().getViewBox()
                        scene = view_box.mapViewToScene(QPointF(float(data.x[index]), float(data.y[index]) * gain + offset))
                        glob = plot.plot_widget.viewport().mapToGlobal(plot.plot_widget.mapFromScene(scene))
                        return glob.x() * self.dpr, glob.y() * self.dpr
                return None
            return self.wait_for(lambda: self.gui(compute), f"trace {name}")

        def actions():
            self.label("Click · select a trace")
            self.click_at(trace_point("Activity/Cadence", 20))
            self.rest(0.7)
            self.label("Drag · move its lane")
            start = trace_point("Activity/Cadence", 42)  # Away from the marker line the click placed.
            self.mouse.move(*start)
            self.mouse.drag(start[0], start[1] + 150, seconds=0.9, hold=0.1, nudge=False)
            self.rest(0.5)
            self.label("Ctrl + wheel on the axis · scale")
            self.mouse.move(*self.time_point(card, 20, 0.55, axis=True))
            self.mouse.key("ctrl", True)
            self.mouse.wheel(4, 0.8)
            self.mouse.key("ctrl", False)
            self.rest(0.6)
            self.label("Drop a video · filmstrip")
            self.mouse.move(*self.item_point(*media, "chase_cam_4k.mp4"))
            self.rest(0.15)
            self.mouse.drag(*self.point(card.legend), seconds=1.0, hold=0.6)
            self.rest(2.2)
        self.record("scene9", actions)

    def scene10(self) -> None:
        """C4, time offset: the foot pod's clock runs 1.7 s ahead of the watch."""
        # The watch's speed climbs at 23-25 s; the foot pod shows the same climb 1.7 s later.
        self.apply([(250, ["watch_speed"]), (250, ["pod_speed"])], navigator=False)
        self.zoom_to(17, 32)
        upper, lower = self.cards()
        self.mouse.jump(*self.time_point(upper, 20, 0.6))

        def actions():
            self.label("Left-click · red marker at the watch's change")
            self.click_at(self.time_point(upper, 24.0, 0.5))
            self.rest(1.0)
            self.label("Clock icon · time offset -1.7 s")
            self.click_at(self.point(lower.offset_button))
            self.rest(0.5)
            self.mouse.tap("ctrl", "a")
            # In the field's own number format: a "." is dropped where the decimal point is a comma.
            self.mouse.type("-1" + self.gui(lambda: lower.offset_editor.locale().decimalPoint()) + "7")
            self.mouse.tap("enter")
            self.rest(0.4)
            self.mouse.move(*self.time_point(lower, 24.0, 0.3), seconds=0.6)
            self.rest(2.0)
        self.record("scene10", actions)

    def scene11(self) -> None:
        """C5, Sample interval and Event/state: gaps, jitter, phases and lap presses."""
        self.apply([(270, ["intervals"]), (270, ["events"])], navigator=False)
        upper, lower = self.cards()
        self.mouse.jump(*self.time_point(upper, 20, 0.5))

        def actions():
            self.label("Sample interval · gaps and jitter")
            self.mouse.move(*self.time_point(upper, 37, 0.35), seconds=0.9)
            self.rest(1.6)
            self.label("Event/state · phases and events")
            self.mouse.move(*self.time_point(lower, 32, 0.5), seconds=0.9)
            self.rest(1.6)
        self.record("scene11", actions)

    def scene12(self) -> None:
        """C6, Frequency: FFT settings and zoom, on the video's soundtrack.

        Not the accelerometer: its timestamps jitter by about 3.5%, more than the Frequency
        card accepts, so it shows no IMU spectrogram (see docs/VIDEO_DEMO.md)."""
        self.apply([(545, ["frequency"])], navigator=False)
        card = self.cards()[0]
        self.wait_for(lambda: self.gui(lambda: card.data is not None), "the spectrogram")
        self.mouse.jump(*self.time_point(card, 20, 0.5))

        def actions():
            self.label("FFT settings · window size")
            self.click_at(self.point(card.fft_settings_button))
            self.rest(0.4)
            self.mouse.move(*self.popup_point("FFT window size"))
            self.rest(0.5)
            self.click_at(self.popup_point("FFT window size", "4096"))
            self.rest(1.2)
            self.label("FFT settings · colour range")
            self.click_at(self.point(card.fft_settings_button))
            self.rest(0.4)
            self.mouse.move(*self.popup_point("Colour range"))
            self.rest(0.5)
            self.click_at(self.popup_point("Colour range", "60 dB"))
            self.rest(1.2)
            self.label("Ctrl + wheel · zoom for more detail")
            self.mouse.move(*self.time_point(card, 32, 0.5), seconds=0.8)
            self.mouse.key("ctrl", True)
            self.mouse.wheel(12, 1.4)
            self.mouse.key("ctrl", False)
            self.rest(2.2)
        self.record("scene12", actions)

    def scene13(self) -> None:
        """C7, X/Y and 3D plot: every Nth sample, the sample limit, rotate and zoom."""
        self.apply([(545, ["xy", "xyz_new"])], navigator=False)
        xy, xyz = self.cards()
        self.mouse.jump(*self.point(xy))

        def actions():
            self.label("Every Nth sample · sample limit")
            self.mouse.move(*self.point(xyz.sample_limit_combo))
            self.rest(1.0)
            self.pick(xyz.sample_limit_combo, "last")  # The highest limit the card offers.
            self.rest(1.4)
            self.label("Drag · rotate")
            centre = self.point(xyz.view)
            self.mouse.move(*centre)
            self.mouse.drag(centre[0] + 220, centre[1] - 40, seconds=1.2, hold=0.1, nudge=False)
            self.rest(0.4)
            self.label("Ctrl + wheel · zoom")
            self.mouse.key("ctrl", True)
            self.mouse.wheel(5, 0.8)
            self.mouse.key("ctrl", False)
            self.rest(1.0)
        self.record("scene13", actions)

    def scene14(self) -> None:
        """C8, Video: sync the chase camera at a lap press; look around in the 360 video."""
        restore = self.keep_info(SESSION)
        self.apply([(170, ["laps"]), (375, ["video", "video_360"])], navigator=False)
        laps, chase, wide = self.cards()
        self.wait_for(lambda: self.gui(lambda: chase.ready and wide.ready), "the videos")
        self.mouse.jump(*self.time_point(laps, 26, 0.5))

        def actions():
            self.label("Left-click · red marker at a lap press")
            self.click_at(self.time_point(laps, 32, 0.5))
            self.rest(0.7)
            self.label("Sync · set data marker")
            self.click_at(self.point(chase.sync_button))
            self.rest(0.5)
            self.click_at(self.point(chase.sync_data_button))
            self.rest(0.7)
            self.label("Seek to the lap in the video")
            # The camera started 4.0 s before the watch: the lap at 32 s is at 36 s in the video.
            seconds = 36.0
            value = self.gui(lambda: chase.timeline.minimum() + seconds / chase.duration
                             * (chase.timeline.maximum() - chase.timeline.minimum()))
            self.click_at(self.slider_point(chase.timeline, value))
            self.rest(1.0)
            self.label("Set video marker")
            self.click_at(self.point(chase.sync_video_button))
            self.rest(1.4)
            centre = self.point(wide.viewport)
            self.label("360 video · drag to look around")
            self.mouse.move(*centre)
            self.mouse.drag(centre[0] - 240, centre[1] + 60, seconds=1.2, hold=0.1, nudge=False)
            self.rest(0.3)
            self.label("Ctrl + drag · roll")
            self.mouse.key("ctrl", True)
            self.mouse.drag(centre[0] + 120, centre[1] + 80, seconds=0.9, hold=0.1, nudge=False)
            self.mouse.key("ctrl", False)
            self.rest(0.3)
            self.label("Ctrl + wheel · field of view")
            self.mouse.key("ctrl", True)
            self.mouse.wheel(-5, 0.9)
            self.mouse.key("ctrl", False)
            self.rest(0.5)
            self.label("Double-click · reset")
            self.mouse.double_click()
            self.rest(1.2)
        try:
            self.record("scene14", actions)
        finally:
            self.gui(lambda: [card.set_playing(False) for card in (chase, wide)])
            restore()

    def scene15(self) -> None:
        """C9, Map and 3D Map: pan, zoom, box zoom, satellite; tilt, rotate, height, map opacity."""
        self.apply([(545, ["gnss", "gnss_3d"])], navigator=False)
        flat, relief = self.cards()
        self.mouse.jump(*self.point(flat))

        def actions():
            centre = self.point(flat.map_view)
            # Satellite first, at the fitted zoom: deeper in, its tiles say "Map data not yet available".
            self.label("Street / Satellite")
            self.click_at(self.point(flat.satellite_radio))
            self.rest(1.6)
            self.label("Drag · pan")
            self.mouse.move(*centre)
            self.mouse.drag(centre[0] + 100, centre[1] + 40, seconds=0.8, hold=0.1, nudge=False)
            self.rest(0.3)
            self.label("Ctrl + wheel · zoom")
            self.mouse.key("ctrl", True)
            self.mouse.wheel(-1, 0.4)
            self.mouse.key("ctrl", False)
            self.rest(0.8)
            self.label("Street / Satellite")
            self.click_at(self.point(flat.street_radio))
            self.rest(1.0)
            self.label("Shift + drag · zoom to a box")
            self.mouse.move(centre[0] - 180, centre[1] - 110)
            self.mouse.key("shift", True)
            self.mouse.drag(centre[0] + 120, centre[1] + 90, seconds=0.8, hold=0.1, nudge=False)
            self.mouse.key("shift", False)
            self.rest(1.2)
            middle = self.point(relief.map_view)
            # A gentle tilt keeps the track in view; a steep one looks at the horizon.
            self.label("Right-drag · tilt and rotate")
            self.mouse.move(*middle)
            self.mouse.drag(middle[0] + 160, middle[1] - 35, seconds=1.2, hold=0.1, button="right", nudge=False)
            self.rest(0.6)
            self.label("Ctrl + wheel · zoom")
            self.mouse.key("ctrl", True)
            self.mouse.wheel(-1, 0.4)
            self.mouse.key("ctrl", False)
            self.rest(0.8)
            self.label("Height · exaggerate the hills")
            slider = relief.altitude_exaggeration
            self.mouse.move(*self.slider_point(slider, self.gui(slider.value)))
            self.mouse.drag(*self.slider_point(slider, 50), seconds=0.7, hold=0.1, nudge=False)
            self.rest(0.9)
            self.label("Map opacity")
            opacity = relief.map_opacity
            self.mouse.move(*self.slider_point(opacity, 100))
            self.mouse.drag(*self.slider_point(opacity, 35), seconds=0.7, hold=0.1, nudge=False)
            self.rest(1.4)
        self.record("scene15", actions)

    def scene16(self) -> None:
        """C10, workspaces: save, close all and reopen exactly as it was."""
        self.apply([(165, ["multi_main"]), (165, ["frequency"]), (215, ["video", "xyz", "gnss"])],
                   navigator=True, markers=(0.35, 0.5))
        workspace = PUBLIC_STAGE / "forest run.umw"
        workspace.parent.mkdir(parents=True, exist_ok=True)
        self.gui(lambda: self.window.write_workspace(workspace))
        self.gui(self.neutral_window)
        self.mouse.jump(*self.point(self.window.plot_workspace))

        def actions():
            self.label("Ctrl + S · save workspace")
            self.mouse.tap("ctrl", "s")
            self.rest(1.2)
            self.label("Close all")
            self.click_at(self.point(self.window.plot_navigator.close_button))
            self.rest(1.2)
            self.label("Open recent workspace")
            self.click_at(self.point(self.window.navigator.controls.recent_workspaces_button))
            self.rest(0.5)
            self.click_at(self.popup_point(workspace.name))
            self.wait_for(lambda: len(self.cards()) >= 5, "the reopened cards")
            self.gui(self.neutral_window)
            self.rest(4.0)
        self.record("scene16", actions)

    # --- Version 3 additions ------------------------------------------------------------------
    def scene17(self) -> None:
        """C2b, values box details: pin them with a click, select the statistics and copy."""
        self.apply([(545, ["accel_line"])], navigator=False, markers=(0.4, 0.6))
        card = self.cards()[0]
        layer = card.series_plot.value_boxes
        boxes = self.wait_for(lambda: self.gui(layer.visible_boxes), "the values boxes")
        self.mouse.jump(*self.time_point(card, 15, 0.8))

        def actions():
            self.label("Click a values box · pin the details")
            self.click_at(self.point(boxes[0]))
            self.rest(0.9)
            details = layer.details
            top_left = self.point(details, QPoint(12, 10))
            bottom_right = self.gui(lambda: QPoint(details.width() - 12, details.height() - 10))
            self.label("Drag · select the statistics")
            self.mouse.move(*top_left)
            self.mouse.drag(*self.point(details, bottom_right), seconds=0.9, hold=0.1, nudge=False)
            self.rest(0.4)
            self.label("Ctrl + C · copy")
            self.mouse.tap("ctrl", "c")
            self.rest(1.4)
        self.record("scene17", actions)

    def scene18(self) -> None:
        """C3b, Card drop selector: hold a series between two cards and pick a card type."""
        activity = ("2026", "November", "03", SESSION, "Activity")
        self.apply([(260, ["multi_main"]), (260, ["accel_line"])], navigator=True, expand=(activity,))
        upper, _lower = self.cards()
        selector = self.window.plot_workspace._data_drop_controller.selector
        self.mouse.jump(*self.point(self.window.plot_workspace))

        def gap_point():  # type: ignore[no-untyped-def]
            def compute():
                rect = upper.geometry()
                glob = upper.parentWidget().mapToGlobal(QPoint(rect.center().x(), rect.bottom() + 4))
                return glob.x() * self.dpr, glob.y() * self.dpr
            return self.gui(compute)

        def actions():
            self.label("Drag a series between two cards")
            self.mouse.move(*self.item_point(*activity, "Speed"))
            self.rest(0.2)
            # The drag by hand: hold between the cards until the selector shows, then drop on a choice.
            self.mouse.press("left")
            self.rest(0.15)
            self.mouse.move(self.mouse.x + 18, self.mouse.y + 6, seconds=0.2)
            self.mouse.move(*gap_point(), seconds=0.9)
            self.wait_for(lambda: self.gui(selector.isVisible), "the Card drop selector", timeout=10)
            self.label("Card drop selector · pick a card type")
            self.rest(0.8)

            def choice():
                options = list(selector.options)
                index = options.index("interval") if "interval" in options else len(options) - 1
                label = selector.labels[index]
                glob = label.mapToGlobal(label.rect().center())
                return glob.x() * self.dpr, glob.y() * self.dpr
            self.mouse.move(*self.gui(choice), seconds=0.6)
            self.rest(0.6)
            self.mouse.release("left")
            self.rest(1.8)
        self.record("scene18", actions)

    def scene19(self) -> None:
        """C4b, Plot navigator bars: drag one sideways (offset), up (reorder), trim an end, reset."""
        self.apply([(170, ["multi_main"]), (170, ["accel_line"]), (170, ["watch_speed"])], navigator=False)
        canvas = self.window.plot_navigator.canvas
        self.mouse.jump(*self.point(self.window.plot_workspace))

        def bar(index: int, where: str = "middle") -> tuple[float, float]:
            def compute():
                entry = self.window.plot_navigator.entries[index]
                rect = canvas._fill_rect(index, entry)
                x = {"middle": rect.center().x(), "left": rect.left() + 1, "right": rect.right() - 1}[where]
                glob = canvas.mapToGlobal(QPoint(round(x), round(rect.center().y())))
                return glob.x() * self.dpr, glob.y() * self.dpr
            return self.gui(compute)

        def actions():
            self.label("Drag a bar sideways · time offset")
            start = bar(2)
            self.mouse.move(*start)
            self.mouse.drag(start[0] + 90, start[1], seconds=0.9, hold=0.2, nudge=False)
            self.rest(0.6)
            self.label("Drag up · reorder the cards")
            start = bar(2)
            self.mouse.move(start[0] - 120, start[1])
            self.mouse.drag(start[0] - 120, bar(0)[1], seconds=0.8, hold=0.3, nudge=False)
            self.rest(0.8)
            self.label("Drag its end · trim the range")
            end = bar(1, "right")
            self.mouse.move(*end)
            self.mouse.drag(end[0] - 260, end[1], seconds=0.8, hold=0.2, nudge=False)
            self.rest(0.7)
            self.label("Right-click · reset")
            self.click_at(bar(1), "right")
            self.rest(0.4)
            self.click_at(self.popup_point("Reset selection"))
            self.rest(1.2)
        self.record("scene19", actions)

    def scene20(self) -> None:
        """C9b, Create plugin: the binary example and its C header become an importer plugin.
        A real run with the AI panel's provider; the edit shortens the wait."""
        plugin_id = "demo_motion_logger"
        plugin_folder = ROOT / "plugin" / plugin_id
        made = STAGE / plugin_id  # Create plugin's examples folder in the data folder.
        for folder in (plugin_folder, made):
            remove_tree(folder)
        drop = self.output / "drop_plugin"
        drop.mkdir(parents=True, exist_ok=True)
        for path in (ROOT / "tests" / "AI_demodata" / "binary" / "c_header").iterdir():
            shutil.copy2(path, drop / path.name)
        self.navigator_state = {"folder": str(STAGE), "group_by": GROUPING_NONE, "filter_key": "", "filter_value": ""}
        self.apply([], navigator=True)
        panel = self.window.ai_panel
        self.gui(lambda: panel.set_expanded(True))
        time.sleep(1.5)
        explorer = ExplorerWindow(drop)
        try:
            explorer.place(-390, 150, 1100, 480)
            left, top, _right, _bottom = explorer.rect()
            self.mouse.jump(left + 760, top + 330)

            def actions():
                self.label("Drop example files and a spec")
                self.click_at((left + 610, top + 272))
                self.mouse.tap("ctrl", "a")
                self.rest(0.4)
                self.mouse.drag(*self.point(panel.plugin_zone), seconds=1.0, hold=0.6)
                self.mouse._log("mark", name="dropped")
                # Create plugin copies the files only when the request is sent: keep the drive until then.
                explorer.close_window()
                self.rest(0.6)
                self.label("Ask for an importer")
                self.click_at(self.point(panel.input))
                self.mouse.type("Write an importer for these logger files.", 0.03)
                self.click_at(self.point(panel.send_button))
                self.label("Plugin ID")
                self.wait_for(lambda: self.gui(lambda: QApplication.activeModalWidget() is not None), "the plugin ID")
                self.rest(0.5)
                self.mouse.tap("ctrl", "a")
                self.mouse.type(plugin_id, 0.04)
                self.rest(0.3)
                self.mouse.tap("enter")
                self.mouse._log("mark", name="sent")
                self.wait_for(lambda: self.gui(lambda: self.window.plugin_chat_job is not None), "Create plugin to start")
                self.mouse.move(*self.point(panel), seconds=0.8)
                self.wait_for(lambda: self.gui(lambda: self.window.plugin_chat_job is None
                                               and self.window.plugin_examples_job is None), "the AI", timeout=1500)
                # The examples import starts just after the AI's answer; wait for its H5 files too.
                self.wait_for(lambda: any(made.glob("*.h5")) and self.gui(lambda: self.window.plugin_examples_job is None),
                              "the examples' H5 files", timeout=180)
                self.mouse._log("mark", name="ai_done")
                self.say(f"Create plugin made plugin/{plugin_id}: {sorted(p.name for p in plugin_folder.glob('*'))}")
                path = self.wait_for(lambda: self.tree_path(made.name), "the examples folder", timeout=30)
                self.rest(0.8)
                item = self.tree_item(*path)
                if not self.gui(item.isExpanded):
                    self.mouse.move(*self.item_point(*path))
                    self.rest(0.15)
                    self.mouse.double_click()
                    self.rest(0.5)
                series = self.wait_for(lambda: self.first_series(path), "a series in the examples folder")
                for depth in range(len(path) + 1, len(series)):
                    self.mouse.move(*self.item_point(*series[:depth]))
                    self.rest(0.15)
                    self.mouse.double_click()
                    self.rest(0.4)
                self.mouse.move(*self.item_point(*series))
                self.rest(0.2)
                self.mouse.double_click()
                self.wait_for(lambda: self.cards(), "the new card")
                self.mouse._log("mark", name="plotted")
                self.rest(0.5)
                self.mouse.move(*self.point(self.window.plot_workspace), seconds=0.8)
                self.rest(2.0)
            self.record("scene20", actions)
        finally:
            explorer.close()
            # The demo plugin is this take's own output: it must not stay installed.
            remove_tree(plugin_folder)
            self.navigator_state = {"folder": str(STAGE), "group_by": "Year/Month/Day",
                                    "filter_key": "dataset", "filter_value": shots.DEMO_DATASET}

    def scene21(self) -> None:
        """C11, Help and Settings: a card's mouse actions, then the Settings dialog."""
        self.apply([(545, ["multi_main"])], navigator=True)
        card = self.cards()[0]
        self.mouse.jump(*self.point(card))

        def actions():
            self.label("? · every mouse action")
            self.click_at(self.point(card.help_button))
            self.rest(2.4)
            self.label("Settings")
            button = self.window.plot_navigator.settings_button

            def dialog_shown() -> bool:
                return self.gui(lambda: getattr(self.window, "settings_dialog", None) is not None
                                and self.window.settings_dialog.isVisible())
            self.click_at(self.point(button))
            try:
                self.wait_for(dialog_shown, "Settings", timeout=2.5)
            except TimeoutError:  # The first click only closed the Help popup.
                self.click_at(self.point(button))
                self.wait_for(dialog_shown, "Settings", timeout=10)
            self.rest(2.6)
            self.mouse.tap("escape")
            self.rest(0.8)
        self.record("scene21", actions)

    def video_card(self):  # type: ignore[no-untyped-def]
        return next(card for card in self.cards() if hasattr(card, "toggle_playback"))

    SCENES = {number: f"scene{number}" for number in range(1, 22)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenes", nargs="+", type=int, default=[1, 2, 3, 4, 5])
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "demo_video")
    arguments = parser.parse_args()
    if not (ROOT / "demodata" / "demo.umw").is_file():
        print("No demo data: run tools/create_demo_dataset.py first.", file=sys.stderr)
        return 1
    arguments.output.mkdir(parents=True, exist_ok=True)

    settings_folder = Path(tempfile.mkdtemp(prefix="lodgazilla-video-"))
    settings_path = settings_folder / "app_settings.json"
    settings = json.loads((ROOT / "app_settings.json").read_text(encoding="utf-8"))
    settings.setdefault("appearance", {})["theme"] = "dark"
    settings.setdefault("lodgazilla", {})["offer_demo_data"] = False
    settings["lodgazilla"].update(link_markers=True, link_plot_x_axes=True, time_axis_display="bottom")
    # Scene 6 sends a synthetic test file to Claude; the user agreed, so the consent question is skipped.
    settings.setdefault("ai", {})["magic_drop_consent"] = True
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    shots.configure_graphics()
    app = QApplication(sys.argv)
    shots.configure_application(app, "dark")
    from src.app.lodgazilla import LODGaZillaWindow

    stage_demo_data()
    window = LODGaZillaWindow(STAGE, settings_path=settings_path)
    dpr = app.primaryScreen().devicePixelRatio()
    window.resize(round(WIDTH / dpr), round(HEIGHT / dpr))
    window.move(40, 40)
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    window.show()
    window.raise_()
    window.activateWindow()
    window.start_loading_cards()

    director = Director(app, window, arguments.output)
    director.records["heart_multi"] = {"type": "multi", "filmstrips": [], "series": [
        {"path": str(director.watch), "name": "Heart rate/Heart rate"}]}
    # The Event/state card opens on a range under a second, and linked time axes pull every card to it.
    director.records["accel_line"] = {"type": "line", "series": [
        {"path": str(director.watch), "name": f"Accelerometer/{axis}"} for axis in "XYZ"]}
    # The closer look's cards.
    foot_pod = director.watch.parent / f"footpod_{SESSION}.h5"
    records = director.records
    records["watch_speed"] = {"type": "line", "series": [{"path": str(director.watch), "name": "Activity/Speed"}]}
    records["pod_speed"] = {"type": "line", "series": [{"path": str(foot_pod), "name": "Foot pod/Speed"}]}
    records["intervals"] = {"type": "interval", "series": [
        {"path": str(director.watch), "name": name} for name in ("GNSS/Latitude", "Heart rate/PPG")]}
    records["events"] = {"type": "event", "series": [
        {"path": str(director.watch), "name": name} for name in ("Activity/Phase", "GNSS/Fix", "Buttons/Lap")]}
    records["accel_frequency"] = {"type": "frequency", "series": [
        {"path": str(director.watch), "name": "Accelerometer/Z"}]}
    records["xyz_new"] = {key: value for key, value in copy.deepcopy(records["xyz"]).items() if key != "sample_limit"}
    records["laps"] = director.series_record("multi", ["Buttons/Lap", "Activity/Speed"])
    videos = [card for card in director.workspace_cards if card["type"] == "video"]
    records["video"] = next(card for card in videos if card["path"].endswith(".mp4"))
    records["video_360"] = next(card for card in videos if card["path"].endswith(".insv"))
    result = {"code": 0}

    def run() -> None:
        try:
            time.sleep(8)
            for number in arguments.scenes:
                getattr(director, Director.SCENES[number])()
        except BaseException:  # noqa: BLE001
            director.say(traceback.format_exc())
            result["code"] = 1
        finally:
            director.gui(window.close)
            director.gui(app.quit)

    threading.Thread(target=run, daemon=True).start()
    app.exec()
    shutil.rmtree(settings_folder, ignore_errors=True)
    return result["code"]


if __name__ == "__main__":
    raise SystemExit(main())
