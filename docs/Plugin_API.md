# LODGaZilla plugin API 1

The host owns discovery, settings presentation, H5 cache writing, LOD generation,
background scheduling and the Navigator. Plugins own source formats, device
commands, export operations and their own preferences. The host must run and its
core tests must pass with no plugin checkouts present.

## Repository layout

Each plugin repository root contains `plugin.json`, implementation files,
`requirements.txt`, `constraints.txt`, `README.md` and `tests/`. Importers also
contain `importer.json`. Plugins live in separate repositories, public or private;
the public ones are listed in the README. A private plugin needs the user's own Git
access to its repository.

An installed checkout goes directly under `plugin/<id>/`. The host reads only
immediate-child manifests, never manifests inside tests or other nested folders.
The application ignores the entire `plugin/` directory in Git. These are ordinary
independent clones, not submodules: cloning the host does not fetch any plugins.
Each plugin README gives its clone URL and installation folder. Work on a plugin
with `git -C plugin/<id> ...`; commit and push it in its own repository.
`LODGAZILLA_PLUGIN_ROOT` overrides this directory for development/test processes.
The setup helper accepts the matching `--plugin-root PATH` argument.

## Manifests and compatibility

```json
{
  "id": "example_importer",
  "title": "Example importer",
  "version": "0.1.0",
  "api_version": 1,
  "capabilities": ["importer"],
  "entry_point": "importer.py"
}
```

Discovery reads metadata without importing Python implementations. The host checks
API compatibility and direct requirements. An incompatible or broken plugin is
reported and does not prevent other plugins or existing caches from working.
Plugin IDs are stable lowercase identifiers; repository display names can differ.
API 1 is the compatibility promise, independently of plugin release versions.

UI plugins use capability `ui` and a `plugin.py` entry point exporting `PLUGIN_CLASS`.
The class receives `(services, log)` on first enable. It can implement `start()`,
`stop()`, `launch()`, `handle_event(name, message)`, `operation_parameters(operation)`
and `add_folder_context_actions(menu, folder)`.

The host builds the plugin menus and icons from the manifest; plugins do not add
menu entries for their main actions in code:

```json
{
  "launcher": { "title": "Example device", "icon": "icon.svg" },
  "operations": {
    "Example export": {
      "worker": "worker.py:run",
      "kind": "export",
      "title": "Example CSV",
      "scopes": ["folder", "batch"]
    }
  }
}
```

- `launcher` (optional): the plugin has a window the user can open. While the plugin
  is enabled, the Plot navigator's Plugins group shows its icon; a click calls
  `launch()`. `title` defaults to the plugin title and is the icon's tooltip. `icon`
  is an SVG in the plugin folder, drawn in a 25 × 25 view box; write its main
  color as `currentColor` and the host draws it in its own icon color for the light
  or dark theme. Without `icon` a generic plugin icon is shown.
