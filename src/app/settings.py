"""Shared JSON settings for GUI widgets and cache-writing options."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_APP_SETTINGS_NAME = "app_settings.json"
# Untracked local settings (plugins, recent folders, metadata keys, columns).
DEBUG_APP_SETTINGS_NAME = "app_settings_debug.json"
# Set by `python -m src.app.lodgazilla --default-settings` to test the committed
# defaults; inherited by child processes.
DEFAULT_SETTINGS_ENV = "LODGAZILLA_DEFAULT_SETTINGS"


def resolve_app_settings_path(root: str | Path = REPOSITORY_ROOT) -> Path:
    """Prefer the local debug settings file; fall back to the committed defaults."""
    root = Path(root)
    debug_path = root / DEBUG_APP_SETTINGS_NAME
    if os.environ.get(DEFAULT_SETTINGS_ENV) == "1" or not debug_path.is_file():
        return root / COMMITTED_APP_SETTINGS_NAME
    return debug_path


DEFAULT_APP_SETTINGS_PATH = resolve_app_settings_path()
HDF5_SECTION = "hdf5"
NAVIGATOR_CONTROLS_SECTION = "navigator_controls"
TREE_COLUMNS_SECTION = "tree_columns"
APPEARANCE_SECTION = "appearance"
PLOT_SECTION = "plot"
LODGAZILLA_SECTION = "lodgazilla"
NAVIGATOR_DIAGNOSTICS_SECTION = "navigator_diagnostics"
PLUGINS_SECTION = "plugins"
# Where Settings > Plugins looks for LODGaZilla-* plugin repositories by default.
DEFAULT_PLUGIN_SOURCES = ["https://github.com/RikuRaitis"]
DEFAULT_INFO_METADATA_SECTION = "default_info_metadata"
AI_SECTION = "ai"

DEFAULT_HDF5_SETTINGS: dict[str, Any] = {
    "compression": "gzip",
    "compression_level": 4,
    "chunk_rows": 65536,
    "lod_target_buckets": 2000,
    "lod_bucket_growth": 4,
}
# Default series, track and lane colors. Blue is left out because the selection is
# painted blue, and red and green because the markers use them.
SERIES_COLOR_DEFAULTS = ("#ff9800", "#9c27b0", "#d81b60", "#00897b", "#9e9d24", "#757575")
DEFAULT_PLOT_SETTINGS: dict[str, Any] = {
    "multi_selected_line_width": 5,
    # Height of one Multi-series lane, as a percentage of the card height.
    "multi_series_lane_height_percent": 20,
    # Height of a video filmstrip lane in a Multi-series card, in screen pixels.
    "multi_filmstrip_height_pixels": 30,
    "minimum_diagnostic_width_pixels": 1,
    "show_sample_loss_areas": False,
    "show_clipping_areas": False,
    "first_marker_color": "#ff3b30",
    "second_marker_color": "#00c853",
    "marker_width": 3,
    "marker_opacity_percent": 100,
    # Fill between the markers; bands drawn over a line use twice this opacity.
    "selection_color": "#2196f3",
    "selection_opacity_percent": 22,
    # A Video card's stored sync data marker.
    "sync_marker_color": "#8e49e6",
    # Background opacity of the per-series values boxes.
    "values_box_opacity_percent": 70,
    "series_colors": list(SERIES_COLOR_DEFAULTS),
}
# Card key: (settings label, default height, minimum height), in pixels.
PLOT_HEIGHT_SETTINGS: dict[str, tuple[str, int, int]] = {
    "line": ("Line (time series)", 200, 80),
    "multi": ("Multi-series", 200, 80),
    "event": ("Event/state", 100, 80),
    "interval": ("Sample interval", 200, 80),
    "xy": ("X/Y", 200, 80),
    "xyz": ("3D plot", 380, 220),
    "frequency": ("Frequency (spectrogram)", 300, 160),
    "gnss": ("Map", 500, 80),
    "gnss_3d": ("3D Map", 500, 360),
    "image": ("Image", 200, 80),
    "video": ("Video", 500, 80),
}
TIME_AXIS_DISPLAY_MODES = ("all", "bottom", "none")
DEFAULT_LODGAZILLA_SETTINGS: dict[str, Any] = {
    "navigator_width_ratio": 0.24,
    "default_plot_heights": {key: default for key, (_, default, _) in PLOT_HEIGHT_SETTINGS.items()},
    "link_plot_x_axes": True,
    "link_plot_time_format": True,
    "link_markers": False,
    "snap_vertical_marker_to_nearest_point": False,
    "marker_info_display": "active",
    # Time axis on every time card, only on the bottom one, or on none.
    "time_axis_display": "none",
    # The Plot navigator's Legends and Card controls buttons.
    "show_legends": False,
    "show_card_controls": True,
    "show_plot_navigator_time_row": True,
    "plot_navigator_expanded": False,
    # The AI panel below the Navigator is open (at its minimum height) or one row.
    "ai_panel_expanded": True,
    "internal_log_expanded": False,
    "internal_log_expanded_height": 261,
    "internal_log_maximum_lines": 5_000,
    "plot_navigator_icon_size": 25,
    "card_drop_selector_delay_ms": 1000,
    "plot_navigator_colors": {
        "line": "#2196f3",
        "multi": "#00acc1",
        "event": "#ff9800",
        "xy": "#9c27b0",
        "xyz": "#00897b",
        "gnss": "#20c56b",
        "frequency": "#7c4dff",
        "interval": "#8d6e63",
        "video": "#e53935",
        "other": "#9e9e9e",
    },
    # Applies to both H5 cache creation and plugin conversion jobs.  Eight is
    # deliberately the hard ceiling: more parser processes make the GUI and
    # storage subsystem less responsive on typical developer laptops.
    "max_background_processes": 8,
    # Workspace (.umw) files most recently saved or opened, newest first.
    "recent_workspaces": [],
    # Ask at startup to create missing demo data; off after "Don't ask again".
    "offer_demo_data": True,
}
DEFAULT_NAVIGATOR_DIAGNOSTICS_SETTINGS: dict[str, Any] = {
    "order": ["sample_loss", "clipping"],
    "sample_loss": {"enabled": True, "color": "#806b00", "bold": True, "italic": False},
    "clipping": {"enabled": True, "color": "#8a4d00", "bold": True, "italic": False},
}
DEFAULT_INFO_METADATA_SETTINGS: dict[str, Any] = {
    "keys": ["diver", "session", "place", "device location"],
}
DEFAULT_AI_SETTINGS: dict[str, Any] = {
    # The Claude Code CLI; empty finds it on PATH or in the VS Code extension.
    "claude_path": "",
    # The Codex CLI; empty finds it on PATH or in the OpenAI VS Code extension.
    "codex_path": "",
    # The user allowed Magic drop to send dropped files to Claude; asked on the first drop.
    "magic_drop_consent": False,
    # The same for Codex (OpenAI), asked on the first drop with Codex.
    "codex_magic_drop_consent": False,
    # Whether each agent may read web pages, such as file format specifications.
    "claude_web_access": True,
    "codex_web_access": True,
    # Limits for one Magic drop's agent run; Codex has no cost limit.
    "max_budget_usd": 2.0,
    "time_limit_minutes": 15,
}

# The desktop application starts one cache session during startup.  All normal
# UI save helpers then update this in-memory copy.  The window flushes it on a
# short debounce after each change (see set_app_settings_changed_callback) and
# again from LODGaZillaWindow.closeEvent.  Utility scripts and tests do not start
# a session, so their existing explicit save calls remain immediately durable.
_SETTINGS_CACHE: dict[Path, dict[str, Any]] = {}
_DIRTY_SETTINGS_PATHS: set[Path] = set()
_CHANGED_CALLBACKS: dict[Path, Callable[[], None]] = {}


@dataclass(frozen=True)
class NavigatorControlSettings:
    align_columns_of_different_type: bool = False
    hide_irrelevant_series: bool = True
    recent_folders: tuple[str, ...] = ()


def default_app_settings_data() -> dict[str, Any]:
    return {
        NAVIGATOR_CONTROLS_SECTION: {
            "align_columns_of_different_type": False,
            "hide_irrelevant_series": True,
            "recent_folders": [],
        },
        APPEARANCE_SECTION: {"theme": "os"},
        PLOT_SECTION: dict(DEFAULT_PLOT_SETTINGS),
        LODGAZILLA_SECTION: {
            **DEFAULT_LODGAZILLA_SETTINGS,
            "default_plot_heights": dict(DEFAULT_LODGAZILLA_SETTINGS["default_plot_heights"]),
        },
        PLUGINS_SECTION: {"enabled": {}, "sources": list(DEFAULT_PLUGIN_SOURCES), "check_at_startup": True},
        DEFAULT_INFO_METADATA_SECTION: dict(DEFAULT_INFO_METADATA_SETTINGS),
        NAVIGATOR_DIAGNOSTICS_SECTION: sanitize_navigator_diagnostics_settings({}),
        HDF5_SECTION: dict(DEFAULT_HDF5_SETTINGS),
        AI_SECTION: dict(DEFAULT_AI_SETTINGS),
        TREE_COLUMNS_SECTION: {
            "shared_widths": {},
            "profiles": {},
        },
    }


def load_app_settings_data(settings_path: str | Path | None = DEFAULT_APP_SETTINGS_PATH) -> dict[str, Any]:
    if settings_path is None:
        return default_app_settings_data()
    path = Path(settings_path).resolve()
    cached = _SETTINGS_CACHE.get(path)
    if cached is not None:
        return cached
    return _load_app_settings_data_from_disk(path)


def _load_app_settings_data_from_disk(path: Path, *, backup_corrupt: bool = False) -> dict[str, Any]:
    data = default_app_settings_data()
    if not path.is_file():
        return data
    logger = logging.getLogger("lodgazilla.settings")
    try:
        with path.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
    except OSError as error:
        logger.warning("Could not read settings from %s; using defaults: %s", path, error)
        return data
    except ValueError as error:  # JSONDecodeError and UnicodeDecodeError.
        if backup_corrupt:
            # Keep the unreadable file: the session's first flush would
            # otherwise overwrite the user's settings with defaults.
            _back_up_corrupt_settings(path, error)
        else:
            logger.warning("Settings file %s is unreadable; using defaults: %s", path, error)
        return data
    if isinstance(loaded, dict):
        lodgazilla = loaded.get(LODGAZILLA_SECTION)
        if isinstance(lodgazilla, dict):
            # Migrate before merging so the new defaults cannot mask the saved
            # shared height. Preserve unrelated settings in this section.
            lodgazilla["default_plot_heights"] = sanitize_plot_heights(
                lodgazilla.get("default_plot_heights"), lodgazilla.pop("default_plot_height", None)
            )
        diagnostics = loaded.get(NAVIGATOR_DIAGNOSTICS_SECTION)
        if isinstance(diagnostics, dict):
            # Migrate before merging: new default switches would otherwise mask
            # the legacy global disabled setting.
            loaded[NAVIGATOR_DIAGNOSTICS_SECTION] = sanitize_navigator_diagnostics_settings(diagnostics)
        merge_settings_data(data, loaded)
    elif backup_corrupt:
        _back_up_corrupt_settings(path, ValueError("top-level JSON value is not an object"))
    return data


def _back_up_corrupt_settings(path: Path, error: Exception) -> Path | None:
    """Rename an unreadable settings file aside before defaults replace it."""
    logger = logging.getLogger("lodgazilla.settings")
    backup = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        path.replace(backup)
    except OSError as rename_error:
        logger.warning(
            "Settings file %s is corrupt (%s) and could not be backed up: %s",
            path, error, rename_error,
        )
        return None
    logger.warning(
        "Settings file %s is corrupt (%s); moved it to %s and started from defaults.",
        path, error, backup,
    )
    return backup


def set_app_settings_changed_callback(
    callback: Callable[[], None] | None,
    settings_path: str | Path = DEFAULT_APP_SETTINGS_PATH,
) -> None:
    """Register the owner notified whenever an active session becomes dirty."""
    path = Path(settings_path).resolve()
    if callback is None:
        _CHANGED_CALLBACKS.pop(path, None)
    else:
        _CHANGED_CALLBACKS[path] = callback


def begin_app_settings_cache(
    settings_path: str | Path = DEFAULT_APP_SETTINGS_PATH,
) -> dict[str, Any]:
    """Begin an in-memory settings session and return its mutable data."""
    path = Path(settings_path).resolve()
    existing = _SETTINGS_CACHE.get(path)
    if existing is not None:
        return existing
    data = _load_app_settings_data_from_disk(path, backup_corrupt=True)
    _SETTINGS_CACHE[path] = data
    _DIRTY_SETTINGS_PATHS.discard(path)
    return data


def app_settings_cache_active(settings_path: str | Path = DEFAULT_APP_SETTINGS_PATH) -> bool:
    return Path(settings_path).resolve() in _SETTINGS_CACHE


def flush_app_settings_cache(settings_path: str | Path = DEFAULT_APP_SETTINGS_PATH) -> bool:
    """Write one active settings session to disk, if it has changed."""
    path = Path(settings_path).resolve()
    cached = _SETTINGS_CACHE.get(path)
    if cached is None or path not in _DIRTY_SETTINGS_PATHS:
        return True
    if not _save_app_settings_data_to_disk(path, cached):
        return False
    _DIRTY_SETTINGS_PATHS.discard(path)
    return True


def end_app_settings_cache(
    settings_path: str | Path = DEFAULT_APP_SETTINGS_PATH,
    *,
    flush: bool = True,
) -> bool:
    """Finish a settings session, optionally flushing its deferred changes."""
    path = Path(settings_path).resolve()
    success = flush_app_settings_cache(path) if flush else True
    if success or not flush:
        _SETTINGS_CACHE.pop(path, None)
        _DIRTY_SETTINGS_PATHS.discard(path)
        _CHANGED_CALLBACKS.pop(path, None)
    return success


def merge_settings_data(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_settings_data(target[key], value)
        else:
            target[key] = value


def save_app_settings_data(settings_path: str | Path, data: dict[str, Any]) -> bool:
    """Persist settings atomically where possible; return False for a file lock.

    OneDrive and editors can temporarily lock the JSON file on Windows. A
    settings write must never crash an interactive GUI callback in that case.
    """
    path = Path(settings_path).resolve()
    if path in _SETTINGS_CACHE:
        _SETTINGS_CACHE[path] = data
        _DIRTY_SETTINGS_PATHS.add(path)
        callback = _CHANGED_CALLBACKS.get(path)
        if callback is not None:
            callback()
        return True
    return _save_app_settings_data_to_disk(path, data)


def _save_app_settings_data_to_disk(path: Path, data: dict[str, Any]) -> bool:
    """Perform the actual atomic JSON write (outside an active UI session)."""
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=True)
            file.write("\n")
        try:
            temp_path.replace(path)
            return True
        except PermissionError:
            # Some applications allow a direct write even though they block
            # os.replace; use it only as a fallback.
            with path.open("w", encoding="utf-8") as file:
                json.dump(data, file, indent=2, ensure_ascii=True)
                file.write("\n")
            return True
    except OSError as error:
        logging.getLogger("lodgazilla.settings").warning("Could not save settings to %s: %s", path, error)
        return False
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def load_settings_section(
    section: str,
    settings_path: str | Path | None = DEFAULT_APP_SETTINGS_PATH,
) -> dict[str, Any]:
    value = load_app_settings_data(settings_path).get(section, {})
    return dict(value) if isinstance(value, dict) else {}


def save_settings_section(
    settings_path: str | Path,
    section: str,
    section_data: dict[str, Any],
) -> bool:
    data = load_app_settings_data(settings_path)
    data[section] = section_data
    return save_app_settings_data(settings_path, data)


def load_default_info_metadata_settings(
    settings_path: str | Path | None = DEFAULT_APP_SETTINGS_PATH,
) -> dict[str, Any]:
    settings = dict(DEFAULT_INFO_METADATA_SETTINGS)
    settings.update(sanitize_default_info_metadata_settings(
        load_settings_section(DEFAULT_INFO_METADATA_SECTION, settings_path)
    ))
    return settings


def sanitize_default_info_metadata_settings(raw_settings: dict[str, Any]) -> dict[str, Any]:
    raw_keys = raw_settings.get("keys", DEFAULT_INFO_METADATA_SETTINGS["keys"])
    keys = raw_keys if isinstance(raw_keys, list) else DEFAULT_INFO_METADATA_SETTINGS["keys"]
    normalised: list[str] = []
    seen: set[str] = set()
    for key in keys:
        text = str(key).strip().casefold()
        if text and text not in seen:
            normalised.append(text)
            seen.add(text)
    return {"keys": normalised}


def load_hdf5_settings(settings_path: str | Path | None = DEFAULT_APP_SETTINGS_PATH) -> dict[str, Any]:
    raw_settings = load_settings_section(HDF5_SECTION, settings_path)
    settings = dict(DEFAULT_HDF5_SETTINGS)
    settings.update(sanitize_hdf5_settings(raw_settings))
    return settings


def sanitize_hdf5_settings(raw_settings: dict[str, Any]) -> dict[str, Any]:
    compression = str(raw_settings.get("compression", DEFAULT_HDF5_SETTINGS["compression"])).strip().lower()
    if compression not in {"gzip", "lzf", "none"}:
        compression = str(DEFAULT_HDF5_SETTINGS["compression"])
    return {
        "compression": compression,
        "compression_level": bounded_int(raw_settings.get("compression_level"), 0, 9, 4),
        "chunk_rows": bounded_int(raw_settings.get("chunk_rows"), 1, 10_000_000, 65536),
        "lod_target_buckets": bounded_int(raw_settings.get("lod_target_buckets"), 1, 1_000_000, 2000),
        "lod_bucket_growth": bounded_int(raw_settings.get("lod_bucket_growth"), 2, 128, 4),
    }


def save_hdf5_settings(settings_path: str | Path, settings: dict[str, Any]) -> None:
    save_settings_section(settings_path, HDF5_SECTION, sanitize_hdf5_settings(settings))


def load_plot_settings(settings_path: str | Path | None = DEFAULT_APP_SETTINGS_PATH) -> dict[str, Any]:
    settings = dict(DEFAULT_PLOT_SETTINGS)
    settings.update(sanitize_plot_settings(load_settings_section(PLOT_SECTION, settings_path)))
    return settings


def sanitize_plot_settings(raw_settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "multi_selected_line_width": bounded_int(raw_settings.get("multi_selected_line_width"), 1, 20, 5),
        "multi_series_lane_height_percent": bounded_int(
            raw_settings.get("multi_series_lane_height_percent"), 2, 50, 20
        ),
        "multi_filmstrip_height_pixels": bounded_int(raw_settings.get("multi_filmstrip_height_pixels"), 15, 240, 30),
        "minimum_diagnostic_width_pixels": bounded_int(
            raw_settings.get("minimum_diagnostic_width_pixels"), 1, 100, 1
        ),
        "show_sample_loss_areas": bool(
            raw_settings.get("show_sample_loss_areas", False)
        ),
        "show_clipping_areas": bool(
            raw_settings.get("show_clipping_areas", False)
        ),
        "first_marker_color": color_setting(raw_settings.get("first_marker_color"), "#ff3b30"),
        "second_marker_color": color_setting(raw_settings.get("second_marker_color"), "#00c853"),
        "marker_width": bounded_int(raw_settings.get("marker_width"), 1, 10, 3),
        "marker_opacity_percent": bounded_int(raw_settings.get("marker_opacity_percent"), 10, 100, 100),
        "selection_color": color_setting(raw_settings.get("selection_color"), "#2196f3"),
        "selection_opacity_percent": bounded_int(raw_settings.get("selection_opacity_percent"), 5, 100, 22),
        "sync_marker_color": color_setting(raw_settings.get("sync_marker_color"), "#8e49e6"),
        "values_box_opacity_percent": bounded_int(raw_settings.get("values_box_opacity_percent"), 0, 100, 70),
        "series_colors": series_color_setting(raw_settings.get("series_colors")),
    }


def series_color_setting(value: Any) -> list[str]:
    """Keep the valid #RRGGBB entries in order; with none, use the defaults."""
    colors = [color_setting(color, "") for color in value] if isinstance(value, list) else []
    colors = [color for color in colors if color]
    return colors or list(SERIES_COLOR_DEFAULTS)


