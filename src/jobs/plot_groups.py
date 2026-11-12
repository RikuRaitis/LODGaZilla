"""One active grouped-plot load per card, with cancellation and newest-request wins."""

from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

class _Signals(QObject):
    completed = Signal(int, object)


class _Load(QRunnable):
    def __init__(self, revision, traces, time_range, load_data, optional_axes=frozenset(), maximum_points=None):
        super().__init__()
        self.revision, self.traces, self.time_range = revision, traces, time_range
        self.maximum_points = maximum_points
        self.cancelled = Event()
        self.signals = _Signals()
        self.load_data = load_data
        self.optional_axes = optional_axes

    def _incomplete(self, axes):
        return any(axis is None for index, axis in enumerate(axes) if index not in self.optional_axes)

    def run(self):
        results = []
        for axes in self.traces:
            if self.cancelled.is_set():
                break
            try:
                # Without a card limit, each loader keeps its own default point limit.
                limit = {} if self.maximum_points is None else {"maximum_points": self.maximum_points}
                results.append(None if self._incomplete(axes) else
                               self.load_data(axes, time_range=self.time_range, cancelled=self.cancelled, **limit))
            except InterruptedError:
                break
            except Exception as error:
                results.append(str(error))
        self.signals.completed.emit(self.revision, results)


class GroupedPlotLoadCoordinator(QObject):
    loaded = Signal(object)

    def __init__(self, parent, load_data, *, optional_axes=()):
        super().__init__(parent)
        self.revision = 0
        self.worker = None
        self.pending = None
        self.closed = False
        self.load_data = load_data
        # Axis slots that the loader can fill itself when they are empty.
        self.optional_axes = frozenset(optional_axes)

    def request(self, traces, time_range=None, maximum_points=None):
        if self.closed:
            return
        self.revision += 1
        self.pending = (self.revision, tuple(tuple(axes) for axes in traces), time_range, maximum_points)
        if self.worker is not None:
            self.worker.cancelled.set()
        else:
            self._start()

    def _start(self):
        if self.pending is None or self.closed:
            return
        revision, traces, time_range, maximum_points = self.pending
        self.worker = _Load(revision, traces, time_range, self.load_data, self.optional_axes, maximum_points)
        self.pending = None
        self.worker.signals.completed.connect(self._completed)
        QThreadPool.globalInstance().start(self.worker)

    def _completed(self, revision, results):
        self.worker = None
        if not self.closed and revision == self.revision:
            self.loaded.emit(results)
        self._start()

    def shutdown(self):
        self.closed = True
        self.pending = None
        if self.worker is not None:
            self.worker.cancelled.set()
