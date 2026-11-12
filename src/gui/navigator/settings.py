"""Persistent column profiles for the data navigator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.app.settings import (
    TREE_COLUMNS_SECTION,
    load_settings_section,
    save_settings_section,
)
from src.data.folder_info import normalise_info_key

COLUMN_DATA = "data"
COLUMN_LOG_LENGTH = "log_length"
COLUMN_SAMPLE_RATE = "sample_rate"
COLUMN_SAMPLE_COUNT = "sample_count"
COLUMN_UNIT = "unit"
COLUMN_INFO_PAIR_VALUE = "value"
CUSTOM_COLUMN_PREFIX = "info:"
COLUMN_CATEGORY_FOLDER = "folder"
COLUMN_CATEGORY_INFO = "info"
COLUMN_CATEGORY_SERIES = "series"
ALL_COLUMN_CATEGORIES = {
    COLUMN_CATEGORY_FOLDER,
    COLUMN_CATEGORY_INFO,
    COLUMN_CATEGORY_SERIES,
}
ALWAYS_VISIBLE_COLUMNS = {COLUMN_DATA}
FIXED_FIRST_COLUMN = COLUMN_DATA
SHARED_WIDTH_COLUMNS = {COLUMN_DATA}


@dataclass
class ColumnProfileSettings:
    order: list[str]
    hidden: set[str]
    widths: dict[str, int]


@dataclass
class ColumnSettings:
    profiles: dict[str, ColumnProfileSettings]
    shared_widths: dict[str, int]


def sanitize_column_order(order: list[str]) -> list[str]:
    seen = {FIXED_FIRST_COLUMN}
    sanitized = [FIXED_FIRST_COLUMN]
    for column_id in order:
        column_id = normalise_column_id(column_id)
        if column_id in seen or not column_id.strip():
            continue
        sanitized.append(column_id)
        seen.add(column_id)
    return sanitized


def parse_column_widths(raw_widths: object) -> dict[str, int]:
    if not isinstance(raw_widths, dict):
        return {}
    return {
        normalise_column_id(column_id): int(width)
        for column_id, width in raw_widths.items()
        if isinstance(column_id, str)
        and normalise_column_id(column_id)
        and isinstance(width, (int, float))
        and int(width) > 0
    }


def normalise_column_id(column_id: str) -> str:
    """Canonicalize persisted custom metadata column identifiers."""

    if column_id.startswith(CUSTOM_COLUMN_PREFIX):
        return f"{CUSTOM_COLUMN_PREFIX}{normalise_info_key(column_id[len(CUSTOM_COLUMN_PREFIX):])}"
    return column_id


def default_column_settings() -> ColumnSettings:
    return ColumnSettings(
        {
            COLUMN_CATEGORY_FOLDER: ColumnProfileSettings([], set(), {}),
            COLUMN_CATEGORY_INFO: ColumnProfileSettings([], set(), {}),
            COLUMN_CATEGORY_SERIES: ColumnProfileSettings([], set(), {}),
        },
        {},
    )


def column_profile_settings(settings: ColumnSettings, category: str) -> ColumnProfileSettings:
    if category not in ALL_COLUMN_CATEGORIES:
        raise ValueError(f"Unknown column category: {category}")
    return settings.profiles[category]


def load_column_settings(settings_path: str | Path | None) -> ColumnSettings:
    if settings_path is None:
        return default_column_settings()
    raw_settings = load_settings_section(TREE_COLUMNS_SECTION, settings_path)
    if not isinstance(raw_settings, dict):
        return default_column_settings()

    settings = default_column_settings()
    settings.shared_widths = parse_column_widths(raw_settings.get("shared_widths", {}))
    raw_profiles = raw_settings.get("profiles")
    if not isinstance(raw_profiles, dict):
        return settings
    for category in ALL_COLUMN_CATEGORIES:
        raw_profile = raw_profiles.get(category)
        if isinstance(raw_profile, dict):
            settings.profiles[category] = parse_column_profile_settings(raw_profile)
    return settings


def parse_column_profile_settings(raw_profile: dict[str, object]) -> ColumnProfileSettings:
    raw_order = raw_profile.get("order", [])
    raw_hidden = raw_profile.get("hidden", [])
    order = [
        normalise_column_id(column_id)
        for column_id in raw_order
        if isinstance(column_id, str) and normalise_column_id(column_id)
    ] if isinstance(raw_order, list) else []
    hidden = {
        normalise_column_id(column_id)
        for column_id in raw_hidden
        if isinstance(column_id, str) and normalise_column_id(column_id)
    } if isinstance(raw_hidden, list) else set()
    hidden.difference_update(ALWAYS_VISIBLE_COLUMNS)
    return ColumnProfileSettings(
        sanitize_column_order(order),
        hidden,
        parse_column_widths(raw_profile.get("widths", {})),
    )


def save_column_settings(settings_path: str | Path, settings: ColumnSettings) -> None:
    payload = {
        "shared_widths": {
            column_id: width
            for column_id, width in sorted(settings.shared_widths.items())
            if column_id in SHARED_WIDTH_COLUMNS and width > 0
        },
        "profiles": {
            category: {
                "order": sanitize_column_order(profile.order),
                "hidden": sorted(profile.hidden),
                "widths": {
                    column_id: width
                    for column_id, width in sorted(profile.widths.items())
                    if column_id not in SHARED_WIDTH_COLUMNS and width > 0
                },
            }
            for category, profile in sorted(settings.profiles.items())
        },
    }
    save_settings_section(settings_path, TREE_COLUMNS_SECTION, payload)
