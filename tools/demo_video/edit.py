"""Edit the recorded scenes into the demo video (see docs/VIDEO_DEMO.md).

``python tools/demo_video/edit.py [--input build/demo_video] [--voice VOICE.wav]``

Reads each scene's raw recording, frame times and event log from ``record.py`` and writes:

- ``lodgazilla_demo.mp4``: subtitles burned in, for players that start muted.
- ``lodgazilla_demo_clean.mp4``: the same without burned-in subtitles, for sites that
  use the ``.srt`` file.
- ``lodgazilla_demo.srt``: the subtitles.
- ``narration.txt``: the narration lines, for the final voice.

Music is generated here and ducked under the narration, unless ``--music`` gives a chosen
track or ``--no-music`` leaves it out.

The cursor, its halo, click rings and the Ctrl badge are drawn from the event log, the
camera zooms and pans to what the narration names, and the Magic drop scene's AI wait is
shortened and labelled as sped up. The voice is a Windows Zira draft made here, unless
``--voice`` gives the final narration track (WAV, placed at 0 s).
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import av  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter,  # noqa: E402
                           QPen, QPolygonF, QRadialGradient)

ROOT = Path(__file__).resolve().parents[2]
WIDTH, HEIGHT, FPS = 1920, 1080, 25
AUDIO_RATE = 48000
RED, GREEN = QColor(239, 68, 68), QColor(34, 197, 94)  # The first and second markers' colours.
REPOSITORY = "github.com/RikuRaitis/LODGaZilla"

# The opening card: the logo and the tagline, before the overview.
TAGLINE = "Every file format, billions of points, one timeline."
OPEN_SECONDS = 4.2
OPEN_LINE = "LODGaZilla. " + TAGLINE
# The overview's narration: (start, end, text) in seconds from the overview's start; see docs/VIDEO_DEMO.md.
OVERVIEW = [
    # Scene 1 has no line of its own: the opening's tagline runs on over it.
    (6.5, 14.5, "Point it at a folder, and your recordings appear by date. Double-click any series to plot it."),
    (15.5, 25.5, "Drag data into cards to compare it side by side. Place markers to read exact values, "
                 "and select a range for its statistics."),
    (27.0, 35.0, "Attach a video, and it plays in sync with your data and the map."),
    (37.5, 44.5, "Zoom from hours of data down to single samples, without waiting."),
    (46.0, 54.5, "Got a file in a new format? Drop it in, and the AI writes the importer for you."),
]
OVERVIEW_END = 55.5
# A closer look: its title, then (scene, narration) for shots C1-C10.
TITLE = ("A closer look at the cards", "A closer look.")
TITLE_SECONDS = 1.8
CHAPTER = [
    ("scene7", "Sample loss and clipping stand out at a glance. Add your own metadata, then group and filter by it."),
    ("scene8", "Line cards shade sample loss and clipping. Pan, zoom, and switch to points."),
    ("scene17", "Click a values box to pin its details, then copy the numbers."),
    ("scene9", "Videos join as filmstrips, and each series gets its own lane to move and scale."),
    ("scene18", "Hold a series between cards, and pick the card type it fits."),
    ("scene10", "Clocks disagree? Shift a series until it lines up."),
    ("scene19", "Drag a card's bar to shift, reorder or trim it."),
    ("scene11", "Spot gaps and jitter, and follow phases and events."),
    ("scene12", "Tune the spectrogram's window and colour range, and zoom in for detail."),
    ("scene13", "X-Y and 3D plots draw every Nth sample; raise the limit for more detail."),
    ("scene14", "Sync a video at a lap press. With 360 video, look around, roll and zoom."),
    ("scene15", "Maps pan, zoom and switch to satellite. The 3D Map tilts and turns, and exaggerates the hills."),
    ("scene20", "Need it again? Create plugin turns example files and a spec into a reusable importer."),
    ("scene16", "Save it all as a workspace, and it reopens exactly as you left it."),
    ("scene21", "Each card's help lists its mouse actions, and Settings holds the rest."),
]
# Create plugin's AI work, shortened to this many seconds and labelled as such.
PLUGIN_LAPSE = 2.0
END_LINE = "LODGaZilla. Free, open source, and ready for your data."
END_SECONDS = 5.6
# The chapter's footage: pauses between inputs longer than IDLE_CAP are cut to it, and it plays
# CHAPTER_SPEED times faster, so the video stays compact and under 3 minutes (docs/VIDEO_DEMO.md).
IDLE_CAP = 0.7
CHAPTER_SPEED = 1.75
WORDS_PER_SECOND = 2.3


@dataclass
class Shot:
    """A scene's stretch in the final video: ``pieces`` of (source start, source end, output seconds)
    in the recording's own seconds from its start, and camera keyframes (output seconds from the
    shot's start, centre x, centre y, zoom) in the recording's pixels."""

    scene: str
    start: float
    pieces: list[tuple[float, float, float]]
    camera: list[tuple[float, float, float, float]] = field(default_factory=lambda: [(0, 960, 540, 1.0)])
    badge: tuple[float, float, str] | None = None  # (from, to, text), output seconds from the shot's start
    kind: str = "scene"  # Or "title" and "end": cards drawn here, without a recording.

    @property
    def duration(self) -> float:
        return sum(piece[2] for piece in self.pieces)


class Recording:
    """A scene's frames, frame times and events, read in time order."""

    def __init__(self, base: Path) -> None:
        self.times = json.loads(base.with_name(base.name + ".frames.json").read_text(encoding="utf-8"))
        log = json.loads(base.with_name(base.name + ".events.json").read_text(encoding="utf-8"))
        self.started = log["started"]
        self.action_start = log["action_start"] - self.started
        self.action_end = log["action_end"] - self.started
        self.rect = log["capture_rect"]
        self.events = [dict(event, t=event["t"] - self.started) for event in log["events"]]
        self.marks = {event["name"]: event["t"] for event in self.events if event["kind"] == "mark"}
        self.relative = [time - self.started for time in self.times]
        self.container = av.open(str(base.with_name(base.name + ".h264")), format="h264")
        self.frames = self.container.decode(video=0)
        self.index = -1
        self.image: QImage | None = None
        self.moves = [event for event in self.events if event["kind"] in ("move", "jump", "start")]
        self.move_times = [event["t"] for event in self.moves]

    def frame_at(self, seconds: float) -> QImage:
        """The last frame recorded at or before ``seconds`` (only moving forward)."""
        wanted = max(0, bisect.bisect_right(self.relative, seconds) - 1)
        while self.index < wanted:
            frame = next(self.frames)
            self.index += 1
            if self.index == wanted:
                array = np.ascontiguousarray(frame.to_ndarray(format="rgb24"))
                self.image = QImage(array.data, array.shape[1], array.shape[0], array.strides[0],
                                    QImage.Format.Format_RGB888).copy()
        assert self.image is not None
        return self.image

    def cursor(self, seconds: float):  # type: ignore[no-untyped-def]
        """The cursor's position in the recording's pixels, and when it last moved."""
        index = bisect.bisect_right(self.move_times, seconds) - 1
        if index < 0:
            return None, -1e9
        event = self.moves[index]
        x, y = event["x"] - self.rect[0], event["y"] - self.rect[1]
        moved = event["t"] if event["kind"] != "start" else -1e9
        # Idle time counts from the last real movement, not from a jump between scenes.
        while index > 0 and self.moves[index]["kind"] == "move" and \
                self.moves[index - 1]["x"] == event["x"] and self.moves[index - 1]["y"] == event["y"]:
            index -= 1
            moved = self.moves[index]["t"]
        return (x, y), moved

    def held(self, seconds: float, kind_down: str, kind_up: str, key: str, value: str) -> bool:
        state = False
        for event in self.events:
            if event["t"] > seconds:
                break
            if event["kind"] == kind_down and event.get(key) == value:
                state = True
            elif event["kind"] == kind_up and event.get(key) == value:
                state = False
        return state

    def label(self, seconds: float) -> str | None:
        """The action label in effect: the last one logged, until the next or for 4 s of recording
        (so a label doesn't linger over a shortened wait)."""
        labels = [event for event in self.events if event["kind"] == "label" and event["t"] <= seconds]
        return labels[-1]["text"] if labels and seconds - labels[-1]["t"] < 4.0 else None

    def clicks(self, seconds: float, within: float = 0.5) -> list[tuple[float, float, float, str]]:
        """Presses in the last ``within`` seconds: (age, x, y, button)."""
        return [(seconds - event["t"], event["x"] - self.rect[0], event["y"] - self.rect[1], event["button"])
                for event in self.events if event["kind"] == "down" and 0 <= seconds - event["t"] <= within]


