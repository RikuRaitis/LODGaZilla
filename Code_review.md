# LODGaZilla code review

Second full review of the main app, tools and tests, 2026-11-13, at version
1.0.0-beta.2 plus the fixes since. Four read-only reviewers covered the app shell and AI,
the plot cards, data and H5 generation, and tools, tests and plugins. I checked their
findings before acting on them. The findings under "Fixed in this review" were fixed and
tested. Plugin findings are in `Code_review_plugin.md`, which is not committed because it
covers private plugins (see AGENTS.md). Line numbers are as of the review and may move.

## Summary

The intermittent full-suite crash is fixed: 31 deferred calls could run after their
widget was deleted, and PySide 6.11 corrupted the heap while reporting the resulting
error. Under full page heap, the test suite crashed at the same place every time before
the fix; afterwards five full runs completed without a crash.

The code is in good shape for a beta. The host/plugin boundary is clean, H5 writes are
atomic, and the shared marker code, bounded loaders and diagnostic areas from the first
review hold up. The main risk now is the AI agent's permissions.

**Fix as soon as possible**, in this order:

1. **AI agent permissions are broader than documented** (security; see AI integration).
   Tokens are withheld from the agent and now also from the code it writes; the tool
   allow-list still lets Python and Git do anything.
2. **A series whose importer key is not `group/name` can't be plotted, or plots another
   series** (see Data). This is a silent wrong result, and Magic drop's agent can produce
   such files.
3. **Plot navigator trims are not saved in workspaces** (see Plot cards), which breaks
   "workspaces reopen exactly as saved".

## Lines of code

Non-blank, non-comment lines; whole files in brackets.

| Component | Files | Code lines (all lines) |
| --- | ---: | ---: |
| `src/gui/plots` (cards) | 42 | 12,336 (14,390) |
| `src/gui` (Navigator, AI panel, settings) | 10 | 4,837 (5,510) |
| `src/app` (main window, settings, startup) | 11 | 4,675 (5,465) |
| `src/jobs` (H5 writer, imports, Magic drop, Create plugin) | 19 | 2,820 (3,376) |
| `src/data` (H5 reading, workspaces, folder info) | 15 | 1,908 (2,353) |
| `src/plugins` (discovery, install, updates) | 8 | 875 (1,018) |
| `src/ai` (Claude, Codex, Magic drop, plugin builder) | 8 | 714 (901) |
| `src/contracts` (public plugin API) | 7 | 75 (91) |
| `src/version.py`, `src/__init__.py` | 2 | 37 (47) |
| **App total** | **122** | **28,277 (33,151)** |
| `tests` | 67 | 14,575 (16,808) |
| `tools` (setup, test runner, demo data, screenshots) | 13 | 2,463 (2,897) |
| Public plugins (gpx, picoscope, pyubx2) | 15 | 1,297 (1,540) |

Counted before tonight's fixes, which change the totals by a few dozen lines. The private
plugins are counted in `Code_review_plugin.md`.

## Fixed in this review

