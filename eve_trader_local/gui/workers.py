"""Runs a `do_*` action (or any blocking call) on a background QThread and
reports the result back via Qt signals - the one mechanism every GUI view
uses to call into actions.py without freezing the window.

`ActionError` (this repo's one user-facing error type, see `errors.py`) is
caught here and surfaced through `failed` with its plain message, same as
the CLI's own top-level `except ActionError` handler prints it. Anything
else escaping is a bug, not a supported failure mode - it propagates through
`failed` too (with its full `repr`) so it's visible instead of silently
swallowed, but callers should treat that case as "something to report/fix",
not as ordinary user-facing error text.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..errors import ActionError


class ActionWorker(QObject):
    """Wraps a zero-arg callable (typically `functools.partial(do_thing, ...)`)
    so it can be moved to a QThread. Construct one, move it to a QThread,
    connect `succeeded`/`failed`, then start the thread with `run` as the
    entry point - see `run_action` below for the usual one-call form."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except ActionError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 - surfaced, not swallowed; see module docstring
            self.failed.emit(f"Unexpected error: {e!r}")
        else:
            self.succeeded.emit(result)


class _ResultBridge(QObject):
    """Exists for exactly one reason: Qt's AutoConnection only auto-detects
    "sender and receiver are on different threads, so queue it" when the
    receiving slot belongs to a real QObject with well-defined thread
    affinity (`QObject.thread()`). `on_success`/`on_failure` are plain
    Python callables (a view's closures) with no such affinity, so
    connecting a worker-thread signal directly to them runs them on the
    *worker* thread - unsafe for anything touching Qt widgets. This bridge
    is constructed on the caller's thread (the Qt UI thread) and never
    moved, so `worker.succeeded.connect(bridge.handle_success)` correctly
    auto-queues onto the UI thread; the bridge's own slots then just call
    through to the real callables, now safely on the right thread."""

    def __init__(self, on_success: Callable[[Any], None], on_failure: Callable[[str], None],
                 parent: QObject | None = None):
        super().__init__(parent)
        self._on_success = on_success
        self._on_failure = on_failure

    @Slot(object)
    def handle_success(self, result: Any) -> None:
        self._on_success(result)

    @Slot(str)
    def handle_failure(self, message: str) -> None:
        self._on_failure(message)


def run_action(parent: QObject, fn: Callable[[], Any],
                on_success: Callable[[Any], None], on_failure: Callable[[str], None]) -> QThread:
    """Starts `fn` on a new QThread and wires its result to `on_success`/
    `on_failure` (both called back on the Qt UI thread - see `_ResultBridge`
    for why that needs an explicit bridge object rather than "just" a Qt
    signal/slot connection). Returns the QThread - the caller (a view) MUST
    keep a reference to it (e.g. `self._threads.append(run_action(...))`)
    until it finishes; a QThread with no live Python reference can be
    garbage collected out from under itself mid-run, a classic PySide
    gotcha.

    `parent` should be the calling view/widget - it anchors the bridge to
    the view's own thread (the Qt UI thread), which is what makes the
    auto-queuing in `_ResultBridge` correct."""
    thread = QThread(parent)
    worker = ActionWorker(fn)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    bridge = _ResultBridge(on_success, on_failure, parent)

    # `_quit` runs via a direct connection, i.e. inside the worker thread
    # itself (the signal is emitted from `worker.run`, which executes there)
    # - QThread.quit() is documented thread-safe, so this is the standard
    # "worker ends its own thread's event loop when done" pattern. It must
    # NOT also call thread.wait() here: that would be the thread waiting on
    # itself and deadlock (Qt logs exactly that if you try).
    def _quit():
        thread.quit()

    worker.succeeded.connect(bridge.handle_success)
    worker.succeeded.connect(_quit)
    worker.failed.connect(bridge.handle_failure)
    worker.failed.connect(_quit)
    # Once the thread's event loop actually stops, reclaim the worker. This
    # also keeps a strong Python reference to `worker`/`bridge` alive on
    # `thread` for the run's whole duration - without it nothing holds one,
    # and a moveToThread'd QObject with no parent and no Python reference
    # can be garbage-collected out from under its own in-flight `run()` call.
    thread._worker_ref = worker
    thread._bridge_ref = bridge
    thread.finished.connect(worker.deleteLater)
    thread.start()
    return thread
