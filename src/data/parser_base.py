"""Shared parser interface and compact presentation JSON helpers."""

from __future__ import annotations

import json
import time
from collections import Counter
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
from src.data.import_sets import ImportSet


ProgressCallback = Callable[[float], None]


@dataclass(frozen=True)
class SeriesDefinition:
    name: str
    navigator_group: str
    unit: str
    visibility: str = "visible"
    navigator_metadata: list[dict[str, str]] = field(default_factory=list)
    max_sample_interval_ms: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    transform: dict[str, Any] = field(default_factory=dict)
    original_unit: str = ""
    state_labels: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.navigator_group}/{self.name}" if self.navigator_group else self.name

    @property
    def visible(self) -> bool:
        return self.visibility.lower() != "hidden"


class ProgressReporter:
    """Rate-limit parser progress while preserving a final completion event."""

    def __init__(self, callback: ProgressCallback | None, interval_seconds: float = 1.0) -> None:
        self.callback = callback
        self.interval_seconds = max(0.0, float(interval_seconds))
        self.last_report_at: float | None = None
        self.last_fraction = 0.0

    def report(self, fraction: float, *, force: bool = False) -> None:
        if self.callback is None:
            return
        fraction = max(self.last_fraction, min(1.0, max(0.0, float(fraction))))
        self.last_fraction = fraction
        now = time.monotonic()
        if not force and self.last_report_at is not None and now - self.last_report_at < self.interval_seconds:
            return
        self.callback(fraction)
        self.last_report_at = now

    def finish(self) -> None:
        self.report(1.0, force=True)


