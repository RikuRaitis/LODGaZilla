"""Still frames for Multi-series filmstrip lanes, decoded off the GUI thread.

Each frame is the nearest keyframe at or before the requested time: seeking to
keyframes is fast enough for a lane of frames, but a frame can be up to one
keyframe interval early.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import logging
from pathlib import Path
import threading

import av
import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

LOG = logging.getLogger(__name__)
# Frames kept for all lanes; panning back over recent frames needs no decoding.
CACHE_SIZE = 300
# Requested times are rounded so panning over an aligned grid hits the cache.
TIME_RESOLUTION = 0.001


_NO_FRAME = np.empty(0, dtype=np.uint8)


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    aspect: float


def frame_key(path: Path, seconds: float, height: int) -> tuple:
    return str(path), round(seconds / TIME_RESOLUTION), int(height)


def read_frames(path: Path, seconds: list[float], height: int, cancelled: threading.Event):
    """Return the video's duration and aspect, and an RGB frame (rows top down) per time."""
    frames = {}
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        time_base = float(stream.time_base)
        duration = (float(stream.duration) * time_base if stream.duration
                    else float(container.duration or 0) / av.time_base)
        aspect = (stream.codec_context.width / stream.codec_context.height
                  if stream.codec_context.height else 16 / 9)
        width = max(2, int(round(height * aspect / 2)) * 2)
        start = float(stream.start_time or 0) * time_base
        for second in seconds:
            if cancelled.is_set():
                break
            if duration and not 0.0 <= second <= duration:
                continue
            try:
                container.seek(int((start + second) / time_base), stream=stream, backward=True, any_frame=False)
                frame = next(container.decode(stream), None)
            except (av.error.FFmpegError, StopIteration, ValueError) as error:
                LOG.debug("No frame at %.3f s in %s: %s", second, path, error)
                continue
            if frame is not None:
                frames[second] = frame.reformat(width=width, height=height, format="rgb24").to_ndarray()
    return VideoInfo(duration, aspect), frames


class _FrameSignals(QObject):
    loaded = Signal(object, int, object, object, object)
    failed = Signal(object, str)


class _FrameTask(QRunnable):
    def __init__(self, path, seconds, height, cancelled):  # type: ignore[no-untyped-def]
        super().__init__()
        self.path, self.seconds, self.height, self.cancelled = path, seconds, height, cancelled
        self.signals = _FrameSignals()

    def run(self) -> None:
        try:
            info, frames = read_frames(self.path, self.seconds, self.height, self.cancelled)
        except Exception as error:  # noqa: BLE001 - report any decoder failure to the lane
            self.signals.failed.emit(self.path, str(error) or type(error).__name__)
            return
        if not self.cancelled.is_set():
            self.signals.loaded.emit(self.path, self.height, info, frames, self.seconds)


class FilmstripFrames(QObject):
    """Decodes and caches filmstrip frames.

    One request per video runs at a time; while it runs, only the newest further
    request waits. Finished frames always reach the cache, so a superseded
    request still saves work for the next view.
    """

    loaded = Signal(object)
    info_loaded = Signal(object, object)
    failed = Signal(object, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.cache: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self.info: dict[str, VideoInfo] = {}
        self._running: dict[str, _FrameTask] = {}
        self._waiting: dict[str, tuple[Path, list[float], int]] = {}
        self._shutdown = threading.Event()
        # Videos whose frames could not be read at all.
        self.failed_paths: set[str] = set()

    def frame(self, path: Path, seconds: float, height: int) -> np.ndarray | None:
        key = frame_key(path, seconds, height)
        image = self.cache.get(key)
        if image is None:
            return None
        self.cache.move_to_end(key)
        # An empty entry records a time without a frame, so it is not requested again.
        return image if image.size else None

    def known(self, path: Path, seconds: float, height: int) -> bool:
        """Whether this time has been decoded, with or without a frame."""
        return frame_key(path, seconds, height) in self.cache

    def request(self, path: Path, seconds: list[float], height: int) -> None:
        """Decode the frames not cached yet, after any request already running for this video."""
        if self._shutdown.is_set():
            return
        if str(path) in self._running:
            self._waiting[str(path)] = (path, list(seconds), height)
            return
        missing = [second for second in seconds if frame_key(path, second, height) not in self.cache]
        if not missing and str(path) in self.info:
            return
        task = _FrameTask(path, missing, height, self._shutdown)
        task.setAutoDelete(False)
        task.signals.loaded.connect(self._loaded)
        task.signals.failed.connect(self._failed)
        self._running[str(path)] = task
        self.pool.start(task)

    def _loaded(self, path, height, info, frames, requested) -> None:  # type: ignore[no-untyped-def]
        self.failed_paths.discard(str(path))
        for second in requested:
            key = frame_key(path, second, height)
            self.cache[key] = frames.get(second, _NO_FRAME)
            self.cache.move_to_end(key)
        while len(self.cache) > CACHE_SIZE:
            self.cache.popitem(last=False)
        self._finished(path)
        if str(path) not in self.info:
            self.info[str(path)] = info
            self.info_loaded.emit(path, info)
        self.loaded.emit(path)

    def _failed(self, path, error: str) -> None:  # type: ignore[no-untyped-def]
        self.failed_paths.add(str(path))
        self._finished(path)
        self.failed.emit(path, error)

    def _finished(self, path) -> None:  # type: ignore[no-untyped-def]
        self._running.pop(str(path), None)
        waiting = self._waiting.pop(str(path), None)
        if waiting is not None:
            self.request(*waiting)

    def shutdown(self) -> None:
        self._shutdown.set()
        self._waiting.clear()
        self.pool.waitForDone(3000)
