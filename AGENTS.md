# LODGaZilla agent guide

This file stores shared vocabulary and common development principles for this
repository. Use these terms in discussions, task descriptions, documentation, and
code comments. Keep the vocabulary current when introducing or renaming UI
elements. Vocabulary alone does not request a code or UI rename.

## Vocabulary

### UI elements

| Common name | Meaning / code reference |
| --- | --- |
| Main window | The application window, `LODGaZillaWindow` in `src/app/lodgazilla.py`. |
| File bar | The icon row above the Navigator controls' group box, replacing the menu bar (the main window has none): **Open workspace**, **Open recent workspace** (a menu of the last ten), **Save workspace**, **Save workspace as** (`file_bar` in `NavigatorControlsWidget`). Ctrl+O, Ctrl+S and Ctrl+Shift+S are window shortcuts. |
| Navigator | The folder and data browser on the left, `DataNavigatorWidget`. Use this name for the data browser; qualify the separate Plot navigator explicitly. |
| Navigator controls | Below the File bar, the always-visible fields above the Navigator tree: the data folder field, whose dropdown lists **[Browse…]** first (opens a folder dialog; `RecentFolderComboBox.browse_requested`) and then the recent folders, Group by, and filter key/value, in one grid whose fields share their right edge; below them, centered above the tree, Refresh, the **Batch menu** (the plugin actions for every folder under the data folder, each confirmed first: **Plugin: Generate H5 files for all folders** and the enabled plugins' `batch` operations; `batch_button`, `batch_menu`, filled by `LODGaZillaWindow.populate_batch_menu`, confirmed by `confirm_data_folder_batch`), the **Sample loss** and **Clipping** buttons (switch the Navigator diagnostics' row styling on or off; `diagnostic_buttons`, saved in `navigator_diagnostics`) and Hide irrelevant series. Implemented by `NavigatorControlsWidget`. |
| Navigator tree / Tree widget | The hierarchical rows inside the Navigator, implemented by `CopyableTreeWidget`. Item types are listed below. |
| Tree item / Tree row | One item in the Navigator tree. Its type determines its available actions. |
| Data column | The tree column containing the item name or metadata key. |
| Value columns | The logical columns displaying metadata values or series details. Several logical columns are painted within one physical Qt Value column. |
| Column header | The heading for a tree column; its context menu controls column visibility and, for logical value columns, ordering. |
| Context menu | The menu opened by right-clicking an item or header. |
| Folder menu | A folder's context menu: Multi-series, **Attach** (Video…, Images…, Audio…) and Show in file manager, then the **plugin actions**, all named `Plugin: …` and always last: Plugin: Generate / update H5 files (disabled until an importer is enabled) and the enabled plugins' `folder` operations (`show_folder_context_menu`, `add_plugin_folder_actions`). On a virtual group or several selected folders the menu has only the plugin actions, for each visible folder under or selected (`folders_context_menu_requested`, `show_folders_context_menu`). |
| Plot area | The right-hand area containing the Plot navigator and Plot workspace. |
| Plot navigator | The overview and shared plot controls above the cards, `PlotNavigatorWidget`. Its first button group, the **Time axis selector**, shows the time axis on all time cards, only the bottom one, or none (`lodgazilla.time_axis_display`). With Plot views linked, its **View box** frames the time cards' visible range; dragging its edges zooms them and dragging its shaded top strip pans them; where a View box edge lies on the active card's bar, the bar takes precedence (`set_active_plot`) (`view_range_changed`, `PlotWorkspace.linked_view_range_changed`). Its **Legends button** and **Card controls button** hide every card's Legend and every card's Header controls including Offset; Close and Help stay (`show_legends`, `show_card_controls`; `PlotLegendWidget.setVisible`, `PlotBaseWidget.set_header_controls_shown`). Its last group, the **Plot navigator button**, hides or shows the overview below the toolbar (`navigator_button`, `set_expanded`, `plot_navigator_expanded`). At the right edge come the **Plugins group** (one icon per enabled plugin with a manifest `launcher`; a click opens its window; hidden when there are none; `plugin_bar`, `set_plugin_launchers`, `plugin_launch_requested`), the **Settings button** (gear, opens the Settings dialog; `settings_button`), the **Help button** (opens the **About dialog**: the LODGaZilla image, version (`APP_VERSION` in `src/version.py`, with `git describe` in a checkout; `version_text`), author and license, and **Open README**, which opens README.md in the system's Markdown viewer; `AboutDialog` in `src/gui/about_dialog.py`, `help_button`, `LODGaZillaWindow.show_help`, `open_readme`) and **Close all plots**. Each card's **bar** shows its time span; dragging a bar sideways changes that card's Time offset, up or down reorders cards, and dragging its ends trims the card's range. Right-clicking a bar offers **Reset offset** and **Reset selection** (the trimmed range). |
| Plot workspace | The scrollable area hosting cards, `PlotWorkspace`. |
| Card | One independently closable visualization in the Plot workspace. The implementation often calls a card a plot or widget, including image and video cards. |
| Card row | A workspace layout row containing one full-width card or compatible cards placed side by side. |
| Divider / Splitter handle | A draggable separator that changes the sizes of adjacent panels or side-by-side cards. Distinct from a card's bottom resize handle. |
| Plot canvas | The drawing and interaction area inside a plot card. |
| Legend | The series names and visual keys associated with a plot. |
| Legend insertion hint | The line marking where a dragged data series will be inserted into a card's legend. |
| Card drop hint | The bounding outline around an entire compatible card when hovering outside its legend during a data drag. Gaps between cards use a horizontal insertion line instead. |
| Legend group | A draggable heading, group color button, and axis rows for one X/Y graph, 3D trace, GNSS track, or 3D Map track. Shared presentation is in `PlotLegendWidget`; assignments and group operations are in `LegendGroups`. |
| Axis label | The X, Y, Z, Lat, Lon, or Alt role preceding an assigned series (or `empty`) in a grouped legend. |
| Group insertion hint | The horizontal line showing where a dropped series or group will create a new legend group. Incomplete groups wait for all required axes before drawing. |
| Legend replacement hint | The outline highlighting the axis row that will receive a dropped series. Same-card drops move by default; holding Ctrl swaps assignments, with dashed outlines on both rows and opposing arrows on the cursor. |
| Card drop selector | The delayed selector shown while dragging data between card rows; its sections offer compatible card types. |
| Marker | A selected time position used to inspect data. The red **first marker** is placed with a left click and the green **second marker** with a right click; a double click with the same button removes it; on time cards either can be dragged. On X/Y, 3D plot and map cards a marker is the point nearest the click, and Video cards show markers on the Timeline slider. With Markers linked, every card and the Plot navigator share both. Card types are listed in `TWO_MARKER_CARD_TYPES` in `src/app/lodgazilla.py`; shared identities and colors are in `src/gui/plots/markers.py`. |
| Values box | One series' values at one marker, placed right of the marker where the series crosses it: one line with the value and unit. Hovering shows its **details** (full values and, with a selection, its statistics); a click pins the details for copying until a click outside them. Implemented by `ValueBox`, `ValueDetails` and `ValueBoxLayer` in `src/gui/plots/value_boxes.py`; X/Y, 3D plot and map cards anchor one box per graph, trace or track at its marker point. |
| Selection / Range selection | The interval between the first and second markers, painted blue, used to inspect a span of data and its statistics. It exists only while both markers are placed. |
| Internal log panel | The collapsible application log beneath the main Navigator and Plot area, also written to the log file. Collapsed, it shows the latest message. It also takes the application's status messages, including the startup loading state: **Loading LODGaZilla…** while the plot card code loads and **Loading data folder …** while the Navigator reads its folder, each followed by a line when done (`_update_loading_status`, `DataNavigatorWidget.loading_changed`). The main window has no status bar. |
| AI panel | The panel for creating plugins with an AI agent, below the Navigator with a Divider between (`AIPanel` in `src/gui/ai/panel.py`, `left_splitter`). Minimized it is the one-line **AI Integration row**; clicking it, or dragging files over it, expands it. Expanded it opens at its minimum height (`EXPANDED_MIN_HEIGHT`); whether it is open is saved in `lodgazilla.ai_panel_expanded` and restored at start (default open). Its header holds the **Provider selector** (Claude or Codex), the sign-in status and **Sign in** (shown while not signed in). Below the header are two **drop boxes** side by side (`DropZone`, `drop_row`): **Magic drop** (`magic_drop_zone`; files start Magic drop, below) and **Create plugin** (`plugin_zone`; files are attached to the next question after a greeting, `attach_for_plugin`). A box is highlighted while files are dragged over it. Once one is used (`mode`), both shrink to one compact row above the AI conversation and AI input, and the panel grows to half the left side (`conversation_started`). While creating a plugin, files dropped on the AI conversation are attached too. Questions go to the chosen provider while creating a plugin (**Create plugin**, below); elsewhere text alone stays in the AI input as Magic drop hints, with a note (`HINTS_KEPT_NOTE`), and a question with files gets a note (`NOT_CONNECTED_REPLY`). |
| AI conversation | The AI panel's one combined view of user questions, their attached files and AI replies (`conversation`). |
| AI input | The box under the AI conversation (`input`). It starts as one line and grows with its text to 10 lines, overlaying the AI conversation's bottom (`AIInputFrame`). Enter sends (outside Create plugin it keeps the text as hints); Shift+Enter starts a new line. Its text goes to Magic drop as hints when files are dropped (`files_dropped`). Attached files show as removable chips above it (`AttachmentChip`). |
| Magic drop | Files dropped on the AI panel become H5 files: a new folder in the data folder gets copies of the files, the **provider** chosen in the AI panel writes a parser into a **draft**, LODGaZilla runs it in its own process (`python -m src.ai.trial_import`) and writes the H5 files and `_info.json` from the draft's `metadata.json`. Providers (`AgentProvider` in `src/ai/providers.py`): **Claude** (the installed Claude Code CLI, run with `-p`, `src/ai/claude_cli.py`; it may only read, write in the draft, run Python and, when allowed, read web pages, `MAGIC_DROP_TOOLS`, `WEB_TOOLS`) and **Codex** (the installed Codex CLI, `codex exec --json` in its workspace-write sandbox, with web search when allowed but no network for its commands, `src/ai/codex_cli.py`; the user's Codex config is kept for its Windows sandbox setup). The user allows sending files once per provider (`ai.magic_drop_consent`, `ai.codex_magic_drop_consent`); several files ask for one folder or one each. Code: `src/ai/magic_drop.py`, `src/jobs/magic_drop.py` (`MagicDropJob`, `SignInCheck`), `LODGaZillaWindow.start_magic_drop`. |
| Draft | A Magic drop's importer plugin folder under `%LOCALAPPDATA%/LODGaZilla/magic_drop/<stamp>/`: `plugin.json`, `importer.json`, `importer.py` (the agent's parser), `metadata.json`, the instructions (`CLAUDE.md` for Claude, `AGENTS.md` for Codex) and reference copies of `H5_Format.md` and `parser_base.py`. Kept after the drop, so it can become an installed importer later. |
| Create plugin | Questions in the AI panel's Create plugin mode go to the chosen provider, which builds a plugin with the user in `plugin/<id>`. The first question asks for the plugin ID (`ask_plugin_id`) and creates that folder as its own Git repository with the provider's instructions file (`CLAUDE.md` or `AGENTS.md`); the instructions and the user's files (`.lodgazilla_ai/`) are kept out of its commits through `.git/info/exclude`. Each question is one agent run there (`PluginChatJob` in `src/jobs/plugin_chat.py`); later ones continue the provider's session (`--resume`, `codex exec resume`). The agent may write only in the plugin folder and run Python and Git (to look); it reads the host's docs and contracts. LODGaZilla commits the folder after each answer, named after the user's request (`commit_changes`), because Codex's sandbox cannot write `.git`; nothing is pushed. Attached files are also copied to the plugin's **examples folder** in the data folder (`<id>`, marked by the read-only info entry `create plugin`; `examples_folder`, `copy_examples`); after each answer, when the plugin declares an importer, `PluginExamplesJob` (`src/jobs/plugin_examples.py`) imports them with it through `src.ai.trial_import` and reports the series in the AI conversation. Code: `src/ai/plugin_builder.py`, `LODGaZillaWindow.on_ai_question`. Magic drop and Create plugin run the agent through `AgentRun` (`src/jobs/agent_run.py`). |
| Send/Stop button | The button right of the AI input: a green play icon sends; while the agent is thinking it becomes a red stop icon (`send_button`, `set_busy`; icons `ai-send`, `ai-stop`). |
| Splash screen | Shown about a second after start, before the heavy modules are imported (`start_application` in `src/app/startup.py`, called from `src/app/lodgazilla.py`'s `__main__` block); it closes when the main window is shown. |
| Settings dialog | The application preferences window, `SettingsDialog`, with General, Navigator, Plot navigator, Plot, Plugins and HDF5 Cache tabs (`SettingsEditorWidget.tabs`). The General tab's **AI** group holds the Claude Code and Codex CLI paths, each one's consent and web access (`claude_web_access`, `codex_web_access`: Claude's WebFetch and WebSearch tools, Codex's web search), and the cost (Claude only) and time limits (`ai` section). Colors are swatch buttons (`ColorButton`). The General tab's **Demo data** group has **Re-generate demo data**, which deletes and recreates `demodata/` (`DemoDataJob` in `src/app/demo_data.py`). Settings that have a main-window button (Hide irrelevant series, Sample loss, Clipping, Plot navigator buttons) are not repeated in it; saving keeps their values. |
| Workspace / UMW file | A saved Navigator and Plot workspace in a LODGaZilla workspace (`.umw`) JSON file, saved and opened from the File bar; format in `src/data/workspace_file.py`. The Plot navigator buttons are user preferences, kept in the app settings and not in workspaces; an older file's `plot_navigator` section is ignored. **Open recent workspace** lists the last ten (`recent_workspaces` setting). While a card is open, Open workspace, Open recent workspace and closing the window ask whether to save first (`confirm_replacing_cards`). |
| Demo data | Synthetic running workouts with videos in `demodata/` (ignored by Git), made by `tools/create_demo_dataset.py`; `demo.umw` is written last and marks a complete set. At startup the app offers to create missing demo data (`offer_demo_data`, `LODGaZillaWindow.offer_demo_data`) and shows a progress dialog while it runs. |
| Plugin sources | GitHub account and Bitbucket workspace URLs under **Plugin sources** on the Settings dialog's Plugins tab. |
| Plugin update check | Background discovery of compatible repositories and revisions. **Check now** runs manually; startup checks use the saved sources. Install/Update downloads a revision for activation on restart. |

### Card types

| Common name | Meaning |
| --- | --- |
| Base widget | The shared card frame and interactions, implemented by `PlotBaseWidget`; not a standalone visualization. |
| Line | Numeric data plotted against time. |
| Multi-series | Multiple series in one card, including numeric traces, state/event lanes and video **filmstrip lanes** (rows of still frames along the time axis, `FilmstripLane` in `src/gui/plots/multi.py`, frames from `src/gui/plots/video/filmstrip.py`). The Navigator series context-menu label is `Multi-series`. A video's default card stays Video; Multi-series is its alternative. |
| Event/state | Discrete events or states displayed along a time axis. |
| X/Y | One or more graphs, each consisting of an X and a Y data series. |
| 3D plot | Cartesian X/Y/Z visualization with one or more 3D traces, `XYZPlotWidget`. Each axis uses a source's values or time; distinct from 3D Map. |
| Frequency | Frequency-domain visualization of a data series. |
| Sample interval | The time between consecutive samples of one or more series, in seconds, along a time axis; `SampleIntervalPlotWidget`, card key `interval`. Internally the loader and plot mode are still named `timing_delta`. |
| Map | A geographic map displaying one or more GNSS tracks, `GnssPlotWidget`. |
| 3D Map | The three-dimensional GNSS track view over terrain, `Gnss3DPlotWidget` in `src/gui/plots/gnss_3d.py`. Its **3D Map controls** are header controls (`control_groups`): **Map** (opacity; 0% hides the map and terrain), **Height** (altitude exaggeration 1–10x), **Offset** (track offset in metres, slider and typed field), **Follow ground** (draws the track on the terrain, ignoring altitude; the default for a track without altitude), **Auto** (sets the offset that puts the lowest point on the ground) and **Center view** (fits the view to the track). Map and Height show their values on the slider (`ValueSlider`). A new card applies 3x, Auto and a fitted view once (`_apply_auto_settings_once`); a restored workspace keeps its saved `map_controls`. The ground comes from the terrain's own Terrarium tiles. |
| Image | An attached image displayed in a card. |
| Video | An attached video with playback, synchronization, and preview controls. |

#### Base widget elements

A **3D trace** is one X/Y/Z source combination in a 3D plot card. The **Trace
selector** chooses which trace the card's X/Y/Z assignment controls edit.

- **Drag strip**: the top strip used to move or rearrange a card (`drag_handle`); tinted while the pointer is over it.
- **Close button**: closes the card (`close_button`).
- **Offset button**: the clock icon immediately left of the Close button, highlighted while a
  **Time offset** is set (`offset_button`). A click replaces it with the **Offset editor**, a
  numeric field with up/down arrows (`offset_editor`), until the field loses focus. The time offset
  only moves what the card shows: shared time = card time + offset. Cards keep their data in their
  own time; the workspace converts markers, Plot navigator bars and ranges, and linked time views
  (`src/gui/plots/time_offset.py`). It is saved in workspaces, not in the data. Image cards have
  none; on a Video card it is the sync offset, saved with the attachment. In a Multi-series card
  with a trace or filmstrip selected, the button and Plot navigator drag edit that **series
  offset** instead (`edited_time_offset`, `MultiSeriesPlot.series_offsets`); a filmstrip's is its
  video's sync offset. A sideways Plot navigator drag shows its change beside the pointer.
- **Help button**: the question-mark icon left of the Offset button; lists the card's
  mouse actions in a popup (`help_button`, text in `src/gui/plots/card_help.py`).
- **Resize handle**: the bottom handle used to adjust card or shared row height (`resize_handle`).
- **Header controls**: widgets in the Drag strip's row; `header_controls` are laid out right to
  left from the Close button and `header_left_controls` left to right. The Map card's
  **Map type selector** (Street/Satellite) is one; the rest of the strip still drags the card.
- **FFT settings button**: the gear left of a Frequency card's Close button; opens the FFT settings menu (`fft_settings_button`).
- **Sample limit selector**: the dropdown immediately left of the Close button on
  cards that draw every Nth sample (X/Y, 3D plot, Map, 3D Map); it sets the
  maximum points per graph, trace or track (`sample_limit_combo`). After a load
  it shows the drawn share of samples, such as `25% · 100k max`, in bold when
  partial (`update_sample_coverage`). Cards opt in with `default_sample_limit`;
  limits are in `src/data/sampling.py`.
- **Draw style selector**: three buttons in the header row, left of the Sample
  limit selector, on cards that draw series as lines (Line, Multi-series, Sample
  interval, X/Y, 3D plot, Map, 3D Map): **Auto** (the card's own way: time cards
  add points only when few raw samples are in view, X/Y and 3D plot draw lines and
  points, maps draw lines), **Lines** and **Points** (no lines between points)
  (`draw_style_selector`, `draw_style_buttons`, `set_draw_style`). Cards opt in with
  `supports_draw_style` and draw with `apply_draw_style`; a style other than Auto is
  saved in workspaces (`draw_style`).

The **Reset camera button** on a 3D plot card is the camera/reset icon left of
its Sample limit selector (`reset_button`). Axis assignments and trace removal are
controlled through the shared Legend.

#### Video elements

| Common name | Meaning |
| --- | --- |
| Video viewport | The area displaying the video frames. |
| Performance label | The label beside the Close button showing the playback FPS; its tooltip names the playback backend. |
| Play/pause button | Starts or pauses playback. |
| Mute button | Toggles audio muting. |
| Sync button | The **Sync** toggle on the Playback bar that shows or hides the **Sync panel**: the Sync buttons, Sync status and Offset input (`sync_button`, `sync_panel`). The panel hides again when a sync completes. |
| Sync buttons | **Set data marker** stores the red marker's time as the sync point, shown as the purple dashed **sync marker** on every card, the Plot navigator and Timeline sliders; **Set video marker** stores the current video position. Either order; once both are set the offset is calculated and saved. Each icon shows whether its point is set. Distinct from Sync status. |
| Sync status | The text reporting synchronization state. |
| Offset input | The Sync panel's numeric field with up/down arrows that sets the sync offset by hand; it applies at once and is saved when it settles (`offset_input`). |
| Volume slider | Adjusts playback volume; the **Volume icon** precedes it. |
| Timeline slider | Seeks within the video. It sits in its own translucent **Timeline row** above the Playback bar (`timeline_row`); its start and end follow the first time card's plot-area edges when they fall within the card, otherwise it spans the row. |
| Playback status | Playback state or position text, shown as the Timeline slider's tooltip. |
| Preview mode | The Auto/Manual dropdown in the card's header row controlling preview sizing; its tooltip gives the preview size. |
| Preview size input | The header-row numeric control for the manual preview size, shown only in Manual mode. |
| Playback bar | The translucent bar along the bottom of the video with the Play/pause and Mute buttons, the Sync button and Sync panel, the Volume icon and the Volume slider (`playback_bar`). |

### Tree widget items

| Common name | Internal type | Meaning |
| --- | --- | --- |
| Virtual group | `virtual` | A grouping heading, such as year, month, day, or metadata value. |
| Folder | `folder` | An actual folder on disk. |
| Series group | `series_group` | A container grouping data series, such as Accelerometer or GNSS. Groups can be nested. |
| Data series | `series` | An individual data series from an H5 file. |
| Info container | `info` | The Info row beneath a folder, containing metadata entries. |
| Metadata entry | `detailed_info` | A key/value pair beneath Info. |
| Video attachment | `video` | A linked video beneath a folder. |
| Image attachment | `image` | A linked image beneath a folder. |
| Error row | `error` | For example, `Unreadable: filename.h5`. |
| Message row | `message` | A status message, such as a missing-folder message. |

The **Add metadata row**, shown as `[ADD NEW KEY]` / `[ADD NEW VALUE]`, is an
empty `detailed_info` item, not a separate internal type. Metadata entries can be
editable or read-only.

The **Media group** is the `series_group` named Media under a folder. It holds the
folder's Video attachments, Image attachments and **audio series**: the Audio
left/right (or Audio) data series of an **audio H5** (`<file name>.audio.h5`,
file attribute `media_kind = "audio"`). Audio files and video soundtracks are
**audio attachments**, linked in the folder info's `audio` list and converted in
the process queue by `src/jobs/media_import.py`.

A **Video attachment** or **Image attachment** is a Navigator tree item. A
**Video card** or **Image card** is its opened visualization in the Plot workspace.
An H5 file supplies data series; it is not a separate tree item type.

## Common development principles

- **Use the shared vocabulary.** Distinguish Navigator from Plot navigator,
  virtual group from series group, and attachment from card. Preserve internal
  identifiers unless a task explicitly requires changing them.
- **Keep changes focused.** Implement the requested behavior, follow nearby
  conventions, and avoid unrelated refactoring or overwriting existing work.
- **Share common behavior.** Put common card interactions in the base widget or
  an appropriate shared helper. Keep card-specific behavior with its card type.
  Marker code is shared in `src/gui/plots/card_markers.py`: `TimeCardMarkers` for
  cards on a time axis (Line, Event/state, Frequency) and `PointMarkers` for cards
  that mark points (X/Y, 3D plot, Map); marker value reads use `MarkerValueLoads`.
- **Share card-selection rules.** `src/data/plot_policy.py` defines default cards
  and minimum-card packing for Navigator menus, activation, and data drops.
  Explicit grouped-card choices retain incomplete groups; incompatible selected
  items open in their default cards. Inspect H5 metadata in background jobs.
  Data-series activation (double-click) uses the active card only when it has
  the series' default card type or is a grouped card with an empty axis, and
  otherwise opens the default card (`activation_uses_card`); series-group
  double-clicks only toggle expansion, and their default menu action names the
  calculated card type with **(default)**. Plot destinations use card names and
  **Active card**, without Open/Plot prefixes.
- **Keep startup fast.** The main window opens before the plot card code
  (pyqtgraph, OpenGL, PyAV, Qt WebEngine and Multimedia) is imported:
  `src/app/lodgazilla.py` refers to card classes and their helpers through
  `LazyAttribute` stand-ins, and `CardModuleLoader` imports `CARD_MODULES` on a
  worker thread once the window is shown (`src/app/card_modules.py`). Do not
  import card modules at the top of the main window module or of the modules it
  imports; add new card modules to `CARD_MODULES`. `tests/test_card_modules.py`
  checks this.
- **Keep the UI responsive.** Avoid lengthy file reads, parsing, decoding, and
  computation on the GUI thread. Reuse the relevant worker/coordinator patterns,
  bound background work, and clean up resources when cards close.
- **Preserve user data and state.** Distinguish original files, generated H5
  caches, metadata, and attachment links. Removing an attachment link must not
  delete its source file. Preserve selection, layout, and playback state when
  the requested change does not require resetting them.
- **Respect module responsibilities.** Keep presentation in `src/gui`, data
  handling in `src/data`, background jobs in `src/jobs`, and application wiring
  in `src/app`. Keep plugin-specific behavior in its plugin where practical.
- **Keep plugins independently testable.** Plugin production code uses the public
  `src/contracts` interfaces. The host must run with no installed plugins; core
  tests use synthetic plugins. Each plugin owns its format/device tests,
  dependencies and documentation. See `docs/Plugin_API.md`. Use
  `python tools/run_tests.py --isolated` for a protected, plugin-free core run,
  or add `--plugin PATH` to test an independent plugin checkout.
- **Keep plugin reviews separate.** A code review writes its host findings to
  `Code_review.md` and its plugin findings to `Code_review_plugin.md`, in the same
  structure. `Code_review.md` never names private plugins or their findings.
- **Keep plugin repositories separate.** The root `plugin/` directory is ignored
  by the application repository and contains optional independent Git checkouts.
  Commit plugin changes in their own repository; do not force-add their source
  or add private plugin submodules to the application repository.
- **Keep settings consistent.** Use existing application settings persistence
  for application preferences and `src/gui/navigator/settings.py` for Navigator
  column profiles. Avoid adding a second source of truth for an existing setting.
- **Keep committed settings clean.** The app reads and saves the untracked local
  `app_settings_debug.json` when it exists, otherwise the committed
  `app_settings.json` (`resolve_app_settings_path()` in `src/app/settings.py`).
  When adding, renaming or removing a settings field, update both files. The
  committed file must use the code defaults, and never contain machine- or
  user-specific values: plugin enables, `recent_folders`,
  `default_info_metadata` or `tree_columns`. Never commit the debug file.
- **Keep mouse help current.** Each card's mouse actions are described only in
  `src/gui/plots/card_help.py`, shown by the card's Help button. Whenever a mouse
  interaction is added, changed or removed in a card, update its rows there in the
  same change. The README only gives an overview, so update it only where it
  describes that action. Do not add mouse-usage hints as tooltips; tooltips
  only name a control.
- **Keep README screenshots current.** `python tools/take_readme_screenshots.py`
  retakes `docs/images/` from the demo data, in the dark theme.
- **Keep workspaces complete.** A UMW workspace must reopen exactly as saved. Each
  card type saves and restores its state in `workspace_state()` and
  `restore_workspace_state()` (shared parts in `PlotBaseWidget`); `PlotWorkspace`
  creates cards from saved records in `_create_card`. When adding a card type, or
  a card or Navigator setting a user can change, extend these and
  `tests/test_workspace_file.py` in the same change. Add fields compatibly; raise
  `UMW_VERSION` only when older files cannot be read.
- **Verify proportionately.** Run checks relevant to the changed behavior. Add
  regression tests for meaningful logic changes or bug fixes; documentation-only
  changes do not require application tests. The full test command is
  `python -m unittest discover -s tests`. GUI tests wait with
  `qt_wait`/`qt_wait_until` from `tests/qt_wait.py`, not `QTest.qWait`, which
  holds Python's lock and starves background loaders. Test runs write their logs
  (`lodgazilla.log`, `lodgazilla_h5_cache.log`, `startup.log`) to
  `%TEMP%/lodgazilla_test_logs`, not to `logs/`; `LODGAZILLA_LOG_DIR` overrides the
  folder (`src/app/log_paths.py`). New log files use `log_path()` from there.
- **Do not generate demo data to verify changes.** The full demo data run
  (`tools/create_demo_dataset.py`, about 40 minutes and 1.7 GB) is not part of the
  test suite and is not needed for other changes. The suite only runs fast demo tests
  (`tests/test_demo_dataset.py` with short simulated runs, `tests/test_demo_data_app.py`
  with a fake generator). Run the real generator only when changing it, and then first
  with `--no-video --long-hours 0` (about a minute).
- **Version deliberately.** `APP_VERSION` in `src/version.py` is the only place the
  version is written; it follows Semantic Versioning (`1.0.0-beta.1`, `1.0.0`). Raise
  it only when making a release, as in `docs/Releasing.md`; note user-visible changes
  under **Unreleased** in `CHANGELOG.md`. A change that breaks workspaces, plugins or
  H5 files needs a MAJOR release after 1.0.0.
- **Report clearly.** Explain what changed, how it was verified, and any known
  limitations. Update this guide when agreed terminology or principles change.

## Useful entry points

- `python -m src.app.lodgazilla [--default-settings] [--no-plugins] [data_folder]`:
  the start command, the same on every platform; on Windows `LODGaZilla.bat` runs
  exactly this one line (passing on its options with `%*`) and must stay free of other logic. It runs
  `tools/setup_environment.py` first. `--default-settings` uses the committed
  `app_settings.json` and saves changes to it, so revert them before committing;
  `--no-plugins` loads no plugin. Both are passed to worker processes as the
  `LODGAZILLA_DEFAULT_SETTINGS` and `LODGAZILLA_NO_PLUGINS` environment variables.
- `src/app/lodgazilla.py`: main window, workspace, and application context menus.
- `src/gui/navigator/widget.py`: Navigator tree items and context-menu dispatch.
- `src/gui/navigator/controls.py`: Navigator controls.
- `src/gui/plots/base.py`: shared card frame and interactions.
- `src/gui/plots/navigator.py`: Plot navigator.
- `src/gui/plots/video/widget.py`: video card controls and behavior.
- `README.md`: setup, application features, and usage.