class DataParser(ABC):
    """Base class implemented by every parser next to its JSON file."""

    name = ""
    priority = 1000
    extensions: frozenset[str] = frozenset()
    format_name = ""

    def __init__(self, config_path: str | Path, config: dict[str, Any] | None = None) -> None:
        self.config_path = Path(config_path)
        self.config = config if config is not None else read_json_file(self.config_path)
        self.navigator_metadata = parse_navigator_metadata(self.config)
        self.series_definitions = parse_series_definitions(self.config, self.navigator_metadata)
        self._series_by_path = {
            (definition.navigator_group, definition.name): definition
            for definition in self.series_definitions
        }
        self._series_by_name: dict[str, list[SeriesDefinition]] = {}
        for definition in self.series_definitions:
            self._series_by_name.setdefault(definition.name, []).append(definition)

    @abstractmethod
    def can_parse(self, input_path: str | Path) -> bool:
        """Return whether this parser understands the file."""

    @abstractmethod
    def parse(
        self,
        input_path: str | Path,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Parse one source file into the common series dictionary."""

    def import_set(self, input_path: str | Path) -> ImportSet | None:
        """Optionally describe the files parsed together with this input.

        ``parse`` may be called with any member and returns all series in the set.
        Returning None preserves the single-file importer contract.
        """
        return None

    def find_series_definition(self, name: str, navigator_group: str = "") -> SeriesDefinition | None:
        exact = self._series_by_path.get((navigator_group, name))
        if exact is not None:
            return exact

        same_name = self._series_by_name.get(name, [])
        if len(same_name) == 1:
            return same_name[0]
        return None

    def make_series(
        self,
        name: str,
        navigator_group: str,
        time_values: Iterable[Any],
        values: Iterable[Any],
        *,
        time_unit: str = "s",
        time_reference: str = "unix",
        description: str | None = None,
        source: str | None = None,
        time_sorted: bool = True,
    ) -> tuple[str, dict[str, Any]] | None:
        definition = self.find_series_definition(name, navigator_group)
        if definition is None:
            return None

        return self.make_series_from_definition(
            definition,
            time_values,
            values,
            time_unit=time_unit,
            time_reference=time_reference,
            description=description,
            source=source,
            time_sorted=time_sorted,
        )

    def make_series_from_definition(
        self,
        definition: SeriesDefinition,
        time_values: Iterable[Any],
        values: Iterable[Any],
        *,
        time_unit: str = "s",
        time_reference: str = "unix",
        description: str | None = None,
        source: str | None = None,
        time_sorted: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        """Build the canonical payload for an already-resolved definition."""

        payload = {
            "unit": definition.unit,
            "description": description or definition.name,
            "source": source or definition.name,
            "navigator_group": definition.navigator_group,
            "navigator_name": definition.name,
            "visible": definition.visible,
            "time": np.asarray(time_values, dtype=np.float64),
            "values": np.asarray(values),
            "time_unit": time_unit,
            "time_reference": time_reference,
            "time_sorted": time_sorted,
        }
        if definition.max_sample_interval_ms is not None:
            payload["max_sample_interval_ms"] = definition.max_sample_interval_ms
        if definition.min_value is not None:
            payload["min_value"] = definition.min_value
        if definition.max_value is not None:
            payload["max_value"] = definition.max_value
        if definition.transform:
            payload["transform"] = definition.transform
        if definition.original_unit:
            payload["original_unit"] = definition.original_unit
        if definition.state_labels:
            payload["state_labels"] = definition.state_labels
        payload["navigator_metadata"] = definition.navigator_metadata or self.navigator_metadata
        return definition.key, payload

    def add_series(
        self,
        output: dict[str, dict[str, Any]],
        name: str,
        navigator_group: str,
        time_values: Iterable[Any],
        values: Iterable[Any],
        **kwargs: Any,
    ) -> None:
        series = self.make_series(name, navigator_group, time_values, values, **kwargs)
        if series is None:
            return
        key, payload = series
        output[key] = payload


def read_json_file(config_path: str | Path) -> dict[str, Any]:
    with Path(config_path).open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{config_path} must contain a JSON object.")
    return data


def parse_series_definitions(
    config: dict[str, Any],
    default_navigator_metadata: list[dict[str, str]] | None = None,
) -> list[SeriesDefinition]:
    definitions: list[SeriesDefinition] = []
    raw_sections = config.get("sections", [])
    if not isinstance(raw_sections, list):
        raise ValueError("Parser JSON must contain a list named 'sections'.")

    for section in raw_sections:
        if not isinstance(section, dict):
            raise ValueError("Each parser JSON section must be an object.")
        section_name = str(section.get("name", "<unnamed>"))
        raw_series = section.get("series", [])
        if not isinstance(raw_series, list):
            raise ValueError(f"Section {section_name!r} must contain a list named 'series'.")

        for item in raw_series:
            if not isinstance(item, dict):
                raise ValueError(f"Section {section_name!r} series entries must be objects.")
            name = str(item.get("name", "")).strip()
            navigator_group = str(item.get("group", "")).strip()
            unit = str(item.get("unit", "")).strip()
            visibility = str(item.get("visibility", "visible")).strip().lower()
            if not name:
                raise ValueError(f"Section {section_name!r} has a series entry without a name.")
            if visibility not in {"visible", "hidden"}:
                raise ValueError(
                    f"Section {section_name!r} series {name!r} has invalid visibility {visibility!r}."
                )
            if "navigator_metadata" in item:
                metadata = item["navigator_metadata"]
                if metadata is None:
                    metadata = []
                if not isinstance(metadata, list):
                    raise ValueError(f"Section {section_name!r} series {name!r} has invalid navigator metadata.")
                metadata = validate_navigator_metadata(metadata)
            else:
                metadata = list(default_navigator_metadata or [])
            max_sample_interval_ms = item.get("max_sample_interval_ms")
            if max_sample_interval_ms is not None:
                try:
                    max_sample_interval_ms = float(max_sample_interval_ms)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Section {section_name!r} series {name!r} has an invalid max sample interval."
                    ) from exc
                if not np.isfinite(max_sample_interval_ms) or max_sample_interval_ms <= 0:
                    raise ValueError(
                        f"Section {section_name!r} series {name!r} max sample interval must be positive."
                    )
            min_value = _optional_finite_config_value(item.get("min_value"), "minimum", section_name, name)
            max_value = _optional_finite_config_value(item.get("max_value"), "maximum", section_name, name)
            if min_value is not None and max_value is not None and min_value >= max_value:
                raise ValueError(
                    f"Section {section_name!r} series {name!r} minimum must be less than its maximum."
                )
            transform = item.get("transform", config.get("series_transform_defaults", {}))
            if transform is None:
                transform = {}
            if not isinstance(transform, dict):
                raise ValueError(f"Section {section_name!r} series {name!r} has invalid transform metadata.")
            transform = dict(transform)
            if str(transform.get("source", "")) == "$self":
                transform["source"] = name
            raw_state_labels = item.get("state_labels", {})
            if raw_state_labels is None:
                raw_state_labels = {}
            if not isinstance(raw_state_labels, dict):
                raise ValueError(f"Section {section_name!r} series {name!r} has invalid state labels.")
            state_labels = {str(key): str(value) for key, value in raw_state_labels.items()}
            definitions.append(SeriesDefinition(
                name, navigator_group, unit, visibility, metadata,
                max_sample_interval_ms, min_value, max_value, dict(transform),
                str(item.get("original_unit", unit)).strip(), state_labels,
            ))

    key_counts = Counter(definition.key for definition in definitions)
    duplicate_keys = sorted(key for key, count in key_counts.items() if count > 1)
    if duplicate_keys:
        raise ValueError(f"Duplicate series definition(s): {', '.join(duplicate_keys)}")
    return definitions


def parse_navigator_metadata(config: dict[str, Any]) -> list[dict[str, str]]:
    navigator = config.get("navigator", {})
    if navigator is None:
        return []
    if not isinstance(navigator, dict):
        raise ValueError("Parser JSON navigator configuration must be an object.")
    return validate_navigator_metadata(navigator.get("series_metadata", []))


def validate_navigator_metadata(raw_metadata: Any) -> list[dict[str, str]]:
    if raw_metadata is None:
        return []
    if not isinstance(raw_metadata, list):
        raise ValueError("Navigator series_metadata must be a list.")
    metadata: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for item in raw_metadata:
        if not isinstance(item, dict):
            raise ValueError("Navigator metadata entries must be objects.")
        column_id = str(item.get("id", "")).strip()
        title = str(item.get("title", column_id)).strip()
        source = str(item.get("source", "")).strip()
        display_format = str(item.get("format", "text")).strip()
        if not column_id or not title or not source:
            raise ValueError("Navigator metadata entries require id, title, and source.")
        if column_id in seen_ids:
            raise ValueError(f"Duplicate navigator metadata id: {column_id}")
        seen_ids.add(column_id)
        metadata.append({"id": column_id, "title": title, "source": source, "format": display_format})
    return metadata


def _optional_finite_config_value(
    value: Any,
    label: str,
    section_name: str,
    series_name: str,
) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Section {section_name!r} series {series_name!r} has an invalid {label} clipping value."
        ) from exc
    if not np.isfinite(result):
        raise ValueError(
            f"Section {section_name!r} series {series_name!r} {label} clipping value must be finite."
        )
    return result
