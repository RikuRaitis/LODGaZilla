"""The About dialog: the LODGaZilla picture, version, author and a button that opens README.md."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout, QWidget

TAGLINE = "Every file format, billions of points, one timeline."
DESCRIPTION = "A desktop viewer for large time-series data, with video in sync."
AUTHOR = "Riku Tervonen"
PROJECT_URL = "https://github.com/RikuRaitis/LODGaZilla"
LICENSE_TEXT = "Free software under the GNU General Public License, version 3 or later."
IMAGE_WIDTH = 520


class AboutDialog(QDialog):
    """What LODGaZilla is and which version runs; ``readme_requested`` asks to open README.md."""

    readme_requested = Signal()

    def __init__(self, image: Path, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About LODGaZilla")
        picture = QLabel()
        pixmap = QPixmap(str(image))
        if not pixmap.isNull():
            ratio = self.devicePixelRatioF()
            pixmap = pixmap.scaledToWidth(round(IMAGE_WIDTH * ratio), Qt.TransformationMode.SmoothTransformation)
            pixmap.setDevicePixelRatio(ratio)
            picture.setPixmap(pixmap)
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = QLabel(
            f"<p><b>{TAGLINE}</b><br>{DESCRIPTION}</p>"
            f"<p>Version {version}<br>By {AUTHOR}<br><a href=\"{PROJECT_URL}\">{PROJECT_URL}</a></p>"
            f"<p>{LICENSE_TEXT}</p>"
        )
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setOpenExternalLinks(True)
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.readme_button = QPushButton("Open README")
        self.readme_button.setToolTip("Open README.md in your Markdown viewer")
        buttons.addButton(self.readme_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.readme_button.clicked.connect(self.readme_requested)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(picture)
        layout.addWidget(self.text)
        layout.addWidget(buttons)
        self.setFixedWidth(IMAGE_WIDTH + 2 * layout.contentsMargins().left())
