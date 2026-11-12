"""Desktop regression for map/video composition, isolated per creation order."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


SCRIPT = r'''
import sys, time
from pathlib import Path
import av
import numpy as np
from PySide6.QtCore import QEvent, QTimer, qInstallMessageHandler
from PySide6.QtGui import QSurface
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from tests.qt_wait import qt_wait
from src.app.graphics import configure_graphics, shutdown_web_engine
from src.gui.plots.base import PlotBaseWidget
from src.gui.plots.workspace_layout import PlotStackSplitter
from shiboken6 import delete
from src.gui.plots.video.viewport import Viewport
from src.gui.plots.video.decoder import Picture
configure_graphics()
app = QApplication([])
messages = []
qInstallMessageHandler(lambda kind, context, message: messages.append(message))
class HostWindow(QWidget):
    def __init__(self):
        self.native_handle_changes = 0
        super().__init__()

    def event(self, event):
        if event.type() == QEvent.Type.WinIdChange:
            self.native_handle_changes += 1
        return super().event(event)

window = HostWindow()
layout = QVBoxLayout(window)
stack = PlotStackSplitter()
layout.addWidget(stack)
window.resize(1200, 800)
# Show an ordinary window first, just as the application does before the user
# adds a map or video. Creating a renderer before show() hides the regression.
window.show()
qt_wait(100)
native_handle = int(window.winId())
native_handle_changes = window.native_handle_changes
assert window.windowHandle().surfaceType() == QSurface.SurfaceType.OpenGLSurface
widgets = {}
cards = {}
loaded = []
class MediaCard(PlotBaseWidget):
    supports_horizontal_layout = True

for kind in sys.argv[1].split(','):
    if kind == 'map':
        widget = QWebEngineView()
        profile = QWebEngineProfile(widget)
        widget.setPage(QWebEnginePage(profile, widget))
        widget.loadFinished.connect(loaded.append)
        widget.page().loadingChanged.connect(lambda info: messages.append(info.errorString()) if info.errorString() else None)
    else:
        widget = Viewport()
        pixels = np.zeros((128, 128, 3), dtype=np.uint8)
        pixels[:, :, 0] = 220
        frame = av.VideoFrame.from_ndarray(pixels, format='rgb24').reformat(format='yuv420p')
        widget.set_picture(Picture(0, 0, (frame,), 'flat', 1))
    widgets[kind] = widget
    card = MediaCard(Path('.'), kind)
    card.content_layout.addWidget(widget)
    cards[kind] = card
    stack.add_card(card, 350)
    if kind == 'map':
        widget.setHtml('<html><body style="margin:0;background:rgb(0,220,0)"></body></html>')
    qt_wait(400)
    assert int(window.winId()) == native_handle, 'Adding ' + kind + ' recreated the host window'
    assert window.native_handle_changes == native_handle_changes, 'Host native handle changed'
deadline = time.monotonic() + 20
while not loaded and time.monotonic() < deadline:
    qt_wait(50)
assert loaded and loaded[-1], messages
qt_wait(500)
video = widgets['video']
assert video.error is None, video.error
picture = video.grabFramebuffer()
assert not picture.isNull(), messages
assert picture.pixelColor(picture.width()//2, picture.height()//2).red() > 150
assert picture.pixelColor(picture.width()//2, picture.height()//2).blue() < 40
map_image = widgets['map'].grab().toImage()
assert map_image.pixelColor(map_image.width()//2, map_image.height()//2).green() > 150
assert map_image.pixelColor(map_image.width()//2, map_image.height()//2).red() < 40
conflicts = [m for m in messages if 'not compatible with' in m or 'Failed to get a QRhi' in m]
assert not conflicts, conflicts
assert int(window.winId()) == native_handle, 'Page loading recreated the host window'
assert window.native_handle_changes == native_handle_changes, 'Host native handle changed'
# Keep both renderers updating until shutdown, as during video playback.
timer = QTimer()
timer.timeout.connect(video.update)
timer.start(15)
widgets['map'].page().runJavaScript("setInterval(() => document.body.style.opacity = '0.99', 15)")
qt_wait(200)
# Move live browser/OpenGL surfaces into and out of a shared row. Neither
# renderer may reload, lose its picture or recreate the top-level window.
page = widgets['map'].page()
context = video.context()
load_count = len(loaded)
for placement in ('right', 'left', 'below'):
    assert stack.move_card(cards['map'], cards['video'], placement)
    qt_wait(250)
    assert widgets['map'].page() is page
    assert len(loaded) == load_count, 'Moving the browser reloaded its page'
    assert video.context() is context, 'Moving the video replaced its GL context'
    assert int(window.winId()) == native_handle
    assert window.native_handle_changes == native_handle_changes
    picture = video.grabFramebuffer()
    assert not picture.isNull()
    assert picture.pixelColor(picture.width()//2, picture.height()//2).red() > 150
    map_image = widgets['map'].grab().toImage()
    assert map_image.pixelColor(map_image.width()//2, map_image.height()//2).green() > 150

timer.stop()
video.cleanup()
video.cleanup()  # The context destruction callback must also be harmless.
shutdown_web_engine(window)
window.close()
delete(window)
qt_wait(100)
failures = [m for m in messages if 'Failed to make OpenGL context current' in m
            or 'OpenGL resources will not be destroyed' in m]
assert not failures, failures
print(sys.argv[1], 'native window preserved; both widgets rendered and shut down without graphics warnings')
'''


@unittest.skipIf(os.environ.get('QT_QPA_PLATFORM') in ('offscreen', 'minimal'), 'Desktop graphics required')
class GraphicsCompositionTests(unittest.TestCase):
    def test_map_and_video_in_both_creation_orders(self):
        for order in ('map,video', 'video,map'):
            with self.subTest(order=order):
                result = subprocess.run([sys.executable, '-c', SCRIPT, order],
                                        cwd=Path(__file__).resolve().parents[1],
                                        capture_output=True, text=True, timeout=40)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