- `operations`: see [Registered background jobs](#registered-background-jobs). The
  object form adds where the host offers the operation.

`add_folder_context_actions(menu, folder)` is for folder actions that are not a
registered operation; they are added at the end of the folder's context menu and
should also be named `Plugin: …`. `add_menu_actions` and `custom_menu` are retired;
the host no longer calls or reads them.
Implementations may use relative imports for their own modules. They must not
modify the host's global import path or import sibling plugins.

## Supported imports and services

Production plugin code imports host features through `src.contracts`:

| Module | Supported facilities |
| --- | --- |
| `importer` | `DataParser`, series definitions, progress reporter, generic GNSS helpers |
| `plugin` | `PluginServices`, API version |
| `jobs` | Serializable job requests, worker limits and shared process-pool helper |
| `metadata` | Folder metadata reads and transactional writes |
| `settings` | Plugin defaults and atomic local settings writes |
| `widgets` | Shared recent-folder selector for Qt plugin dialogs |

The UI services object supplies `parent_widget`, `selected_folder()`, `data_folder()`,
`recent_folders()`, `set_data_folder(folder)`, `refresh_navigator()`,
`log(message, level)` and `submit_job(operation, folder, recursive=False, parameters=None)`.
The parent widget is for dialog ownership; it is not permission to depend on
application internals. Navigation and Qt operations run on the GUI thread.

An importer implements `DataParser.can_parse` and `parse`. The result uses the
existing common series dictionary: time/value arrays, explicit time units and
reference, physical units, names and presentation metadata. The host writes H5;
plugins must not duplicate the host H5/LOD implementation. Prefer explicit units
and conversions over plot-side knowledge of a source format.

See [H5 format and importer data contract](H5_Format.md) for the complete payload
and file layout, shared/per-series timestamps, units, state labels, diagnostics,
LOD, metadata and runnable examples. Use that guide as the format reference when
developing an importer manually or with an AI agent.

## Registered background jobs

A UI plugin can declare `"operations": {"Example export": "worker.py:run"}`; the
plugin then starts the job itself with `services.submit_job`. The object form
`{"worker": "worker.py:run", "kind": "export", "title": "Example CSV", "scopes": ["folder", "batch"]}`
also lets the host offer it:

| Field | Meaning |
| --- | --- |
| `kind` | `import` or `export`; the menu entry reads `Plugin: Export Example CSV`. |
| `title` | The name in that entry; defaults to the operation name. |
| `scopes` | `folder`: the folder context menu, for that folder. `batch`: the context menu of a virtual group or several selected folders (run on each folder), and the Navigator's **Batch** menu (the whole data folder, recursively, after a confirmation). |

Before a host-started job, the host calls the plugin's optional
`operation_parameters(operation)`; the returned dictionary becomes
`request.parameters`.
The worker is an immediate-child Python file defining
`run(request, events, cancel_event)`. It receives a serializable `PluginJobRequest`:
folder, recursion, process-slot budget and plugin-owned parameters. No Qt objects
or GUI callbacks cross the process boundary. Worker module names remain importable
by nested spawned pools. Pool work must respect `request.process_slots`.

The host owns cancellation, process-tree cleanup, shared worker slots and conflicts
between overlapping jobs with the same plugin/operation identity. A worker must
check cancellation during discovery and between work units. Existing progress
tuples are supported:

- `("message", text)`
- `("process_started", folder, process_id, source_name, total)`
- `("process_progress", folder, process_id, source_name, completed, total)`
- `("process_eta", folder, process_id, source_name, remaining_seconds)`
- `("process_finished", folder, process_id, source_name, success)`
- `("finished", completed_count, failed_count, error_text)`

Plugin-specific measurements use `("plugin_event", name, payload)` and are delivered
to the originating plugin's `handle_job_event(name, payload)` on the GUI thread,
including completion of work that was already running when a plugin was disabled.
The host does not interpret calibration data or persist plugin-specific values.

## Dependencies and settings

Each plugin declares its direct Python dependencies as named PEP 508 requirements,
one per line in `requirements.txt`. Comments and environment markers are supported.
Do not put credentials, VCS URLs, pip options or nested requirements includes there.
`constraints.txt` pins the tested environment, including transitive dependencies;
it does not replace the supported ranges in requirements. External device tools
are documented and validated by their owning plugin, not installed by the host.
The initial pins apply to Windows/Python 3.11 via environment markers. Other
platforms and interpreters resolve requirement ranges until validated pins are added.

`tools/setup_environment.py` resolves core and installed-plugin dependencies together
in the Python installation running the helper, checks declared requirements and
runs `pip check`. It does not create a virtual environment. Its ignored stamp
includes the interpreter, dependency files, constraints and manifest set. It does
not update Git repositories. API-incompatible plugins are excluded from dependency
setup and remain unavailable in the application.

`load_plugin_settings(folder, defaults)` reads `settings.local.json`, falling back
to committed `settings.json`. `save_plugin_settings` atomically writes only the
ignored local file. Plugins keep their settings keys and defaults. Metadata writes
go through `src.contracts.metadata` so concurrent edits retain the host's protection.

## Test ownership and execution

Core tests use synthetic importer/UI fixtures for discovery, H5 writing, scheduling,
settings and lifecycle behavior. Plugin-specific parser/device/export tests live
in each plugin's `tests/` (the runner also accepts `Tests/`). Mixed review regression modules have been split by
ownership. Generic GNSS math remains tested in the host; format-to-H5 integrations
remain with their plugins. Core test imports must not reference installed plugins.

```powershell
python HOST/tools/run_tests.py --host HOST --isolated
python HOST/tools/run_tests.py --host HOST --plugin PLUGIN_CHECKOUT --isolated
```

The runner snapshots the host without plugins, installs only the chosen plugin
under its stable ID in that snapshot, and supplies `LODGAZILLA_HOST` to tests.
Tests resolve their own plugin resources relative to their file. The supplied host
checkout can be a pinned release or the candidate host change. `--all-plugins`
adds installed siblings for integration checks; omission proves independence.

CI should run the core and each public plugin against the same pinned host; a
private plugin is tested the same way where its repository is accessible. Keep
private fixtures and logs in their private repositories. Native graphics and physical-device
tests need appropriate runners and remain separate from headless tests.

## Runtime discovery and installation

Settings > Plugins accepts GitHub account and Bitbucket Cloud workspace URLs.
Repository names must start with `LODGaZilla-` (case insensitive) and expose a root
`plugin.json`. The manifest ID, rather than the repository suffix, determines the
installation directory. A supported API is required before Install/Update is
offered; required entry points and dependency declarations are validated after
download without importing plugin code.

`src/plugins/repositories.py` uses the providers' read-only APIs, paginates results,
and pins candidates to a default-branch commit. GitHub discovery includes both
public owner repositories and private repositories accessible to the authenticated
user. Authentication comes from Git's credential helper or the documented token
environment variables; credentials are never stored in settings or pending requests.
Git never prompts during a check. With a Bitbucket source, Settings > Plugins offers a
sign-in that lets Git Credential Manager show its own sign-in once and saves the result with
`git credential approve`; a Bitbucket OAuth token from it is sent as a Bearer token.
API reference: [GitHub repositories](https://docs.github.com/en/rest/repos/repos),
[GitHub contents](https://docs.github.com/en/rest/repos/contents),
[Bitbucket repositories](https://developer.atlassian.com/cloud/bitbucket/rest/api-group-repositories/),
[Bitbucket source](https://developer.atlassian.com/cloud/bitbucket/rest/api-group-source/).

`PluginUpdates` owns one spawned worker for network/Git work, caches its results for
the Settings dialog, and shuts down its process tree with the main window. Checks
never install anything. Explicit downloads go under `plugin/.pending/`; the startup
dependency helper applies them before selecting requirements. Existing checkouts
receive fast-forward updates only, after rechecking their origin, revision, local
changes, and ignored-file collisions. Runtime plugin modules are never hot-reloaded.
The next process loads the installed revision; new plugins remain disabled until
the user enables them. `python -m src.app.lodgazilla` finishes pending installations
and installs their dependencies before importing application libraries.
