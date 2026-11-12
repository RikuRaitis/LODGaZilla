"""Inspect drop capabilities without reading samples on the GUI thread."""

from pathlib import Path

import h5py
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from src.data.plot_policy import PlotInput, PlotSource, compatible_cards, coordinate_identity, gnss_coordinate_sets


def series_card_types(path: Path, name: str) -> tuple[str, ...]:
    return inspect_source(PlotSource("series", Path(path), name)).supported


def inspect_source(source):
    if source.kind == "video":
        # A video opens in a Video card by default, or as a Multi-series filmstrip lane.
        return PlotInput(source, ("video", "multi"))
    if source.kind != "series":
        return PlotInput(source, (source.kind,))
    # Reuse the same metadata/name resolution as the actual card readers.
    from src.data.h5_series import _series_group_for_navigator_name

    try:
        with h5py.File(source.path, "r") as cache:
            group = _series_group_for_navigator_name(cache, source.name)
            return _inspect_series_group(source, group)
    except (OSError, KeyError, TypeError, ValueError):
        return PlotInput(source, ())


def _inspect_series_group(source, group):
    from src.data.h5_series import _attribute_text, is_event_state_group

    time_name = _attribute_text(group.attrs.get("time_dataset", "time_unix"))
    if (time_name not in group or group["value"].dtype.kind not in "biuf"
            or group["value"].ndim != 1):
        return PlotInput(source, ())
    state = is_event_state_group(group)
    name = _attribute_text(group.attrs.get("navigator_name", source.name))
    navigator_group = _attribute_text(group.attrs.get("navigator_group", ""))
    parent, role = coordinate_identity("/".join(part for part in (navigator_group, name) if part))
    options = (["multi", "line", "event", "interval"] if state else
               ["multi", "line", "frequency", "event", "interval"])
    # Grouped cards can start with one axis and be completed in the legend.
    options.extend(("xy", "xyz", "gnss", "gnss_3d"))
    return PlotInput(source, tuple(options), not state, role, parent)


def inspect_sources(sources):
    return tuple(inspect_source(source) for source in dict.fromkeys(sources))


def inspect_menu_sources(sources):
    """Inspect selections and their coordinate siblings using metadata only."""
    from src.data.h5_series import _navigator_series_names, _series_group_for_navigator_name

    items = inspect_sources(sources)
    candidates = list(items)
    families = {}
    for item in items:
        if item.source.kind == "series" and item.supported:
            families.setdefault(item.source.path, set()).add(item.coordinate_group)
    for path, parents in families.items():
        try:
            with h5py.File(path, "r") as cache:
                for name in _navigator_series_names(cache):
                    parent, role = coordinate_identity(name)
                    if parent not in parents or not role:
                        continue
                    try:
                        source = PlotSource("series", path, name)
                        candidates.append(_inspect_series_group(source, _series_group_for_navigator_name(cache, name)))
                    except (KeyError, TypeError, ValueError):
                        continue
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return items, gnss_coordinate_sets(candidates)


class _InspectionSignals(QObject):
    completed = Signal(object)


class _InspectionTask(QRunnable):
    def __init__(self, sources, inspect):
        super().__init__()
        self.sources = sources
        self.inspect = inspect
        self.signals = _InspectionSignals()

    def run(self):
        try:
            result = self.inspect(self.sources)
        except Exception:  # noqa: BLE001 - always complete so the owner is released.
            result = None
        self.signals.completed.emit(result)


class SelectionInspection(QObject):
    """Inspect metadata in a worker, delivering results on the owner's GUI thread."""
    def __init__(self, parent, sources, callback, *, inspect=inspect_sources, empty_result=()):
        super().__init__(parent)
        self.callback = callback
        self.empty_result = empty_result
        self.cancelled = False
        self.worker = _InspectionTask(tuple(sources), inspect)
        self.worker.signals.completed.connect(self._finished)
        QThreadPool.globalInstance().start(self.worker)

    def cancel(self):
        self.cancelled = True

    @Slot(object)
    def _finished(self, items):
        # A failed inspection still calls back, with an empty result.
        if items is None:
            items = self.empty_result
        if not self.cancelled:
            self.callback(items)
        self.deleteLater()


class DropMetadataSignals(QObject):
    completed = Signal(object, object)
    inspected = Signal(object, object)


class DropMetadataTask(QRunnable):
    def __init__(self, key, series, *, selection=False):
        super().__init__()
        self.key = key
        self.series = series
        self.selection = selection
        self.signals = DropMetadataSignals()

    def run(self):
        sources = (self.series if self.selection else
                   [PlotSource("series", Path(path), name) for path, name in
                    (self.series if isinstance(self.series, list) else [self.series])])
        items = inspect_sources(sources)
        options = compatible_cards(items)
        if not self.selection:
            options = tuple(kind for kind in options if all(kind in item.supported for item in items))
        self.signals.inspected.emit(self.key, items)
        self.signals.completed.emit(self.key, options)