def color_setting(value: Any, default: str) -> str:
    """Return a #RRGGBB color, or ``default`` for anything else."""
    text = str(value or "").strip()
    valid = len(text) == 7 and text.startswith("#") and all(char in "0123456789abcdefABCDEF" for char in text[1:])
    return text.lower() if valid else default


def load_lodgazilla_settings(
    settings_path: str | Path | None = DEFAULT_APP_SETTINGS_PATH,
) -> dict[str, Any]:
    settings = dict(DEFAULT_LODGAZILLA_SETTINGS)
    settings.update(sanitize_lodgazilla_settings(load_settings_section(LODGAZILLA_SECTION, settings_path)))
    return settings


def sanitize_plot_heights(raw_heights: Any, legacy_height: Any = None) -> dict[str, int]:
    """Validate per-card heights and migrate the former shared height setting."""
    raw_heights = raw_heights if isinstance(raw_heights, dict) else {}
    legacy = bounded_int(legacy_height, 80, 2_000, 200)
    heights = {}
    for key, (_, default, minimum) in PLOT_HEIGHT_SETTINGS.items():
        if legacy_height is not None:
            # Preserve the previous compact timeline and 3D minimum on migration.
            default = min(legacy, 130) if key == "event" else max(minimum, legacy)
        heights[key] = bounded_int(raw_heights.get(key), minimum, 2_000, default)
    return heights


