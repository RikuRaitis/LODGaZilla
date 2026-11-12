"""Create synthetic demo data: simulated wrist-worn running workouts with videos, audio and a route picture.

The output folder (by default demodata in this checkout) holds four sessions:

- 20261103_071500  Forest run, 60 s: every watch series at up to 1000 Hz, a foot pod,
  a 4K chase-camera video, an Insta360-style 360 video (INSV), their soundtracks and a route picture.
- 20261105_174500 and 20261108_101500  Two more 60 s runs with other metadata.
- 20261109_060000  A long run (2 h by default) for performance: 1000 Hz accelerometer and the slower series.

Every device writes one CSV (a metadata header, then one row per sample) and the
matching LODGaZilla H5 cache. demo.umw opens a workspace with every card type;
it is written last, so it marks a complete set.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.demo_data.course import Track, main_course, make_scenery, random_course, simulate  # noqa: E402
from tools.demo_data.sensors import FOOT_POD_CLOCK_ERROR_S, coverage, foot_pod_series, watch_series  # noqa: E402
from tools.demo_data.writers import (  # noqa: E402
    convert_soundtrack, relative_link, summary, write_device, write_folder_info,
)

MAIN_SESSION = "20261103_071500"
FLAT_LEAD_IN = 4.0  # the chase camera starts recording this long before the watch
INSV_LEAD_IN = 2.5
COMMON = {"dataset": "LODGaZilla demo data (synthetic)", "location": "Forest trail, Espoo (synthetic route)"}
SESSIONS = (
    (MAIN_SESSION, "Forest run", {"runner": "Demo runner A", "device": "Demo watch", "firmware": "2.31.4",
                                  "sport": "Trail running", "weather": "Overcast, +6 C", "surface": "Trail"}, "DEMO-0001"),
    ("20261105_174500", "Evening intervals", {"runner": "Demo runner A", "device": "Demo watch", "firmware": "2.31.4",
                                              "sport": "Trail running", "weather": "Light rain, +4 C",
                                              "surface": "Trail"}, "DEMO-0001"),
    ("20261108_101500", "Hill repeats", {"runner": "Demo runner B", "device": "Demo watch S", "firmware": "2.29.0",
                                         "sport": "Hill running", "weather": "Clear, -2 C", "surface": "Frozen trail"},
     "DEMO-0002"),
    ("20261109_060000", "Long run", {"runner": "Demo runner B", "device": "Demo watch S", "firmware": "2.29.0",
                                     "sport": "Long run", "weather": "Fog, +1 C", "surface": "Gravel road"}, "DEMO-0002"),
)
# Shares of a full run's time with the default options (42 min in all); the long run's is per hour.
STAGE_WEIGHTS = {"data": 0.02, "image": 0.01, "flat": 0.38, "insv": 0.53, "session": 0.005, "long_hour": 0.025}


class Progress:
    """Prints "PROGRESS <fraction>" lines for LODGaZilla's progress bar (--progress)."""

    def __init__(self, weights: list[float], enabled: bool) -> None:
        self.weights = weights
        self.enabled = enabled
        self.total = sum(weights) or 1.0
        self.done = 0.0
        self.stage = 0

    def step(self, fraction: float) -> None:
        """Report ``fraction`` of the current stage done."""
        if self.enabled and self.stage < len(self.weights):
            share = self.weights[self.stage] * min(1.0, max(0.0, fraction))
            print(f"PROGRESS {(self.done + share) / self.total:.4f}", flush=True)

    def next(self) -> None:
        """Finish the current stage."""
        if self.stage < len(self.weights):
            self.done += self.weights[self.stage]
            self.stage += 1
        if self.enabled:
            print(f"PROGRESS {self.done / self.total:.4f}", flush=True)


