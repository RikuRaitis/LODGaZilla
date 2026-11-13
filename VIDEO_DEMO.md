# LODGaZilla demo video: plan

Status: plan agreed, recording test passed (2026-11-13); building not started. A
one-minute video with narration that shows new users what LODGaZilla does.

## Goal

- At most 60 seconds, for people who have never used LODGaZilla.
- Mainly footage of the app, with zooms and pans to the parts being talked about.
- Mouse cursor visible, and highlighted when pointing or clicking.
- A natural female voice for the narration.
- Subtitles with the narration's words, for people watching without sound.
- Dark theme, 1920 × 1080. Not published under any brand.

## Screen layout

- **Three card rows in view.** Multi-series on top, the **Frequency** card of the video's
  soundtrack (`Media/Audio left`) second, and the Video, 3D plot and Map cards side by
  side at the bottom. Tested: with row heights 165, 165 and 215 the whole layout fits
  without scrolling, the Video card's Playback bar included.
- **Legends hidden** (the Plot navigator's Legends button), so the plots get the
  space. They are shown once, in scene 3: the cursor clicks the Legends button, the
  legends appear while a series is dragged into a card (where the Legend insertion hint
  shows), and the Legends button hides them again before scene 4.
- **AI Integration collapsed** to its single row, except in scene 6 (Magic drop), where
  it opens.
- The Navigator stays at the left; the Navigator tree is expanded to the demo run
  before scene 2, so the panel isn't empty.

**App details the layout test showed, now fixed:**

- The Frequency card's status line under the plot is gone; its FFT details and problems
  go to the Internal log panel, once per change ("every 3rd window", no longer "3th").
- The Frequency card's time axis has no "Time (x1e+09)" label, and its colour bar
  shows "dB" when the full "Amplitude" label doesn't fit.
- The 3D plot lists its three axis minimums in one label at the shared corner, where
  three labels overlapped.
- Header controls that don't fit a narrow card continue on a row below, so the 3D plot's
  Reset camera button and the Map type selector are no longer clipped.
- The Map and 3D Map cards show their values boxes again (not needed in the video).

Without the Frequency card's status line the rows have a little more room; the row
heights are checked again when the recording script is built.

## How it is made

The video is made in three steps, all from this repository's code and the demo data. No
video editing software or other AI tool is needed.

1. **Record.** A script starts LODGaZilla with the demo data and drives it scene by
   scene on the narration's timing. A separate recorder process captures the window
   from the screen while the app runs, and the script logs every cursor move and click.
2. **Edit in code.** A second script adds the cursor highlight and click effects from
   the log, zooms and pans the camera to the part the narration mentions, adds the
   subtitles, and encodes the video.
3. **Voice.** The narration is added, first as a Zira draft and then as the final voice
   (see Voice below).

**Recording.** PyAV (already installed) grabs the screen with FFmpeg's `gdigrab`, at a
fixed rectangle around the window's contents, so nothing else on the desktop is
captured. The recorder runs in its own process, so it doesn't slow the app. It writes a
raw H.264 stream and the time of each frame; the editing step turns that into the final
MP4. During a take the window stays on top and the mouse and keyboard must not be
touched.

**Window size.** The window's contents are exactly 1920 × 1080 screen pixels, which
fits on the 2560 × 1440 screen. At Windows' 150% scaling the app lays itself out as
1280 × 720, so each scene shows only a few cards, large enough to read.

**Fallback: frame by frame.** If a scene can't be recorded smoothly, the script instead
saves the window frame by frame, as `tools/take_readme_screenshots.py` does for the
README pictures: each frame waits until the app has drawn it. That needs no real-time
speed, but the Video card would have to be stepped one frame at a time.

### Cursor highlight and click effects

The recorder captures the screen without the Windows cursor (`draw_mouse=0`). The
editing step draws the cursor itself, from the log, so its look is the same in every
scene and every effect lands on the exact frame.

- **Driving the mouse.** The script moves the real cursor along smooth, slightly curved
  paths at a natural speed, and clicks, double-clicks and drags with real Windows input
  (`SendInput`), so the app reacts exactly as it does for a user.
- **The log.** Each move, button press and release is logged with its position and
  time. The recorder stamps its frames with the same clock (Windows'
  `QueryPerformanceCounter`), so every event maps to a frame without guessing.
- **What is drawn.**
  - **Cursor:** a standard arrow, slightly enlarged, with a soft, semi-transparent halo
    so it stands out on the dark theme.
  - **Click:** a ring that expands and fades over about 0.4 s. Left clicks are red and
    right clicks green, the colors of the first and second markers they place.
  - **Double-click:** two quick rings.
  - **Drag:** the halo stays filled while the button is held, so a drag reads as
    one gesture.
  - **Idle:** after about two seconds without movement the halo fades, so a resting
    cursor doesn't distract.
- **With zoom.** The effects are drawn on the full-size frame before the camera crop,
  so they scale and move with the zoom.
- **Drawing.** Qt's `QPainter` on each frame (antialiased; PySide6 is already
  installed), then encoding with PyAV.