def sanitize_lodgazilla_settings(raw_settings: dict[str, Any]) -> dict[str, Any]:
    try:
        navigator_width_ratio = float(
            raw_settings.get("navigator_width_ratio", DEFAULT_LODGAZILLA_SETTINGS["navigator_width_ratio"])
        )
    except (TypeError, ValueError):
        navigator_width_ratio = float(DEFAULT_LODGAZILLA_SETTINGS["navigator_width_ratio"])
    colors_raw = raw_settings.get("plot_navigator_colors", {})
    colors_raw = colors_raw if isinstance(colors_raw, dict) else {}
    navigator_colors = {
        name: str(colors_raw.get(name, color)).strip() or color
        for name, color in DEFAULT_LODGAZILLA_SETTINGS["plot_navigator_colors"].items()
    }
    return {
        "navigator_width_ratio": max(0.10, min(0.90, navigator_width_ratio)),
        "default_plot_heights": sanitize_plot_heights(
            raw_settings.get("default_plot_heights"), raw_settings.get("default_plot_height")
        ),
        "link_plot_x_axes": bool(raw_settings.get("link_plot_x_axes", True)),
        "link_plot_time_format": bool(raw_settings.get("link_plot_time_format", True)),
        "link_markers": bool(raw_settings.get("link_markers", False)),
        "snap_vertical_marker_to_nearest_point": bool(
            raw_settings.get("snap_vertical_marker_to_nearest_point", False)
        ),
        "marker_info_display": str(raw_settings.get("marker_info_display", "active"))
        if str(raw_settings.get("marker_info_display", "active")) in {"off", "active", "all"}
        else "active",
        "time_axis_display": str(raw_settings.get("time_axis_display", "none"))
        if str(raw_settings.get("time_axis_display", "none")) in TIME_AXIS_DISPLAY_MODES
        else "none",
        "show_legends": bool(raw_settings.get("show_legends", False)),
        "show_card_controls": bool(raw_settings.get("show_card_controls", True)),
        "show_plot_navigator_time_row": bool(
            raw_settings.get("show_plot_navigator_time_row", True)
        ),
        "plot_navigator_expanded": bool(raw_settings.get("plot_navigator_expanded", False)),
        "ai_panel_expanded": bool(raw_settings.get("ai_panel_expanded", True)),
        "internal_log_expanded": bool(raw_settings.get("internal_log_expanded", False)),
        "internal_log_expanded_height": bounded_int(
            raw_settings.get("internal_log_expanded_height"), 80, 5_000, 261
        ),
        "internal_log_maximum_lines": bounded_int(
            raw_settings.get("internal_log_maximum_lines"), 100, 100_000, 5_000
        ),
        "plot_navigator_icon_size": bounded_int(
            raw_settings.get("plot_navigator_icon_size"), 16, 48, 25
        ),
        "card_drop_selector_delay_ms": bounded_int(
            raw_settings.get("card_drop_selector_delay_ms"), 0, 10_000, 1000
        ),
        "plot_navigator_colors": navigator_colors,
        "max_background_processes": bounded_int(
            raw_settings.get("max_background_processes"), 1, 8, 8
        ),
        "recent_workspaces": sanitize_recent_folders(raw_settings.get("recent_workspaces", []))[:10],
        "offer_demo_data": bool(raw_settings.get("offer_demo_data", True)),
    }


