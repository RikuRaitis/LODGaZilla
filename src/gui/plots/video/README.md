# LODGaZilla video card

From the repository root:

```powershell
python -m pip install -r requirements.txt
python -m src.gui.plots.video
python -m src.gui.plots.video "C:\videos\VID_example.insv"
python -m src.gui.plots.video "C:\videos\example.mp4"
python -m src.gui.plots.video "C:\videos\preview.lrv"
```

In LODGaZilla, attach a video to a data folder and open its video card.
`VideoPlotWidget` inherits from `PlotBaseWidget`, including card activation,
reordering, height resizing and close controls.
MP4 uses the first video track (ignoring cover art) and fits the image to the
window with its display aspect ratio and rotation metadata respected. MP4
shares the decoder, hardware acceleration, OpenGL renderer, audio output,
timeline and playback controls with INSV; no additional dependencies are needed.

For INSV, drag with the left mouse button to look left/right and
up/down relative to the current screen. Horizontal dragging keeps following the
screen's left/right direction even after looking straight up or down, rather
than spinning around the scene's fixed vertical axis. You can look through a
full 360° in either direction.
Hold Ctrl and scroll to zoom. Hold Ctrl and drag horizontally to roll around
the current viewing direction. Double-click to reset the view and zoom.
Without Ctrl, the wheel scrolls the LODGaZilla workspace.
Use the play/pause and mute buttons, volume slider, and draggable timeline.
Volume and mute affect the file's original audio. Files without audio also play.
360° dragging and zoom apply only to INSV; standard MP4 remains fitted to the window.

The card uses `widget.py` for UI/synchronization, `decoder.py` for background
media readers, and `viewport.py` for OpenGL display. No conversion, remux, disk
proxy, external FFmpeg executable, or Insta360 SDK is needed.

## Data synchronization

The folder H5 cache supplies `log_start_unix_seconds`. Video time maps to log
time as `log_start + offset_seconds + video_position`. Press **Sync** on the
playback bar to show the sync controls; they hide again when a sync completes.
Sync uses two buttons, pressed in either order:

- **Set data marker** stores the red (first) marker's time as the sync point.
  With Markers linked it is the last red marker placed in a data card, so video
  playback moving the shared marker does not change it; unlinked, it is the
  active card's red marker. The stored time is shown as a purple dashed **sync
  marker** on every card, the Plot navigator and Timeline sliders, and stays put
  while the red marker moves.
- **Set video marker** stores the current video position: seek to the frame that
  matches the sync point, then press it.

Each icon shows whether its point is set; pressing it again replaces the point.
Once both are set, the workspace calculates
`offset = sync point - log_start - video position`, clears both points and the
sync marker, and saves it to that video's entry in the folder metadata's `videos` array, alongside
its `path` and `offset_seconds`. Each attached video has its own offset.
The offset field beside the sync buttons adjusts the offset by hand, typed or
with its up/down arrows (0.1 s steps). The change applies at once, and with
Markers linked the video follows the red marker; it is saved once it stops
changing.
Linked markers seek video; video playback
updates data markers at most ten times per second. Out-of-range markers show
a blank frame and pause playback.

Cards open paused. Removing a card stops its timers, audio and decoder workers.

## Shared components

- **PyAV / FFmpeg** (`decoder.py`): demuxing, video/audio decoding, hardware
  acceleration, seeking, scaling and audio resampling. One video reader for
  regular MP4, two synchronized readers for dual-lens INSV.
- **PySide6 / Qt** (`widget.py`, `audio.py`): widgets, playback controls and timing;
  `QAudioSink` plays decoded stereo PCM.
- **PyOpenGL / OpenGL 3.3** (`viewport.py`): reusable YUV/RGB textures in a
  `QOpenGLWidget`, color conversion, and either flat video display or INSV
  fisheye projection and blending.
- **D3D11 / WGL interop** (`d3d11.py`, Windows only): optional GPU texture sharing
  between hardware decoding and OpenGL, with CPU transfer fallback.

## Performance

- Preview size defaults to **Auto**. It follows the fitted MP4 image dimensions
  or estimates the lens resolution needed for the INSV field of view. Zooming
  into INSV increases the requested lens size. Source resolution and an
  8192-pixel maximum bound the result; automatic sizes align to even pixels.
- Auto uses physical pixels (including display scaling) and the renderer's
  existing 1000 x 600 maximum render target, so larger textures are not requested
  merely to match a larger upscaled window. It is a resolution-based estimate,
  not a GPU benchmark or automatic frame-rate controller.
