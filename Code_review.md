# LODGaZilla — remaining review findings

Updated 2026-11-12 against the current working tree. This document consolidates the unresolved and partially resolved findings from the Claude and Codex reviews. 

## Open findings

**Full test suite crashes in `test_gui_plot_widget` (found 2026-11-12, to fix before the public beta):**
`python -m unittest discover -s tests` ends with `Windows fatal exception: code
0xc0000374` (heap corruption) in `PlotSeriesLoadingTests.tearDown`
(`tests/test_gui_plot_widget.py`, line 40), during
`test_converts_device_relative_time_to_a_common_real_time_axis`. The tearDown disposes,
closes and deletes every top-level `SeriesPlotWidget` in the shared `QApplication`,
including widgets that earlier test modules left behind.

- Seen in two full runs in a row on 2026-11-12, after the Create plugin work (AI
  panel questions, `src/jobs/agent_run.py`, `src/jobs/plugin_chat.py`). The full run
  before that work passed all 663 tests. The first full run of the same day, before
  most of that day's changes, also stopped early without a result, likely the same crash.
- Not reproduced: the module alone passes (three runs), and so do the test modules
  before it in two halves together with it. Only the whole preceding sequence crashes.
- Without `-X faulthandler` the run can end with no result line and no error
  message, so check the log for `OK` or `FAILED`; `-X faulthandler` prints the stack.
- Next steps: bisect with growing prefixes of the module list; check whether
  earlier modules leave top-level `SeriesPlotWidget`s or pyqtgraph items alive
  (`deleteLater` without processing events); make the tearDown touch only the
  widgets its own tests created.

## Accepted design decisions

**Claude C9, series metadata reads:** When a series is assigned, Line, Multi-series and Sample interval cards still read its time axis metadata and its sample-loss and clipping ranges on the GUI thread (`SeriesPlotWidget._set_primary_series()`). These are small attribute and range reads. Linked time axes and the time axis format use the metadata as soon as a card is created, so loading it later would spread waiting states to that code for little gain.

## Validation limitation


