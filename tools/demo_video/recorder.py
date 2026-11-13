"""Record a screen rectangle to a raw H.264 stream until "stop" arrives on stdin.

``python recorder.py X Y W H OUTPUT [--fps 25]`` writes ``OUTPUT.h264`` and
``OUTPUT.frames.json`` (each frame's ``time.perf_counter()`` time, the clock the
driver's event log uses). Runs in its own process, started with the environment from
before Qt loads (Qt puts its own FFmpeg libraries on PATH). The Windows cursor is left
out; the editing step draws its own from the event log.

Raw H.264, not MP4 or MKV: PyAV's file writer corrupted frames under load in both
containers during the recording test (see docs/VIDEO_DEMO.md).
"""
import argparse
import ctypes
import json
import sys
import threading
import time
from fractions import Fraction

import av

ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Physical pixels, as the driver gives them.

parser = argparse.ArgumentParser()
for name in ("x", "y", "w", "h"):
    parser.add_argument(name, type=int)
parser.add_argument("output")
parser.add_argument("--fps", type=int, default=25)
arguments = parser.parse_args()

stop = threading.Event()
threading.Thread(target=lambda: (sys.stdin.readline(), stop.set()), daemon=True).start()

source = av.open("desktop", format="gdigrab", options={
    "framerate": str(arguments.fps), "offset_x": str(arguments.x), "offset_y": str(arguments.y),
    "video_size": f"{arguments.w}x{arguments.h}", "draw_mouse": "0"})
encoder = av.CodecContext.create("libx264", "w")
encoder.width, encoder.height, encoder.pix_fmt = arguments.w, arguments.h, "yuv420p"
encoder.time_base = Fraction(1, 1000)
encoder.framerate = Fraction(arguments.fps)
# Near-lossless, fast and without B-frames: the editing step encodes the final video.
encoder.options = {"preset": "ultrafast", "crf": "12", "bframes": "0"}
encoder.open()

times: list[float] = []
start = time.perf_counter()
print("recording", flush=True)
with open(arguments.output + ".h264", "wb") as stream:
    for frame in source.decode(video=0):
        now = time.perf_counter()
        if times and now - times[-1] < 0.001:
            continue
        converted = frame.reformat(format="yuv420p")
        converted.pts, converted.time_base = round((now - start) * 1000), encoder.time_base
        for packet in encoder.encode(converted):
            stream.write(bytes(packet))
        times.append(now)
        if stop.is_set():
            break
    for packet in encoder.encode(None):
        stream.write(bytes(packet))
source.close()
with open(arguments.output + ".frames.json", "w", encoding="utf-8") as file:
    json.dump(times, file)
gaps = [b - a for a, b in zip(times, times[1:])]
print(json.dumps({"frames": len(times), "seconds": round(times[-1] - times[0], 2),
                  "fps": round((len(times) - 1) / (times[-1] - times[0]), 1),
                  "max_gap_ms": round(max(gaps) * 1000)}), flush=True)