def stage_weights(args: argparse.Namespace) -> list[float]:
    """The stages in the order main() runs them."""
    weights = [STAGE_WEIGHTS["data"], STAGE_WEIGHTS["image"]]
    if not args.no_video:
        weights += [STAGE_WEIGHTS["flat"], STAGE_WEIGHTS["insv"]]
    weights += [STAGE_WEIGHTS["session"]] * 2
    if args.long_hours > 0:
        weights.append(STAGE_WEIGHTS["long_hour"] * args.long_hours)
    return weights


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path,
                        help="Folder for the sessions (default: demodata in this checkout).")
    parser.add_argument("--no-video", action="store_true", help="Skip the videos and their audio (much faster).")
    parser.add_argument("--long-hours", type=float, default=2.0, help="Length of the long run; 0 skips it.")
    parser.add_argument("--fps", type=int, default=30, help="Video frame rate.")
    parser.add_argument("--width", type=int, default=3840, help="Chase-camera video width.")
    parser.add_argument("--height", type=int, default=2160, help="Chase-camera video height.")
    parser.add_argument("--lens-size", type=int, default=1920, help="Width and height of each INSV lens track.")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2),
                        help="Processes rendering video frames.")
    parser.add_argument("--progress", action="store_true", help='Also print "PROGRESS <fraction>" lines.')
    return parser.parse_args()


def log_start_for(folder_name: str) -> float:
    """The local wall-clock start in a YYYYMMDD_HHMMSS name, as the importer reads it from file names."""
    return datetime.strptime(folder_name, "%Y%m%d_%H%M%S").astimezone().timestamp()


def course_for(index: int, folder_name: str, long_hours: float):  # type: ignore[no-untyped-def]
    if folder_name == MAIN_SESSION:
        return main_course()
    if index == 3:
        return random_course(long_hours * 3600, 109, jumps_per_minute=0.05)
    return random_course(60.0, 100 + index, jumps_per_minute=2.0 if index == 2 else 1.0)


def log(message: str) -> None:
    print(message, flush=True)


def write_media(folder: Path, stamp: str, track: Track, log_start: float, args: argparse.Namespace,
                progress: Progress) -> dict:  # type: ignore[type-arg]
    from tools.demo_data.render import route_picture
    from tools.demo_data.sound import soundtrack
    from tools.demo_data.video import encode

    scenery_seed = track.spec.seed + 1
    scenery = make_scenery(track, scenery_seed)
    image = folder / f"route_{stamp}.png"
    route_picture(track, scenery, image)
    log(f"  {image.name}")
    progress.next()
    media: dict = {"image": image, "videos": [], "audio": []}  # type: ignore[type-arg]
    if args.no_video:
        return media
    end = track.spec.duration
    flat = folder / "chase_cam_4k.mp4"
    started = datetime.fromtimestamp(log_start - INSV_LEAD_IN)
    insv = folder / f"VID_{started:%Y%m%d_%H%M%S}_00_001.insv"
    for path, kind, lead_in, offset in ((flat, "flat", FLAT_LEAD_IN, 0.0), (insv, "insv", INSV_LEAD_IN, -INSV_LEAD_IN)):
        log(f"  Rendering {path.name} with {args.workers} processes...")
        encode(path, kind, track.spec, scenery_seed, start=-lead_in, end=end, fps=args.fps,
               audio=soundtrack(track, scenery, -lead_in, end), workers=args.workers, progress=log,
               options={"width": args.width, "height": args.height, "lens_size": args.lens_size, "log_start": log_start},
               frames=progress.step)
        # The chase camera is left unsynchronised (offset 0) to try the Sync buttons; the INSV is synchronised.
        media["videos"].append((path, offset))
        media["audio"].append((convert_soundtrack(folder, path, log_start, offset), path, offset))
        log(f"  {path.name} and its audio H5")
        progress.next()
    return media


