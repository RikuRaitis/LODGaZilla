# LODGaZilla demo video

A video of about 2 min 50 s that shows new users what LODGaZilla does: a one-minute
overview, then **A closer look at the cards**. It is made entirely from this
repository's code and the demo data, by scripts in `tools/demo_video/`: the app is
driven with real mouse and keyboard input, recorded from the screen, and edited in code.

Status (2026-11-13): draft with the Windows Zira voice and generated music; the final
voice comes from Clipchamp.

## Contents

| Part | Scenes | What you see |
| --- | --- | --- |
| Opening card | – | Black, then the logo and the tagline "Every file format, billions of points, one timeline." |
| Overview | 1–6 | The main window full of cards; the Navigator by date and a double-click; dragging data into a card, both markers and a values box; a video playing in sync; zooming from hours to single samples, slowing down as it closes in so the points appear gradually; Magic drop with a real AI run (sped up, with a badge saying so) |
| Title | – | "A closer look at the cards" |
| A closer look | 7–21 | Fifteen shots, below |
| End card | – | The logo, "Free and open source" and the GitHub address |

The closer look, in order (scene numbers are `record.py`'s):

| Scene | Shows |
| --- | --- |
| 7 | Navigator: Sample loss and Clipping highlights; add a metadata key, delete it, group by `runner`, filter by `sport` |
| 8 | Line card: zoom to the shaded clipping and sample loss at 29 s, pan, Points / Lines / Auto |
| 17 | Values box details: click to pin, select the statistics, Ctrl+C |
| 9 | Multi-series: drop a video as a filmstrip first (its frames render meanwhile), then select a trace, move its lane, scale it |
| 18 | Card drop selector: hold a series between cards, pick Sample interval |
| 10 | Time offset: the foot pod's speed lines up with the watch's after −1.7 s |
| 19 | Plot navigator bars: drag sideways (offset), up (reorder), trim an end, reset |
| 11 | Sample interval (the GNSS gap) and Event/state (laps, fix, phases) |
| 12 | Frequency card on the video's soundtrack: FFT window size, colour range, zoom |
| 13 | X/Y and 3D plot: raise the sample limit (every Nth sample), rotate, zoom |
| 14 | Video sync at a lap press; 360 video: look around, roll, field of view, reset |
| 15 | Map: Satellite, pan, zoom, Street, box zoom; 3D Map: tilt and rotate, zoom, height, map opacity |
| 20 | Create plugin: drop a binary example and its C header, plugin ID, a real AI run (sped up, with a badge), the import plotted |
| 16 | Workspaces: Ctrl+S, Close all, Open recent workspace |
| 21 | A card's Help and the Settings dialog |

Every action in the closer look has a short label, such as **Ctrl + wheel · zoom**.

## Making the video

```
python tools/create_demo_dataset.py           # the demo data, if it isn't there
python tools/demo_video/record.py             # all 21 scenes; or --scenes 3 6
python tools/demo_video/edit.py               # the videos, with the Zira draft voice
python tools/demo_video/edit.py --voice narration.wav   # with the final voice
python tools/demo_video/preview.py            # each scene playable for review; or scene3 scene6
```

**Reviewing scenes** (`preview.py`, seconds). Players guess the raw `scene<N>.h264`
streams' speed (VLC plays them too fast). `preview.py` copies them, without re-encoding,
into `build/demo_video/preview/scene<N>.mp4` with their recorded frame times, so they
play at real speed and the player's clock matches the scene's `events.json` times. The
cursor is drawn only by `edit.py`.

**Recording** (`record.py`, about 15 minutes plus two AI runs). It starts LODGaZilla in
a window whose contents are 1920 × 1080 screen pixels and plays each scene: set up off
camera, then recorded while the real cursor moves, clicks and types. Nobody may touch the
mouse or keyboard meanwhile, and the screen must stay unlocked. Its progress goes to
`build/demo_video/record.log` (the app takes over standard output).

- **The recorder** (`recorder.py`) runs in its own process, started with the environment
  from before Qt loads (Qt puts its own FFmpeg libraries on PATH). It grabs the window's
  rectangle with FFmpeg's `gdigrab` through PyAV at 25 frames per second, without the
  Windows cursor, into a raw H.264 stream: PyAV's MP4 and MKV writers corrupted frames
  under load.
- **Input** (`mouse.py`) uses Windows `SendInput`, so the app reacts as for a user; every
  move, click, key and label is logged with its time, on the same clock as the frames.
- **The data folder** is a staged copy, `C:\Users\Public\LODGaZilla_demo\demodata`: hard
  links to `demodata/`, with the info files and audio H5 files copied because the app
  writes to them. No user name shows in the picture, and edits on camera never reach
  `demodata/`. Delete `C:\Users\Public\LODGaZilla_demo` when you no longer record.
- **Files from Explorer** (scenes 6 and 20) come from a real Explorer window that shows
  them through a temporary drive letter, placed so its side pane with the PC's drive
  names is outside the picture.
- **AI runs:** scene 6 (Magic drop, `tests/AI_demodata/csv/bike_ride_power.csv`) and
  scene 20 (Create plugin, `tests/AI_demodata/binary/c_header/`) use Claude; a Magic
  drop take cost $0.34, and Create plugin took about 1.5 minutes of AI work. The consent question is skipped in the take's own settings. Scene 20's plugin,
  `plugin/demo_motion_logger`, is deleted after the take.
- **Map tiles** load from the internet; the take needs a connection.

**Editing** (`edit.py`, about 5 minutes: both videos in one pass, each frame decoded and painted once) writes to `build/demo_video/` (ignored by Git):

- `lodgazilla_demo.mp4`: subtitles burned in, for players that start muted.
- `lodgazilla_demo_clean.mp4`: no burned-in subtitles, for sites that use the `.srt`.
- `lodgazilla_demo.srt`: the subtitles.
- `narration.txt`: the narration, one line per paragraph, for the voice tool.

It draws the cursor with a halo, red and green click rings (the markers' colours) and a
Ctrl badge, moves the camera, draws the labels, subtitles, opening, title and end cards,
and mixes the voice and music. In the closer look, pauses between inputs are cut to
0.7 s and the footage plays 1.75× faster, but each shot lasts at least as long as its
voice line. The AI runs are shortened to about two seconds, with a badge giving the
factor. The overview is not sped up.

## Narration

The lines are in `tools/demo_video/edit.py` (`OPEN_LINE`, `OVERVIEW`, `TITLE`,
`CHAPTER`, `END_LINE`); `edit.py` writes them to `narration.txt`, and times the closer
look's shots by how long each line takes to say.

1. LODGaZilla. Every file format, billions of points, one timeline.
2. Point it at a folder, and your recordings appear by date. Double-click any series to plot it.
3. Drag data into cards to compare it side by side. Place markers to read exact values, and select a range for its statistics.
4. Attach a video, and it plays in sync with your data and the map.
5. Zoom from hours of data down to single samples, without waiting.
6. Got a file in a new format? Drop it in, and the AI writes the importer for you.
7. A closer look.
8. Sample loss and clipping stand out at a glance. Add your own metadata, then group and filter by it.
9. Line cards shade sample loss and clipping. Pan, zoom, and switch to points.
10. Click a values box to pin its details, then copy the numbers.
11. Videos join as filmstrips, and each series gets its own lane to move and scale.
12. Hold a series between cards, and pick the card type it fits.
13. Clocks disagree? Shift a series until it lines up.
14. Drag a card's bar to shift, reorder or trim it.
15. Spot gaps and jitter, and follow phases and events.
16. Tune the spectrogram's window and colour range, and zoom in for detail.
17. X-Y and 3D plots draw every Nth sample; raise the limit for more detail.
18. Sync a video at a lap press. With 360 video, look around, roll and zoom.
19. Maps pan, zoom and switch to satellite. The 3D Map tilts and turns, and exaggerates the hills.
20. Need it again? Create plugin turns example files and a spec into a reusable importer.
21. Save it all as a workspace, and it reopens exactly as you left it.
22. Each card's help lists its mouse actions, and Settings holds the rest.
23. LODGaZilla. Free, open source, and ready for your data.

**Final voice:** paste `narration.txt` into Clipchamp and choose a natural female voice.
Place each line at its start time in `lodgazilla_demo.srt` and export one WAV track, then
run `edit.py --voice narration.wav`. The shots are always timed by the Zira lines, so the
times stay the same; a final line longer than the Zira one may run into the next.

## Music

`edit.py` generates the music in code, so no licence applies: soft pad chords
(A minor, F, C, G), a bass note, a pulse at 100 beats a minute, an arpeggio and a melody
of quarter notes over the chords (`MELODY`). The opening card starts with the full beat
and an impact as the logo appears, and a second as the tagline appears; the end card's
logo gets one too. It pulses through the overview, accents the chapter title, carries on
nearly as strong through the closer look and resolves on the end card. It peaks about
15 dB below full scale and drops another 8 dB under each narration line, but only 4 dB
under the opening and end cards' lines.

`--music track.wav` uses a chosen track instead, with the same ducking and fades; use
only a track whose licence allows use in a published video, keep a copy of that licence,
and credit it if it asks. `--no-music` leaves the music out.

## Decisions

- **Look:** dark theme; window contents 1920 × 1080 at 25 frames per second; not
  published under any brand.
- **Layout:** legends hidden except when a shot uses them; AI Integration collapsed
  except in the AI shots.
- **Length:** under 3 minutes and as compact as possible; speeding up footage is
  preferred over leaving a feature out.
- **Subtitles:** the narration's words, burned in, plus an `.srt` file.
- **Tagline:** its claims were tested by the author.

## Known app issues seen while recording

- **The Frequency card shows nothing for the IMU series** (tested: Accelerometer X and
  Z, Gyroscope Z). The accelerometer's timestamps jitter by about ±3.5% around 1 ms,
  more than the card's 2% tolerance (`src/gui/plots/frequency_data.py`): "No valid FFT
  windows: data contain gaps or are not regularly sampled." The demo data README says
  the bridge's 50 Hz vibration shows in the Frequency card; it can't with this data.
  Scene 12 uses the video's soundtrack instead.
- **An Event/state card can open on a range under a second**, and with linked time views
  every time card follows it. `record.py` zooms out to the whole run before each scene.
- **With no grouping, the Navigator's Group by field reads "[FILTER]".**
- **The 3D Map page logs "Expected value to be of type number, but found null"** (also
  in `Code_review.md`).
