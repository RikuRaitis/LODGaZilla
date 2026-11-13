# LODGaZilla demo video: plan

Status: plan agreed in outline, not started. A one-minute video with narration that
shows new users what LODGaZilla does.

## Goal

- At most 60 seconds, for people who have never used LODGaZilla.
- Mainly footage of the app, with zooms and pans to the parts being talked about.
- A natural female voice for the narration.

## How it is made

**Footage, without screen recording.** A script drives LODGaZilla with the demo data and
saves the window frame by frame, as `tools/take_readme_screenshots.py` already does for
the README pictures. Nothing is recorded in real time, so a slow laptop does not matter:
each frame waits until the app has drawn it, and the result plays smoothly. No personal
PC or screen recorder is needed.

**Zoom, pan and captions in code.** The camera glides to the Navigator, a values box or
the map exactly when the narration mentions it. Encoding uses ffmpeg and PyAV, which are
already installed; no video editing software is needed.

**Voice.** The voices built into Windows here (Microsoft Zira and Hazel) sound robotic.
The plan:

1. Claude writes the narration script with exact timings and makes a draft video with
   the Zira voice, to judge the pacing.
2. For the final, the script goes into **Clipchamp** (free, comes with Windows 11) to
   make the narration with one of its natural female text-to-speech voices. Claude then
   places the timed voice track in the video.

No other AI tool is needed: the video is made from this repository's code and demo data.

## Storyboard (60 s, about 140 words of narration)

| Time | What you see | Narration (draft) |
| --- | --- | --- |
| 0–6 s | Splash, then the main window full of demo workout cards | "Meet LODGaZilla: hours of sensor data, plotted in an instant." |
| 6–15 s | Zoom to the Navigator: data folder, grouped by date; a double-click opens a Line card | "Point it at a folder. Your recordings appear by date, and a double-click plots any series." |
| 15–26 s | Series dragged into a Multi-series card and a Map; red and green markers; zoom into a values box and the selection statistics | "Drag data into cards, compare it side by side, and place markers to read exact values and statistics." |
| 26–36 s | Video card playing in sync, with the marker moving along the data and map | "Attach video, and it plays in sync with your data and the map." |
| 36–44 s | Plot navigator: zoom from a full day down to single samples, smoothly | "Zoom from a whole day down to single samples, without waiting." |
| 44–54 s | Magic drop: a CSV dropped on the AI panel; the AI writes a parser; a new folder appears and is plotted | "Got a file in a new format? Drop it in. The AI writes the importer for you." |
| 54–60 s | End card: logo, "Free and open source", GitHub link | "LODGaZilla. Free, open source and ready for your data." |

## Open decisions

- **Theme:** light (suggested; reads better in small players) or dark, as in the README.
- **Scenes and wording:** change anything in the storyboard above before building.
- **Voice:** which Clipchamp voice for the final.

## Notes

- **Magic drop scene:** a real run takes minutes and spends tokens, so it uses one run
  recorded once and shortened; still real footage.
- **Publishing:** if the video goes out under Suunto's name or on Suunto channels, it is
  reviewed before publishing. The end card names only the public GitHub repository, never
  a private plugin.
- **Effort:** the frame-capture script, the camera moves and a draft with the Zira voice
  fit in one session; the draft is reviewed before the final voice.