def ease(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3 - 2 * value)


def camera_at(keys: list[tuple[float, float, float, float]], seconds: float) -> tuple[float, float, float]:
    """Centre and zoom between keyframes, eased; a zoom never shows outside the recording."""
    if seconds <= keys[0][0]:
        _t, x, y, zoom = keys[0]
    elif seconds >= keys[-1][0]:
        _t, x, y, zoom = keys[-1]
    else:
        index = max(i for i, key in enumerate(keys) if key[0] <= seconds)
        (t0, x0, y0, z0), (t1, x1, y1, z1) = keys[index], keys[index + 1]
        f = ease((seconds - t0) / (t1 - t0))
        x, y, zoom = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, z0 + (z1 - z0) * f
    half_w, half_h = WIDTH / zoom / 2, HEIGHT / zoom / 2
    return min(max(x, half_w), WIDTH - half_w), min(max(y, half_h), HEIGHT - half_h), zoom


# --- Drawing -------------------------------------------------------------------------------
ARROW = [(0, 0), (0, 17), (4, 13), (7, 20), (10, 19), (7, 12.5), (12.5, 12.5)]


def draw_cursor(painter: QPainter, x: float, y: float, scale: float, idle: float, pressed: str | None) -> None:
    halo = max(0.0, min(1.0, 1 - (idle - 2.0) / 0.6)) if not pressed else 1.0
    if halo > 0:
        radius = 26 * scale
        gradient = QRadialGradient(QPointF(x, y), radius)
        colour = QColor(RED if pressed == "left" else GREEN if pressed == "right" else QColor(255, 236, 140))
        colour.setAlphaF((0.55 if pressed else 0.38) * halo)
        gradient.setColorAt(0.0, colour)
        edge = QColor(colour)
        edge.setAlphaF(0.0)
        gradient.setColorAt(1.0, edge)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QPointF(x, y), radius, radius)
    size = 1.45 * scale
    polygon = QPolygonF([QPointF(x + px * size, y + py * size) for px, py in ARROW])
    painter.setPen(QPen(QColor(0, 0, 0), 1.6 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(QColor(255, 255, 255))
    painter.drawPolygon(polygon)


def draw_click(painter: QPainter, x: float, y: float, age: float, button: str, scale: float) -> None:
    progress = age / 0.5
    colour = QColor(RED if button == "left" else GREEN)
    colour.setAlphaF(0.9 * (1 - progress))
    painter.setPen(QPen(colour, 3.5 * scale))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    radius = (10 + 34 * ease(progress)) * scale
    painter.drawEllipse(QPointF(x, y), radius, radius)


def draw_badge(painter: QPainter, x: float, y: float, text: str, size: int = 26) -> None:
    font = QFont("Segoe UI", size)
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    metrics = QFontMetricsF(font)
    box = QRectF(x, y, metrics.horizontalAdvance(text) + size, metrics.height() + size * 0.45)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(20, 20, 20, 220))
    painter.drawRoundedRect(box, size * 0.35, size * 0.35)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


def split_subtitles(narration, width: int = 42):  # type: ignore[no-untyped-def]
    """Narration lines as subtitles of at most two lines of ``width`` characters, timed by words."""
    cues = []
    for start, end, text in narration:
        sentences, current = [], ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if len(candidate) > 2 * width and current:
                sentences.append(current)
                current = word
            else:
                current = candidate
            if current.endswith((".", "?")) and len(current) > width:
                sentences.append(current)
                current = ""
        if current:
            sentences.append(current)
        words = [len(part.split()) for part in sentences]
        total, cursor = sum(words), start
        for part, count in zip(sentences, words):
            length = (end - start) * count / total
            cues.append((cursor, cursor + max(1.0, length), wrap(part, width)))
            cursor += length
    return cues


def wrap(text: str, width: int) -> list[str]:
    if len(text) <= width:
        return [text]
    words = text.split()
    best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
    return [" ".join(words[:best]), " ".join(words[best:])]


def draw_subtitle(painter: QPainter, lines: list[str], top: bool) -> None:
    font = QFont("Segoe UI", 44)
    font.setPixelSize(44)
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    metrics = QFontMetricsF(font)
    line_height = metrics.height()
    width = max(metrics.horizontalAdvance(line) for line in lines) + 56
    height = line_height * len(lines) + 26
    y = 40 if top else HEIGHT - height - 56
    box = QRectF((WIDTH - width) / 2, y, width, height)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 178))
    painter.drawRoundedRect(box, 14, 14)
    painter.setPen(QColor(255, 255, 255))
    for index, line in enumerate(lines):
        painter.drawText(QRectF(box.left(), box.top() + 13 + index * line_height, box.width(), line_height),
                         Qt.AlignmentFlag.AlignCenter, line)


