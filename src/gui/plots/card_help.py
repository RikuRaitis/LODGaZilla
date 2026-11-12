"""Mouse actions shown by each card's help button.

This is the single description of how the mouse works in cards. Whenever a
card's mouse behaviour changes, update its rows here (see AGENTS.md).
"""

from __future__ import annotations

from html import escape

# Every card, in its frame.
COMMON = (
    ("Top strip: drag", "Move the card. Maps, videos and images can be dropped beside each other; "
                        "a Video card dropped on a Multi-series card's legend adds its video as a filmstrip."),
    ("Bottom edge: drag", "Change the card's height."),
    ("Click", "Make this the active card."),
    ("Click the clock icon", "Set the card's time offset; it only moves what the card shows. Not on image cards."),
    ("Wheel", "Scroll the plot workspace."),
)

# Shared by the time cards: Line, Multi-series, Sample interval, Event/state and Frequency.
_TIME = (
    ("Left-click", "Place the red first marker."),
    ("Right-click", "Place the green second marker. The area between the markers is the selection."),
    ("Double-click (left / right)", "Remove the first / second marker."),
    ("Drag a marker line", "Move that marker."),
    ("Drag the plot", "Pan time."),
    ("Ctrl+wheel over the plot", "Zoom time."),
    ("Drag the time axis", "Pan time."),
    ("Right-click the time axis", "Choose real date/time or time from log start."),
)
_Y_AXIS = (
    ("Drag the left axis", "Pan the Y axis."),
    ("Ctrl+wheel over the left axis", "Zoom the Y axis."),
)
_VALUES = (
    ("Hover a values box", "Show the series' full values and, with a selection, its statistics."),
    ("Click a values box", "Keep the details open to select and copy them; click outside to close."),
)
_LEGEND = (
    ("Drag a legend row", "Reorder the series, or move it to another card."),
    ("Right-click a legend row", "Remove the series."),
    ("Click a legend color", "Change the series color."),
)
_GROUP_LEGEND = (
    ("Drag a legend axis row", "Move that source to another axis or card; hold Ctrl to swap two axes in this card."),
    ("Drag a legend group heading", "Reorder the group, or move it to another card."),
    ("Right-click a group heading", "Remove the group."),
    ("Right-click a legend axis row", "Plot the source's values or its time."),
    ("Click a group color", "Change the group color."),
)
_POINT_MARKERS = (
    ("Left-click", "Place the red first marker on the nearest point."),
    ("Right-click", "Place the green second marker on the nearest point. The stretch between the markers is "
                    "the selection."),
)

MOUSE_HELP = {
    "line": _TIME + _Y_AXIS + _VALUES + _LEGEND,
    "interval": _TIME + _Y_AXIS + _VALUES + _LEGEND,
    "event": _TIME + _VALUES + _LEGEND,
    "frequency": _TIME + _Y_AXIS + (
        ("FFT settings button", "Change the FFT window, scale and strongest-frequency line."),
    ) + _VALUES + _LEGEND,
    "multi": (
        ("Left-click a trace", "Select it and place the red first marker."),
        ("Drag a trace up or down", "Move that series' lane."),
        ("Click a filmstrip; drag it up or down", "Select it; move the filmstrip lane."),
        ("Left-click empty space", "Deselect and place the red first marker."),
        ("Clock icon with a trace or filmstrip selected", "Set the time offset of that series only."),
        ("Ctrl+wheel over the left axis", "Scale the selected series, or all numeric series if none is selected."),
    ) + tuple(row for row in _TIME if row[0] != "Left-click") + (
        ("Drag a filmstrip legend row", "Move the video to another Multi-series card."),
    ) + _VALUES + _LEGEND,
    "xy": _POINT_MARKERS + (
        ("Double-click (left / right)", "Remove the first / second marker."),
        ("Drag the plot", "Pan."),
        ("Right-drag the plot", "Zoom each axis."),
        ("Ctrl+wheel over the plot", "Zoom."),
    ) + _VALUES + _GROUP_LEGEND,
    "xyz": _POINT_MARKERS + (
        ("Double-click (left / right)", "Remove the first / second marker."),
        ("Drag the view", "Rotate the camera."),
        ("Ctrl+drag the view", "Pan the camera."),
        ("Ctrl+wheel over the view", "Zoom the camera."),
        ("Reset camera button", "Return to the initial view."),
    ) + _VALUES + _GROUP_LEGEND,
    "gnss": _POINT_MARKERS + (
        ("Double-click", "Remove the first marker."),
        ("Right-click twice quickly", "Remove the second marker."),
        ("Drag the map", "Pan."),
        ("Shift+drag the map", "Zoom to the dragged box."),
        ("Ctrl+wheel over the map", "Zoom."),
        ("Street / Satellite", "Change the map type."),
    ) + _VALUES + _GROUP_LEGEND,
    "gnss_3d": _POINT_MARKERS + (
        ("Double-click", "Remove the first marker."),
        ("Right-click twice quickly", "Remove the second marker."),
        ("Drag the map", "Pan."),
        ("Right-drag the map", "Tilt and rotate; drag upward to tilt toward the ground."),
        ("Ctrl+wheel over the map", "Zoom."),
    ) + _VALUES + _GROUP_LEGEND,
    "video": (
        ("Drag the video (360°)", "Look around."),
        ("Ctrl+drag the video", "Roll the view."),
        ("Ctrl+wheel over the video", "Change the field of view."),
        ("Double-click the video", "Reset the view."),
        ("Click or drag the timeline", "Seek. The timeline shows the markers and the selection."),
        ("Sync button", "Show or hide the sync buttons and the offset field."),
        ("Set data marker button", "Use the red marker's time as the sync point (shown purple everywhere)."),
        ("Set video marker button", "Use the current video position; with both set, the sync offset is saved."),
        ("Offset field arrows", "Nudge the sync offset by 0.1 s; it is saved once it stops changing."),
    ),
    "image": (),
}


def mouse_help_html(title: str, topic: str | None) -> str:
    """The card's mouse actions, then those of every card, as a two-column table."""
    rows = MOUSE_HELP.get(topic or "", ()) + COMMON
    cells = "".join(
        f"<tr><td style='padding:2px 10px 2px 0'><b>{escape(action)}</b></td><td>{escape(effect)}</td></tr>"
        for action, effect in rows
    )
    return f"<b>{escape(title)}: mouse actions</b><table style='margin-top:4px'>{cells}</table>"
