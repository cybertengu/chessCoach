"""Run slow work (Stockfish, network) off the GUI thread."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


class TaskThread(QThread):
    """Runs ``fn(progress, should_stop)`` in a background thread.

    ``progress(done, total)`` may be called from ``fn`` to report progress;
    ``should_stop()`` returns True after :meth:`requestInterruption`.
    """

    progress = Signal(int, int)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[Callable[[int, int], None], Callable[[], bool]], Any], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn(self.progress.emit, self.isInterruptionRequested)
        except InterruptedError:
            return
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return
        if not self.isInterruptionRequested():
            self.succeeded.emit(result)