def end_card(painter: QPainter, seconds: float) -> None:
    painter.fillRect(QRectF(0, 0, WIDTH, HEIGHT), QColor(8, 16, 23))  # The logo's own background.
    fade = ease(seconds / 0.6)
    painter.setOpacity(fade)
    logo = QImage(str(ROOT / "assets" / "lodgazilla" / "LODGaZilla_big_dark.png"))
    if not logo.isNull():
        scaled = logo.scaledToWidth(980, Qt.TransformationMode.SmoothTransformation)
        painter.drawImage(QPointF((WIDTH - scaled.width()) / 2, 300 - scaled.height() / 2), scaled)
    for text, size, y, colour in (("Free and open source", 54, 610, QColor(255, 255, 255)),
                                  (REPOSITORY, 40, 700, QColor(200, 200, 200))):
        font = QFont("Segoe UI", size)
        font.setPixelSize(size)
        painter.setFont(font)
        painter.setPen(colour)
        painter.drawText(QRectF(0, y, WIDTH, size * 1.6), Qt.AlignmentFlag.AlignCenter, text)
    painter.setOpacity(1.0)


def opening_card(painter: QPainter, seconds: float) -> None:
    """From black to the logo and the tagline, and back to black before the overview."""
    painter.fillRect(QRectF(0, 0, WIDTH, HEIGHT), QColor(0, 0, 0))
    opacity = min(ease((seconds - 0.2) / 0.6), ease((OPEN_SECONDS - seconds) / 0.35))
    if opacity <= 0:
        return
    painter.setOpacity(opacity)
    painter.fillRect(QRectF(0, 0, WIDTH, HEIGHT), QColor(8, 16, 23))  # The logo's own background.
    logo = QImage(str(ROOT / "assets" / "lodgazilla" / "LODGaZilla_big_dark.png"))
    if not logo.isNull():
        scaled = logo.scaledToWidth(1100, Qt.TransformationMode.SmoothTransformation)
        painter.drawImage(QPointF((WIDTH - scaled.width()) / 2, 420 - scaled.height() / 2), scaled)
    font = QFont("Segoe UI", 50)
    font.setPixelSize(50)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255))
    painter.setOpacity(opacity * ease((seconds - 0.7) / 0.6))
    painter.drawText(QRectF(0, 640, WIDTH, 90), Qt.AlignmentFlag.AlignCenter, TAGLINE)
    painter.setOpacity(1.0)


def title_card(painter: QPainter, seconds: float) -> None:
    """The chapter title on the logo's background."""
    painter.fillRect(QRectF(0, 0, WIDTH, HEIGHT), QColor(8, 16, 23))
    painter.setOpacity(ease(seconds / 0.4))
    font = QFont("Segoe UI", 72)
    font.setPixelSize(72)
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(QRectF(0, 0, WIDTH, HEIGHT - 120), Qt.AlignmentFlag.AlignCenter, TITLE[0])
    painter.setOpacity(1.0)


