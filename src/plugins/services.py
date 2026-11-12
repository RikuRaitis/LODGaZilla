"""Host implementation of the small service interface passed to UI plugins."""
from __future__ import annotations

from pathlib import Path

from src.contracts.jobs import PluginJobRequest, configured_import_worker_count
from src.plugins.catalog import local_file


class ApplicationPluginServices:
    def __init__(self, window, descriptor):
        self._window = window
        self._descriptor = descriptor

    @property
    def parent_widget(self):
        return self._window

    def selected_folder(self):
        from src.gui.navigator.widget import ITEM_PATH_ROLE, ITEM_TYPE_ROLE, ROW_TYPE_FOLDER
        item = self._window.navigator.tree.currentItem()
        while item is not None:
            if item.data(0, ITEM_TYPE_ROLE) == ROW_TYPE_FOLDER:
                return Path(str(item.data(0, ITEM_PATH_ROLE)))
            item = item.parent()
        return None

    def data_folder(self):
        return self._window.navigator.data_folder()

    def recent_folders(self):
        return list(self._window.navigator.navigator_control_settings.recent_folders)

    def set_data_folder(self, folder):
        self._window.navigator.set_data_folder(str(folder))

    def refresh_navigator(self):
        self._window.navigator.refresh_tree(rescan=True)

    def log(self, message, level="info"):
        if level not in {"info", "warning", "error", "command", "reply"}:
            raise ValueError(f"Unknown log level: {level}")
        getattr(self._window.internal_logger, level)(message)

    def submit_job(self, operation, folder, *, recursive=False, parameters=None):
        filename, function = self._descriptor.operations[operation].split(":")
        entry = local_file(self._descriptor.path.parent, filename)
        limit = self._window.max_background_processes
        slots = limit if recursive else 1
        self._window.start_import(PluginJobRequest(
            Path(folder).resolve(), recursive, configured_import_worker_count(slots, limit),
            self._descriptor.plugin_id, operation, str(entry), function, dict(parameters or {}),
        ))
