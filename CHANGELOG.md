# Changelog

All notable changes to LODGaZilla are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/); see [Releasing](docs/Releasing.md).

## [Unreleased]

## [1.0.0-beta.3] - 2026-11-13

### Security

- The AI consent question and the README now say plainly what the AI can do on your
  computer: on Windows, the Python Claude runs can read your files and reach the internet;
  Codex's commands cannot reach the internet. Codex is recommended for files from others.
- Claude may use Git only to look (status, diff, log, show), and its file tools can no
  longer read known credential folders such as `.ssh` or Git and cloud sign-ins.

### Added

- A plugin added while LODGaZilla runs, such as one made with Create plugin, can be enabled
  in Settings > Plugins at once, without a restart. A restart is still needed to install a
  plugin's new Python packages, and to change the UI code of a plugin that is already running.
- Create plugin copies the attached files to a folder named after the plugin in the data
  folder and, once the plugin has an importer, imports them with it after each answer,
  showing the series found or the error in the AI panel.
- Magic drop asks the enabled importer plugins first. If one reads the files, it imports
  them without AI and nothing is sent; afterwards it asks whether to keep the result,
  improve that plugin with the AI or create a new plugin. When the AI imports the files
  and finds them close to an installed plugin's format, it suggests updating that plugin.
- Magic drop accepts a format description, such as a document or source code, dropped
  together with the data: the AI uses it, and it is kept without an H5 file of its own.
- For developers: synthetic test files in many formats (`tests/AI_demodata`, made by
  `tools/create_ai_demodata.py`) and live AI tests of Magic drop and Create plugin with
  Claude and Codex (`tools/run_ai_tests.py`, run only on purpose: they spend tokens).

### Changed

- The video card's Mute button sits next to the Volume slider.
- Workspaces no longer save the Plot navigator buttons (linking, legends, card controls,
  time axis, snapping); they keep your own preferences. Older workspaces still open.
- While cards are open, Open workspace, Open recent workspace and closing the window ask
  whether to save the cards first.
- Double-clicking a folder generates H5 files only when the folder holds data files of
  its own; a parent folder only expands or collapses.

### Fixed

- A series is found by the key its importer gave it, so a key unlike its group and name
  (as an AI-written importer may use) plots, and a bare name never shows another group's
  series.
- Plot navigator trims are saved in workspaces.
- Event/state cards stay responsive with dense data in view, and new H5 files need about a
  quarter of the LOD data they did (no levels finer than 16 samples per bucket).
- Create plugin copies attached files without freezing the window.
- Stop, and closing LODGaZilla, now end an import that the AI's parser is running in Magic
  drop or Create plugin, instead of letting it run for up to 10 minutes.