def compressed(log: dict, begin: float, end: float, speed: float) -> list[tuple[float, float, float]]:
    """The recording from ``begin`` to ``end`` (seconds from its start) as pieces: pauses between
    inputs over IDLE_CAP are cut to it, and it plays ``speed`` times faster."""
    started = log["started"]
    inputs = sorted(event["t"] - started for event in log["events"]
                    if event["kind"] not in ("mark", "label", "start", "jump") and begin <= event["t"] - started <= end)
    times = [begin, *inputs, end]
    stretches, current = [], begin
    for before, after in zip(times, times[1:]):
        if after - before > IDLE_CAP:
            # Keep the start of the pause, so the result of the last action shows, then cut.
            stretches.append((current, before + IDLE_CAP))
            current = after - 0.1
    stretches.append((current, end))
    return [(start, stop, (stop - start) / speed) for start, stop in stretches if stop > start]


def chapter_pieces(folder: Path, scene: str, narration: str, spoken: float | None = None) -> list[tuple[float, float, float]]:
    """A chapter shot's pieces at CHAPTER_SPEED, but never shorter than its narration line needs."""
    log = json.loads((folder / f"{scene}.events.json").read_text(encoding="utf-8"))
    begin, end = log["action_start"] - log["started"] - 0.3, log["action_end"] - log["started"] + 0.5
    pieces = compressed(log, begin, end, 1.0)
    length = sum(piece[2] for piece in pieces)
    # As long as the line takes to say (measured when known), with a short pause after it.
    needed = (spoken if spoken is not None else len(narration.split()) / WORDS_PER_SECOND) + 0.5
    speed = max(1.0, min(CHAPTER_SPEED, length / needed))
    pieces = [(start, stop, out / speed) for start, stop, out in pieces]
    shortfall = needed - sum(piece[2] for piece in pieces)
    if shortfall > 0:  # Hold the end longer: the recording runs on after the actions.
        start, stop, out = pieces[-1]
        pieces[-1] = (start, stop + shortfall, out + shortfall)
    return pieces


def plugin_shot(folder: Path, at: float) -> Shot:
    """Create plugin: the drop and the request at CHAPTER_SPEED, the AI's work shortened to
    PLUGIN_LAPSE seconds with a badge that says so, then the new plugin's import plotted."""
    log = json.loads((folder / "scene20.events.json").read_text(encoding="utf-8"))
    started = log["started"]
    marks = {event["name"]: event["t"] - started for event in log["events"] if event["kind"] == "mark"}
    begin = log["action_start"] - started - 0.3
    wait_from, resume = marks["sent"] + 0.6, marks["ai_done"] + 0.3
    before = compressed(log, begin, wait_from, CHAPTER_SPEED)
    after = compressed(log, resume, marks.get("plotted", resume + 4) + 2.0, CHAPTER_SPEED)
    lead = sum(piece[2] for piece in before)
    speedup = (resume - wait_from) / PLUGIN_LAPSE
    return Shot("scene20", at, [*before, (wait_from, resume, PLUGIN_LAPSE), *after],
                camera=[(0, 560, 560, 1.35), (lead, 420, 760, 1.5), (lead + PLUGIN_LAPSE, 420, 760, 1.5),
                        (lead + PLUGIN_LAPSE + 1.0, 960, 540, 1.0)],
                badge=(lead, lead + PLUGIN_LAPSE, f"AI at work · shown {speedup:.0f}× faster"))


# --- The edit --------------------------------------------------------------------------------
def timeline(folder: Path, spoken: dict[str, float] | None = None):  # type: ignore[no-untyped-def]
    """Every shot of the video, its narration lines and its length in seconds."""
    plan = [Shot("opening", 0.0, [(0.0, OPEN_SECONDS, OPEN_SECONDS)], kind="opening")]
    narration = [(0.5, OPEN_SECONDS + 2.5, OPEN_LINE)]  # It runs on over the start of scene 1.
    for shot in shots(folder):
        shot.start += OPEN_SECONDS
        plan.append(shot)
    narration += [(start + OPEN_SECONDS, end + OPEN_SECONDS, text) for start, end, text in OVERVIEW]
    at = OPEN_SECONDS + OVERVIEW_END
    plan.append(Shot("title", at, [(0.0, TITLE_SECONDS, TITLE_SECONDS)], kind="title"))
    narration.append((at, at + TITLE_SECONDS, TITLE[1]))
    at += TITLE_SECONDS
    for scene, line in CHAPTER:
        shot = plugin_shot(folder, at) if scene == "scene20" else Shot(scene, at, chapter_pieces(folder, scene, line, (spoken or {}).get(line)))
        if scene == "scene7":  # The Navigator, at the left: closer.
            shot.camera = [(0, 500, 415, 1.3)]
        plan.append(shot)
        narration.append((at + 0.3, at + shot.duration - 0.2, line))
        at += shot.duration
    plan.append(Shot("end", at, [(0.0, END_SECONDS, END_SECONDS)], kind="end"))
    narration.append((at, at + END_SECONDS, END_LINE))
    return plan, narration, at + END_SECONDS


