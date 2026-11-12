import os
import sys
import unittest
from pathlib import Path
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QTreeWidget  # noqa: E402

from src.gui.navigator.controls import (  # noqa: E402
    BROWSE_ITEM_TEXT,
    ElidedPathLineEdit,
    FILTER_KEY_PLACEHOLDER,
    FILTER_VALUE_PLACEHOLDER,
    NavigatorControlsWidget,
    RecentFolderComboBox,
    contrasting_icon_color,
    make_expand_icon,
    middle_elide_text,
)
from PySide6.QtGui import QColor, QFocusEvent  # noqa: E402


class FixedWidthMetrics:
    def horizontalAdvance(self, text: str) -> int:  # noqa: N802
        return len(text)


class NavigatorControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fields_are_always_visible_and_share_their_right_edge(self):
        controls = NavigatorControlsWidget()
        self.addCleanup(controls.deleteLater)
        controls.resize(420, 200)
        controls.show()
        self.app.processEvents()

        for field in (controls.folder_combo, controls.group_by_combo, controls.filter_key_combo,
                      controls.filter_value_combo):
            self.assertTrue(field.isVisible())

        def right(widget):
            return widget.mapTo(controls, widget.rect().topRight()).x()

        self.assertEqual(right(controls.folder_combo), right(controls.group_by_combo))
        self.assertEqual(right(controls.folder_combo), right(controls.filter_value_combo))
        # No Browse button column: the fields reach the group box's right edge.
        self.assertFalse(hasattr(controls, "browse_button"))
        frame_right = controls.frame.mapTo(controls, controls.frame.contentsRect().topRight()).x()
        self.assertLessEqual(frame_right - right(controls.folder_combo), 12)

    def test_browse_item_is_first_and_asks_for_a_folder_without_changing_the_field(self):
        path = r"C:\data\run"
        combo = RecentFolderComboBox()
        self.addCleanup(combo.deleteLater)
        combo.set_recent_folders((path,))
        combo.lineEdit().setText(path)
        self.assertEqual(combo.count(), 2)
        self.assertTrue(combo.is_browse_item(0))
        self.assertEqual(combo.itemText(0), BROWSE_ITEM_TEXT)
        self.assertEqual(combo.full_folder_path(1), path)
        requests = []
        combo.browse_requested.connect(lambda: requests.append(True))
        combo.setCurrentIndex(0)  # As a click does; the field shows the item's text.
        combo.activated.emit(0)
        self.assertEqual(requests, [True])
        self.assertEqual(combo.lineEdit().text(), path)
        self.assertNotIn("Browse", combo.lineEdit().displayed_text())
        # Rebuilding the list keeps the field's folder and the item first.
        combo.set_recent_folders(())
        self.assertEqual(combo.count(), 1)
        self.assertEqual(combo.lineEdit().text(), path)

    def test_tree_buttons_are_centered_below_the_fields(self):
        controls = NavigatorControlsWidget()
        self.addCleanup(controls.deleteLater)
        controls.resize(420, 200)
        controls.show()
        self.app.processEvents()
        buttons = [controls.refresh_button, controls.sample_loss_button, controls.clipping_button,
                   controls.hide_irrelevant_button]
        self.assertEqual(sorted(buttons, key=lambda button: button.x()), buttons)
        left = buttons[0].mapTo(controls, buttons[0].rect().topLeft()).x()
        right = buttons[-1].mapTo(controls, buttons[-1].rect().topRight()).x()
        self.assertLessEqual(abs(left - (controls.width() - 1 - right)), 2)
        self.assertGreater(buttons[0].mapTo(controls, buttons[0].rect().topLeft()).y(),
                           controls.filter_key_combo.mapTo(controls, controls.filter_key_combo.rect().bottomLeft()).y())

    def test_middle_elide_text_reserves_more_space_for_path_end(self):
        path = r"C:\Users\example1\OneDrive - ExampleCo\Desktop\LOG\SensorRecordings\Data"

        display_text = middle_elide_text(path, FixedWidthMetrics(), 30)
        prefix, suffix = display_text.split("...")

        self.assertLessEqual(len(display_text), 30)
        self.assertEqual(prefix, path[:len(prefix)])
        self.assertEqual(suffix, path[-len(suffix):])
        self.assertGreater(len(suffix), len(prefix))

    def test_folder_edit_elides_display_but_keeps_full_text_and_tooltip(self):
        path = r"C:\Users\example1\OneDrive - ExampleCo\Desktop\LOG\SensorRecordings\Data"
        edit = ElidedPathLineEdit()
        edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        edit.resize(180, edit.sizeHint().height())
        edit.show()
        self.app.processEvents()

        edit.setText(path)

        self.assertEqual(edit.text(), path)
        self.assertEqual(edit.toolTip(), path)
        self.assertIn("...", edit.displayed_text())
        self.assertTrue(path.startswith(edit.displayed_text().split("...", 1)[0]))
        self.assertTrue(path.endswith(edit.displayed_text().split("...", 1)[1]))

    def test_folder_edit_updates_elision_after_resize(self):
        path = r"C:\Users\example1\OneDrive - ExampleCo\Desktop\LOG\SensorRecordings\Data"
        edit = ElidedPathLineEdit()
        edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        edit.resize(150, edit.sizeHint().height())
        edit.show()
        self.app.processEvents()
        edit.setText(path)
        narrow_text = edit.displayed_text()

        edit.resize(340, edit.sizeHint().height())
        self.app.processEvents()

        self.assertEqual(edit.text(), path)
        self.assertGreater(len(edit.displayed_text()), len(narrow_text))

    def test_folder_edit_never_replaces_the_full_path_with_its_display_elision(self):
        path = r"C:\Users\example1\OneDrive - ExampleCo\Desktop\LOG\SensorRecordings\Data"
        edit = ElidedPathLineEdit()
        edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        edit.resize(150, edit.sizeHint().height())
        edit.show()
        self.app.processEvents()
        edit.setText(path)

        self.assertIn("...", edit.displayed_text())
        QApplication.sendEvent(edit, QFocusEvent(QEvent.Type.FocusOut))

        self.assertEqual(edit.text(), path)

    def test_recent_folder_items_are_elided_without_widening_the_combo(self):
        path = r"C:\Users\example1\OneDrive - ExampleCo\Desktop\LOG\SensorRecordings\Data"
        combo = RecentFolderComboBox()
        combo.resize(220, combo.sizeHint().height())
        combo.set_recent_folders((path,))
        combo.show()
        self.app.processEvents()

        # Item 0 is [Browse…].
        self.assertEqual(combo.full_folder_path(1), path)
        self.assertIn("...", combo.itemText(1))
        self.assertTrue(path.startswith(combo.itemText(1).split("...", 1)[0]))
        self.assertTrue(path.endswith(combo.itemText(1).split("...", 1)[1]))
        self.assertLess(combo.minimumSizeHint().width(), 400)

    def test_expand_icon_uses_contrast_in_light_and_dark_palettes(self):
        light_icon_color = contrasting_icon_color(QColor(240, 240, 240))
        dark_icon_color = contrasting_icon_color(QColor(35, 35, 35))

        self.assertLess(light_icon_color.lightness(), 80)
        self.assertGreater(dark_icon_color.lightness(), 180)
        self.assertFalse(make_expand_icon(False, light_icon_color).isNull())
        self.assertFalse(make_expand_icon(True, dark_icon_color).isNull())

    def test_filter_placeholders(self):
        controls = NavigatorControlsWidget()

        self.assertEqual(controls.filter_key_combo.currentText(), FILTER_KEY_PLACEHOLDER)
        self.assertEqual(
            controls.filter_value_combo.lineEdit().placeholderText(),
            FILTER_VALUE_PLACEHOLDER,
        )


if __name__ == "__main__":
    unittest.main()