def load_navigator_diagnostics_settings(
    settings_path: str | Path | None = DEFAULT_APP_SETTINGS_PATH,
) -> dict[str, Any]:
    settings = dict(DEFAULT_NAVIGATOR_DIAGNOSTICS_SETTINGS)
    settings.update(sanitize_navigator_diagnostics_settings(
        load_settings_section(NAVIGATOR_DIAGNOSTICS_SECTION, settings_path)
    ))
    return settings


def sanitize_navigator_diagnostics_settings(raw_settings: dict[str, Any]) -> dict[str, Any]:
    defaults = DEFAULT_NAVIGATOR_DIAGNOSTICS_SETTINGS
    raw_order = raw_settings.get("order", [])
    order = []
    for name in (raw_order if isinstance(raw_order, list) else []) + defaults["order"]:
        if name in defaults["order"] and name not in order:
            order.append(name)
    # Migrate the former global switch to both independent switches.
    legacy_enabled = bool(raw_settings.get("enabled", True))
    return {
        "order": order,
        **{
            name: sanitize_diagnostic_row_style(
                raw_settings.get(name), {**defaults[name], "enabled": legacy_enabled}
            )
            for name in defaults["order"]
        },
    }


def sanitize_diagnostic_row_style(raw_style: object, default: object) -> dict[str, Any]:
    source = raw_style if isinstance(raw_style, dict) else {}
    fallback = default if isinstance(default, dict) else {}
    color = str(source.get("color", fallback.get("color", "#808080"))).strip()
    if not color.startswith("#") or len(color) not in {4, 7, 9}:
        color = str(fallback.get("color", "#808080"))
    return {
        "enabled": bool(source.get("enabled", fallback.get("enabled", True))),
        "color": color,
        "bold": bool(source.get("bold", fallback.get("bold", False))),
        "italic": bool(source.get("italic", fallback.get("italic", False))),
    }