def shots(folder: Path) -> list[Shot]:
    """The overview's scenes: their stretches and camera moves, on the narration's timeline."""
    def head(scene: str, lead: float = 0.2) -> float:
        log = json.loads((folder / f"{scene}.events.json").read_text(encoding="utf-8"))
        return log["action_start"] - log["started"] - lead

    s2, s3, s4 = (head(name) for name in ("scene2", "scene3", "scene4"))
    # Scene 5 starts when Ctrl goes down, so the zoom ends with time to see the single samples.
    five = json.loads((folder / "scene5.events.json").read_text(encoding="utf-8"))
    s5 = next(event["t"] for event in five["events"] if event["kind"] == "key_down") - five["started"] - 0.4
    six = json.loads((folder / "scene6.events.json").read_text(encoding="utf-8"))
    marks = {event["name"]: event["t"] - six["started"] for event in six["events"] if event["kind"] == "mark"}
    s6 = six["action_start"] - six["started"]
    dropped, done = marks["dropped"], marks["ai_done"]
    wait_from = dropped + 0.6
    lapse = 1.8  # The AI's work, shortened to this many seconds and labelled as such.
    resume = done + 0.8  # The AI's last words are on screen; the cursor heads for the new folder.
    drop_part = wait_from - s6
    rest = 10.0 - drop_part - lapse  # The new folder opened and its series plotted.
    speedup = (resume - wait_from) / lapse
    return [
        Shot("scene1", 0.0, [(0.3, 6.3, 6.0)], camera=[(0, 960, 540, 1.10), (6.0, 960, 540, 1.0)]),
        Shot("scene2", 6.0, [(s2, s2 + 9.0, 9.0)],
             camera=[(0, 420, 470, 1.55), (6.6, 420, 470, 1.55), (8.2, 960, 540, 1.0)]),
        Shot("scene3", 15.0, [(s3, s3 + 11.5, 11.5)],
             camera=[(0, 960, 540, 1.0), (5.6, 960, 540, 1.0), (6.6, 1180, 560, 1.3), (10.2, 1180, 560, 1.3),
                     (11.2, 960, 540, 1.0)]),
        Shot("scene4", 26.5, [(s4, s4 + 10.0, 10.0)],
             camera=[(0, 960, 540, 1.0), (2.0, 960, 540, 1.0), (4.0, 1150, 760, 1.25), (10.0, 1150, 760, 1.25)]),
        Shot("scene5", 36.5, [(s5, s5 + 9.0, 9.0)]),
        Shot("scene6", 45.5, [(s6, wait_from, drop_part), (wait_from, resume, lapse), (resume, resume + rest, rest)],
             camera=[(0, 560, 560, 1.35), (drop_part, 420, 760, 1.5), (drop_part + lapse, 420, 760, 1.5),
                     (drop_part + lapse + 1.0, 960, 540, 1.0)],
             badge=(drop_part, drop_part + lapse, f"AI at work · shown {speedup:.0f}× faster")),
    ]


def source_time(shot: Shot, seconds: float) -> float:
    for start, end, length in shot.pieces:
        if seconds <= length or (start, end, length) == shot.pieces[-1]:
            return start + (end - start) * min(1.0, seconds / length)
        seconds -= length
    return shot.pieces[-1][1]


def to_frame(image: QImage, index: int) -> av.VideoFrame:
    array = np.frombuffer(image.constBits(), np.uint8).reshape(HEIGHT, image.bytesPerLine())[:, :WIDTH * 3]
    frame = av.VideoFrame.from_ndarray(array.reshape(HEIGHT, WIDTH, 3).copy(), format="rgb24")
    frame.pts = index
    return frame


