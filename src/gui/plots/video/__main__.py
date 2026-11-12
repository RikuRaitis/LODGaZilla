import argparse
import logging
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description='LODGaZilla video card: INSV, LRV and standard video')
    parser.add_argument('video', nargs='?', type=Path, help='Video file to open')
    parser.add_argument('--max-side', type=int, help='Manual preview texture size; default: automatic')
    parser.add_argument('--hwaccel', default='auto',
                        choices=['auto', 'none', 'd3d11va', 'qsv', 'cuda', 'vaapi', 'videotoolbox'])
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--cpu-transfer', action='store_true', help='Disable Windows GPU texture sharing')
    args = parser.parse_args()
    if args.max_side is not None and not 128 <= args.max_side <= 8192:
        parser.error('--max-side must be between 128 and 8192')
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    try:
        from PySide6.QtGui import QSurfaceFormat
        from PySide6.QtWidgets import QApplication, QFileDialog
        from .widget import VideoPlotWidget
        from src.app.branding import configure_application, prepare_windows_taskbar
        from src.app.graphics import configure_graphics
        from src.gui.theme import load_theme
        from src.app.settings import DEFAULT_APP_SETTINGS_PATH
    except ImportError as exc:
        parser.exit(1, f'{exc}\nInstall: python -m pip install -r requirements.txt\n')
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)
    prepare_windows_taskbar()
    configure_graphics()
    app = QApplication(sys.argv[:1])
    configure_application(app, load_theme(DEFAULT_APP_SETTINGS_PATH))
    path = args.video
    if path is None:
        name, _ = QFileDialog.getOpenFileName(None, 'Open video', '',
                                            'Videos (*.insv *.lrv *.mp4 *.mov *.mkv *.avi *.m4v);;All files (*)')
        if not name:
            return 0
        path = Path(name)
    window = VideoPlotWidget(path.parent, path, max_side=args.max_side,
                             hardware=args.hwaccel, gpu_output=not args.cpu_transfer)
    window.setWindowTitle(path.name + ' — LODGaZilla Video')
    window.setWindowIcon(app.windowIcon())
    window.resize(1040, 750)
    window.close_requested.connect(lambda _: window.close())
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
