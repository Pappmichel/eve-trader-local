"""A much simpler, CLI-appropriate cut of the parent's logging_setup.py.

The parent's version exists because `scheduler.py` runs background jobs for
hours with nobody watching a terminal, so a console handler alone would lose
everything - it needs a persistent file trail. This repo has no long-running
server process at all (see cli.py's own docstring: "commands run and exit"),
and every command's own `print()` output already goes straight to the
terminal in the same process that ran it - so a *console* handler here would
be pure duplication, not a gap to fill.

What's still genuinely worth having: several already-ported modules
(candidate_discovery.py, history_backtest.py, goonmetrics_client.py,
actions.py, refining/actions.py) call `logger.exception`/`logger.warning` on
best-effort paths that are deliberately swallowed rather than raised (a
per-type ESI backfill failure, a bad Goonmetrics chunk, ...). Without any
handler configured, those go to Python's own "handler of last resort" -
unformatted, easy to miss in a long `pipeline`/`sync-esi` run's scrollback,
and gone the moment the terminal is closed. A small rotating file under the
same data directory as the SQLite DB gives a real answer to "what actually
went wrong" after the fact, for the cost of one file handler - worth it;
adding a whole parallel console stream on top is not.

Scoped to the "eve_trader_local" logger namespace (every logger in this
package is a child of it), same reasoning as the parent: don't touch the
bare root logger, so nothing else importing this package doubles or
reformats its own logging.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .paths import data_dir

_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent - safe to call more than once without piling up duplicate
    handlers and duplicating every log line. `data_dir()` is resolved here,
    not at import time, so this stays correct under the test suite's
    per-test EVE_TRADER_LOCAL_DATA_DIR override rather than baking in
    whatever directory happened to exist when this module was first
    imported."""
    global _configured
    if _configured:
        return
    _configured = True

    log_path = data_dir() / "eve-trader-local.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    # 2MB x 2 backups - this is a CLI, not an always-on process; a normal
    # day of pipeline/sync-esi runs is a fraction of the parent's 5MBx3
    # server-scale budget.
    file_handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
    file_handler.setFormatter(formatter)

    logger = logging.getLogger("eve_trader_local")
    logger.setLevel(level)
    logger.addHandler(file_handler)
    logger.propagate = False  # no root/console handler to also feed


def reset_for_tests() -> None:
    """Test-only: clears the idempotency guard and any handlers already
    attached, so a test can reconfigure against its own tmp data dir instead
    of silently reusing whichever directory called configure_logging()
    first in this process."""
    global _configured
    _configured = False
    logger = logging.getLogger("eve_trader_local")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