### Subtitles

The subtitles use the narration's exact words, so they need no separate script. They
come in two forms, made from the same timed text:

- **Burned into the picture** (open captions), so they show everywhere, including
  players that start muted and sites without caption support. The editing step draws
  them with `QPainter` after the camera crop, so they keep the same size and place
  whatever the zoom.
- **A separate `.srt` file** next to the MP4, for sites such as YouTube where viewers
  can turn captions on and off. If a site uses the `.srt`, upload the version of the
  video without burned-in subtitles too, so they don't show twice. Both versions are
  made in the same run.

**Look.** White text, about 44 px high at 1080p, on a semi-transparent black box with
rounded corners, centred near the bottom. At most two lines of about 42 characters, so
a long narration line is split at a natural pause into two subtitles. Each subtitle
shows for the time its words are spoken, at least one second.

**Timing.** First from the narration script's start and end times, split by word
count. Once the final voice track exists, the times are set from the track itself: the
pauses between lines are found in the audio, so each subtitle starts with its words.

**Placement.** The bottom of the picture shows the Video card's Playback bar and the
Map. Where a subtitle would cover something the narration talks about, it moves to the
top of the picture for that subtitle.

### Voice

The voices built into Windows here (Microsoft Zira and Hazel) sound robotic, so:

1. Claude makes a draft video with the Zira voice, to judge the pacing.
2. For the final, the script goes into **Clipchamp** (free, comes with Windows 11) for
   one of its natural female text-to-speech voices. Claude then places the timed voice
   track in the video. The voice is chosen later.

## Recording test (2026-11-13)

The heaviest scene was recorded for 10 seconds: the Video card playing, with a Line card,
a 3D plot and a Map in view and both markers placed.

| Measure | Without recording | While recording |
| --- | --- | --- |
| Video card playback | 25–31 FPS | 28–31 FPS |
| Longest app pause | 33 ms | 46 ms |
| Recorded frames | – | 25.3 per second, longest gap 68 ms |
| Frames that decode | – | 255 of 255 |

**Result: screen recording works.** The app runs just as smoothly while being recorded,
and the Video, 3D plot (OpenGL) and Map (web view) cards all appear in the recording as
on screen. Findings for the build:

- **Record at 25 frames per second.** A 1920 × 1080 screen grab reaches about 25 per
  second in the heaviest scene, so 25 is steady, where 30 would stutter.
- **Write a raw H.264 stream, not MP4 or MKV.** PyAV's file writer corrupted frames
  under load in both containers; the raw stream was clean. The editing step writes the
  final MP4 when nothing else is running.
- **Start the recorder before Qt loads.** Qt adds its own FFmpeg libraries to `PATH`;
  the recorder gets the environment from before that.
- **Hide the real folder path.** The data folder field shows the user's path; show
  `LODGaZilla\demodata` instead, as the README screenshots do. Also clear the status
  bar of unrelated messages before each take.

## Storyboard (60 s, about 100 words of narration)

