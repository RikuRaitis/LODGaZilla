"""The demo workspace (.umw): one card of each type over the forest run."""
from __future__ import annotations

from pathlib import Path

from src.data.workspace_file import write_workspace_file


def _entry(path: Path, name: str) -> dict:  # type: ignore[type-arg]
    return {"path": str(path), "name": name}


def _axes(path: Path, *names: str) -> dict:  # type: ignore[type-arg]
    return {"axes": [{**_entry(path, name), "component": "value"} for name in names], "color": "#e53935"}


def _video(folder: Path, path: Path) -> dict:  # type: ignore[type-arg]
    return {"type": "video", "folder": str(folder), "path": str(path), "offset": 0.0, "position": 0.0,
            "volume": 80, "muted": False, "preview": {"automatic": True, "size": 720}}


def write_demo_workspace(path: Path, root: Path, folder: Path, watch: Path, foot_pod: Path, *, log_start: float,
                         flat_video: Path | None, insv_video: Path | None, image: Path | None,
                         markers: tuple[float, float]) -> Path:
    """Write the workspace; ``markers`` are seconds from the log start (the log jump)."""
    from src.gui.navigator.widget import FILTER_NONE

    first_row = [{"type": "gnss", "groups": [_axes(watch, "GNSS/Latitude", "GNSS/Longitude")], "map_type": "Street"}]
    if flat_video is not None:
        first_row.insert(0, _video(folder, flat_video))
    if image is not None:
        first_row.append({"type": "image", "folder": str(folder), "path": str(image)})
    multi = {"type": "multi", "series": [_entry(watch, name) for name in (
        "Heart rate/Heart rate", "Activity/Speed", "Barometer/Altitude", "GNSS/Altitude", "Activity/Phase",
        "Activity/Step", "Buttons/Lap")], "filmstrips": []}
    if flat_video is not None:
        multi["filmstrips"].append({"path": str(flat_video), "folder": str(folder), "offset": 0.0})
    rows = [
        {"height": 380, "widths": None, "cards": first_row},
        {"height": 460, "widths": None, "cards": [multi]},
        {"height": 280, "widths": None, "cards": [{
            "type": "line", "series": [_entry(watch, f"Accelerometer/{axis}") for axis in "XYZ"],
            "markers": [log_start + markers[0], log_start + markers[1]]}]},
        # The foot pod's clock is 1.7 s ahead: its strikes trail the watch's right-foot steps.
        {"height": 220, "widths": None, "cards": [{"type": "multi", "series": [
            _entry(watch, "Activity/Step"), _entry(foot_pod, "Foot pod/Foot strike"),
            _entry(watch, "Activity/Cadence"), _entry(foot_pod, "Foot pod/Cadence")], "filmstrips": []}]},
        {"height": 200, "widths": None, "cards": [{"type": "event", "series": [_entry(watch, name) for name in (
            "Activity/Phase", "GNSS/Fix", "Activity/Jump", "Buttons/Lap")]}]},
        {"height": 260, "widths": None, "cards": [{"type": "frequency", "series": [_entry(watch, "Accelerometer/Z")]}]},
        {"height": 260, "widths": None, "cards": [{"type": "interval", "series": [_entry(watch, name) for name in (
            "Accelerometer/X", "Heart rate/PPG", "Barometer/Pressure", "GNSS/Latitude")]}]},
        {"height": 320, "widths": None, "cards": [{"type": "xy", "groups": [
            _axes(watch, "Activity/Speed", "Heart rate/Heart rate")]}]},
        {"height": 360, "widths": None, "cards": [{"type": "xyz", "groups": [
            _axes(watch, "Accelerometer/X", "Accelerometer/Y", "Accelerometer/Z")], "sample_limit": 100_000}]},
        {"height": 400, "widths": None, "cards": [
            {"type": "gnss_3d", "groups": [_axes(watch, "GNSS/Latitude", "GNSS/Longitude", "GNSS/Altitude")]},
            *([_video(folder, insv_video)] if insv_video is not None else [])]},
    ]
    state = {
        "navigator": {"folder": str(root), "group_by": "Year/Month/Day", "filter_key": FILTER_NONE, "filter_value": ""},
        "workspace": {"rows": rows, "active": 2, "scroll": 0},
    }
    return write_workspace_file(path, state)
