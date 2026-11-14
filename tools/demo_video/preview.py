"""Make the recorded scenes playable at their real speed, for review scene by scene.

``python tools/demo_video/preview.py [--input build/demo_video] [scene1 scene7 ...]``

The raw ``scene<N>.h264`` streams carry no frame times, so players guess their speed
(VLC plays them too fast). This copies each stream, without re-encoding, into
``preview/scene<N>.mp4`` with the frame times from ``scene<N>.frames.json``. The player's
clock then shows seconds from the scene's start, the times in ``scene<N>.events.json``
and in ``edit.py``. The cursor is not in the recordings; ``edit.py`` draws it.
"""
from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path

import av

ROOT = Path(__file__).resolve().parents[2]


def preview(base: Path, output: Path) -> int:
    times = json.loads(base.with_name(base.name + ".frames.json").read_text(encoding="utf-8"))
    log = json.loads(base.with_name(base.name + ".events.json").read_text(encoding="utf-8"))
    started = log["started"]
    source = av.open(str(base.with_name(base.name + ".h264")), format="h264")
    target = av.open(str(output), "w")
    stream = target.add_stream_from_template(source.streams.video[0])
    stream.time_base = Fraction(1, 1000)
    count = 0
    for packet in source.demux(video=0):
        if packet.size == 0 or count >= len(times):
            continue
        # No B-frames: decode order is display order, one packet per frame.
        # Set the stream first: assigning it rescales the packet's times.
        packet.stream = stream
        packet.time_base = Fraction(1, 1000)
        packet.pts = packet.dts = max(0, round((times[count] - started) * 1000))
        target.mux(packet)
        count += 1
    target.close()
    source.close()
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=ROOT / "build" / "demo_video")
    parser.add_argument("scenes", nargs="*", help="Scene names, such as scene7; all by default.")
    arguments = parser.parse_args()
    folder = arguments.input
    names = arguments.scenes or sorted((path.stem for path in folder.glob("scene*.h264")),
                                       key=lambda name: int(name[5:]))
    (folder / "preview").mkdir(exist_ok=True)
    for name in names:
        output = folder / "preview" / f"{name}.mp4"
        frames = preview(folder / name, output)
        print(f"{output}: {frames} frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
