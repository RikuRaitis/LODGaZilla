"""Per-card frequency band and FFT resolution controls."""

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QVBoxLayout,
)

from src.data.frequency import FFTSettings, requested_window_size


class FrequencySettingsDialog(QDialog):
    def __init__(self, settings: FFTSettings, sample_rate: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Frequency range and resolution")
        self.settings = settings
        self.sample_rate = sample_rate
        self.setMinimumWidth(450)
        self.full_range = QCheckBox("Full frequency range (0 to Nyquist)")
        self.full_range.setChecked(settings.frequency_min_hz == 0 and settings.frequency_max_hz is None)
        self.minimum = self._frequency_spin(settings.frequency_min_hz, maximum=sample_rate / 2)
        self.maximum = self._frequency_spin(settings.frequency_max_hz or sample_rate / 2, maximum=sample_rate / 2)
        self.target_enabled = QCheckBox("Choose FFT size from target bin spacing")
        self.target_enabled.setChecked(settings.target_bin_hz is not None)
        self.target = self._frequency_spin(settings.target_bin_hz or sample_rate / settings.window_size)
        self.target.setMinimum(0.000001)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.explanation = QLabel(
            "Longer windows give finer frequency detail but blur rapid changes in time. "
            "Closely spaced tones may still overlap, depending on the window function. "
            "The achieved bin spacing is shown below the plot; short time selections "
            "or the maximum FFT size may limit it."
        )
        self.explanation.setWordWrap(True)
        form = QFormLayout()
        form.addRow(self.full_range)
        form.addRow("Minimum frequency", self.minimum)
        form.addRow("Maximum frequency", self.maximum)
        form.addRow(self.target_enabled)
        form.addRow("Target FFT bin spacing", self.target)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.preview)
        layout.addWidget(self.explanation)
        layout.addWidget(self.buttons)
        self.full_range.toggled.connect(self.update_preview)
        self.target_enabled.toggled.connect(self.update_preview)
        for spin in (self.minimum, self.maximum, self.target):
            spin.valueChanged.connect(self.update_preview)
        self.update_preview()

    @staticmethod
    def _frequency_spin(value, *, maximum=1e12):
        spin = QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(0, maximum)
        spin.setSuffix(" Hz")
        spin.setGroupSeparatorShown(True)
        spin.setValue(value)
        return spin

    def selected_settings(self) -> FFTSettings:
        return replace(
            self.settings,
            frequency_min_hz=0.0 if self.full_range.isChecked() else self.minimum.value(),
            frequency_max_hz=None if self.full_range.isChecked() else self.maximum.value(),
            target_bin_hz=self.target.value() if self.target_enabled.isChecked() else None,
        )

    def update_preview(self, *_args):
        self.minimum.setEnabled(not self.full_range.isChecked())
        self.maximum.setEnabled(not self.full_range.isChecked())
        self.target.setEnabled(self.target_enabled.isChecked())
        try:
            settings = self.selected_settings()
        except ValueError as error:
            self.preview.setText(str(error))
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            return
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        size = requested_window_size(self.sample_rate, settings)
        self.preview.setText(
            f"FFT: {size:,} samples · Bin spacing: {self.sample_rate / size:.6g} Hz\n"
            f"Analysis window: {1000 * size / self.sample_rate:.6g} ms"
        )
