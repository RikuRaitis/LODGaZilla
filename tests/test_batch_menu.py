"""The Navigator's Batch button: generate missing H5 files, with one plugin, or all again."""
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMenu, QMessageBox  # noqa: E402

from src.app.lodgazilla import LODGaZillaWindow  # noqa: E402
from src.data.folder_info import SOURCE_EXPORT, SOURCE_IMPORT  # noqa: E402
from src.jobs.source_detection import (  # noqa: E402
    STATE_CURRENT, STATE_MISSING, STATE_NEW, STATE_OUTDATED, STATE_UNSUPPORTED, SourceRow,
)

FOLDER = Path("data/run")


def row(source, state, plugin_id="device", kind=SOURCE_IMPORT, **extra):
    return SourceRow(kind, source, "", "Importer", plugin_id, state, **extra)


ROWS = [
    row("new.bin", STATE_NEW),
    row("gone.bin", STATE_MISSING),
    row("done.bin", STATE_CURRENT),
    row("old.bin", STATE_OUTDATED),
    row("other.csv", STATE_NEW, plugin_id="csv"),
    row("notes.txt", STATE_UNSUPPORTED, plugin_id=""),
    row("drop.dat", STATE_NEW, plugin_id="", draft="draft"),
    row("member.bin", STATE_NEW, member="set"),
    row("LODGaZilla", STATE_NEW, kind=SOURCE_EXPORT, plugin_id="export", operation="CSV"),
]


class BatchMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        manager = mock.Mock()
        manager.enabled_importers.return_value = [SimpleNamespace(plugin_id="device", title="Device importer"),
                                                  SimpleNamespace(plugin_id="csv", title="CSV importer")]
        manager.operations.return_value = [(SimpleNamespace(plugin_id="export", title="CSV export"),
                                            SimpleNamespace(kind="export", name="CSV"))]
        window = SimpleNamespace(batch_menu=QMenu(), plugin_manager=manager, run_source=mock.Mock(),
                                 internal_logger=mock.Mock(),
                                 navigator=SimpleNamespace(all_source_rows=lambda: [(FOLDER, r) for r in ROWS]))
        for name in ("batch_rows", "populate_batch_menu", "_add_batch_plugin_menu", "run_batch"):
            setattr(window, name, getattr(LODGaZillaWindow, name).__get__(window))
        self.addCleanup(window.batch_menu.deleteLater)
        return window

    def ran(self, window):
        return [r.source for _folder, r in (call.args for call in window.run_source.call_args_list)]

    def test_menu_and_generate_missing(self):
        window = self.window()
        window.populate_batch_menu()
        texts = [action.text() for action in window.batch_menu.actions() if not action.isSeparator()]
        self.assertEqual(texts, ["Generate missing H5 files (3)", "Generate with plugin", "Re-generate all (5)…"])
        # Only files an importer plugin reads and that have no H5 file yet; Magic drop rows,
        # unsupported files, set members and exports stay out.
        window.batch_menu.actions()[0].trigger()
        self.assertEqual(self.ran(window), ["new.bin", "gone.bin", "other.csv"])

    def test_re_generate_all_asks_first(self):
        window = self.window()
        window.populate_batch_menu()
        again = window.batch_menu.actions()[-1]
        with mock.patch("src.app.lodgazilla.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            again.trigger()
        window.run_source.assert_not_called()
        with mock.patch("src.app.lodgazilla.QMessageBox.question",
                        return_value=QMessageBox.StandardButton.Yes) as question:
            again.trigger()
        self.assertIn("replaces 2 generated file(s)", question.call_args.args[2])
        self.assertEqual(self.ran(window), ["new.bin", "gone.bin", "done.bin", "old.bin", "other.csv"])

    def test_one_plugin_missing_or_all(self):
        window = self.window()
        window.populate_batch_menu()
        plugins = window.batch_menu.actions()[1].menu()
        self.assertEqual([action.text() for action in plugins.actions()], ["Device importer", "CSV importer", "CSV export"])
        device = plugins.actions()[0].menu()
        self.assertEqual([action.text() for action in device.actions()], ["Generate missing (2)", "Re-generate all (4)…"])
        device.actions()[0].trigger()
        self.assertEqual(self.ran(window), ["new.bin", "gone.bin"])
        window.run_source.reset_mock()
        export = plugins.actions()[2].menu()
        export.actions()[0].trigger()
        self.assertEqual(self.ran(window), ["LODGaZilla"])


if __name__ == "__main__":
    unittest.main()
