"""Render frames in worker processes and encode them, with the soundtrack, using PyAV.

The flat video is an ordinary H.264 MP4. The 360 video is an Insta360-style
INSV: one MP4 container with two square fisheye H.264 tracks (back lens first,
front lens second) and the shared soundtrack.
"""
from __future__ import annotations

from collections import deque
from fractions import Fraction
import multiprocessing
import os
from pathlib import Path
import time
from typing import Callable

import numpy as np

from tools.demo_data.course import CourseSpec, make_scenery, simulate
from tools.demo_data.sound import SAMPLE_RATE

_RENDERER = None


def _init_worker(kind: str, spec: CourseSpec, scenery_seed: int, options: dict) -> None:  # type: ignore[type-arg]
    global _RENDERER
    from tools.demo_data.render import FlatRenderer, InsvRenderer

    track = simulate(spec)
    scenery = make_scenery(track, scenery_seed)
    if kind == "flat":
        _RENDERER = FlatRenderer(track, scenery, options["width"], options["height"], options["log_start"])
    else:
        _RENDERER = InsvRenderer(track, scenery, options["lens_size"])


def _render(t: float):  # type: ignore[no-untyped-def]
    return _RENDERER.frame(t)  # type: ignore[union-attr]


def _video_stream(container, size: tuple[int, int], fps: int, encoder: str):  # type: ignore[no-untyped-def]
    stream = container.add_stream(encoder, rate=fps)
    stream.width, stream.height = size
    stream.pix_fmt = "yuv420p"
    if encoder == "libx264":
        stream.options = {"preset": "veryfast", "crf": "20"}
    return stream


def encode(path: Path, kind: str, spec: CourseSpec, scenery_seed: int, *, start: float, end: float, fps: int,
           audio: np.ndarray, options: dict, workers: int, progress: Callable[[str], None],  # type: ignore[type-arg]
           frames: Callable[[float], None] | None = None) -> Path:
    """Render log times start..end at ``fps`` and write the video atomically.

    ``progress`` gets a status line every five seconds of video, ``frames`` the share done every second.
    """
    import av

    times = start + np.arange(int(round((end - start) * fps))) / fps
    partial = path.with_name(f".{path.name}.partial")
    container = av.open(str(partial), "w", format="mp4")
    try:
        if kind == "flat":
            streams = [_video_stream(container, (options["width"], options["height"]), fps, "libx264")]
        else:
            # Two square lens tracks: the player's 360 view needs exactly this layout.
            streams = [_video_stream(container, (options["lens_size"], options["lens_size"]), fps, "libx264")
                       for _ in range(2)]
        sound = container.add_stream("aac", rate=SAMPLE_RATE)
        sound.layout = "stereo"
        sound.bit_rate = 192_000
        context = multiprocessing.get_context("spawn")
        started = time.monotonic()
        with context.Pool(workers, initializer=_init_worker, initargs=(kind, spec, scenery_seed, options)) as pool:
            pending: deque = deque()
            submitted = 0
            for number in range(len(times)):
                while submitted < len(times) and len(pending) < workers * 2:
                    pending.append(pool.apply_async(_render, (float(times[submitted]),)))
                    submitted += 1
                result = pending.popleft().get()
                images = [result] if kind == "flat" else list(result)
                for stream, image in zip(streams, images):
                    frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(image), format="rgb24")
                    frame.pts = number
                    frame.time_base = Fraction(1, fps)
                    for packet in stream.encode(frame):
                        container.mux(packet)
                if frames is not None and number % fps == 0:
                    frames(number / len(times))
                if number % (fps * 5) == 0 and number:
                    elapsed = time.monotonic() - started
                    remaining = elapsed / number * (len(times) - number)
                    progress(f"  {path.name}: {number}/{len(times)} frames, about {remaining / 60:.0f} min left")
        for stream in streams:
            for packet in stream.encode():
                container.mux(packet)
        block = 1024
        for index, offset in enumerate(range(0, audio.shape[1], block)):
            chunk = np.ascontiguousarray(audio[:, offset:offset + block])
            if chunk.shape[1] < block:
                chunk = np.pad(chunk, ((0, 0), (0, block - chunk.shape[1])))
            frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
            frame.sample_rate = SAMPLE_RATE
            frame.pts = index * block
            frame.time_base = Fraction(1, SAMPLE_RATE)
            for packet in sound.encode(frame):
                container.mux(packet)
        for packet in sound.encode():
            container.mux(packet)
        container.close()
        os.replace(partial, path)
    except BaseException:
        container.close()
        partial.unlink(missing_ok=True)
        raise
    return path
