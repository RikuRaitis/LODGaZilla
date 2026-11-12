"""Shared default-card and minimum-card planning, independent of the GUI."""

from dataclasses import dataclass
from pathlib import Path


CARD_TITLES = {
    "multi": "Multi-series", "line": "Line", "frequency": "Frequency", "event": "Event/state",
    "interval": "Sample interval",
    "xy": "X/Y", "xyz": "3D", "gnss": "Map", "gnss_3d": "3D Map",
    "image": "Image", "video": "Video",
}
GROUP_WIDTHS = {"xy": 2, "xyz": 3, "gnss": 2, "gnss_3d": 3}
# Card types whose widgets set `supports_horizontal_layout` and can share a card row.
HORIZONTAL_CARD_TYPES = frozenset({"gnss", "gnss_3d", "image", "video"})
SELECTION_MIME_TYPE = "application/x-lodgazilla-selection"


@dataclass(frozen=True)
class PlotSource:
    kind: str
    path: Path
    name: str = ""
    folder: Path | None = None
    offset: float = 0.0

    @property
    def entry(self):
        return self.path, self.name

    def payload(self):
        return {"kind": self.kind, "path": str(self.path), "name": self.name,
                "folder": str(self.folder) if self.folder is not None else None, "offset": self.offset}

    @classmethod
    def from_payload(cls, payload):
        kind = payload["kind"]
        if kind not in {"series", "image", "video"}:
            raise ValueError("Unknown plot source")
        return cls(kind, Path(payload["path"]), str(payload.get("name", "")),
                   Path(payload["folder"]) if payload.get("folder") else None, float(payload.get("offset", 0)))


@dataclass(frozen=True)
class PlotInput:
    source: PlotSource
    supported: tuple[str, ...]
    numeric: bool = False
    coordinate_role: str = ""
    coordinate_group: str = ""


@dataclass(frozen=True)
class CardPlan:
    kind: str
    items: tuple[PlotInput, ...]


def coordinate_identity(name):
    """Recognize coordinate names within their logical Navigator group."""
    parent, _, leaf = name.casefold().rpartition("/")
    role = ("lat" if leaf in {"lat", "latitude", "lat_deg", "latitude_deg", "latitude_rad"} else
            "lon" if leaf in {"lon", "longitude", "lon_deg", "longitude_deg", "longitude_rad"} else
            "alt" if leaf in {"alt", "altitude", "altitude_m", "height", "height_m", "elevation", "elevation_m"} else "")
    return parent, role


def gnss_coordinate_sets(items):
    """Collect complete tracks, excluding unrelated series and incomplete families."""
    families = {}
    for item in items:
        if item.numeric and item.coordinate_role:
            family = families.setdefault((item.source.path, item.coordinate_group), {})
            family.setdefault(item.coordinate_role, item)
    result = {}
    for kind, roles in (("gnss", ("lat", "lon")), ("gnss_3d", ("lat", "lon", "alt"))):
        coordinates = tuple(family[role] for family in families.values()
                            if all(role in family for role in roles) for role in roles)
        if coordinates:
            result[kind] = coordinates
    return result


def default_series_card(items):
    if len(items) in (2, 3) and all(item.numeric for item in items):
        roles = {item.coordinate_role for item in items}
        families = {(item.source.path, item.coordinate_group) for item in items}
        expected = {"lat", "lon"} if len(items) == 2 else {"lat", "lon", "alt"}
        if roles == expected and len(families) == 1:
            return "gnss" if len(items) == 2 else "gnss_3d"
        return "xy" if len(items) == 2 else "xyz"
    return "multi"


def compatible_cards(items):
    return tuple(kind for kind in CARD_TITLES if any(kind in item.supported for item in items))


def activation_uses_card(items, card_kind, has_empty_axis=False):
    """Whether double-clicked items go into an active card of ``card_kind``.

    Only the items' default card is used, so a double-click never replaces a
    Frequency signal or starts a group in an unrelated card. A grouped card
    waiting for an axis is the exception: filling it completes the graph.
    """
    if card_kind not in compatible_cards(items):
        return False
    if has_empty_axis and card_kind in GROUP_WIDTHS:
        return True
    return any(plan.kind == card_kind for plan in plan_cards(items))


def _ordered_coordinates(kind, items):
    if kind not in {"gnss", "gnss_3d"}:
        return tuple(items)
    roles = ("lat", "lon") if kind == "gnss" else ("lat", "lon", "alt")
    remaining, ordered = list(items), []
    # Recognized tracks retain their coordinate meaning, regardless of tree order.
    for item in items:
        family = (item.source.path, item.coordinate_group)
        group = [next((candidate for candidate in remaining
                       if (candidate.source.path, candidate.coordinate_group) == family
                       and candidate.coordinate_role == role), None) for role in roles]
        if all(candidate is not None for candidate in group):
            ordered.extend(group)
            for candidate in group:
                remaining.remove(candidate)
    return tuple(ordered + remaining)


def _plans_for(kind, items):
    if not items:
        return []
    if kind in {"frequency", "image", "video"}:
        return [CardPlan(kind, (item,)) for item in items]
    return [CardPlan(kind, _ordered_coordinates(kind, items))]


def plan_cards(items, preferred=None):
    """Pack supported items together; use defaults for every remaining item."""
    items = tuple(dict.fromkeys(items))
    if preferred is not None:
        supported = tuple(item for item in items if preferred in item.supported)
        remaining = tuple(item for item in items if preferred not in item.supported)
        return _plans_for(preferred, supported) + plan_cards(remaining)
    series = tuple(item for item in items if item.source.kind == "series")
    plans = _plans_for(default_series_card(series), series)
    for item in items:
        if item.source.kind in {"image", "video"}:
            plans.extend(_plans_for(item.source.kind, (item,)))
    return plans
