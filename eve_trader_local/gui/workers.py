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

from PySide6.QtCore import QObject, QThread, QSize, Signal, Slot
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from . import icons
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


class BusyMixin:
    """Shared busy-state/status-label/`run_action` machinery for anything
    that calls into `actions.py` through a worker thread - both
    `views.base.BaseView` (a QWidget-based workspace tab) and the app-level
    dialogs (`SettingsDialog`, `CharactersDialog`, both `QDialog`s) mix this
    in instead of duplicating it.

    Deliberately *not* a `QObject` subclass itself: PySide6 doesn't support
    a class inheriting from two different `QObject`-derived branches (e.g.
    `QWidget` and `QDialog`) at once, so this stays a plain Python mixin -
    it only assumes `self.setEnabled(...)` is provided by whichever real Qt
    widget/dialog class it's mixed into, and that `_init_busy()` has been
    called (from that class's own `__init__`, after `super().__init__()`)
    before any of these methods are used.

    `status_row` is what callers actually put in a layout (`self.status_row`,
    not `self.status_label` - that name is kept only for the plain text
    itself, still used directly by anything that just wants to read/set the
    message). It's a small icon + text banner styled via `theme.py`'s
    `banner-error`/`banner-info`/`banner-busy` cssClass, hidden entirely
    (`setVisible(False)`) until the first `set_busy`/`show_error`/
    `show_info` call - an empty bordered box sitting under every view before
    anything ever happened would look like a rendering glitch, not a status
    area."""

    def _init_busy(self) -> None:
        self._threads: list = []  # keeps QThreads alive until they finish - see run_action above
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self._status_icon = QLabel()
        self._status_icon.setFixedSize(16, 16)

        # QFrame, not a plain QWidget - a bare QWidget ignores QSS
        # background-color/border unless WA_StyledBackground is set; QFrame
        # paints its own stylesheet background out of the box.
        self.status_row = QFrame()
        self.status_row.setVisible(False)
        row_layout = QHBoxLayout(self.status_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        row_layout.addWidget(self._status_icon, 0)
        row_layout.addWidget(self.status_label, 1)

    def _set_banner(self, css_class: str, icon_name: str, message: str) -> None:
        self.status_label.setText(message)
        self._status_icon.setPixmap(icons.icon(icon_name).pixmap(QSize(16, 16)))
        self.status_row.setProperty("cssClass", css_class)
        self.status_row.style().unpolish(self.status_row)
        self.status_row.style().polish(self.status_row)
        self.status_row.setVisible(True)

    def set_busy(self, busy: bool, message: str = "Wird geladen...") -> None:
        self.setEnabled(not busy)
        if busy:
            self._set_banner("banner-busy", "refresh", message)

    def show_error(self, message: str) -> None:
        self._set_banner("banner-error", "warning", message)

    def show_info(self, message: str) -> None:
        self._set_banner("banner-info", "check", message)

    def run_action(self, fn: Callable[[], Any], on_success: Callable[[Any], None],
                   busy_message: str = "Wird geladen...") -> None:
        """The one call every view/dialog makes to reach `actions.py` (or any
        other blocking call, e.g. `TokenManager.login`). Runs `fn` (a
        zero-arg callable) on a worker thread; on success calls
        `on_success(result)` after clearing the busy state, on an
        ActionError shows its message in the status label. Disabled while
        busy so a second click can't fire the same action twice
        concurrently."""
        self.set_busy(True, busy_message)

        def _on_success(result):
            self.set_busy(False)
            on_success(result)

        def _on_failure(message):
            self.set_busy(False)
            self.show_error(message)

        thread = run_action(self, fn, _on_success, _on_failure)
        self._threads.append(thread)
        # Same plain-lambda-receiver caveat _ResultBridge documents at length
        # - harmless here since this only touches a Python list, not a widget.
        thread.finished.connect(lambda: self._threads.remove(thread) if thread in self._threads else None)