- Code written by the AI (Magic drop and Create plugin's trial imports) no longer gets the
  GitHub and Bitbucket tokens, and Create plugin's commits run no Git hooks.
- A fresh installation uses the intended defaults again: compressed H5 caches, the system
  theme and Navigator diagnostics on.
- Double-clicking a parent folder no longer starts H5 generation.
- Complex values are refused with a clear message instead of keeping only their real part.
- Map cards keep their values boxes, and 3D plot cards their traces, while reloading for a
  Sample limit or trim.
- Event/state lanes can no longer be dragged out of place by their names.
- LODGaZilla starts even when unrelated installed Python packages conflict.
- An intermittent crash: a delayed update (such as a legend's repositioning) could still run
  after its card was closed, and PySide then corrupted memory reporting the error.
- A plugin installation no longer waits for the next start when Windows briefly holds the
  downloaded files.
- The 3D Map's terrain no longer goes beyond the map tiles' zoom, so MapLibre drapes the map
  correctly when zoomed in close (no more "cannot calculate elevation" warnings).
- H5 generation builds each LOD level from the level below, so long series need far less
  memory; without a conversion, `value_raw` is no longer a second copy of the values.
- Series whose names become the same H5 name (such as "Speed (km/h)" and "Speed [km/h]")
  are both written instead of failing the whole file.
- Time that a parser reports as sorted is checked, so a clock reset no longer gives wrong
  ranges.
- The Navigator reads each series' Sample loss and Clipping warning from the file's series
  index instead of walking the file.
- Editing folder metadata no longer fails while the Navigator is reading the file.
- Interrupted audio conversions no longer leave files behind, and LODGaZilla's own working
  files are no longer offered to the importers.
- A plugin folder can no longer take the place of an installed Python package in plugin jobs.
- X/Y, Map and 3D Map cards read long series in bounded pieces, so very long logs no longer
  run out of memory when plotted.
- Cards save their time axis choice (real date/time or time from log start) in workspaces.
- Sample loss and Clipping areas are shown for every series of a Line or Multi-series card,
  not only the first, and no longer slow down panning.
- A snapping marker on a Line card snaps to the nearest sample of any of its series.
- Event/state lanes stay fast with dense data, and keep their legend while panning.
- X/Y cards keep their values boxes and their graphs while reloading; 3D plot markers and
  Multi-series filmstrips no longer redo all their work on each marker move or pan step.
- A saved Sample limit the selector does not list is shown as itself; the Help button no
  longer misses a click after its popup closed.
- Event/state cards show no values outside the Plot navigator trim.
- A malformed workspace file reports the cards it cannot read instead of failing to open.
- The internal log length setting applies at once, not after a restart.
- Recent folders and workspaces whose path contains "..." are kept.
- Saving Settings repaints the cards and refreshes the Navigator only when their settings
  changed.
- The Navigator filter, the Plot navigator bar drag and progress updates no longer read the
  disk on every keystroke, mouse move or event; moving a busy audio file is retried in the
  background instead of pausing the window.
- Stop and the time limit end everything an AI agent started, not only the agent itself.
- In the AI panel, Enter outside Create plugin keeps the typed text as Magic drop hints
  instead of discarding it.
- A queued Magic drop goes to the data folder it was dropped into, even if the data
  folder changes meanwhile.
- Saving the Settings dialog no longer withdraws an AI consent given while it was open.
- Finding the Claude and Codex CLIs no longer blocks the window; after Sign in, the
  provider that is signing in is checked again.
- Map cards draw tracks at Sample limits of 200k points and above.
- 3D plot cards no longer repaint continuously while idle.
- Values boxes on 3D plot and Map cards come back when their point returns to view.
- Times stored in milliseconds, microseconds or nanoseconds (written out) plot at the
  right scale.

## [1.0.0-beta.2] - 2026-11-12

### Added

- Settings > Plugins looks for plugins at `https://github.com/RikuRaitis` by default,
  where the GPX, PicoScope CSV and UBX GNSS importers are published.

## [1.0.0-beta.1] - 2026-11-12

The first public beta. LODGaZilla's history starts here; the plugins are released
separately.

### Changed

- Workspace files are called LODGaZilla workspaces (`*.umw`); existing ones still open.

### Known issues

- The full test suite can crash with a heap corruption error in
  `tests/test_gui_plot_widget.py`; see Code_review.md. The test modules pass on their
  own.

## [0.2.0] - 2026-11-12

A preview release on the way to the public beta.

### Changed

- New defaults from daily use: legends, Plot navigator, time axis, sample loss and
  clipping areas, marker linking and snapping start switched off; values boxes show
  in the active card; wider markers and taller Multi-series lanes.

### Added

- A version scheme: the version in the About dialog and the log, and the version
  that saved them in workspace and H5 files.
- Create plugin: the AI builds a plugin with you in `plugin/<id>`, its own Git
  repository, in a conversation that continues from question to question.
- Codex as a second AI provider for Magic drop and Create plugin.
- Magic drop and Create plugin may read web pages, such as format specifications;
  switch it off per provider in Settings.
- AI panel: Magic drop and Create plugin drop boxes; the panel remembers whether it
  is open.
- Draw style selector on cards that draw lines: Auto, Lines or Points.

### Fixed

- Multi-series cards draw event and state series marked only by
  `event_state_kind` as labelled bars, as card selection already did; labelled
  events stay separate and their labels no longer overlap.