| What | Where |
| --- | --- |
| **The full-suite heap crash** (0xc0000374, access violations in shiboken6). 31 deferred calls, `QTimer.singleShot(delay, callable)`, had no context object, so they still ran after their widget was deleted (first caught: a floating legend's `reposition`). The call raised "Internal C++ object already deleted" inside Qt's event handling, and PySide 6.11 corrupts the heap while reporting that error. Found with full page heap and `cdb`: every page-heap run crashed in `test_filmstrip.py` before the fix; afterwards five full page-heap runs completed without a crash. Every timer now names its owner; `tests/test_timer_context.py` keeps it so. | `src/` (31 calls), `tests/test_timer_context.py`, AGENTS.md |
| A plugin installation was postponed to the next start when a virus scanner or the indexer briefly held the fresh checkout ("Access is denied"); the move is retried for a few seconds. | `src/plugins/installation.py` |
| The AI-written importer in Magic drop's and Create plugin's trial import ran with the GitHub and Bitbucket tokens, which the agent itself doesn't get. It now gets the agent's environment. | `src/ai/magic_drop.py` `run_trial_import` |
| Create plugin's commit after each answer ran Git hooks, which the agent can write into the plugin's `.git`; hooks are now disabled for it, as for plugin installs. | `src/ai/plugin_builder.py` `commit_changes` |
| The committed `app_settings.json` differed from the code defaults in 8 values (dark theme, uncompressed H5 caches, other LOD settings, diagnostics off). It now matches; a test compares the whole file. | `app_settings.json`, `tests/test_app_settings.py` |
| Double-clicking a parent folder (such as a year folder) still started H5 generation: the `_info.json.lock` file counted as data. | `src/data/navigator_data.py` `folder_needs_h5_generation` |
| Complex values were written with only their real part; they are now refused with a clear message. `H5_Format.md` no longer says the writer trusts `time_sorted`. | `src/jobs/import_data.py`, `docs/H5_Format.md` |
| Plugin problems were logged again at every look for new plugins (twice at start, again at each Settings Apply). | `src/plugins/manager.py` `refresh` |
| Four test modules built main windows that started the real plugin update check against GitHub (slow, network-dependent, and could use up the GitHub API limit). | `test_core_startup`, `test_workspace_file`, `test_demo_data_app`, `test_review_app` |
| `tools/run_tests.py` wrote every run's app logs to one shared folder (rotation failures, mixed logs) and left its host copy behind when interrupted. | `tools/run_tests.py` |
| A `pip check` conflict between unrelated packages in the same Python stopped LODGaZilla from starting; it is now a warning, and LODGaZilla's own requirements are still checked strictly. | `tools/setup_environment.py` |
| The Map card's values boxes did not come back after a Sample limit change or trim. | `src/gui/plots/gnss.py` |
| The 3D plot went blank while reloading for a Sample limit or trim; it now keeps its traces until the new ones arrive, as X/Y does. | `src/gui/plots/xyz.py` |
| Dragging the lane names of an Event/state card moved the lanes, although the card has no Y zoom; that drag is now off where Y zoom is off. | `src/gui/plots/interactive.py` |
| The 3D Map's altitude read ignored cancellation, so a replaced load still read the whole altitude range. | `src/gui/plots/gnss_3d.py` |
| Every right-click on a Plot navigator bar or a legend row left one more menu alive. | `src/gui/plots/navigator.py`, `src/gui/plots/legend.py` |
| Dead code: `nearest_sample_indices`, two identical branches in the Event/state legend (and their extra sort per reload), two unreachable branches in `refresh_marker_info_boxes`. | `src/data/xyz.py`, `src/gui/plots/event_state.py`, `src/app/lodgazilla.py` |
| Stop and Close did not stop the AI-written parser in Magic drop and Create plugin's example import: it ran outside the process tree, up to 10 minutes per file, and kept running after the window closed. It now runs with `popen_in_tree`, and Stop, Close or the timeout end it and everything it started. | `src/ai/magic_drop.py` `run_trial_import`, `src/jobs/plugin_examples.py` |
| Two tests failed only under page heap's slowness: a 2-second wait, and a test deleting a file its loader thread was still closing. | `tests/test_audio_media.py`, `tests/test_review_plots.py` |

Also new since the first review, as you asked: a plugin added while LODGaZilla runs (such
as one made with Create plugin) can be enabled at once in Settings > Plugins.

Not done: about 99 old `%TEMP%\lodgazilla-tests-*` folders from earlier test runs (about
2 GB) are still left for you to delete.

## Open findings

Priority: **P1** fix as soon as possible, **P2** soon, **P3** when convenient.

### AI integration

- **P1 — Agent permissions broader than documented.** `Bash(python:*)` lets the agent run
  any Python, which can read or write anywhere and reach the network even with web access
  off; Read/Glob/Grep are unrestricted; `PLUGIN_TOOLS` allows any Git command though the
  instructions say "Git to look". A dropped file with prompt-injection text could make the
  agent read `~/.ssh` or CLI credentials and send them out. Options: restrict Git to
  `git status`/`diff`/`log`, run the trial import as a fixed command instead of free
  Python, and document the real confinement in AGENTS.md and the consent text
  (`src/ai/claude_cli.py:26-28`).
- **P2 — Create plugin copies the attached files on the GUI thread**
  (`lodgazilla.py` `_copy_plugin_examples` → `plugin_builder._copy_numbered`), comparing
  identical files byte by byte. Attaching a multi-GB log freezes the window.
- **P3 — A cancelled Create plugin question loses its text and attachments**
  (`src/gui/ai/panel.py:542` clears them before the main window can cancel on consent or
  the plugin ID dialog).
- **P3 — One Create plugin per session, and no way back to an earlier plugin**:
  `plugin_chat_id` is never reset, and after a restart `check_plugin_id` rejects the
  existing folder.
- **P3 — `PluginManager.refresh` drops a removed plugin whose UI is running**, so its
  `stop()` never runs (edge case: its folder deleted while LODGaZilla runs).

### Plot cards

- **P2 — Plot navigator trims are not saved in workspaces.** A trim lives only in
  `PlotWorkspace._plot_navigator_ranges` (`lodgazilla.py`); neither the workspace nor the
  card records save it. Save it in the card record and reapply it after the time ranges.
- **P2 — Event/state lanes read every sample in view and sort it on the GUI thread on each
  pan** (`event_state.py` `load_event_state_series`, `_looks_like_binary_state` twice per
  lane, the label pass). Drawing is bounded since the first review; reading and sorting
  are not. Decimate in the loader, or compute the state flag once per load.
- **P3 — The 3D plot uses seconds for relative times; Line and X/Y use the native unit**
  (`src/data/xyz.py:149-150`), so a linked marker on a millisecond series without a log
  start misses the 3D trace.
- **P3 — The Map card turns its track lists into arrays and searches them with `argmin` on
  every marker move** (`gnss.py` `_track_samples`, `_update_selection`), including every
  video frame during playback; X/Y and the 3D plot use a binary search.

### Data and H5 generation

- **P2 — The series index `name` is the importer's dict key**, and lookups only know the
  physical path, `navigator_name` and `group/name` (`import_data.py:499`,
  `h5_series.py:56-59`). A key such as `"acc_x"` for group `Accelerometer`, name `X` can't
  be plotted (`KeyError`); a leaf name can resolve to another group's series of the same
  name, which then plots silently. Write the full navigator name as the index `name` and
  register full names before leaf aliases; existing caches need regenerating.
- **P2 — LOD levels on very long series.** Level 1 is about 1.5× the raw data and is held
  in memory; `_normalise_series` copies time and values, and a `$self` transform
  normalises the same series twice. A 500M-sample series can run the worker out of memory.
  Start the levels at a minimum bucket size (as the audio writer does) and write levels
  block by block.
- **P3 — One duplicate output stops the whole batch** (`import_batch.py:92-96`): two sources
  with the same stem in one folder stop every folder's import.
- **P3 — The stale-temp cleanup can remove Magic drop's temp file on Linux and macOS**
  (`import_batch.py:120-139`), since both use the same `.tmp` pattern; Windows protects the
  open file.

### Architecture

- **`src/app/lodgazilla.py` is about 3,800 lines.** `PlotWorkspace` is GUI code with H5
  reads inside the wiring module; move it to `src/gui/plots/workspace.py`, keeping the
  card stand-ins that keep startup fast. `LODGaZillaWindow` also holds import scheduling
  and progress, attachments, AI and demo data.
- **Layering**: `src/jobs/import_data.py` and `src/ai/trial_import.py` import
  `src.app.settings`; `InternalLogPanel` (a widget) lives in `src/app`; `src/gui/theme.py`
  imports app settings.
- **Plugin tests import host internals** (`src.jobs.import_data`, `src.gui.plots`,
  `src.plugins`). Allowed, but moving `PlotWorkspace` or `import_data` will break plugin
  tests.
- **The plugin boundary is clean**: plugin production code imports only `src.contracts`,
  `src/` names no plugin, manifests are validated, installs are pinned.

### Tests

- **P3 — 19 test modules still add `src/` to `sys.path`**, leftovers that make `app`,
  `data`, `plugins` and others importable as top-level names, so a stray import could load
  a second copy of a module.
- **P3 — Seven modules have their own `wait_for`** (`test_filmstrip`,
  `test_frequency_plot`, `test_gui_plot_policy`, `test_point_markers`,
  `test_review_plots`, `test_selection_duration`, `test_value_boxes`) instead of
  `qt_wait_until`; `test_frequency_plot.py` asserts wall-clock time under 1 s.
- **Untested code**: `src/jobs/agent_run.py`, `src/ai/agent_common.py`,
  `src/jobs/plugin_repository.py`, `src/gui/plots/video/audio.py`, `src/app/startup.py`
  (Splash screen); `card_markers.py` only through the card tests.

### Tools

- **P3 — `create_large_h5_fixture.py` reads whole datasets back into memory** before
  building LOD levels; `--reuse-binary` doesn't check the reused file's size.
- **P3 — `take_readme_screenshots.py` pairs cards with picture names by position**, so one
  card that isn't restored shifts every later picture's name; it leaves its settings
  folder in `%TEMP%` on an unexpected error.
- **P3, needs a decision — `LODGaZilla.bat` works only from the checkout folder.** A
  shortcut with another start folder fails with "No module named src". Fixing it
  (`cd /d "%~dp0"`) means changing the AGENTS.md rule that keeps the file to one line.

## Accepted design decisions

**Claude C9, series metadata reads:** When a series is assigned, Line, Multi-series and Sample interval cards still read its time axis metadata and its sample-loss and clipping ranges on the GUI thread (`SeriesPlotWidget._set_primary_series()`). These are small attribute and range reads. Linked time axes and the time axis format use the metadata as soon as a card is created, so loading it later would spread waiting states to that code for little gain.

## Test run

2026-11-13, with full page heap enabled for `python.exe` (Windows Debugging Tools; a
memory error stops the program where it happens):

- Before the timer fix: every page-heap run crashed in `test_filmstrip.py`; without page
  heap, a full run crashed about half the time.
- After it: four full page-heap runs (697 tests, about 23–31 minutes each) completed
  without a crash. Two had one failure each, from page heap's slowness (both fixed); two
  passed completely.
- A fifth full page-heap run with every change in this review except the last (stopping
  trial imports, tested on its own): passed completely, no crash and no failure.
- The modules touched tonight (plugins, settings, AI, Navigator, cards, data, the four
  offline window modules) pass on their own.
- pyflakes: only the deliberate re-exports remain (`src/contracts/jobs.py`,
  `branding.version_text`, `claude_cli.agent_environment`). vulture is not installed.
