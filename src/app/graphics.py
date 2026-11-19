"""One composition API for WebEngine/Qt Quick maps and OpenGL video."""
import os

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtWidgets import QWidget

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


def _gpu_platform() -> bool:
    """Headless test platforms do not support GPU composition."""
    return os.environ.get("QT_QPA_PLATFORM", "").split(":", 1)[0].casefold() not in {"offscreen", "minimal"}


def compose_with_opengl(window: QWidget) -> None:
    """Create ``window`` as an OpenGL-composed window from the start.

    The first WebEngine/QQuickWidget or QOpenGLWidget would otherwise switch an
    already visible window from raster to OpenGL and recreate its native handle on
    Windows. A hidden OpenGL child does this for the main window only: the
    ``QT_WIDGETS_RHI`` environment variables would make every window OpenGL, the
    splash screen too, whose first OpenGL start delayed it by about two seconds.
    Call it before the window is shown.
    """
    if not _gpu_platform() or window.findChild(QOpenGLWidget, _COMPOSITION_HOLDER) is not None:
        return
    holder = QOpenGLWidget(window)
    holder.setObjectName(_COMPOSITION_HOLDER)
    holder.resize(1, 1)
    holder.hide()


_COMPOSITION_HOLDER = "openglCompositionHolder"


def configure_graphics():
    """Configure composition before QApplication or any native window is created."""
    global _configured
    if _configured:
        return
    # Windows are composed with OpenGL where they need it (compose_with_opengl), not all
    # of them, so the splash screen appears without starting OpenGL. Qt Quick (the
    # Map cards' Qt WebEngine) uses OpenGL; RHI would otherwise default to Direct3D.
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
