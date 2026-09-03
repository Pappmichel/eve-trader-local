"""GUI entry point - `eve-trader-local-gui` (see pyproject.toml's
[project.scripts]). Deliberately thin: argument parsing/env setup that the
CLI already needs (paths.py, logging_setup.py) is reused as-is, not
reimplemented here."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .. import storage
from ..logging_setup import configure_logging
from .main_window import MainWindow


def main() -> int:
    configure_logging()
    storage.init_db()
    app = QApplication(sys.argv)
    # Organization name feeds QSettings' storage location (see
    # main_window.py's own module docstring for what's persisted there) -
    # application name was already set here before this, kept as-is so an
    # existing install's saved settings aren't orphaned under a new name.
    app.setOrganizationName("eve-trader-local")
    app.setApplicationName("eve-trader-local")
    window = MainWindow()
    window.restore_state()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