The final wording and timings are in [Narration script](#narration-script).

| Time | What you see | Narration (first draft) |
| --- | --- | --- |
| 0–6 s | Splash, then the main window full of demo workout cards | "Meet LODGaZilla: hours of sensor data, plotted in an instant." |
| 6–15 s | Zoom to the Navigator: data folder, grouped by date; a double-click opens a Line card | "Point it at a folder. Your recordings appear by date, and a double-click plots any series." |
| 15–26 s | Series dragged into a Multi-series card and a Map; red and green markers; zoom into a values box and the selection statistics | "Drag data into cards, compare it side by side, and place markers to read exact values and statistics." |
| 26–36 s | Video card playing in sync, with the marker moving along the data and map | "Attach video, and it plays in sync with your data and the map." |
| 36–44 s | Plot navigator: zoom from a full day down to single samples, smoothly | "Zoom from a whole day down to single samples, without waiting." |
| 44–54 s | Magic drop: a CSV dropped on the AI panel; the AI writes a parser; a new folder appears and is plotted | "Got a file in a new format? Drop it in. The AI writes the importer for you." |
| 54–60 s | End card: logo, "Free and open source", GitHub link | "LODGaZilla. Free, open source and ready for your data." |

## Narration script

About 100 words in 60 seconds, roughly 2.2 words per second. That is calm for a
text-to-speech voice, and it leaves short pauses so each scene can land before the next
line starts. Times are where each line starts and should end; the footage follows them.

| # | Start–end | Scene | Line | Words |
| --- | --- | --- | --- | --- |
| 1 | 0.5–5.5 s | Splash, main window with demo cards | Meet LODGaZilla. Hours of sensor data, plotted in an instant. | 10 |
| 2 | 6.5–14.5 s | Navigator, grouped by date; double-click opens a Line card | Point it at a folder, and your recordings appear by date. Double-click any series to plot it. | 17 |
| 3 | 15.5–25.5 s | Drag into Multi-series and Map cards; red and green markers; values box and selection statistics | Drag data into cards to compare it side by side. Place markers to read exact values, and select a range for its statistics. | 23 |
| 4 | 27.0–35.0 s | Video card playing in sync, marker moving on the data and map | Attach a video, and it plays in sync with your data and the map. | 14 |
| 5 | 37.0–43.5 s | Plot navigator: zoom from the long run down to single samples | Zoom from hours of data down to single samples, without waiting. | 11 |
| 6 | 45.0–53.5 s | Magic drop: CSV dropped, AI writes a parser, new folder plotted | Got a file in a new format? Drop it in, and the AI writes the importer for you. | 17 |
| 7 | 55.0–59.5 s | End card: logo, "Free and open source", GitHub link | LODGaZilla. Free, open source, and ready for your data. | 9 |

**Text for the voice tool.** Paste this as it is. Each blank line is a pause of about one
second; Clipchamp's pause setting, or splitting the lines into separate clips, places
them at the start times above.

```
Meet LODGaZilla. Hours of sensor data, plotted in an instant.

Point it at a folder, and your recordings appear by date. Double-click any series to plot it.

Drag data into cards to compare it side by side. Place markers to read exact values, and select a range for its statistics.

Attach a video, and it plays in sync with your data and the map.

Zoom from hours of data down to single samples, without waiting.

Got a file in a new format? Drop it in, and the AI writes the importer for you.

LODGaZilla. Free, open source, and ready for your data.
```

**Notes on the script**

- **Scene 5 says "hours", not "a whole day".** The longest demo run is 120 minutes. To
  show a full day, record this scene on the 25-hour file from
  `tools/create_large_h5_fixture.py`, and the line can say "a whole day" again.
- **Scene 6** matches what Magic drop does: the AI reads the file, writes and tests a
  parser, and LODGaZilla writes the H5 file and adds the folder to the Navigator.
- **"Free, open source"** is accurate: LODGaZilla is under GPL-3.0 (see `LICENSE`).
- **Pronunciation:** check how the voice says "LODGaZilla". If it reads the letters
  oddly, spell it phonetically for the voice only, for example "Lodge-a-zilla".
- **Timing check:** after the Zira draft, move each clip to its start time. A line that
  runs past its end time is shortened, not sped up.

## Decisions

- **Theme:** dark.
- **Window:** 1920 × 1080 screen pixels, recorded from the screen at 25 frames per second.
- **Brand:** none; the video is not published under any brand.
- **Voice:** a Clipchamp voice, chosen later, after the Zira draft.
- **Layout:** three card rows (Multi-series, Frequency, then Video, 3D plot and Map);
  legends hidden except in scene 3; AI Integration collapsed except in scene 6.
- **Subtitles:** the narration's words, burned in, plus an `.srt` file.

## Notes

- **Magic drop scene:** a real run takes minutes and spends tokens, so it uses one run
  recorded once and shortened; still real footage.
- **End card:** names only the public GitHub repository, never a private plugin.
- **Effort:** the recording script, the cursor effects, the camera moves and a draft with
  the Zira voice fit in one session; the draft is reviewed before the final voice.
