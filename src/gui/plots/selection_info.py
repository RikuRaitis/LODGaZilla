"""Shared selected-time duration for time-based cards and their values boxes."""


class SelectionDurationInfo:
    """Shows the selection's Δt in the card's expanded values boxes."""

    def __init__(self, canvas, value_boxes, metadata, *, connect=True):
        self.canvas = canvas
        self.value_boxes = value_boxes
        self.metadata = metadata
        self.duration: float | None = None
        if connect:
            canvas.selection_changed.connect(self.show)
        canvas.selection_cleared.connect(self.clear)

    def show(self, left, right):
        metadata = self.metadata()
        if metadata is None:
            self.clear()
            return
        # Absolute/canonical coordinates already use seconds; relative caches
        # without a log start keep their native ms/us/etc. coordinates.
        scale = 1.0 if metadata.is_unix_time or metadata.log_start_unix_seconds is not None else metadata.seconds_per_unit
        self.duration = abs(right - left) * scale
        self.value_boxes.set_selection_duration(self.duration)

    def clear(self):
        self.duration = None
        self.value_boxes.set_selection_duration(None)
