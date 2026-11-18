"""Navigator plot actions populated from the shared card planner."""

from PySide6.QtWidgets import QMenu
from src.data.plot_policy import CARD_TITLES, PlotSource, compatible_cards, plan_cards
from src.jobs.plot_drop import SelectionInspection, inspect_menu_sources


def context_sources(navigator, fallback):
    getter = getattr(navigator, "selected_plot_sources", None)
    sources = getter() if callable(getter) else None
    if not isinstance(sources, (list, tuple)):
        sources = [PlotSource("series", path, name) for path, name in navigator.selected_series_entries()]
    if any((source.kind, source.path, source.name) == (fallback.kind, fallback.path, fallback.name)
           for source in sources):
        return sources
    return [fallback]


def _add_gnss_actions(menu, workspace, coordinates, boundary=None):
    for kind, title in (("gnss", CARD_TITLES["gnss"]), ("gnss_3d", CARD_TITLES["gnss_3d"])):
        items = coordinates.get(kind)
        if not items:
            continue
        action = menu.addAction(title, lambda checked=False, items=items, kind=kind:
                                workspace.open_plot_inputs(items, kind))
        action.setData("detected_" + kind)
        if boundary is not None:
            menu.insertAction(boundary, action)


def populate_plot_menu(menu, workspace, sources, *, group=False):
    """Show a responsive menu while H5 metadata is inspected in the background."""
    if group:
        plot_action = menu.addAction("Loading card choices…", lambda: workspace.open_plot_sources(sources))
        plot_action.setEnabled(False)
        boundary = None
    else:
        placeholder = menu.addAction("Loading card choices…")
        placeholder.setEnabled(False)
        # Insert actions before this boundary, preserving attachment actions below.
        boundary = menu.addSeparator()

    def ready(result):
        items, coordinates = result
        plans = plan_cards(items)
        kinds = tuple(dict.fromkeys(plan.kind for plan in plans))
        title = " + ".join(CARD_TITLES[kind] for kind in kinds)
        if group:
            plot_action.setText(f"{title} (default)")
            plot_action.setEnabled(True)
            _add_gnss_actions(menu, workspace, coordinates)
            plot_action.setData("default")
            return
        menu.removeAction(placeholder)
        if any(item.source.kind in {"series", "video"} for item in items):
            active_action = menu.addAction("Active card", lambda: workspace.plot_in_active_card(items))
            active_action.setData("active")
            active_action.setEnabled(workspace.active_card_accepts(items))
            menu.insertAction(boundary, active_action)
        action = menu.addAction(f"{title} (default)", lambda: workspace.open_plot_inputs(items))
        action.setData("default")
        menu.insertAction(boundary, action)
        _add_gnss_actions(menu, workspace, coordinates, boundary)
        for kind in compatible_cards(items):
            if kinds == (kind,):
                continue
            title = CARD_TITLES[kind]
            if kind in coordinates:
                title += " (selected series)"
            action = menu.addAction(title,
                                    lambda checked=False, kind=kind: workspace.open_plot_inputs(items, kind))
            action.setData(kind)
            count = sum(kind in item.supported for item in items)
            if count < len(items):
                action.setToolTip(f"{count} of {len(items)} selected items; remaining items open in their default cards")
            menu.insertAction(boundary, action)
        numeric = [item.source.entry for item in items if item.numeric]
        if len(numeric) == len(items) == 3:
            targets = [plot for plot in workspace.plots() if getattr(plot, "drop_card_type", None) == "xyz"]
            if targets:
                # Parented to the menu: one from addMenu(title) is deleted with its Python wrapper.
                submenu = QMenu("Existing 3D cards", menu)
                menu.addMenu(submenu)
                for index, plot in enumerate(targets):
                    submenu.addAction(f"3D card {index + 1} ({len(plot.traces)} traces)",
                                      lambda checked=False, plot=plot: plot.add_trace(numeric))
                menu.insertAction(boundary, submenu.menuAction())
        menu.setToolTipsVisible(True)

    inspection = SelectionInspection(menu, sources, ready, inspect=inspect_menu_sources, empty_result=((), {}))
    menu.aboutToHide.connect(inspection.cancel)
