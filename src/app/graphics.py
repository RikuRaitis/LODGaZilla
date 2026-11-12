"""One composition API for WebEngine/Qt Quick maps and OpenGL video."""
import os

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

_configured = False


def shutdown_web_engine(window):
    """Destroy browser compositors before their top-level native surface closes.

    Deferred deletion after the event loop exits is too late for Chromium's
    shared OpenGL textures.
    Only call during final window shutdown, after stopping playback updates.
    """
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from shiboken6 import delete

    for view in window.findChildren(QWebEngineView):
        view.blockSignals(True)
        view.stop()
        delete(view)


def configure_graphics():
    """Configure composition before QApplication or any native window is created."""
    global _configured
    if _configured:
        return
    # The first WebEngine/QQuickWidget or QOpenGLWidget would otherwise switch
    # an already visible window from raster to OpenGL and recreate its native
    # handle on Windows. Start with the final composition API, without loading
    # Chromium. Both controls are needed: RHI defaults to Direct3D on Windows.
    # Headless test platforms do not support this GPU composition path.
    if os.environ.get("QT_QPA_PLATFORM", "").split(":", 1)[0].casefold() not in {"offscreen", "minimal"}:
        os.environ["QT_WIDGETS_RHI"] = "1"
        os.environ["QT_WIDGETS_RHI_BACKEND"] = "opengl"
    QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.OpenGL)
    # Qt WebEngine (the Map cards) loads after the QApplication exists; it needs shared
    # contexts, which can only be requested before the application is created.
    if QCoreApplication.instance() is None:
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)
    _configured = True
