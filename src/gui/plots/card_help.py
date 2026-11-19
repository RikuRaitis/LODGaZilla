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
    ("Click the card controls icon", "Make this the active card and show its controls, this help among them; "
                                     "they hide when another card becomes active."),
    ("Click the legend icon", "Show or hide this card's legend. The Plot navigator's Legends button sets every card."),
    ("Click the clock icon", "Set the card's time offset; it only moves what the card shows. Not on image cards."),
    ("Markers", "Placed only while the Plot navigator's Markers button is on."),
    ("Wheel", "Scroll the plot workspace."),
)

# Shared by the time cards: Line, Multi-series, Sample interval, Event/state and Frequency.
_TIME = (
    ("Left-click", "Place the red first marker."),
    ("Right-click", "Place the green second marker. The area between the markers is the selection, shaded "
                    "in its color (Settings, Plot)."),
    ("Double-click (left / right)", "Remove the first / second marker."),
    ("Drag a marker line", "Move that marker."),
    ("Drag the plot", "Pan time."),
    ("Ctrl+wheel over the plot", "Zoom time."),
    ("Center view button", "Show the card's whole time range again."),
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
_NAMES = (
    ("Rest the pointer on a series or legend row", "Show the series' whole name; it hides as soon as the pointer "
                                                   "moves. Set the delay in Settings, Plot."),
)
_FREQUENCY_LEGEND = (
    ("Drag the legend row", "Move the series to another card."),
    ("Right-click the legend row", "Remove the series and close the card."),
    ("Click the legend color", "Change the strongest-frequency line color."),
    ("Drop a series on the legend", "Show that series instead."),
)
# A left click also selects the series under it (series_style.SELECT_TOLERANCE_PIXELS).
_SELECT = (
    ("Left-click a series", "Select it: drawn wider, its Legend row highlighted. Elsewhere, select none."),
)
_POINT_MARKERS = (
    ("Left-click", "Place the red first marker on the nearest point."),
    ("Right-click", "Place the green second marker on the nearest point. The stretch of each trace between "
                    "the markers is the selection, drawn in its own color at the trace's width; Settings, Plot "
                    "sets its color and whether it is drawn on top, on its own trace or not at all."),
)
# Cards that draw every Nth sample and choose how series are drawn.
_SAMPLES = (
    ("Sample limit selector", "Set the most points drawn per graph, trace or track; it shows the share drawn."),
)
_DRAW_STYLE = (
    ("Auto / Lines / Points", "Draw the series the card's own way, as lines only, or as points only."),
)

MOUSE_HELP = {
    "line": _TIME + _SELECT + _Y_AXIS + _DRAW_STYLE + _VALUES + _LEGEND + _NAMES,
    "interval": _TIME + _SELECT + _Y_AXIS + _DRAW_STYLE + _VALUES + _LEGEND + _NAMES,
    "event": _TIME + _SELECT + _VALUES + _LEGEND + _NAMES,
    "frequency": _TIME + _Y_AXIS + (
        ("FFT settings button", "Change the FFT window, scale and strongest-frequency line."),
    ) + _VALUES + _FREQUENCY_LEGEND + _NAMES,
    "multi": (
        ("Left-click a trace", "Select it and place the red first marker."),
        ("Drag a trace up or down", "Move that series' lane."),
        ("Click a filmstrip; drag it up or down", "Select it; move the filmstrip lane."),
        ("Left-click empty space", "Deselect and place the red first marker."),
        ("Clock icon with a trace or filmstrip selected", "Set the time offset of that series only."),
        ("Ctrl+wheel over the left axis", "Scale the selected series, or all numeric series if none is selected."),
    ) + tuple(row for row in _TIME if row[0] != "Left-click") + (
        ("Drag a filmstrip legend row", "Move the video to another Multi-series card."),
    ) + _DRAW_STYLE + _VALUES + _LEGEND + _NAMES,
    "xy": _POINT_MARKERS + _SELECT + (
        ("Double-click (left / right)", "Remove the first / second marker."),
        ("Drag the plot", "Pan."),
        ("Right-drag the plot", "Zoom each axis."),
        ("Ctrl+wheel over the plot", "Zoom."),
        ("Center view button", "Show every graph again."),
    ) + _DRAW_STYLE + _SAMPLES + _VALUES + _GROUP_LEGEND + _NAMES,
    "xyz": _POINT_MARKERS + _SELECT + (
        ("Double-click (left / right)", "Remove the first / second marker."),
        ("Drag the view", "Rotate the camera."),
        ("Ctrl+drag the view", "Pan the camera."),
        ("Click an axis line", "Select it; click it again, or anywhere else, to deselect."),
        ("Ctrl+wheel over the view", "Stretch or shrink the selected axis; with none selected, zoom the camera."),
        ("Equal scale button", "Draw one metre the same length on every axis; latitude and longitude in metres."),
        ("Fit button", "Stretch every axis to fill the box again."),
        ("Center view button", "Return the camera to the whole trace."),
        ("Use shader", "Draw the traces at the Series line widths (otherwise 1 px)."),
        ("Axes selector", "Draw the axes through 0, 0, 0, or along the corner of the data box."),
        ("3D model checkbox or list", "Show a 3D model attached to the folder of the card's data "
                                      "(Attach, 3D model on the folder's menu)."),
        ("0, 0, 0 / GNSS buttons", "Place the model's anchor at the plot's 0, 0, 0 (east, north and up along "
                                   "X, Y and Z, in metres), or at its GNSS position on latitude and longitude axes."),
    ) + _DRAW_STYLE + _SAMPLES + _VALUES + _GROUP_LEGEND + _NAMES,
    "gnss": _POINT_MARKERS + _SELECT + (
        ("Double-click", "Remove the first marker."),
        ("Right-click twice quickly", "Remove the second marker."),
        ("Drag the map", "Pan."),
        ("Ctrl+drag with a track selected", "Move that track, in this card only; its legend heading says "
                                            "(moved). With none selected, pan."),
        ("Shift+drag with a track selected", "Rotate that track around its centre, in this card only. "
                                             "With none selected, zoom to the dragged box."),
        ("Reset position button", "Put every moved track back where it was recorded."),
        ("Ctrl+wheel over the map", "Zoom."),
        ("Street / Satellite", "Change the map type."),
        ("Center view button", "Show every track again."),
        ("Scale bar", "Show or hide the metric scale at the bottom right."),
    ) + _DRAW_STYLE + _SAMPLES + _VALUES + _GROUP_LEGEND + _NAMES,
    "gnss_3d": _POINT_MARKERS + _SELECT + (
        ("Double-click", "Remove the first marker."),
        ("Right-click twice quickly", "Remove the second marker."),
        ("Drag the map", "Pan."),
        ("Right-drag the map", "Tilt and rotate; drag upward to tilt toward the ground."),
        ("Ctrl+wheel over the map", "Zoom."),
        ("Map slider", "Fade the map; below 100% the track shows through the terrain, 0% hides the map."),
        ("Height slider", "Exaggerate the altitudes 1–10x."),
        ("Offset slider or field", "Raise or lower the track, in metres."),
        ("Follow ground", "Draw the track on the ground, ignoring its altitude."),
        ("Auto button", "Set the offset that puts the track's lowest point on the ground."),
        ("Center view button", "Show the whole track again."),
        ("Scale bar", "Show or hide the metric scale at the bottom right."),
    ) + _DRAW_STYLE + _SAMPLES + _VALUES + _GROUP_LEGEND + _NAMES,
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