def write_readme(root: Path, sessions: list[str]) -> None:
    text = f"""# LODGaZilla demo data

Synthetic data made by `tools/create_demo_dataset.py`: simulated running workouts with a
watch on the left wrist. Nothing here is a real recording. Open this folder in the Navigator,
or open `demo.umw` (Open workspace on the File bar) for a workspace with every card type.

Sessions: {", ".join(sessions)}

Each session folder holds one CSV per device (a `#` header with the session and every series'
unit, description, sensor, nominal rate, range, clipping limits and value meanings, then
`time_unix_s,time_s,series,value,unit` rows) and the LODGaZilla H5 cache made from the same data,
with the same metadata as H5 attributes.

## Forest run ({MAIN_SESSION})

- Watch at up to 1000 Hz: accelerometer, gyroscope and magnetometer (sample loss at three FIFO
  overflows in all nine series; clipping at the jump landings and on the steel bridge in eight),
  compass, GNSS (no fix under dense canopy at 34-40 s), barometer (one duplicated timestamp),
  heart rate and raw PPG (timestamp jitter and a 0.5 s gap), steps, cadence, speed, distance,
  workout phase, jumps and lap presses, and hidden system series.
- A 50 Hz vibration on the wooden bridge at 41-43 s shows in the Frequency card.
- Foot pod: its clock runs {FOOT_POD_CLOCK_ERROR_S} s ahead of the watch. Its right-foot strikes trail the watch's
  right-foot Step events until you set a time offset of -{FOOT_POD_CLOCK_ERROR_S} s.
- `chase_cam_4k.mp4` started {FLAT_LEAD_IN} s before the watch and is not synchronised. Synchronise it at a lap
  press: the watch beeps (left channel) and its screen lights up at 1, 32 and 52 s.
- The `.insv` file is an Insta360-style 360 video, already synchronised ({-INSV_LEAD_IN:+} s).
- Both soundtracks are linked as audio series in the Media group.
- `route_*.png` is an illustrated map of the route.

## Long run

The long run has 1000 Hz accelerometer data for performance testing of zooming, LOD and sample limits.
"""
    (root / "README.md").write_text(text, encoding="utf-8")


def main() -> None:
    args = parse_arguments()
    from tools.demo_data.render import ensure_gui

    ensure_gui()
    root = (args.output_dir or ROOT / "demodata").resolve()
    root.mkdir(parents=True, exist_ok=True)
    log(f"Writing demo data to {root}")
    started = time.monotonic()
    progress = Progress(stage_weights(args), args.progress)
    progress.step(0.0)
    written = []
    main_paths = None
    for index, (stamp, title, editable, serial) in enumerate(SESSIONS):
        if index == 3 and args.long_hours <= 0:
            continue
        spec = course_for(index, stamp, args.long_hours)
        folder = root / stamp
        folder.mkdir(exist_ok=True)
        log(f"{stamp} {title} ({spec.duration / 60:.0f} min)")
        track = simulate(spec)
        log_start = log_start_for(stamp)
        session = {"title": title, **COMMON, **editable, "serial number": serial}
        series = watch_series(track, full=index != 3, seed=spec.seed)
        imu = [item for item in series if item.group in ("Accelerometer", "Gyroscope", "Magnetometer")]
        both, _ = coverage(imu)
        log(f"  {both} of {len(imu)} IMU series have both sample loss and clipping")
        watch = write_device(folder, f"watch_{stamp}", series, log_start, session)
        log(f"  {watch.name}: {len(summary(watch))} series")
        media: dict = {"videos": [], "audio": []}  # type: ignore[type-arg]
        foot_pod = None
        if stamp == MAIN_SESSION:
            foot_pod = write_device(folder, f"footpod_{stamp}", foot_pod_series(track, np.random.default_rng(spec.seed + 2)),
                                    log_start, {**session, "device": "Demo foot pod", "serial number": "DEMO-POD-01"})
            log(f"  {foot_pod.name}: {len(summary(foot_pod))} series")
            progress.next()
            media = write_media(folder, stamp, track, log_start, args, progress)
        notes = {"notes": f"{title}, synthetic. See README.md in {root.name}."}
        write_folder_info(folder, {**COMMON, **editable, **notes}, serial, videos=media["videos"],
                          images=[media["image"]] if media.get("image") else [], audio=media["audio"])
        written.append(f"{stamp} ({title})")
        if stamp != MAIN_SESSION:
            progress.next()
        if stamp == MAIN_SESSION:
            jump = spec.jumps[0]
            videos = dict((path.suffix, path) for path, _ in media["videos"])
            main_paths = (folder, watch, foot_pod, log_start, videos.get(".mp4"), videos.get(".insv"),
                          media.get("image"), (jump.takeoff - 0.4, jump.landing + 0.6))
    write_readme(root, written)
    if main_paths is not None:
        from tools.demo_data.workspace import write_demo_workspace

        folder, watch, foot_pod, log_start, flat, insv, image, markers = main_paths
        workspace = write_demo_workspace(root / "demo.umw", root, folder, watch, foot_pod, log_start=log_start,
                                         flat_video=flat, insv_video=insv, image=image, markers=markers)
        log(f"Workspace {relative_link(root, workspace)}")
    log(f"Done in {(time.monotonic() - started) / 60:.1f} min: {root}")


if __name__ == "__main__":
    main()
