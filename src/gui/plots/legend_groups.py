"""Shared axis assignments and group operations for all grouped card legends."""

from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, Signal

from src.data.xyz import AxisSource
from src.gui.plots.base import SERIES_COLORS, first_unused_color, series_legend_name


GROUP_MIME_TYPE = "application/x-lodgazilla-series-group"
GROUP_COLORS = SERIES_COLORS


@dataclass
class SeriesGroup:
    axes: tuple[AxisSource | None, ...]
    color: str
    sources: tuple[AxisSource, ...] = ()


def group_display_name(group: SeriesGroup) -> str:
    """A group's sources, such as "run1/Speed / run1/Power", as its Legend rows name them."""
    names = [series_legend_name(axis.path, axis.name) + (" [time]" if axis.component == "time" else "")
             for axis in getattr(group, "axes", ()) if axis is not None]
    return " / ".join(names)


class LegendGroups(QObject):
    """Own group cardinality, empty slots, ordering and source assignments."""

    changed = Signal()

    def __init__(self, parent, roles, *, title="Graph", allow_time=False):
        super().__init__(parent)
        self.roles = tuple(roles)
        self.title = title
        self.allow_time = allow_time
        self.groups: list[SeriesGroup] = []

    @property
    def width(self):
        return len(self.roles)

    def entries(self):
        return list(dict.fromkeys((axis.path, axis.name) for group in self.groups
                                  for axis in group.axes if axis is not None))

    def insert(self, axes, index=None, color=None):
        """Pack incoming axes into target-sized groups, padding the final group."""
        axes = tuple(axes)
        if not axes or not any(axis is not None for axis in axes):
            return
        if any(axis is not None and axis.component == "time" for axis in axes) and not self.allow_time:
            raise ValueError("This card uses data values for its axes")
        index = len(self.groups) if index is None else max(0, min(index, len(self.groups)))
        for offset in range(0, len(axes), self.width):
            chunk = axes[offset:offset + self.width]
            if not any(axis is not None for axis in chunk):
                continue
            chunk += (None,) * (self.width - len(chunk))
            sources = tuple(dict.fromkeys(replace(axis, component="value") for axis in chunk if axis is not None))
            # A position-based default could repeat a neighbouring group's color.
            group_color = color or first_unused_color(
                GROUP_COLORS, (group.color for group in self.groups), index
            )
            self.groups.insert(index, SeriesGroup(chunk, group_color, sources))
            index += 1
        self.changed.emit()

    def axis(self, key):
        return self.groups[key // self.width].axes[key % self.width]

    def _set_axis(self, key, axis):
        group = self.groups[key // self.width]
        axes = list(group.axes)
        axes[key % self.width] = axis
        group.axes = tuple(axes)
        if axis is not None:
            group.sources = tuple(dict.fromkeys((*group.sources, replace(axis, component="value"))))

    def replace(self, key, axis):
        if not 0 <= key < len(self.groups) * self.width:
            return
        if axis is not None and axis.component == "time" and not self.allow_time:
            return
        self._set_axis(key, axis)
        self.changed.emit()

    def swap(self, source, target):
        if source == target or not all(0 <= key < len(self.groups) * self.width for key in (source, target)):
            return
        first, second = self.axis(source), self.axis(target)
        self._set_axis(source, second)
        self._set_axis(target, first)
        self.changed.emit()

    def fill_empty_axes(self, axes):
        """Assign incoming sources to vacant roles before appending new groups."""
        remaining = list(axes)
        assigned = False
        for key in range(len(self.groups) * self.width):
            if not remaining:
                break
            if self.axis(key) is None:
                self._set_axis(key, remaining.pop(0))
                assigned = True
        if assigned:
            self.changed.emit()
        return remaining

    def remove(self, index):
        if 0 <= index < len(self.groups):
            del self.groups[index]
            self.changed.emit()

    def reorder(self, source, boundary):
        if not 0 <= source < len(self.groups):
            return
        group = self.groups.pop(source)
        self.groups.insert(max(0, min(boundary - int(boundary > source), len(self.groups))), group)
        self.changed.emit()

    def set_color(self, index, color):
        if 0 <= index < len(self.groups):
            self.groups[index].color = color
            self.changed.emit()
