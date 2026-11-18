"""Markers are hidden by default (the Plot navigator's Markers button); tests that place
markers turn them on for each test with ``@with_markers_shown``."""
from functools import wraps

from src.gui.plots.markers import set_markers_shown


def with_markers_shown(test_class):
    original = test_class.setUp

    @wraps(original)
    def setUp(self):  # noqa: N802 - unittest's name
        set_markers_shown(True)
        self.addCleanup(set_markers_shown, False)
        original(self)

    test_class.setUp = setUp
    return test_class
