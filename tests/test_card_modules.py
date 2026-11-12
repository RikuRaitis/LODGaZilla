"""The plot card code loads after the main window opens: lazy stand-ins and the background loader."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from src.app import card_modules  # noqa: E402
from src.app.card_modules import CardModuleLoader, LazyAttribute, loaded_class  # noqa: E402
from tests.qt_wait import qt_wait_until  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class LazyAttributeTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.directory / "lazy_sample_cards.py").write_text(
            "from PySide6.QtWidgets import QWidget\n"
            "class SampleCard(QWidget):\n    kind = 'sample'\n"
            "def double(value):\n    return 2 * value\n", encoding="utf-8")
        sys.path.insert(0, str(self.directory))
        self.addCleanup(sys.path.remove, str(self.directory))
        self.addCleanup(sys.modules.pop, "lazy_sample_cards", None)

    def test_instance_checks_never_import_and_use_imports(self):
        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        card = LazyAttribute("lazy_sample_cards", "SampleCard")
        double = LazyAttribute("lazy_sample_cards", "double")
        widget = QWidget()
        self.addCleanup(widget.deleteLater)
        # While the module is not loaded, no card of the class can exist.
        self.assertFalse(isinstance(widget, card))
        self.assertFalse(isinstance(widget, (card, double)))
        self.assertIsNone(loaded_class(card))
        self.assertNotIn("lazy_sample_cards", sys.modules)
        created = card()
        self.addCleanup(created.deleteLater)
        self.assertIn("lazy_sample_cards", sys.modules)
        self.assertTrue(isinstance(created, card))
        self.assertTrue(isinstance(created, (QApplication, card)))
        self.assertTrue(issubclass(type(created), card))
        self.assertIs(loaded_class(card), type(created))
        self.assertEqual(card.kind, "sample")
        self.assertEqual(double(4), 8)
        self.assertIs(loaded_class(QWidget), QWidget)


class CardModuleLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_loader_imports_on_a_worker_and_reports_errors(self):
        modules = ("json", "lazy_missing_module_for_test")
        loader = CardModuleLoader()
        self.addCleanup(loader.deleteLater)
        results = []
        loader.finished.connect(results.append)
        with mock.patch.object(card_modules, "CARD_MODULES", modules), \
                mock.patch.object(card_modules, "QT_BINDINGS", ()), \
                mock.patch.object(card_modules, "_worker", None):
            loader.start()
            worker = card_modules._worker
            self.assertIsNotNone(worker)
            self.assertTrue(qt_wait_until(lambda: results, 10_000))
            card_modules.wait_for_card_modules()
        self.assertIn("lazy_missing_module_for_test", results[0])
        self.assertFalse(worker.is_alive())

    def test_main_window_module_loads_without_the_card_libraries(self):
        code = ("import sys; import src.app.lodgazilla; "
                "heavy = [name for name in ('pyqtgraph', 'OpenGL', 'av', 'PySide6.QtWebEngineWidgets', "
                "'PySide6.QtMultimedia') + card_modules.CARD_MODULES if name in sys.modules]; print(heavy)")
        result = subprocess.run([sys.executable, "-c", "from src.app import card_modules; " + code], cwd=ROOT,
                                capture_output=True, text=True, timeout=300,
                                env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "[]")


if __name__ == "__main__":
    unittest.main()