def load_navigator_control_settings(
    settings_path: str | Path | None = DEFAULT_APP_SETTINGS_PATH,
) -> NavigatorControlSettings:
    raw_settings = load_settings_section(NAVIGATOR_CONTROLS_SECTION, settings_path)
    return NavigatorControlSettings(
        align_columns_of_different_type=bool(
            raw_settings.get("align_columns_of_different_type", False)
        ),
        hide_irrelevant_series=bool(raw_settings.get("hide_irrelevant_series", True)),
        recent_folders=tuple(sanitize_recent_folders(raw_settings.get("recent_folders", []))),
    )


def save_navigator_control_settings(
    settings_path: str | Path,
    settings: NavigatorControlSettings,
) -> None:
    save_settings_section(
        settings_path,
        NAVIGATOR_CONTROLS_SECTION,
        {
            "align_columns_of_different_type": bool(settings.align_columns_of_different_type),
            "hide_irrelevant_series": bool(settings.hide_irrelevant_series),
            "recent_folders": sanitize_recent_folders(settings.recent_folders),
        },
    )


def sanitize_recent_folders(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    folders: list[str] = []
    seen: set[str] = set()
    for item in value:
        folder = str(item).strip()
        key = folder.casefold()
        # Display strings containing the middle-elision marker are not paths.
        # Keep only the original full paths in persistent settings.
        if not folder or "..." in folder or key in seen:
            continue
        folders.append(folder)
        seen.add(key)
        if len(folders) == 10:
            break
    return folders


def load_ai_settings(settings_path: str | Path | None = DEFAULT_APP_SETTINGS_PATH) -> dict[str, Any]:
    return sanitize_ai_settings(load_settings_section(AI_SECTION, settings_path))


def sanitize_ai_settings(raw_settings: dict[str, Any]) -> dict[str, Any]:
    defaults = DEFAULT_AI_SETTINGS
    return {
        "claude_path": str(raw_settings.get("claude_path") or "").strip(),
        "codex_path": str(raw_settings.get("codex_path") or "").strip(),
        "magic_drop_consent": raw_settings.get("magic_drop_consent") is True,
        "codex_magic_drop_consent": raw_settings.get("codex_magic_drop_consent") is True,
        "claude_web_access": raw_settings.get("claude_web_access", True) is not False,
        "codex_web_access": raw_settings.get("codex_web_access", True) is not False,
        "max_budget_usd": max(0.1, min(100.0, float_or_default(raw_settings.get("max_budget_usd"),
                                                               defaults["max_budget_usd"]))),
        "time_limit_minutes": bounded_int(raw_settings.get("time_limit_minutes"), 1, 120,
                                          defaults["time_limit_minutes"]),
    }


def bounded_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, number))


def float_or_default(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