def render(folder: Path, outputs: list[tuple[Path, bool]], audio: np.ndarray, cues, plan, total) -> None:  # type: ignore[no-untyped-def]
    """Write each of ``outputs`` (path, burned-in subtitles) in one pass: every frame is decoded
    and painted once, then encoded for each output, with the subtitles drawn on a copy."""
    targets = []
    for output, subtitles in outputs:
        container = av.open(str(output), "w")
        video = container.add_stream("libx264", rate=FPS)
        video.width, video.height, video.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        video.options = {"crf": "18", "preset": "medium"}
        sound = container.add_stream("aac", rate=AUDIO_RATE)
        sound.layout = "mono"
        targets.append((container, video, sound, subtitles))
    canvas = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB888)
    recordings: dict[str, Recording] = {}
    for index in range(round(total * FPS)):
        now = index / FPS
        shot = next((shot for shot in plan if shot.start <= now < shot.start + shot.duration), plan[-1])
        painter = QPainter(canvas)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        if shot.kind == "end":
            end_card(painter, now - shot.start)
        elif shot.kind == "title":
            title_card(painter, now - shot.start)
        elif shot.kind == "opening":
            opening_card(painter, now - shot.start)
        else:
            recording = recordings.setdefault(shot.scene, Recording(folder / shot.scene))
            local = now - shot.start
            moment = source_time(shot, local)
            frame = recording.frame_at(moment)
            cx, cy, zoom = camera_at(shot.camera, local)
            crop = QRectF(cx - WIDTH / zoom / 2, cy - HEIGHT / zoom / 2, WIDTH / zoom, HEIGHT / zoom)
            painter.drawImage(QRectF(0, 0, WIDTH, HEIGHT), frame, crop)

            def place(x: float, y: float) -> tuple[float, float]:
                return (x - crop.left()) * zoom, (y - crop.top()) * zoom

            for age, x, y, button in recording.clicks(moment):
                draw_click(painter, *place(x, y), age, button, zoom ** 0.5)
            position, moved = recording.cursor(moment)
            if position is not None and 0 <= position[0] < WIDTH and 0 <= position[1] < HEIGHT:
                pressed = ("left" if recording.held(moment, "down", "up", "button", "left") else
                           "right" if recording.held(moment, "down", "up", "button", "right") else None)
                x, y = place(*position)
                draw_cursor(painter, x, y, zoom ** 0.5, moment - moved, pressed)
                if recording.held(moment, "key_down", "key_up", "key", "ctrl"):
                    draw_badge(painter, x + 30, y + 30, "Ctrl", 24)
            if shot.badge and shot.badge[0] <= local < shot.badge[1]:
                draw_badge(painter, 40, 40, shot.badge[2], 30)
            label = recording.label(moment)
            if label:
                # Bottom left, above the subtitles: the action happens elsewhere, often top left.
                draw_badge(painter, 40, HEIGHT - 250, label, 30)
        painter.end()
        cue = next((cue for cue in cues if cue[0] <= now < cue[1]), None)
        for container, video, _sound, subtitles in targets:
            image = canvas
            if subtitles and cue is not None:
                image = canvas.copy()
                overlay = QPainter(image)
                overlay.setRenderHints(QPainter.RenderHint.Antialiasing)
                draw_subtitle(overlay, cue[2], top=False)
                overlay.end()
            for packet in video.encode(to_frame(image, index)):
                container.mux(packet)
        if index % 250 == 0:
            print(f"  {now:.0f} s of {total:.0f} s", flush=True)
    samples = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    chunk = 1024
    for container, video, sound, _subtitles in targets:
        for packet in video.encode():
            container.mux(packet)
        for offset in range(0, len(samples), chunk):
            piece = samples[offset:offset + chunk]
            frame = av.AudioFrame.from_ndarray(piece.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = AUDIO_RATE
            frame.pts = offset
            frame.time_base = Fraction(1, AUDIO_RATE)
            for packet in sound.encode(frame):
                container.mux(packet)
        for packet in sound.encode():
            container.mux(packet)
        container.close()


def zira_lines(texts) -> dict[str, np.ndarray]:  # type: ignore[no-untyped-def]
    """Each narration line spoken by Windows' Zira voice (the draft voice), by its text."""
    spoken = {}
    with tempfile.TemporaryDirectory() as folder:
        for index, text in enumerate(dict.fromkeys(texts)):
            path = Path(folder) / f"line{index}.wav"
            script = ("Add-Type -AssemblyName System.Speech; "
                      "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                      "$s.SelectVoice('Microsoft Zira Desktop'); "
                      f"$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo({AUDIO_RATE}, "
                      "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
                      "[System.Speech.AudioFormat.AudioChannel]::Mono); "
                      f"$s.SetOutputToWaveFile('{path}', $f); $s.Speak($env:LINE); $s.Dispose()")
            subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True,
                           env={**os.environ, "LINE": text.replace("LODGaZilla", "Lodge a zilla")})
            with wave.open(str(path)) as speech:
                spoken[text] = np.frombuffer(speech.readframes(speech.getnframes()), np.int16).astype(np.float32) / 32768
    return spoken


def zira_track(lines, total: float, spoken: dict[str, np.ndarray]) -> np.ndarray:  # type: ignore[no-untyped-def]
    """The draft narration: each spoken line placed at its start time; overlaps are reported."""
    track = np.zeros(int(total * AUDIO_RATE), np.float32)
    starts = [start for start, _end, _text in lines[1:]] + [total]
    for index, ((start, _end, text), following) in enumerate(zip(lines, starts)):
        data = spoken[text]
        seconds = len(data) / AUDIO_RATE
        note = "" if start + seconds <= following else f"  (overlaps the next line by {start + seconds - following:.1f} s)"
        print(f"  line {index + 1}: {seconds:.1f} s{note}")
        at = int(start * AUDIO_RATE)
        track[at:at + len(data)] += data[:len(track) - at]
    return track


def read_voice(path: Path, total: float) -> np.ndarray:
    container = av.open(str(path))
    resampler = av.AudioResampler(format="flt", layout="mono", rate=AUDIO_RATE)
    pieces = [resampled.to_ndarray().reshape(-1) for frame in container.decode(audio=0)
              for resampled in resampler.resample(frame)]
    track = np.zeros(int(total * AUDIO_RATE), np.float32)
    data = np.concatenate(pieces)[:len(track)]
    track[:len(data)] = data
    return track


# --- Music -----------------------------------------------------------------------------------
TEMPO = 100  # Beats a minute.
# A minor, F, C, G: two bars each. (root, chord tones) in hertz.
CHORDS = [(110.0, (220.0, 261.63, 329.63)), (87.31, (174.61, 220.0, 261.63)),
          (130.81, (261.63, 329.63, 392.0)), (98.0, (196.0, 246.94, 293.66))]
# The melody, a quarter note a beat over each chord's two bars, in A minor.
A4, B4, C5, D5, E5, F5, G4, G5 = 440.0, 493.88, 523.25, 587.33, 659.25, 698.46, 392.0, 783.99
MELODY = [(A4, C5, E5, D5, C5, B4, C5, E5),   # A minor
          (A4, C5, F5, E5, C5, A4, C5, D5),   # F
          (G4, C5, E5, G5, E5, D5, C5, E5),   # C
          (G4, B4, D5, G5, F5, D5, B4, D5)]   # G


