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
  - accent (cyan):    #2ecdf5 - selection, focus, active tab, primary actions
  - accent (amber):   #e8b923 - header rows, hover emphasis

Deliberately no bundled font file - CCP's own EVE UI font is proprietary,
and a system font (Segoe UI on Windows, with generic fallbacks) avoids both
the licensing question and any packaging complexity of shipping font
assets. Icons come from `icons.py` (qtawesome) instead, colored from this
same palette rather than any hardcoded hex at the call site.

Two opt-in "class" selectors, set via `QWidget.setProperty("cssClass", ...)`
+ `style().polish(widget)` (Qt's own dynamic-property-as-CSS-class
mechanism - there's no native `class=` attribute), let a handful of call
sites step outside the plain-widget default look without a one-off inline
stylesheet each:
  - `QPushButton[cssClass="primary"]`: the one clear call-to-action button
    in a toolbar/dialog (e.g. "Update Selected", "Log In...") gets a filled
    cyan treatment instead of the default outlined button, the same
    "one obvious primary action" convention most modern UIs use.
  - `QFrame[cssClass="banner-error"|"banner-info"|"banner-busy"]`: replaces
    `workers.py`'s old bare colored text with a padded, left-accented,
    icon-prefixed banner box (`BusyMixin.status_row`) - see that module for
    how/when each is applied. A `QFrame`, not the `QLabel` it wraps, since a
    plain `QWidget`/`QLabel` ignores QSS background/border unless
    `WA_StyledBackground` is set - `QFrame` paints its stylesheet out of the
    box.
  - `QLabel[cssClass="section-header"]`: a small bold/amber heading with a
    bottom rule, for views that group multiple logical sections
    (`views/base.py`'s `_add_section_header`).
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget

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
BANNER_ERROR_BG = "#2a1414"
BANNER_ERROR_BORDER = "#c0392b"
BANNER_ERROR_TEXT = "#e8a29c"
BANNER_INFO_BG = "#132218"
BANNER_INFO_BORDER = "#2f8f4e"
BANNER_INFO_TEXT = "#9fd6ae"
BANNER_BUSY_BG = "#12202a"
BANNER_BUSY_BORDER = ACCENT_CYAN_DIM
BANNER_BUSY_TEXT = "#a9d8e8"

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
    border-radius: 4px;
    padding: 5px 8px;
}}

QMenuBar {{
    background-color: {BG_PANEL};
    border-bottom: 1px solid {BORDER};
    padding: 2px 4px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 6px 12px;
    border-radius: 4px;
}}
QMenuBar::item:selected {{
    background-color: {BG_FIELD};
    color: {ACCENT_CYAN};
}}
QMenu {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 7px 28px 7px 10px;
    border-radius: 4px;
    icon-size: 15px 15px;
}}
QMenu::icon {{
    padding-left: 4px;
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
    margin: 5px 8px;
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    background-color: {BG_PANEL};
    border-radius: 0 6px 6px 6px;
    top: -1px;
}}
QTabBar::tab {{
    background-color: {BG_WINDOW};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 7px 16px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background-color: {BG_PANEL};
    color: {ACCENT_CYAN};
    border-bottom: 2px solid {ACCENT_CYAN};
    font-weight: 600;
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
QLabel[cssClass="section-header"] {{
    color: {ACCENT_AMBER};
    font-weight: 600;
    font-size: 13px;
    padding-bottom: 4px;
    border-bottom: 1px solid {BORDER};
    margin-top: 4px;
}}
QFrame[cssClass="banner-error"] {{
    background-color: {BANNER_ERROR_BG};
    border: 1px solid {BANNER_ERROR_BORDER};
    border-left: 3px solid {BANNER_ERROR_BORDER};
    border-radius: 4px;
    padding: 7px 10px;
}}
QFrame[cssClass="banner-error"] QLabel {{
    color: {BANNER_ERROR_TEXT};
}}
QFrame[cssClass="banner-info"] {{
    background-color: {BANNER_INFO_BG};
    border: 1px solid {BANNER_INFO_BORDER};
    border-left: 3px solid {BANNER_INFO_BORDER};
    border-radius: 4px;
    padding: 7px 10px;
}}
QFrame[cssClass="banner-info"] QLabel {{
    color: {BANNER_INFO_TEXT};
}}
QFrame[cssClass="banner-busy"] {{
    background-color: {BANNER_BUSY_BG};
    border: 1px solid {BANNER_BUSY_BORDER};
    border-left: 3px solid {BANNER_BUSY_BORDER};
    border-radius: 4px;
    padding: 7px 10px;
}}
QFrame[cssClass="banner-busy"] QLabel {{
    color: {BANNER_BUSY_TEXT};
}}

QPushButton {{
    background-color: {BG_FIELD};
    border: 1px solid {BORDER_LIGHT};
    padding: 7px 16px;
    border-radius: 5px;
    icon-size: 15px 15px;
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
QPushButton[cssClass="primary"] {{
    background-color: {ACCENT_CYAN_DIM};
    border: 1px solid {ACCENT_CYAN};
    color: #06222b;
    font-weight: 600;
}}
QPushButton[cssClass="primary"]:hover {{
    background-color: {ACCENT_CYAN};
    color: #06222b;
}}
QPushButton[cssClass="primary"]:pressed {{
    background-color: {ACCENT_AMBER};
    border-color: {ACCENT_AMBER};
    color: #2b2200;
}}
QPushButton[cssClass="primary"]:disabled {{
    background-color: {BG_FIELD_DISABLED};
    border-color: {BORDER};
    color: {TEXT_DIM};
}}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {BG_FIELD};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 4px;
    padding: 4px 8px;
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
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 4px;
    selection-background-color: {BG_FIELD};
    selection-color: {ACCENT_CYAN};
    padding: 2px;
}}

QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {BORDER_LIGHT};
    border-radius: 3px;
    background-color: {BG_FIELD};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT_CYAN_DIM};
    border-color: {ACCENT_CYAN};
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {ACCENT_AMBER};
}}

QTableWidget, QTableView {{
    background-color: {BG_PANEL};
    alternate-background-color: {BG_WINDOW};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 6px;
    selection-background-color: {ACCENT_CYAN_DIM};
    selection-color: {TEXT};
}}
QTableWidget::item, QTableView::item {{
    padding: 3px 6px;
}}
QHeaderView::section {{
    background-color: {BG_FIELD};
    color: {ACCENT_AMBER};
    font-weight: 600;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
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


# Shared layout rhythm - `views.base.BaseView` applies this to every view's
# `root_layout` itself; app-level `QDialog`s (which don't go through
# `BaseView`) call this once on their own top-level layout instead, so a
# dialog and a workspace tab breathe the same amount rather than a dialog
# being stuck with Qt's tighter platform default.
LAYOUT_MARGINS = (14, 14, 14, 14)
LAYOUT_SPACING = 10


def apply_layout_rhythm(layout) -> None:
    layout.setContentsMargins(*LAYOUT_MARGINS)
    layout.setSpacing(LAYOUT_SPACING)
