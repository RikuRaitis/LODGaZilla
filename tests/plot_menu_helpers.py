import time

from tests.qt_wait import qt_wait

# Background inspection can be slow on a loaded machine; wait on wall time.
WAIT_SECONDS = 10.0


def wait_for_plot_menu(menu):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if any(action.data() == "default" for action in menu.actions()):
            return
        qt_wait(10)
    raise AssertionError("Plot menu metadata did not finish loading")
