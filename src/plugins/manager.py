"""Discovery, settings, launchers, operations and event dispatch for application plugins."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.plugins.loader import load_module_from_path
from src.plugins.catalog import PLUGIN_ROOT, PluginDescriptor, PluginOperation, discover_plugins, plugins_disabled
from src.plugins.services import ApplicationPluginServices


class PluginManager:
    """Own plugin instances and keep application code unaware of plugin rules."""

    def __init__(
        self,
        main_window,
        *,
        log: Callable[[str], None] | None = None,
        plugin_root: str | Path = PLUGIN_ROOT,
    ) -> None:  # type: ignore[no-untyped-def]
        self.main_window = main_window
        self.log = log or (lambda _message: None)
        self.plugin_root = Path(plugin_root)
        # --no-plugins: no plugin is discovered, so none loads; saved enable settings are kept.
        self.descriptors = [] if plugins_disabled() else discover_plugins(plugin_root, log=self.log)
        # Plugin classes are instantiated only when first enabled, so disabled
        # plugins never run their constructors (for example a USB listener).
        self.plugins: dict[str, object] = {}
        self.enabled: dict[str, bool] = {}

    def refresh(self) -> list[str]:
        """Find plugins added or changed since start, such as one made with Create plugin.

        Importers and operations run in worker processes that load their code each time,
        so a new or changed one works once it is enabled. A plugin whose UI code already
        runs keeps its descriptor: changing that code still needs a restart. Returns the
        IDs of plugins that are new since the last look.
        """
        if plugins_disabled():
            return []
        known = {descriptor.plugin_id: descriptor for descriptor in self.descriptors}
        # Problems were logged when each plugin was first found; only new ones are logged here.
        found = discover_plugins(self.plugin_root)
        new = [descriptor for descriptor in found if descriptor.plugin_id not in known]
        for descriptor in new:
            if descriptor.error:
                self.log(f"Plugin {descriptor.title} unavailable: {descriptor.error}")
        self.descriptors = [known[descriptor.plugin_id] if descriptor.plugin_id in self.plugins else descriptor
                            for descriptor in found]
        return [descriptor.plugin_id for descriptor in new]

    def _instantiate(self, descriptor: PluginDescriptor) -> object | None:
        plugin = self.plugins.get(descriptor.plugin_id)
        if plugin is not None or "ui" not in descriptor.capabilities or descriptor.error:
            return plugin
        try:
            module = load_module_from_path(descriptor.entry_point, module_namespace="lodgazilla_plugin")
            plugin = module.PLUGIN_CLASS(ApplicationPluginServices(self.main_window, descriptor), self.log)
        except Exception as error:  # noqa: BLE001 - plugin boundary.
            self.log(f"Plugin {descriptor.title} could not start ({type(error).__name__}): {error}")
            return None
        self.plugins[descriptor.plugin_id] = plugin
        return plugin

    def _run_lifecycle(self, plugin_id: str, name: str) -> None:
        lifecycle = getattr(self.plugins.get(plugin_id), name, None)
        if callable(lifecycle):
            try:
                lifecycle()
            except Exception as error:  # noqa: BLE001 - plugin boundary.
                self.log(f"Plugin {plugin_id} lifecycle failed ({type(error).__name__}): {error}")

    def set_enabled(self, enabled: dict[str, object]) -> None:
        previous = dict(self.enabled)
        self.enabled = {
            descriptor.plugin_id: bool(enabled.get(descriptor.plugin_id, False)) and not descriptor.error
            for descriptor in self.descriptors
        }
        for descriptor in self.descriptors:
            plugin_id = descriptor.plugin_id
            was_enabled, now_enabled = bool(previous.get(plugin_id, False)), self.is_enabled(plugin_id)
            if was_enabled == now_enabled:
                continue
            if now_enabled and "ui" in descriptor.capabilities and self._instantiate(descriptor) is None:
                self.enabled[plugin_id] = False
                continue
            if plugin_id in self.plugins:
                self._run_lifecycle(plugin_id, "start" if now_enabled else "stop")

    def shutdown(self) -> None:
        """Stop every enabled plugin before the main window is torn down."""
        for plugin_id in list(self.plugins):
            if self.is_enabled(plugin_id):
                self._run_lifecycle(plugin_id, "stop")
        self.enabled = {plugin_id: False for plugin_id in self.enabled}

    def is_enabled(self, plugin_id: str) -> bool:
        return bool(self.enabled.get(plugin_id, False))

    def enabled_importers(self) -> list[PluginDescriptor]:
        """Enabled, loadable importer plugins."""
        return [descriptor for descriptor in self.descriptors
                if "importer" in descriptor.capabilities and self.is_enabled(descriptor.plugin_id)
                and not descriptor.error]

    def has_enabled_importer(self) -> bool:
        """Whether an enabled manifest-only importer plugin is available."""
        return any(
            "importer" in descriptor.capabilities and self.is_enabled(descriptor.plugin_id)
            for descriptor in self.descriptors
        )

    def launchers(self) -> list[PluginDescriptor]:
        """Enabled plugins with a window the user can open, in discovery order."""
        return [descriptor for descriptor in self.descriptors
                if descriptor.launcher is not None and self.is_enabled(descriptor.plugin_id)]

    def operations(self, scope: str) -> list[tuple[PluginDescriptor, PluginOperation]]:
        """Enabled plugins' import and export operations offered for ``scope`` ("folder")."""
        return [(descriptor, operation) for descriptor in self.descriptors
                if self.is_enabled(descriptor.plugin_id)
                for operation in descriptor.operation_specs if operation.kind and scope in operation.scopes]

    def run_operation(self, plugin_id: str, operation: str, folder: Path, *, recursive: bool = False,
                      parameters: dict | None = None) -> None:
        """Start a declared operation for the host; the plugin may add job parameters.

        ``parameters`` from the host, such as a Source data row's ``source_files``,
        take precedence over the plugin's own.
        """
        descriptor = next((item for item in self.descriptors if item.plugin_id == plugin_id), None)
        if descriptor is None or not self.is_enabled(plugin_id):
            return
        host_parameters, parameters = dict(parameters or {}), {}
        get_parameters = getattr(self.plugins.get(plugin_id), "operation_parameters", None)
        try:
            if callable(get_parameters):
                parameters = dict(get_parameters(operation) or {})
            parameters.update(host_parameters)
            ApplicationPluginServices(self.main_window, descriptor).submit_job(
                operation, folder, recursive=recursive, parameters=parameters)
        except Exception as error:  # noqa: BLE001 - plugin boundary.
            self.log(f"Plugin {plugin_id} operation {operation} failed ({type(error).__name__}): {error}")

    def launch(self, plugin_id: str) -> None:
        if not self.is_enabled(plugin_id):
            return
        plugin = self.plugins.get(plugin_id)
        if plugin is None:
            return
        launch = getattr(plugin, "launch", None)
        if callable(launch):
            try:
                launch()
            except Exception as error:  # noqa: BLE001 - plugin boundary.
                self.log(f"Plugin {plugin_id} launch failed ({type(error).__name__}): {error}")

    def dispatch_event(self, event_name: str, message: str) -> None:
        """Offer application events to enabled plugins only."""
        for plugin_id, plugin in self.plugins.items():
            if not self.is_enabled(plugin_id):
                continue
            handler = getattr(plugin, "handle_event", None)
            if not callable(handler):
                continue
            try:
                handler(event_name, message)
            except Exception as error:  # noqa: BLE001 - plugin boundary.
                self.log(f"Plugin {plugin_id} event failed ({type(error).__name__}): {error}")

    def add_folder_context_actions(self, menu, folder: Path) -> None:  # type: ignore[no-untyped-def]
        """Let enabled plugins extend a navigator folder context menu.

        The host deliberately owns the menu.  Plugins only add their own
        actions, which keeps plugin-specific file detection out of the
        navigator and lets disabled plugins disappear completely.
        """
        for plugin_id, plugin in self.plugins.items():
            if not self.is_enabled(plugin_id):
                continue
            add_actions = getattr(plugin, "add_folder_context_actions", None)
            if not callable(add_actions):
                continue
            try:
                add_actions(menu, folder)
            except Exception as error:  # noqa: BLE001 - plugin boundary.
                self.log(f"Plugin {plugin_id} folder menu failed ({type(error).__name__}): {error}")

    def dispatch_job_event(self, plugin_id: str, name: str, payload) -> None:
        # A job that was already running may finish after its plugin was disabled.
        handler = getattr(self.plugins.get(plugin_id), "handle_job_event", None)
        if callable(handler):
            try:
                handler(name, payload)
            except Exception as error:
                self.log(f"Plugin {plugin_id} job event failed: {error}")