def make_music(total: float, plan: list[Shot]) -> np.ndarray:
    """A short electronic bed, made here so no licence applies: soft pad chords, a bass note, a
    light pulse and an arpeggio. It swells in with the opening card, pulses through the overview,
    accents the chapter title, is lighter through the chapter and resolves on the end card."""
    count = int(total * AUDIO_RATE)
    t = np.arange(count) / AUDIO_RATE
    beat = 60 / TEMPO
    bar = 4 * beat
    chord_index = (t // (2 * bar)).astype(int) % len(CHORDS)
    sections = {shot.kind: (shot.start, shot.start + shot.duration) for shot in plan if shot.kind != "scene"}
    overview_end = sections["title"][0]
    end_start = sections["end"][0]
    # The last chord before the end card resolves to A minor... then C major on the end card.
    roots = np.array([root for root, _tones in CHORDS])[chord_index]
    tones = np.array([chord for _root, chord in CHORDS])[chord_index]
    on_end = t >= end_start
    roots[on_end], tones[on_end] = 130.81, (261.63, 329.63, 392.0)
    rng = np.random.default_rng(7)
    pad = np.zeros(count)
    for voice in range(3):
        for detune in (-0.12, 0.12):
            phase = np.cumsum(2 * np.pi * tones[:, voice] * (1 + detune / 100) / AUDIO_RATE)
            pad += np.sin(phase) + 0.25 * np.sin(2 * phase) + 0.1 * np.sin(3 * phase)
    pad /= 9
    bass = np.sin(np.cumsum(2 * np.pi * roots / AUDIO_RATE)) * 0.5
    # The pulse: a soft kick on beats 1 and 3, a quiet hat on every eighth.
    in_beat = t % beat
    beat_number = (t // beat).astype(int) % 4
    kick = np.where(np.isin(beat_number, (0, 2)), np.sin(2 * np.pi * 55 * in_beat * (1 + 2 * np.exp(-in_beat * 30)))
                    * np.exp(-in_beat * 9), 0.0)
    eighth = t % (beat / 2)
    noise = rng.standard_normal(count)
    hat = np.diff(noise, prepend=0.0) * np.exp(-eighth * 60) * 0.15
    # The arpeggio: chord tones an octave up, on eighths, plucked.
    step = (t // (beat / 2)).astype(int) % 4
    arp_pitch = np.choose(np.minimum(step, 2), [tones[:, 0], tones[:, 1], tones[:, 2]]) * 2
    arp_pitch = np.where(step == 3, tones[:, 1] * 2, arp_pitch)
    arp_phase = np.cumsum(2 * np.pi * arp_pitch / AUDIO_RATE)
    arp = (2 / np.pi) * np.arcsin(np.sin(arp_phase)) * np.exp(-eighth * 7) * 0.35
    # The melody: a quarter-note line over each chord's two bars, a soft lead with a little vibrato.
    in_chord = (t // beat).astype(int) % 8
    melody_index = np.where(on_end, 2, chord_index)
    pitch = np.array(MELODY)[melody_index, in_chord] * (1 + 0.004 * np.sin(2 * np.pi * 5.5 * t))
    lead_phase = np.cumsum(2 * np.pi * pitch / AUDIO_RATE)
    lead = (np.sin(lead_phase) + 0.3 * np.sin(2 * lead_phase) + 0.12 * np.sin(3 * lead_phase)) / 1.4
    lead *= (1 - np.exp(-in_beat * 60)) * (0.35 + 0.65 * np.exp(-in_beat * 3))

    def window(start: float, stop: float, fade: float = 1.0) -> np.ndarray:
        return np.clip(np.minimum((t - start) / fade, (stop - t) / fade), 0, 1)

    # The opening comes in at once, with the full beat, so the logo lands with weight.
    opening = window(0.0, sections["opening"][1] + 0.5, 0.25)
    overview = window(sections["opening"][1] - 0.3, overview_end + 0.4, 0.8)
    chapter = window(sections["title"][1] - 0.2, end_start + 0.3, 0.8)
    end_fade = np.clip((total - t) / 3.0, 0, 1) * (t >= end_start - 0.5)
    pad_level = 0.6 * opening + 0.45 * overview + 0.45 * chapter + 0.7 * end_fade
    pulse_level = 0.85 * opening + 0.55 * overview + 0.5 * chapter + 0.5 * end_fade
    arp_level = 0.55 * opening + 0.5 * overview + 0.4 * chapter + 0.3 * end_fade
    lead_level = 0.6 * opening + 0.4 * overview + 0.35 * chapter + 0.55 * end_fade
    music = (pad * pad_level + bass * 0.6 * (1.3 * opening + overview + chapter + end_fade)
             + kick * 0.5 * pulse_level + hat * pulse_level + arp * arp_level + lead * lead_level)

    def hit(at: float, low: float, bright: tuple[float, ...], weight: float) -> np.ndarray:
        """An impact: a falling low boom, a noise burst and a bright chord stab, from ``at``."""
        since = np.clip(t - at, 0, None)
        after = t >= at
        boom = np.sin(2 * np.pi * low * since * (1 + 3 * np.exp(-since * 20))) * np.exp(-since * 3)
        burst = noise * np.exp(-since * 14) * 0.25
        stab = sum(np.sin(2 * np.pi * tone * t) for tone in bright) / len(bright) * np.exp(-since * 1.4)
        return (boom + burst + stab * 0.8) * after * weight

    # Impacts as the logo appears and as the tagline appears, and on the end card's logo.
    opening_start = sections["opening"][0]
    music += hit(opening_start + 0.25, 45, (220.0, 329.63, 440.0, 523.25), 1.0)
    music += hit(opening_start + 0.9, 55, (440.0, 523.25, 659.25), 0.6)
    music += hit(end_start, 45, (261.63, 329.63, 392.0, 523.25), 0.8)
    # One accent on the chapter title: a bright chord that fades.
    title_start = sections["title"][0]
    since = np.clip(t - title_start, 0, None)
    accent = (np.sin(2 * np.pi * 523.25 * t) + np.sin(2 * np.pi * 659.25 * t) + np.sin(2 * np.pi * 783.99 * t)) / 3
    music += accent * np.exp(-since * 1.6) * (t >= title_start) * 0.45
    music = np.tanh(music * 1.2)  # Soft saturation: the hits stand out without one peak setting the level.
    return (music / max(1e-9, np.max(np.abs(music)))).astype(np.float32)


def duck(music: np.ndarray, narration, level_db: float = -15.0, under_db: float = -8.0,
         card_under_db: float = -4.0) -> np.ndarray:  # type: ignore[no-untyped-def]
    """The music at ``level_db`` (peak), lowered by ``under_db`` while a narration line plays, and
    only by ``card_under_db`` under the opening and end cards' lines (the first and last)."""
    gain = np.ones(len(music))
    last = len(narration) - 1
    for index, (start, end, _text) in enumerate(narration):
        under = card_under_db if index in (0, last) else under_db
        gain[int(max(0, start - 0.15) * AUDIO_RATE):int((end + 0.25) * AUDIO_RATE)] = 10 ** (under / 20)
    # Smooth the gain changes over about 0.3 s.
    # A moving average from a running sum: np.convolve with this long kernel takes minutes.
    width = int(0.3 * AUDIO_RATE)
    padded = np.concatenate([np.full(width // 2, gain[0]), gain, np.full(width - width // 2, gain[-1])])
    sums = np.concatenate([[0.0], np.cumsum(padded)])
    gain = (sums[width:width + len(gain)] - sums[:len(gain)]) / width
    return music * gain * 10 ** (level_db / 20)


def read_music(path: Path, total: float) -> np.ndarray:
    """A chosen track, cut to the video's length (a shorter one ends early), faded in and out, peak-normalised."""
    track = read_voice(path, total)
    t = np.arange(len(track)) / AUDIO_RATE
    fade = np.clip(np.minimum(t / 1.5, (total - t) / 3.0), 0, 1)
    return (track * fade / max(1e-9, np.max(np.abs(track)))).astype(np.float32)


def write_srt(cues, path: Path) -> None:  # type: ignore[no-untyped-def]
    def stamp(seconds: float) -> str:
        millis = round(seconds * 1000)
        return f"{millis // 3600000:02}:{millis // 60000 % 60:02}:{millis // 1000 % 60:02},{millis % 1000:03}"
    blocks = [f"{index}\n{stamp(start)} --> {stamp(end)}\n" + "\n".join(lines)
              for index, (start, end, lines) in enumerate(cues, 1)]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=ROOT / "build" / "demo_video")
    parser.add_argument("--voice", type=Path, help="The final narration (WAV), placed at 0 s.")
    parser.add_argument("--music", type=Path, help="A chosen music track instead of the generated one; "
                                                   "use only a track whose licence allows it.")
    parser.add_argument("--no-music", action="store_true", help="Leave the music out.")
    arguments = parser.parse_args()
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    folder = arguments.input
    texts = [OPEN_LINE, *(text for _s, _e, text in OVERVIEW), TITLE[1], *(line for _scene, line in CHAPTER), END_LINE]
    # The Zira lines time the shots even with a final voice, so the timing (and the
    # subtitles the final voice follows) stays the same.
    spoken = zira_lines(texts)
    plan, narration, total = timeline(folder, {text: len(data) / AUDIO_RATE for text, data in (spoken or {}).items()})
    print(f"Length {total:.1f} s; chapter shots: " + ", ".join(
        f"{shot.scene} {shot.duration:.1f} s" for shot in plan if shot.scene.startswith("scene")
        and int(shot.scene[5:]) >= 7))
    print("Voice:")
    audio = read_voice(arguments.voice, total) if arguments.voice else zira_track(narration, total, spoken)
    if not arguments.no_music:
        print("Music:", arguments.music or "generated")
        music = read_music(arguments.music, total) if arguments.music else make_music(total, plan)
        audio = np.clip(audio + duck(music, narration), -1, 1)
    cues = split_subtitles(narration)
    write_srt(cues, folder / "lodgazilla_demo.srt")
    # The script for the final voice (Clipchamp): one line per paragraph.
    lines = [text for _start, _end, text in narration]
    (folder / "narration.txt").write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    print("Rendering lodgazilla_demo.mp4 and lodgazilla_demo_clean.mp4")
    render(folder, [(folder / "lodgazilla_demo.mp4", True), (folder / "lodgazilla_demo_clean.mp4", False)],
           audio, cues, plan, total)
    print("Done:", folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