- Window resizing and zoom changes settle for 200 ms before resizing textures.
  Playback continues without seeking; paused CPU frames are refreshed at the
  same position. GPU frames can be resized directly while paused.
- Select **Manual** to type any preview size from 128 to 8192 pixels. Passing
  `--max-side N` starts in Manual mode; omitting it starts in Auto mode.
- PyAV calls libavformat/libavcodec directly. On Windows, auto mode tries
  D3D11VA, then CUDA, then CPU. Each backend must successfully decode a frame;
  the status bar reports the active backend for each video track.
- For INSV, two independent readers address the two video tracks. This avoids retaining
  large bursts of decoded frames when the camera interleaves tracks in chunks.
- Video and audio decode on background threads. Three frame pairs and 32 PCM
  chunks bound the queues. Seeking invalidates old work using generation IDs.
- Each image is scaled to the selected preview size before upload. YUV420
  planes go directly to reusable OpenGL textures; there is no NumPy RGB image
  conversion. The shader dewarps, blends and converts color only for the visible
  view, capped at 1000 x 600 pixels, then GPU-upscales to the window size.
- Mouse movement reuses current textures, including while paused.
- With audio available, consumed PCM samples drive the master clock. Video
  selects frames by PTS and skips late display frames. Without audio output,
  a monotonic clock drives video. Seeking flushes PCM and video buffers.

On compatible Windows hardware, D3D11 frames stay on the GPU for scaling/color
conversion and sharing with OpenGL. If texture sharing fails, the player restarts
using CPU transfer. Other hardware backends also transfer to CPU YUV before
upload. `--cpu-transfer` disables the shared texture path explicitly.
A smaller viewport does not reduce HEVC decoding cost. Performance with two
full-resolution X6 streams must be measured on the target laptop; there is no
promised 8K30/50 frame rate.

```powershell
python -m src.gui.plots.video --hwaccel d3d11va --max-side 1024 --verbose "clip.insv"
python -m src.gui.plots.video --hwaccel none "clip.insv"
```

Explicit backend choices also fall back to CPU on initialization failure and
report that choice. Decode errors occurring after playback starts are surfaced
in the GUI; reopen with `--hwaccel none` to diagnose driver failures.

## Supported scope and remaining camera validation

Supported inputs are **two square fisheye video tracks inside a single INSV or LRV**
and **ordinary flat MP4 or single-track LRV video**. LRV files can be selected in
both the folder attachment dialog and the standalone player's file picker.
They use the same playback, audio, seeking, and per-attachment sync offsets.
LRV track layout is detected when opening: two square lens tracks use the 360°
renderer. Insta360 previews with camera metadata and two square views packed
side by side in one track (including X6 LRV) are split into separate lens images
and use the same renderer and calibration. Other single-track previews display
as encoded. Packed previews support hardware decoding with CPU frame transfer
for lens splitting.
Single-track packed INSV and older cameras'
paired files remain unsupported. MP4 files are displayed as flat video;
equirectangular 360° MP4 projection is not implemented.

The bounded trailer reader and factory UCM lens model are adapted from
[VR180 Silver Bullet](https://github.com/silverqsy/VR180-Silver-Bullet), including
sensor crop, principal points, radial/tangential/thin-prism terms and reversed
track order. Its measured X6 KB lens model is the labeled fallback when metadata
is missing. See `THIRD_PARTY_NOTICES.md` for the MIT notice and source locations.

The camera is assumed to have stock back-to-back lenses with upright video
streams. Factory lens **extrinsic rotations/translations are not applied**;
seam blending uses ideal opposing optical axes. This can leave alignment errors,
ghosting and parallax, particularly close to the camera. This is a basic test
viewer, not Insta360 Studio-quality stitching. No optical flow, gyro/FlowState
stabilization, horizon lock, lens-guard profile, I-Log LUT, HDR tone mapping, or
view-dependent ambisonic audio is implemented. Preview is 8-bit SDR, with the
frame's YUV matrix/range respected; log/HDR clips will not look like a graded
Studio export. Audio is downmixed to stereo.

Synthetic tests exercise parsing, dual-track INSV and single-track MP4 decoding,
pairing, PCM, bounds, seeking, display aspect/rotation and flat/360 rendering.
A real X6 recording is still required to verify file variants,
orientation, seam alignment, color metadata and sustained performance.

```powershell
python -m unittest tests.test_video_decoder -v
python -m unittest tests.test_video_gui -v
```

The second command requires an interactive desktop and OpenGL 3.3. It creates
brief test windows and checks GPU rendering, pause/seek, volume/mute and EOF.

Synthetic H.264 validation verifies decoding and rendering behavior, not HEVC
8K throughput or actual X6 image quality.
