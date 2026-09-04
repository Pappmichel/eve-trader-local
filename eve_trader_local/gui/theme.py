"""An EVE Online-inspired dark theme, applied once at startup (see
`main.py`) - the rest of the GUI (`views/base.py`'s `BaseView`/`TableView`,
every `dialogs/*.py`) is built from plain shared Qt widget classes
(QMainWindow/QMenuBar/QTabWidget/QTableWidget/QPushButton/...), so a single
global stylesheet covers every view/dialog without touching any of them
individually.

Palette (New Eden's own UI: near-black backgrounds, cyan interactive
accents, amber/gold reserved for emphasis - ISK, warnings, the active
selection):
  - background:      #0a0e14 (window) / #10151d (panels, tables) /
                      #161c26 (input fields)
  - border/gridline:  #2a3542
  - primary text:     #c9d6e3
  - accent (cyan):    #2ecdf5 - selection, focus, active tab
  - accent (amber):   #e8b923 - header rows, hover emphasis

Deliberately no bundled font file - CCP's own EVE UI font is proprietary,
and a system font (Segoe UI on Windows, with generic fallbacks) avoids both
the licensing question and any packaging complexity of shipping font
assets. `workers.py`'s existing inline status colors (error red/info green,
set per-state on `status_label`) are untouched by this stylesheet; only the
default (unset) QLabel color needed to change here, since the old default
was black-on-white and this theme is dark.
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication

BG_WINDOW = "#0a0e14"
BG_PANEL = "#10151d"
BG_FIELD = "#161c26"
BG_FIELD_DISABLED = "#12161d"
BORDER = "#2a3542"
BORDER_LIGHT = "#3a4757"
TEXT = "#c9d6e3"
TEXT_DIM = "#7c8a9c"
ACCENT_CYAN = "#2ecdf5"
ACCENT_CYAN_DIM = "#1b8aa8"
ACCENT_AMBER = "#e8b923"

STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "Cantarell", "DejaVu Sans", sans-serif;
    font-size: 13px;
    color: {TEXT};
}}

QMainWindow, QDialog, QWidget {{
    background-color: {BG_WINDOW};
}}

QToolTip {{
    background-color: {BG_PANEL};
    color: {TEXT};
    border: 1px solid {ACCENT_CYAN_DIM};
    padding: 4px;
}}

QMenuBar {{
    background-color: {BG_PANEL};
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item {{
    background: transparent;
    padding: 5px 10px;
}}
QMenuBar::item:selected {{
    background-color: {BG_FIELD};
    color: {ACCENT_CYAN};
}}
QMenu {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
}}
QMenu::item {{
    padding: 5px 22px;
}}
QMenu::item:selected {{
    background-color: {BG_FIELD};
    color: {ACCENT_CYAN};
}}
QMenu::item:disabled {{
    color: {TEXT_DIM};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 6px;
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    background-color: {BG_PANEL};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {BG_WINDOW};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-bottom: none;
    padding: 6px 14px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background-color: {BG_PANEL};
    color: {ACCENT_CYAN};
    border-bottom: 2px solid {ACCENT_CYAN};
}}
QTabBar::tab:hover:!selected {{
    color: {TEXT};
}}
QTabBar::close-button {{
    subcontrol-position: right;
}}

QLabel {{
    background: transparent;
}}

QPushButton {{
    background-color: {BG_FIELD};
    border: 1px solid {BORDER_LIGHT};
    padding: 5px 14px;
    border-radius: 2px;
}}
QPushButton:hover {{
    border-color: {ACCENT_CYAN};
    color: {ACCENT_CYAN};
}}
QPushButton:pressed {{
    border-color: {ACCENT_AMBER};
    color: {ACCENT_AMBER};
}}
QPushButton:disabled {{
    background-color: {BG_FIELD_DISABLED};
    color: {TEXT_DIM};
    border-color: {BORDER};
}}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {BG_FIELD};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 2px;
    padding: 3px 6px;
    selection-background-color: {ACCENT_CYAN_DIM};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT_CYAN};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    background-color: {BG_FIELD_DISABLED};
    color: {TEXT_DIM};
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    selection-background-color: {BG_FIELD};
    selection-color: {ACCENT_CYAN};
}}

QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {BORDER_LIGHT};
    background-color: {BG_FIELD};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT_CYAN_DIM};
    border-color: {ACCENT_CYAN};
}}

QTableWidget, QTableView {{
    background-color: {BG_PANEL};
    alternate-background-color: {BG_WINDOW};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_CYAN_DIM};
    selection-color: {TEXT};
}}
QHeaderView::section {{
    background-color: {BG_FIELD};
    color: {ACCENT_AMBER};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 4px 6px;
}}
QTableCornerButton::section {{
    background-color: {BG_FIELD};
    border: none;
}}

QScrollBar:vertical {{
    background: {BG_WINDOW};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_LIGHT};
    min-height: 24px;
    border-radius: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT_CYAN_DIM};
}}
QScrollBar:horizontal {{
    background: {BG_WINDOW};
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_LIGHT};
    min-width: 24px;
    border-radius: 2px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {ACCENT_CYAN_DIM};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}

QScrollArea {{
    border: none;
}}

QStatusBar {{
    background-color: {BG_PANEL};
    border-top: 1px solid {BORDER};
}}
"""


def apply(app: QApplication) -> None:
    """Call once, right after constructing the QApplication (see
    `main.py`). "Fusion" is a neutral cross-platform base style that
    actually respects QSS consistently (Windows' native style ignores parts
    of it), so it's set first and the stylesheet layers on top of it."""
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
