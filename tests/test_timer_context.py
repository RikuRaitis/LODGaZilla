"""Every deferred call names the object it belongs to.

``QTimer.singleShot(delay, callable)`` without a context object still fires after its
widget is deleted; the call then raises "Internal C++ object already deleted" inside
Qt's event handling, and PySide 6.11 corrupts the heap while reporting that error
(the intermittent full-suite crashes). ``QTimer.singleShot(delay, owner, callable)``
is cancelled when ``owner`` is deleted.
"""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src"


class TimerContextTests(unittest.TestCase):
    def test_every_single_shot_timer_has_a_context_object(self):
        missing = []
        for path in sorted(SOURCE.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "singleShot" and len(node.args) == 2):
                    missing.append(f"{path.relative_to(SOURCE.parent)}:{node.lineno}")
        self.assertEqual(missing, [], "QTimer.singleShot(delay, owner, callable) needs its owner")

    def test_a_deleted_owner_cancels_its_timer(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QWidget
        from tests.qt_wait import qt_wait

        app = QApplication.instance() or QApplication([])
        calls = []
        widget = QWidget()
        QTimer.singleShot(0, widget, lambda: calls.append("fired"))
        # The widget is deleted before the timer can fire, as when a card closes.
        import shiboken6
        shiboken6.delete(widget)
        qt_wait(50)
        self.assertEqual(calls, [])
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
