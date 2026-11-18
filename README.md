# LODGaZilla

**Every file format, billions of points, one timeline.** Because "open it in Excel"
stopped working three gigabytes ago.

Got a folder of logs nobody can open, a sensor that samples way too often, and a video
of the exact moment it all went wrong? LODGaZilla puts them on one timeline.

- **Every file format. Really.** Plugins read the usual suspects (CSV, JSON, binary
  logs, GPX…). For the format your logger invented last Tuesday, drop a file on the AI
  panel: Magic drop works it out and plots it. Need it again? The AI writes you a
  plugin.
- **Organise the pile.** Group recordings by date or by any key/value you add (site,
  rider, firmware, whatever), then filter down to the one run you're after.
- **Plot it every which way.** Lines, stacked lanes, events and states, X/Y, 3D,
  spectra, sample intervals, maps and 3D maps, and video synced to the data.
- **Plug in anything.** Plugins import, export, or pull data straight off devices:
  data loggers, development kits, sensor boxes.
- **Fast.** Billions of points, zoomed and panned without waiting. The catch: each
  source file is converted once. For a big log, that's a coffee break; after that,
  it's quick every time.
- **Look, don't touch.** LODGaZilla is a viewer. Your data stays exactly as it was;
  you only edit metadata and attachment links.

**Who's it for?** Anyone with more data than patience: vehicle, drone and robotics
testers, sports scientists with sensors on athletes and cameras rolling, field teams
running dozens of loggers, and developers tuning algorithms against hours of raw
recordings. If it has a timestamp, LODGaZilla wants to see it. And when the question
is "what actually happened at 12:04:31?", the synced video answers it.

![LODGaZilla with the demo data open](docs/images/main_window.png)

**See it in action** (2 min 50 s on YouTube):

