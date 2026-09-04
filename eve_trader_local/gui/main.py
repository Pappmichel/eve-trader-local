"""GUI entry point - `eve-trader-local-gui` (see pyproject.toml's
[project.scripts]). Deliberately thin: argument parsing/env setup that the
CLI already needs (paths.py, logging_setup.py) is reused as-is, not
reimplemented here."""
from __future__ import annotations

import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from .. import storage
from ..logging_setup import configure_logging
from ..paths import data_dir, is_frozen
from . import theme
from .main_window import MainWindow


def main() -> int:
    configure_logging()
    storage.init_db()
    app = QApplication(sys.argv)
    theme.apply(app)
    # Organization name feeds QSettings' storage location (see
    # main_window.py's own module docstring for what's persisted there) -
    # application name was already set here before this, kept as-is so an
    # existing install's saved settings aren't orphaned under a new name.
    app.setOrganizationName("eve-trader-local")
    app.setApplicationName("eve-trader-local")
    if is_frozen():
        # The packaged .exe is portable (see paths.py's own data_dir()
        # docstring) - QSettings' native format would otherwise write
        # window/tab state into the Windows registry, which doesn't travel
        # with the .exe. Redirecting IniFormat's implicit path here covers
        # every bare QSettings() call this app makes (main_window.py) with
        # no change needed at those call sites.
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(data_dir()))
    window = MainWindow()
    window.restore_state()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
