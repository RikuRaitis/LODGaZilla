# Changelog

All notable changes to LODGaZilla are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/); see [Releasing](docs/Releasing.md).

## [Unreleased]

### Fixed

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