[![LODGaZilla demo video](https://img.youtube.com/vi/xR2_yrjDPYI/maxresdefault.jpg)](https://youtu.be/xR2_yrjDPYI)

## Getting started

### 1. Install Git

Git downloads LODGaZilla and keeps it up to date, and plugins are installed with it
too. It's free:

- **Windows:** download the installer from [git-scm.com](https://git-scm.com/downloads/win)
  and run it. The default choices are fine; just keep clicking **Next**.
- **macOS:** open Terminal and type `git --version`. If Git is missing, macOS offers to
  install it (as part of the Command Line Tools). Say yes.
- **Linux:** install it with your package manager, for example
  `sudo apt install git` (Ubuntu, Debian) or `sudo dnf install git` (Fedora).

Check that it works: open a new terminal and run `git --version`. It should print a
version number, such as `git version 2.47.1`.

Rather click than type? [GitHub Desktop](https://desktop.github.com/) does the same
with buttons.

### 2. Get LODGaZilla

LODGaZilla lives on GitHub:
[github.com/RikuRaitis/LODGaZilla](https://github.com/RikuRaitis/LODGaZilla). In a
terminal, go to the folder where you want it, such as `Documents`, and run:

```
git clone https://github.com/RikuRaitis/LODGaZilla.git
```

This creates a `LODGaZilla` folder. To update later, run `git pull` in that folder.

No Git? On the repository page, click the green **Code** button and choose
**Download ZIP**, then unzip it. To update, download a new ZIP. You'll still need Git
for plugins.

### 3. Install Python

You need Python 3.11 or newer. On Windows, download it from
[python.org](https://www.python.org/downloads/) and tick **Add Python to PATH** in the
installer. Missing that box is the number one reason for "python is not recognized".

### 4. Start it

On Windows, double-click `LODGaZilla.bat` in the LODGaZilla folder; a shortcut to it
works from anywhere, such as the desktop or the Start menu. On any platform,
you can also open a terminal in that folder and run:

```
python -m src.app.lodgazilla
```

(On Linux and macOS the command may be `python3`.) To open a data folder straight away,
add it to the command, for example `python -m src.app.lodgazilla D:\logs`.

The first start installs the Python libraries LODGaZilla needs, which takes a few
minutes. Later starts skip that step unless something has changed. If you prefer a
virtual environment, create and activate it before the first start
(`python -m venv .venv`).

### 5. Plot your first data

No data yet? No problem. Pick one:

**Try the demo data.** A few synthetic forest runs with sensors, GNSS and video, made
for poking at.

- **On the first start,** LODGaZilla offers to create it. Say yes, and it generates
  the data in the background with a progress bar. It takes a while, because it renders
  two videos; **Cancel** stops it, and the next start asks again.
- **Later,** choose **Settings > General > Demo data > Re-generate demo data**. This
  deletes the `demodata` folder, including any changes you made there, and creates it
  again.

When it's ready, click **Open workspace** at the top left and open `demodata/demo.umw`
to see every card type at once. See [Demo data](#demo-data) for what's inside.

**Drop your own file.** Got a log lying around? Drag it onto the **Magic drop** box in
the AI panel below the Navigator. [Magic drop](#magic-drop-import-any-file-with-ai) works out the format
and converts it into a new folder in the Navigator, no plugin needed; double-click a
data series there to plot it. It needs the Claude Code or Codex tool, as described there.

**Open a data folder.** Once you have files and plugins for them, choose the folder in
the **Data folder** field at the top left (its dropdown starts with **[Browse…]**).
Expand a folder and its **Source data** group, and double-click a file marked
**Not imported** to convert it to H5. Then double-click a data series to plot it, or drag
it into the plot area on the right.

## Using LODGaZilla

### The main window

![The main window with its areas numbered](docs/images/main_window_annotated.png)

| # | Area | What it is |
| --- | --- | --- |
| 1 | **File bar** | Open workspace, Open recent workspace, Save workspace, Save workspace as. |
| 2 | **Navigator controls** | The data folder, **Group by** and **Filter** fields, and the [Navigator buttons](#buttons). |
| 3 | **Navigator** | The folders, metadata, data series and attachments in the data folder. |
| 4 | **AI Integration** | The AI panel: the [Magic drop](#magic-drop-import-any-file-with-ai) and [Create plugin](#create-plugin) boxes. Open by default; the arrow in its header closes it to one row. |
| 5 | **Plot navigator toolbar** | Buttons that apply to all cards, then Settings, Help and Close all. |
| 6 | **Plot navigator timeline** | A bar per card on a shared timeline, with the markers and the selection. |
| 7 | **Plot workspace** | The cards. |
| 8 | **Internal log** | What LODGaZilla is doing: loading, imports, warnings. Click it to expand. |

Drag the dividers between the areas to resize them.

### Buttons

**File bar** (top left):

| Icon | Button | Does |
| --- | --- | --- |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/navigator_icons/dark/workspace-open.svg"><img src="assets/navigator_icons/light/workspace-open.svg" width="20" alt="workspace open"></picture> | Open workspace | Opens a saved [workspace](#workspaces) (Ctrl+O). |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/navigator_icons/dark/workspace-open-recent.svg"><img src="assets/navigator_icons/light/workspace-open-recent.svg" width="20" alt="workspace open recent"></picture> | Open recent workspace | Lists the last ten workspaces. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/navigator_icons/dark/workspace-save.svg"><img src="assets/navigator_icons/light/workspace-save.svg" width="20" alt="workspace save"></picture> | Save workspace | Saves the current view (Ctrl+S). |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/navigator_icons/dark/workspace-save-as.svg"><img src="assets/navigator_icons/light/workspace-save-as.svg" width="20" alt="workspace save as"></picture> | Save workspace as | Saves it to a new file (Ctrl+Shift+S). |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/navigator_icons/dark/settings.svg"><img src="assets/navigator_icons/light/settings.svg" width="20" alt="settings"></picture> | Settings | Opens the Settings dialog. |

**Navigator buttons** (at the right end of the same row as the workspace buttons):

| Icon | Button | Does |
| --- | --- | --- |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/navigator_icons/dark/refresh.svg"><img src="assets/navigator_icons/light/refresh.svg" width="20" alt="refresh"></picture> | Refresh | Reads the data folder again. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/navigator_icons/dark/batch-h5.svg"><img src="assets/navigator_icons/light/batch-h5.svg" width="20" alt="batch"></picture> | Batch | For the whole data folder: **Generate missing H5 files** (files an importer plugin reads that have no H5 file yet), **Generate with plugin** (one importer or export plugin: missing ones, or all again) and **Re-generate all**, after an "Are you sure?". |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/navigator_icons/dark/sample-loss.svg"><img src="assets/navigator_icons/light/sample-loss.svg" width="20" alt="sample loss"></picture> | Sample loss | Highlights series with dropped samples. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/navigator_icons/dark/clipping.svg"><img src="assets/navigator_icons/light/clipping.svg" width="20" alt="clipping"></picture> | Clipping | Highlights series that hit their sensor's limits. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/navigator_icons/dark/hide-irrelevant.svg"><img src="assets/navigator_icons/light/hide-irrelevant.svg" width="20" alt="hide irrelevant"></picture> | Hide irrelevant series | Hides series the importer marks as hidden, such as system counters. |

**Plot navigator toolbar**, from left to right. A toggle button shows its current state,
so its icon changes when you click it; the table shows each one switched on.

| Icons | Buttons | Do |
| --- | --- | --- |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/time-axis-all.svg"><img src="assets/plot_icons/light/time-axis-all.svg" width="20" alt="time axis all"></picture> <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/time-axis-bottom.svg"><img src="assets/plot_icons/light/time-axis-bottom.svg" width="20" alt="time axis bottom"></picture> <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/time-axis-none.svg"><img src="assets/plot_icons/light/time-axis-none.svg" width="20" alt="time axis none"></picture> | Time axis | Show the time axis on every card, only on the bottom one, or on none. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/markers-shown.svg"><img src="assets/plot_icons/light/markers-shown.svg" width="20" alt="markers shown"></picture> | Markers | Off by default: no card places markers. Switching it off removes every marker and selection. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/markers-linked.svg"><img src="assets/plot_icons/light/markers-linked.svg" width="20" alt="markers linked"></picture> | Markers linked | All cards share the same markers. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/snap-nearest-timestamp.svg"><img src="assets/plot_icons/light/snap-nearest-timestamp.svg" width="20" alt="snap nearest timestamp"></picture> | Snap to nearest timestamp | A marker jumps to the nearest sample instead of staying where you clicked. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/views-linked.svg"><img src="assets/plot_icons/light/views-linked.svg" width="20" alt="views linked"></picture> | Plot views linked | Zooming or panning one time card zooms and pans them all. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/info-clicked.svg"><img src="assets/plot_icons/light/info-clicked.svg" width="20" alt="info clicked"></picture> <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/info-all.svg"><img src="assets/plot_icons/light/info-all.svg" width="20" alt="info all"></picture> <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/info-off.svg"><img src="assets/plot_icons/light/info-off.svg" width="20" alt="info off"></picture> | Values boxes | Show values only in the card where you placed the marker, in all cards, or nowhere. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/show-sample-loss.svg"><img src="assets/plot_icons/light/show-sample-loss.svg" width="20" alt="show sample loss"></picture> <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/show-clipping.svg"><img src="assets/plot_icons/light/show-clipping.svg" width="20" alt="show clipping"></picture> | Sample loss, Clipping | Show or hide those areas on the cards. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/legend-shown.svg"><img src="assets/plot_icons/light/legend-shown.svg" width="20" alt="legend shown"></picture> | Legends | Show or hide every card's legend at once, for more room; new cards follow it. Each card also has its own Legend button. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/plot-navigator-shown.svg"><img src="assets/plot_icons/light/plot-navigator-shown.svg" width="20" alt="plot navigator shown"></picture> | Plot navigator | Show or hide the timeline with the card bars. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/plugin.svg"><img src="assets/plot_icons/light/plugin.svg" width="20" alt="plugin"></picture> | Plugins | One icon per plugin with its own window; a plugin can bring its own icon. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/help.svg"><img src="assets/plot_icons/light/help.svg" width="20" alt="help"></picture> <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/close-all-plots.svg"><img src="assets/plot_icons/light/close-all-plots.svg" width="20" alt="close all plots"></picture> | About, Close all | **About LODGaZilla** (version, author, and a button that opens this README in your Markdown viewer), and closing every card. |

**Card header** (top right of each card). A card shows only Close, its Legend button and
the card controls button; click the card controls button to make the card active and show the rest. They
hide again when another card becomes active.

| Icon | Button | Does |
| --- | --- | --- |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/legend-shown.svg"><img src="assets/plot_icons/light/legend-shown.svg" width="20" alt="legend shown"></picture> | Legend | Shows or hides this card's legend. Only on cards with a legend; saved in workspaces. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/card-controls-hidden.svg"><img src="assets/plot_icons/light/card-controls-hidden.svg" width="20" alt="card controls"></picture> | Card controls | Shows or hides this card's controls below. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/help.svg"><img src="assets/plot_icons/light/help.svg" width="20" alt="help"></picture> | Help | Lists every mouse action of this card. When in doubt, click it. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/center-track.svg"><img src="assets/plot_icons/light/center-track.svg" width="20" alt="center view"></picture> | Center view | Shows all the card's data again: a time card's whole time range (or the part trimmed on the Plot navigator), every graph or track. On every card that zooms: Line, Multi-series, Sample interval, Event/state, Frequency, X/Y, 3D plot (the camera back to the whole trace), Map and 3D Map. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/time-offset.svg"><img src="assets/plot_icons/light/time-offset.svg" width="20" alt="time offset"></picture> | Time offset | Sets the card's [time offset](#time-offsets). Highlighted while an offset is set. |
| <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/close.svg"><img src="assets/plot_icons/light/close.svg" width="20" alt="close"></picture> | Close | Closes the card. Always shown, rightmost. |

Cards that draw lines add the [draw style](#lines-or-points) buttons <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/draw-auto.svg"><img src="assets/plot_icons/light/draw-auto.svg" width="20" alt="draw auto"></picture>
<picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/draw-lines.svg"><img src="assets/plot_icons/light/draw-lines.svg" width="20" alt="draw lines"></picture> <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/draw-points.svg"><img src="assets/plot_icons/light/draw-points.svg" width="20" alt="draw points"></picture> (Auto, Lines, Points).
Some cards add their own controls next to these: the [sample limit](#sample-limits)
dropdown, the Frequency card's FFT settings <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/fft-settings.svg"><img src="assets/plot_icons/light/fft-settings.svg" width="20" alt="fft settings"></picture>, the 3D plot's axes buttons <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/axes-origin.svg"><img src="assets/plot_icons/light/axes-origin.svg" width="20" alt="axes at origin"></picture> <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/axes-corner.svg"><img src="assets/plot_icons/light/axes-corner.svg" width="20" alt="axes at corner"></picture> (through 0, 0, 0 or along the corner), the Map type (street or satellite) and the Video
card's preview size. The [3D Map](#3d-map) puts its map controls along the left of its
top strip. Drag the strip at the top of a card to move it, and its bottom edge
to resize it.

### The Navigator

Each recording folder has an **Info** row with its metadata. Edit the values, or add
your own keys at **[ADD NEW KEY]**, such as diver, place or weather. Then use them:

- **Group by** arranges recordings by date (year, month, day) or by any metadata key.
- **Filter** shows only the recordings with a given metadata value.

Right-click a column header to choose which columns are shown. Right-click a folder
for:

- **Multi-series**: all the folder's series in one card.
- **Attach**: link a video, images or audio to the folder. Removing a link later never
  deletes the file. A folder's **Media** group has the same **Attach** menu.
- **Show in file manager.**
- **Copy folder location**: puts the folder's full path on the clipboard.

#### Source data

Below Info, each folder's **Source data** group lists what was imported or exported,
and what can be, in three columns:

| Source | Output | Plugin |
| --- | --- | --- |
| `log_20260909_104101.bin` | `log_20260909_104101.h5` | Example binary importer |
| `log_20260910_090000.bin` | Not imported | Example binary importer |
| `notes.txt` | Not imported | No compatible plugin |
| LODGaZilla | `20260909_104101.csv` | Example CSV export |

- Every data file of the folder is listed. One that isn't imported yet reads
  **Not imported** (dimmed), with the enabled importer plugin that can read it, or
  **No compatible plugin** when none can; try [Magic drop](#magic-drop-import-any-file-with-ai)
  for those. Files Magic drop imported show **Magic drop** as the plugin.
- An export appears when the folder has files it reads, as **Not exported** until it runs.
- An importer that combines several files shows one expandable Source row per
  output. Expand it to see its inputs and any timing reference; double-click the
  parent to import them together. The output filename is shown before import.
  An incomplete set names its missing inputs and cannot be imported. Changing
  any member or reference marks the output outdated.
- An output whose source changed after it was written is marked **(outdated)**; one that
  was deleted, **(missing)**. Click **Refresh** to look again.
- **Double-click** a row to import, re-import or export it. LODGaZilla asks first when
  the output exists, because it is replaced. **Right-click** a row for the same action
  and **Show in file manager**.

LODGaZilla records each import and export in the folder's `_info.json` (`sources`).
Imports and exports start only from Source data rows; there is no folder-level or
batch conversion.

### Opening cards and dragging data

- **Double-click** a series to open it in its default card. If the active
  (highlighted) card fits, the series goes there instead. Press **Esc** or click empty
  space to make no card active.
- **Right-click** one or more series to choose the card type, or **Active card**.
- **Drag** series from the Navigator onto a card to add them, or into empty space to
  open a new card. Hold a drag between two cards for a second and the
  **Card drop selector** offers every card type that fits.


| You select                           | Default card                                            |
| ------------------------------------ | ------------------------------------------------------- |
| One series                           | Multi-series                                            |
| Two numeric series                   | X/Y, or Map for latitude and longitude                  |
| Three numeric series                 | 3D plot, or 3D Map for latitude, longitude and altitude |
| More, or a mix with events or states | Multi-series                                            |
| A video or image                     | Video or Image                                          |

### Selecting a series

**Left-click a series** (a line, lane, graph, trace or map track) to select it: it is
drawn wider and in front, and its Legend row is highlighted. A click farther from every
series selects none. The left click still places the red marker. Every card with more
than one series can select one; the Frequency card holds one series and selects none.
The widths of all series and of the selected one are **Series line width** (2 px) and
**Selected series line width** (5 px) in Settings, Plot. The 3D plot draws its traces 1 px
wide, as OpenGL does, unless **Use shader** is on in its card controls (off by default); then
they follow these widths, drawn by the graphics card. The selection is not saved in
workspaces.

### Legends

Each card's legend lists its series, at the card's right edge. Every legend is the
same share of its card's width (25% by default; **Legend width** in Settings, Plot); a
name too long for it is shortened in the middle with `...`.

- Rest the pointer on a series, in the legend or in the plot itself, to see its whole
  name; it hides as soon as the pointer moves (**Series name delay** in Settings, Plot).
- Click a colour swatch to change the colour.
- Drag a series to reorder it, or onto another card's legend to move it there.
- Right-click a series to remove it from the card.

X/Y, 3D plot and map cards group their legend by graph: a heading and one row per axis
(X/Y, X/Y/Z, Lat/Lon or Lat/Lon/Alt). Drop a series on a row to assign it; hold
**Ctrl** while dropping to swap two rows. An axis shows **empty** until it gets a
series, and the graph appears once all its axes have one. Right-click a heading to
remove the whole graph.

### Markers and selection

Markers are off until you switch on the **Markers** button in the Plot navigator; switching it
off again removes every marker and selection. With it on, every card except Image has two markers:


| Action                            | Result                          |
| --------------------------------- | ------------------------------- |
| Left-click                        | Places the red first marker.    |
| Right-click                       | Places the green second marker. |
| Double-click with the same button | Removes that marker.            |
| Drag a marker                     | Moves it, on time cards.        |

With both markers placed, the time between them is the **selection**, painted blue.
With **Markers linked**, every card shows the same markers. On X/Y, 3D plot and map
cards a click marks the nearest point, and the selection is that stretch of the graph
or track, drawn in its own colour at the track's width. Settings > Plot sets that colour
and opacity apart from the time cards' shading, and whether the stretch is drawn above
every track, only above its own track, or not at all.

A **values box** shows each series' value at a marker. Hover over it for the details,
which start with the series' whole name; with a selection, they include minimum,
maximum, average, median and a histogram.
Click a box to pin the details so you can copy them.

### The Plot navigator

![The Plot navigator](docs/images/plot_navigator.png)

The Plot navigator sits above the cards. Its toolbar holds the buttons that apply to
every card (see [Buttons](#buttons)); below it, each card has a bar on a shared
timeline, so you see at a glance which recordings overlap and where the markers are.

- Drag a bar's ends to trim that card's time range; right-click a bar to reset it.
- Drag a bar sideways to give the card a [time offset](#time-offsets), or up and down
  to reorder the cards.
- With **Plot views linked**, a frame shows the visible time range. Drag its edges to
  zoom all time cards, or its top strip to pan them.
- Click or right-click the timeline to place the markers there too.

### Arranging cards

Drag a card by its top strip to move it. Maps, videos and images can sit side by side;
drag the divider between them to share the width. Closing a card leaves the others in
place.

### Time offsets

When two recordings' clocks disagree (they always do), shift a card in time: click the
clock in its header and type the offset in seconds, or drag its bar sideways in the
Plot navigator. Only the view moves; the data stays as it is. In a Multi-series card,
select a trace first to shift just that series. On a Video card, the offset is the
video's sync offset.

### Sample limits

X/Y, 3D plot, Map and 3D Map cards draw every Nth sample, up to a limit per graph:
100k for X/Y, 50k for 3D plot and 20k for maps. The dropdown in the card header shows
how much is drawn, such as **25% · 100k max**, in bold when not everything fits.
Choose a higher limit for more detail or **all** for every sample; the card slows down
as the number grows. Trimming the time range in the Plot navigator adds detail too.

### Lines or points

Cards that draw series as lines (Line, Multi-series, Sample interval, X/Y, 3D plot,
Map and 3D Map) have three buttons in their header: **Auto**, **Lines** and
**Points**. Auto is each card's usual drawing; a Line card, for example, adds points
when you zoom in close. Lines never shows points, and Points shows only the samples,
with no lines between them.

### Workspaces

**Save workspace** (Ctrl+S) saves everything you see into a `.umw` file: the cards,
their layout, series, zoom and settings, both markers, and the Navigator's folder,
grouping and filter. **Open workspace** (Ctrl+O) brings it all back exactly as it was.
The Plot navigator buttons (linking, legends, time axis) are your own
preferences and are not saved in workspaces. While cards are open, opening a workspace
first asks whether to save them. Closing LODGaZilla asks too when **Ask to save the
workspace on exit** is on in Settings, General (off by default).
**Open recent workspace** lists the last ten. Paths are stored relative to the
workspace file, so a workspace saved next to its data still opens after you move the
folder.

## Cards

Everything you plot lives in a **card**. Mix and match them; they all share the same
time and the same markers.


| Card                                | Shows                                                                 |
| ----------------------------------- | --------------------------------------------------------------------- |
| [Line](#line)                       | Numeric series against time.                                          |
| [Multi-series](#multi-series)       | Many series in one card, each in its own lane, plus video filmstrips. |
| [Event/state](#eventstate)          | States and events as labelled bars.                                   |
| [Sample interval](#sample-interval) | The time between samples: gaps, jitter and dropped data.              |
| [Frequency](#frequency)             | A spectrogram of one series.                                          |
| [X/Y](#xy)                          | One series against another.                                           |
| [3D plot](#3d-plot)                 | Three series in 3D.                                                   |
| [Map](#map)                         | GNSS tracks on a street or satellite map.                             |
| [3D Map](#3d-map)                   | GNSS tracks on a 3D map with terrain.                                 |
| [Video](#video)                     | Video in sync with the data.                                          |
| [Image](#image)                     | A picture, such as a route map.                                       |

Audio isn't a card of its own: attached audio becomes data series you can plot in
any time card.

### Line

![Line card](docs/images/card_line.png)

Numeric series against time. **Ctrl+wheel** zooms and dragging pans. Shaded areas mark
sample loss and clipping, for every series; with several series, a band along the bottom
of an area shows the series' color. A snapping marker snaps to the nearest sample of any
series in the card.

### Multi-series

![Multi-series card](docs/images/card_multi_series.png)

Many series in one card, each scaled into its own lane so they don't pile on top of
each other. Click a trace to select it and show its Y axis, drag it up or down to move
it, and **Ctrl+wheel** over the Y axis to scale it. Event and state series appear as
labelled bars.

A video can join as a **filmstrip**: a row of frames along the time axis. Drag a
video attachment onto the card to add one. Great for spotting the exact frame where
the runner hit the bridge.

### Event/state

![Event/state card](docs/images/card_event_state.png)

States and events, such as workout phase, GNSS fix or button presses, as labelled
bars along the time axis.

### Sample interval

![Sample interval card](docs/images/card_sample_interval.png)

The time between consecutive samples. Dropped samples, gaps, jitter and rate changes
jump right out; above, the GNSS goes quiet for seven seconds under the trees. Drop
several series on one card to compare their timing. With both markers placed, the
values boxes show the interval statistics.

### Frequency

![Frequency card](docs/images/card_frequency.png)

A spectrogram of one series, here the soundtrack of the demo video: time across, frequency up, colour for amplitude. Its legend names the series; drop another series on the card to show it instead. The gear
button sets the FFT window, overlap and colour range, or an exact frequency band and
resolution. A white line follows the strongest frequency. The card needs regularly
sampled data; it leaves gaps rather than guessing across missing samples.

### X/Y

![X/Y card](docs/images/card_xy.png)

One series against another, such as heart rate against speed. A card can hold several
graphs.

### 3D plot

![3D plot card](docs/images/card_3d_plot.png)

Three series as X, Y and Z, such as the three accelerometer axes. Drag to rotate,
**Ctrl+drag** to pan and **Ctrl+wheel** to zoom; the camera button resets the view. The
axes meet at 0, 0, 0 by default, or, from the axes buttons, at the corner of the data;
when 0 is outside a series' range, its axis stays at the edge of the data. A card can
hold several traces.

Each axis fills the box by default, whatever its unit. **Click an axis line** to select
it (it turns thicker); **Ctrl+wheel** then stretches or shrinks just that axis. Click it
again, or anywhere else, to deselect it; with no axis selected, Ctrl+wheel zooms the camera. In the card's controls, **Equal scale**
<picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/axes-equal.svg"><img src="assets/plot_icons/light/axes-equal.svg" width="20" alt="equal scale"></picture> draws one metre the same length on every axis, so a GNSS track
shows its true shape: latitude and longitude are estimated in metres for this, while the
labels stay in degrees. Any two axes in degrees count as latitude and longitude (their
names say which, or else X is the longitude and Y the latitude); coordinates stored as
fixed-point numbers (unit `raw`, degrees × 10⁷) are read as the Map card reads them. Every other axis needs a
length unit; otherwise Equal scale is greyed out and its tooltip names the axis.
**Fit** <picture><source media="(prefers-color-scheme: dark)" srcset="assets/plot_icons/dark/axes-fit.svg"><img src="assets/plot_icons/light/axes-fit.svg" width="20" alt="fit axes"></picture> fills the box again. The stretch is saved in workspaces.

### Map

![Map card](docs/images/card_map.png)

GNSS tracks on a street or satellite map. Recognised latitude and longitude series
open here by default. A card can hold several tracks. A scale bar at the bottom right
shows a distance at the current zoom; **Scale bar** in the card's controls hides it. The
map data's credit (© OpenStreetMap, or Esri for satellite) is small at the top right.

### 3D Map

![3D Map card](docs/images/card_3d_map.png)

GNSS tracks in 3D over terrain and buildings, which hide the track where it goes
behind them. A new card frames the whole track, with heights exaggerated three times
to make those hills look properly hilly. The controls along the card's top:

- **Map**: the map's opacity; 0% hides the map and its terrain. Below 100% the whole
  track shows, drawn over the map, including where it goes behind a hill.
- **Height**: altitude exaggeration, from 1x to 10x, for both the terrain and the track.
- **Offset**: moves the track up or down, in metres. Drag the slider or type a value.
- **Follow ground**: draws the track on the ground and ignores its altitude. A track
  without altitude follows the ground by default.
- **Auto**: sets the offset so that the track's lowest point touches the ground.
  A new card does this by itself.
- **Center view**: brings the whole track back into view when you've lost it.
- **Scale bar**: shows or hides the scale bar at the bottom right, a distance at the
  current zoom (on by default).

### Video

![Video card](docs/images/card_video.png)

Video in sync with the data, with play, volume and a timeline that shows the markers.
To sync a video: press **Sync**, set the data marker at a moment you can recognise,
such as a lap press, and set the video marker at the same moment in the video.
LODGaZilla saves the offset with the video. Attach videos with **Attach > Video…** on
a folder; their soundtracks become audio series. See
[video controls and synchronization](src/gui/plots/video/README.md).

### Image

![Image card](docs/images/card_image.png)

A picture attached to a folder, such as a route map or a photo of the test setup.
Attach images with **Attach > Images…**.

## Magic drop: import any file with AI

Got a file nobody has written a plugin for? Drop it on the **Magic drop** box in the
AI panel below the Navigator. That's all.

The AI panel shows two boxes side by side, **Magic drop** and **Create plugin**. A box
lights up while you drag files over it. Once you've used one, both shrink to a row
above the conversation, so you can drop more files on either. Close the panel to one
**AI Integration** row with the arrow in its header, and click the row to open it
again; LODGaZilla remembers which way you left it.

1. LODGaZilla first asks your enabled importer plugins. If one reads the file, it
   creates a new folder in the data folder, copies the file there and generates its
   H5 files with that plugin, without AI and without sending anything. When the H5
   files are ready, the panel shows the series found and asks whether the result is
   OK: **Improve** the plugin with the AI, **Create a new plugin** from scratch, or
   **Keep the result**. Either AI choice opens [Create plugin](#create-plugin) with the
   copied file attached and a request ready to send.
2. Otherwise the AI takes over, Claude or Codex as chosen in the panel header (it asks
   for consent first). LODGaZilla creates the new folder and copies the file there; the
   original stays where it was. The AI reads the file, finds the data series, writes a
   parser and tests it. In the conversation it tells you the format, the series it
   found and what it assumed about units and time. It then asks whether the data looks
   right and whether you'd like a plugin for the format.
3. LODGaZilla runs the parser, writes the H5 file and the folder's `_info.json` with
   the fixed values the AI found, such as device and serial number. The new folder
   appears in the Navigator.
4. The AI is told which importer plugins are installed. If the file looks like a
   variant of a format one of them reads, LODGaZilla asks whether to update that plugin
   or create a new one, again through Create plugin.

Tips:

- Type hints in the box under the conversation before your next drop, such as
  "column 3 is pressure in mbar". They go to the AI with the file.
- Drop several files at once and LODGaZilla asks whether they belong in one folder
  or one folder each.
- Have a description of the format, such as a document or a C header? Drop it with the
  data file, into one folder. The AI uses it, and it stays in the folder without an H5
  file of its own.
- The play button turns into a stop button while Magic drop runs. Stopping, or a
  failure, removes the new folder.

Magic drop uses a command-line tool installed on your computer: **Claude Code**, or
**Codex** from OpenAI. LODGaZilla finds each on `PATH` or inside its VS Code extension,
or you can set its path. **Sign in** in the panel header opens the tool's own sign-in;
LODGaZilla never sees a password or key. The first drop with each tool asks whether
files may be sent to it (Claude to Anthropic, Codex to OpenAI). Don't drop files with
personal data, such as the location or heart rate of real people, unless you are
allowed to share them. The answers, whether each tool may read web pages, the cost
limit (Claude only, default $2 per drop or question) and the time limit (15 minutes)
are in **Settings > AI**.

#### What the AI can do on your computer

Both tools run Python on your computer with your permissions, to inspect the file and
test the parser they write. The parser then runs in a separate process with a time
limit, which **Stop** ends.

- **Claude** may use only its file tools, Python and (when allowed) web pages; any other
  tool or command is denied, and its file tools can't open known credential folders such
  as `.ssh` or the Git and cloud sign-in folders. But on Windows Claude Code has no
  sandbox, so **the Python it runs can read any file you can and reach the internet**.
- **Codex** runs its commands in its sandbox: they can write only in its working folder,
  the new data folder and the temporary folder, and have **no internet access**. They can
  still read your files. Its own web search reads pages when allowed.

A dropped file can contain text written to steer the AI (a "prompt injection"), for
example asking it to read and send out your files. Drop only files you trust; for files
from others, Codex is the safer choice, since its commands can't send anything out. The
Git hosting tokens of your environment are never passed to either tool or to the code it
writes. Web access can be switched off for each tool in Settings.

### Create plugin

Want a proper plugin instead, for a format you use every day, a device such as a data
logger over USB, or an export? Drop example files and the format specification on the
**Create plugin** box, say what you need (paste a link to the specification if there
is one), and press Enter.

1. On your first question LODGaZilla asks for the plugin's ID, such as
   `weather_logger_importer`, and creates `plugin/<id>` as its own Git repository.
2. The AI (Claude or Codex, as chosen in the panel header) reads your files and the
   plugin guide, asks what it needs to know, writes the plugin with its tests and runs
   them. LODGaZilla commits the plugin's changes after each answer; nothing is pushed.
3. Keep talking: each answer continues the same conversation. Drop more files on the
   box or the conversation whenever they help.
4. Your files are also copied to a folder named after the plugin in the data folder.
   Once the plugin has an importer, LODGaZilla imports them with it after each answer,
   so you see the H5 files in the Navigator and, in the AI panel, the series found or
   why a file could not be read.
5. When the plugin works, enable it in **Settings > Plugins**; no restart is needed. Only a
   plugin that needs new Python packages asks for one, to install them.

Your files go to the plugin's `.lodgazilla_ai` folder, which is never committed, as
are the AI's instructions. Claude may write only in the plugin's folder, run Python and
use Git only to look (status, diff, log, show); LODGaZilla makes the commits, with Git
hooks off. What the AI can do on your computer is the same as for Magic drop (above).

## Plugins

A plugin adds a file format or an action, such as an export. LODGaZilla runs fine
without any and still opens existing H5 files, images and videos.

Plugins are released separately, each in its own Git repository named
`LODGaZilla-<name>`:

| Plugin | Reads |
| --- | --- |
| [GPX importer](https://github.com/RikuRaitis/LODGaZilla-gpx_importer) | GPX tracks: points, segments and elevations |
| [PicoScope CSV importer](https://github.com/RikuRaitis/LODGaZilla-picoscope_csv_importer) | PicoScope CSV exports |
| [UBX GNSS importer](https://github.com/RikuRaitis/LODGaZilla-pyubx2_importer) | GNSS receiver logs in UBX and NMEA |

### Installing plugins

In **Settings > Plugins**, **Check now** looks in the **Plugin sources**, which start
with `https://github.com/RikuRaitis`; add any GitHub account or Bitbucket workspace that
publishes plugins. LODGaZilla lists the `LODGaZilla-*` repositories it finds there, with
**Install** and **Update** buttons. Installing and updating finish when LODGaZilla next
starts; new plugins start disabled, so enable them in Settings afterwards. A plugin you add to
the `plugin` folder yourself, or make with Create plugin, can be enabled at once.

Private plugins need a sign-in. With a Bitbucket workspace among the sources, **Sign in to
Bitbucket** appears below them: it opens Git Credential Manager's sign-in once, and the
check then runs again and finds the private plugins. Later checks use the saved sign-in.
For private GitHub repositories, LODGaZilla uses the sign-in Git already has.

LODGaZilla never stores passwords or tokens; Git Credential Manager keeps the sign-in in
the operating system's credential store. Only install plugins you trust: a plugin
runs Python code with your permissions.

### Where plugin actions appear

| Where | What |
| --- | --- |
| A folder's [Source data](#source-data) group | Each file an importer reads, and each export, with its plugin in the Plugin column. |
| Plot navigator toolbar | One icon per plugin with its own window. |

## Demo data

The demo data is a set of synthetic runs in `demodata/`. See
[Getting started](#5-plot-your-first-data) for how to create it.


| Session                              | Contents                                                                                                                                    |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `20261103_071500` Forest run, 60 s   | A watch with a 1000 Hz IMU, GNSS, barometer, heart rate, steps and more; a foot pod; a 4K chase-camera video; a 360 video; a route picture. |
| `20261105_174500`, `20261108_101500` | Two more 60 s runs with different metadata, for grouping and filtering.                                                                     |
| `20261109_060000` Long run, 2 h      | A 1000 Hz accelerometer and slower series, for zooming and sample limits.                                                                   |

The forest run hides a few problems for you to find:

- **Sample loss and clipping:** FIFO overflows drop IMU samples, and the jump landings
  and the steel bridge clip.
- **GNSS:** no fix under dense canopy.
- **Barometer:** one duplicated timestamp.
- **PPG:** timestamp jitter and a gap.
- **Vibration:** 50 Hz on the bridge.
- **Foot pod:** its clock runs 1.7 s ahead of the watch.
- **Chase camera:** started 4 s early and not synchronized. Sync it at a lap press,
  when the watch beeps and its screen lights up.

`demodata/README.md` has the details.

## Under the hood

### Why plotting is fast

- **Level-of-detail (LOD) summaries.** At import, each series is split into buckets,
  and each bucket's minimum and maximum are stored, at several zoom levels. Zoomed out,
  a card reads only as many buckets as the screen has pixels. Short peaks stay visible,
  because the minimum and maximum are kept. (Yes, that's where the name comes from.)
- **Every Nth sample.** X/Y, 3D plot and Map cards combine several series per point,
  so they draw every Nth sample instead, up to a [limit](#sample-limits) you choose.
- **Background work.** Reading data, statistics and video decoding run in the
  background, so the window stays responsive.
- **The GPU.** Video decodes on the graphics card when it can; the 3D cards draw
  with OpenGL and WebGL.

[HDF5](https://www.hdfgroup.org/solutions/hdf5/) is an open format for large numeric
data: a program can read a small part of a big array without loading the whole file.

### Plugin architecture

Each plugin is its own Git repository, installed as a folder under `plugin/`. There
are two kinds:

- **Importers** read one source format and return its data series. LODGaZilla does
  the rest: it writes the H5 file and its summaries, and runs imports in parallel.
- **UI plugins** add actions. An **operation** is a background job, such as exporting
  folders to CSV, which LODGaZilla lists in its menus. A plugin with its own window
  gets an icon in the Plot navigator toolbar.

Writing a plugin? See [the plugin API](docs/Plugin_API.md) and the
[H5 format guide](docs/H5_Format.md).

### Platforms

LODGaZilla is developed and tested on Windows 11. It is written in Python with
cross-platform libraries (Qt through PySide6, PyQtGraph, h5py and PyAV), so Linux and
macOS should work with little or no change. Nobody has tried yet, so be the first.


| Platform   | Status                                                                        |
| ---------- | ----------------------------------------------------------------------------- |
| Windows 11 | Tested. Main development platform.                                            |
| Linux      | Untested. Starts the same way. Video tries CUDA and VA-API hardware decoding. |
| macOS      | Untested. Starts the same way. Video tries VideoToolbox hardware decoding.    |

On Linux and macOS, video frames go through the CPU instead of staying on the GPU,
and cancelling an import stops its workers a little less abruptly.

## For developers

### H5 files

Each series is a group under `/series/<group>/<name>` with its timestamps, its
`value` dataset, optional original values in `value_raw`, and `lod/level_###`
summaries. The full layout, for plugin developers and AI agents alike, is in the
[H5 format guide](docs/H5_Format.md).

### Installing the requirements without starting

```
python tools/setup_environment.py
python tools/setup_environment.py --force
```

### Importing from the command line

With an importer plugin enabled:

```
python -m src.jobs.import_data "path/to/capture"
```

The H5 file is written next to the source.

### Tests

```
python tools/run_tests.py --isolated
python tools/run_tests.py --plugin plugin/<plugin id> --isolated
```

The first runs the core tests without any plugin, on a protected copy of the
settings. The second runs one plugin's tests against this checkout. The plain
`python -m unittest discover -s tests` works too, on the working checkout.

To test with really big data, `tools/create_large_h5_fixture.py 1024 --output-dir data/Synthetic`
creates a 1 GiB synthetic log and its H5 file: about 25 hours of three series at
1000 Hz. It needs a few GB of RAM.

To create the demo data by hand, run `python tools/create_demo_dataset.py`, or add
`--no-video --long-hours 0` for a one-minute version without videos.

To try [Magic drop](#magic-drop-import-any-file-with-ai) and [Create plugin](#create-plugin)
by hand, `tests/AI_demodata/` holds synthetic files in many formats: CSV, JSON, JSON
Lines, YAML, text, GPX, NMEA and binary, the binary ones with their descriptions as a
document or as C, C++ or Python source. Its
[README](tests/AI_demodata/README.md) lists what each file truly contains, so you can
check what the AI found. The small files are in Git. To make the big ones (about 80 MB,
in `tests/AI_demodata/big/`, ignored by Git) or to make all of them again, run
`python tools/create_ai_demodata.py`; it takes about 20 seconds. Add `--no-big` to skip
the big files, or `--output <folder>` to write them somewhere else.

The **live AI tests** run Magic drop and Create plugin on these files with the real
Claude and Codex, and check each result against the files' true contents
(`tests/AI_demodata/cases.json`): the series found, their sample counts and value
ranges, the start time, the duration and the metadata. They send the files to Anthropic
and OpenAI and spend tokens, so they are not part of the test suite; run them only
deliberately:

```
python tools/run_ai_tests.py --list                    # the cases
python tools/run_ai_tests.py --case csv_bike_power     # one case, both providers and modes
python tools/run_ai_tests.py --provider codex --mode magic
python tools/run_ai_tests.py                           # everything but the big files
python tools/run_ai_tests.py --big --jobs 3            # everything, three runs at a time
```

Before starting, it shows the number of runs and Claude's maximum cost (Claude's cost
limit in Settings, per agent run) and asks to confirm; `--yes` skips the question. Each
run takes up to the AI time limit (15 minutes by default); a full run of 15 cases is 60
runs. `--web` lets the agents read web pages; by default they don't, so results stay
comparable. The results, each agent's transcript and `report.md` go to
`logs/ai_tests/<time>/`. A run is **pass** when every check passes, **partial** when some
do, **fail** when none do, and **error** when no H5 file was made. Ctrl+C stops the
running agents and writes the report so far.

### Starting for testing


| Command                                           | Use                                                                                                                                                    |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `python -m src.app.lodgazilla --default-settings` | Uses the committed `app_settings.json` instead of your local `app_settings_debug.json`. Changes are saved to `app_settings.json`, so don't commit them. |
| `python -m src.app.lodgazilla --no-plugins`       | Loads no plugin, as on a clean installation. Your plugin settings are kept.                                                                            |
| `python -m src.app.lodgazilla --help`             | Lists the options.                                                                                                                                     |

Copy `app_settings.json` to the ignored `app_settings_debug.json` to keep your recent
folders, plugin choices and column layouts out of Git.

### Demo video

The demo video (about 3 minutes) is made from the demo data by scripts in
`tools/demo_video/`, on Windows. They drive LODGaZilla with real mouse and keyboard
input, record the window from the screen, and edit the recordings in code:

```
python tools/create_demo_dataset.py           # the demo data, if it isn't there
python tools/demo_video/record.py             # record every scene, about 15 minutes
python tools/demo_video/edit.py               # cut the video, about 10 minutes
```

While `record.py` runs, don't touch the mouse or keyboard and keep the screen unlocked.
Two scenes run Magic drop and Create plugin with Claude, so they need it signed in and
spend tokens; the map scenes need an internet connection. Re-record single scenes with
`--scenes`, such as `--scenes 3 6`. `edit.py` writes `build/demo_video/lodgazilla_demo.mp4`
(subtitles burned in), `lodgazilla_demo_clean.mp4`, `lodgazilla_demo.srt` and
`narration.txt`; `--voice narration.wav` uses a final narration instead of the Windows
draft voice. [docs/VIDEO_DEMO.md](docs/VIDEO_DEMO.md) describes the scenes, the narration
and how it all works.

### Versions and releases

LODGaZilla uses [Semantic Versioning](https://semver.org/): `1.0.0-beta.1`, then
`1.0.0`, `1.1.0`, `1.1.1`. The help (?) button's About dialog and the first lines of
`logs/lodgazilla.log` show the running version. What changed in each release is in
the [changelog](CHANGELOG.md), and how to make a release is in
[Releasing](docs/Releasing.md).

## License

Copyright (C) 2026 Riku Tervonen

LODGaZilla is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later version. It is distributed in
the hope that it will be useful, but without any warranty; without even the implied
warranty of merchantability or fitness for a particular purpose. See [LICENSE](LICENSE)
for the full text (SPDX: `GPL-3.0-or-later`).

Plugins are separate projects in their own repositories, each with its own license.

## Third-party libraries and licenses

LODGaZilla stands on the shoulders of these open-source projects. Versions are the
ones pinned in `constraints.txt` for Windows and Python 3.11.

**Python libraries**, installed by the setup:

| Library | Version | Used for | License |
| --- | --- | --- | --- |
| [PySide6](https://doc.qt.io/qtforpython-6/) (Qt for Python, with shiboken6 and Qt itself) | 6.11.0 | The whole user interface, the web view for maps, OpenGL | LGPL-3.0 (or GPL-2.0 / GPL-3.0) |
| [PyQtGraph](https://www.pyqtgraph.org/) | 0.14.0 | Plotting in time, X/Y, Frequency and 3D plot cards | MIT |
| [NumPy](https://numpy.org/) | 1.25.2 | Number crunching everywhere | BSD-3-Clause |
| [h5py](https://www.h5py.org/) (with the bundled HDF5 library) | 3.16.0 | Reading and writing H5 files | BSD-3-Clause (HDF5: BSD-style) |
| [PyAV](https://pyav.basswood-io.com/) (with the bundled FFmpeg libraries) | 18.1.0 | Video decoding and audio import | PyAV: BSD-3-Clause. The bundled FFmpeg is built with x264 and x265, which makes it GPL-2.0-or-later. |
| [PyOpenGL](https://pyopengl.sourceforge.net/) | 3.1.10 | Drawing video frames and 3D plots | BSD-style |
| [packaging](https://packaging.pypa.io/) | 23.1 | Checking plugin requirements and versions | Apache-2.0 or BSD-2-Clause |

Python itself is under the [PSF License](https://docs.python.org/3/license.html).

**JavaScript libraries**, loaded from [unpkg.com](https://unpkg.com/) by the map cards:

| Library | Version | Used for | License |
| --- | --- | --- | --- |
| [Leaflet](https://leafletjs.com/) | 1.9.4 | Map card | BSD-2-Clause |
| [MapLibre GL JS](https://maplibre.org/) | 5.12.0 | 3D Map card | BSD-3-Clause |
| [deck.gl](https://deck.gl/) | 9.2.0 | 3D Map card tracks | MIT |

**Map data and tiles**, fetched while a map card is open. These are services with
their own terms of use and attribution, not software licenses:

| Service | Used for | Terms |
| --- | --- | --- |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) tiles | Map card, street view | Data under ODbL; the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/) allows light use only. |
| [OpenFreeMap](https://openfreemap.org/) | 3D Map card | Free to use; data © OpenMapTiles and OpenStreetMap contributors. |
| [Esri World Imagery](https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9) | Map card, satellite view | [Esri terms of use](https://www.esri.com/en-us/legal/terms/full-master-agreement); attribution required. |
| [Terrain Tiles on AWS](https://registry.opendata.aws/terrain-tiles/) | 3D Map card terrain | Open data from several sources; attribution required. |
