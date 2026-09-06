"""The Settings dialog: one tab per tool's config dataclass
(`TradingConfig`/`ProductionConfig`/`DoctrineConfig`/`RefiningConfig`/
`StationTradingConfig`), each tab built generically off the dataclass's own
fields rather than one hand-written form per tool - adding a field to any of
those five dataclasses shows up here automatically, no dialog change needed.

This is the first real caller of any of the five `do_update_settings`
functions anywhere in the app (see each `actions.py`'s own docstring) - there
is no CLI precedent for the UI side of this, only the underlying action and
`config.validate_config_overrides`/`ConfigError` -> `ActionError` for the
error path, which `workers.BusyMixin.run_action` already surfaces the same
way every other view's `run_action` call does.

Field-to-widget mapping (`_classify_field`/`_ConfigForm._make_widget`):
- a field named in `enum_options` (Production's `*_structure_type`/
  `*_rig_tier`, Refining's `structure_type`/`rig_tier`/`implant`) -> QComboBox
  over the exact options `validate_production_overrides`/
  `validate_refining_overrides` would accept, so a typo here is structurally
  impossible.
- `bool` -> QCheckBox.
- `Optional[int | float | str]` -> QLineEdit (blank means "set to None" on
  save) - a QSpinBox has no representation for "no value", and several of
  these (structure/character/location ids) can be None as a fully supported
  state (see each config module's own field comments).
- a plain (non-Optional) `int`/`float` -> QSpinBox/QDoubleSpinBox, ranged off
  `config._FIELD_RANGES` where that field has an entry (the same bounds
  `validate_config_overrides` itself enforces, reused here rather than
  duplicated so the dialog can never accept something the backend would
  reject anyway) - unbounded fields get a wide but finite range, not Qt's
  default (which is too narrow for ISK-scale values).
- a plain `str` -> QLineEdit.
- a bare `tuple` field (`excluded_path_prefixes`, a tuple of plain strings) ->
  a multi-line QPlainTextEdit, one entry per line - split/joined on
  read/save, blank lines dropped.
- a `dict[str, int]` field (`ore_family_skill_levels`) -> a small two-column
  (family name / skill level) QTableWidget with Add/Remove Row buttons - see
  `_DictIntTableEditor` below. Free-text keys, not a fixed enum: an ore/ice
  family name isn't cross-referenced against SDE/constants data anywhere
  else in this dialog, and a typo here is harmless by the underlying field's
  own design (`RefiningConfig.ore_family_skill_levels`'s own docstring: a
  family missing from the dict defaults to skill level 5, not an error) -
  so there is nothing to validate against beyond "is the level a whole
  number".
- anything else (there is currently nothing else this generic mapping
  doesn't cover) -> a read-only QLineEdit showing its repr, same fallback
  the tuple/dict cases used before their own editors existed.
"""
from __future__ import annotations

import dataclasses
import functools
import typing
from typing import Any, Callable, Optional

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ... import actions
from ... import config as trading_config
from ...config import _FIELD_RANGES
from ...doctrine import actions as doctrine_actions
from ...doctrine import config as doctrine_config
from ...production import actions as production_actions
from ...production import config as production_config
from ...production.constants import RIG_TIERS, STRUCTURE_TYPES
from ...refining import actions as refining_actions
from ...refining import config as refining_config
from ...refining.constants import implant_options, rig_options, structure_options
from ...station_trading import actions as station_trading_actions
from ...station_trading import config as station_trading_config
from .. import icons, theme
from ..workers import BusyMixin

# Wide-but-finite fallbacks for numeric fields with no _FIELD_RANGES entry -
# Qt's own QSpinBox/QDoubleSpinBox defaults (0..99) are far too narrow for
# ISK-scale values, and leaving them unset would silently clamp a valid entry.
_INT_FALLBACK = (-1_000_000_000, 1_000_000_000)
_FLOAT_FALLBACK = (-1.0e12, 1.0e12)


def _classify_field(hint: Any) -> tuple[str, bool]:
    """Returns (kind, optional) where kind is one of "bool"/"int"/"float"/
    "str"/"other". `hint` is a resolved (not a `from __future__ import
    annotations` string) type, via `typing.get_type_hints`."""
    origin = typing.get_origin(hint)
    optional = False
    real = hint
    if origin is typing.Union:
        args = typing.get_args(hint)
        if type(None) in args:
            optional = True
        remaining = [a for a in args if a is not type(None)]
        if remaining:
            real = remaining[0]
    if real is bool:
        return "bool", optional
    if real is int:
        return "int", optional
    if real is float:
        return "float", optional
    if real is str:
        return "str", optional
    if real is tuple or typing.get_origin(real) is tuple:
        return "tuple", optional
    if typing.get_origin(real) is dict:
        return "dict", optional
    return "other", optional


class _DictIntTableEditor(QWidget):
    """Editor for a `dict[str, int]` config field (`ore_family_skill_levels`)
    - a two-column (key/int value) QTableWidget plus Add/Remove Row buttons.
    Keys are free text, not a fixed enum - see module docstring for why."""

    def __init__(self, value: dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Family", "Skill Level"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setMaximumHeight(160)
        for key, level in value.items():
            self._append_row(str(key), str(level))
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        add_btn = QPushButton(icons.icon("add"), "Add Row")
        add_btn.clicked.connect(lambda: self._append_row("", "5"))
        buttons.addWidget(add_btn)
        remove_btn = QPushButton(icons.icon("remove"), "Remove Selected Row")
        remove_btn.clicked.connect(self._remove_selected_row)
        buttons.addWidget(remove_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def _append_row(self, key: str, level: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(key))
        self.table.setItem(row, 1, QTableWidgetItem(level))

    def _remove_selected_row(self) -> None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            self.table.removeRow(row)

    def value(self) -> dict:
        """Blank-key rows are dropped (an easy way to "remove" a row without
        the button); a non-integer level falls back to 0 rather than raising
        - a bad paste here shouldn't crash the dialog's save path, just save
        something visibly wrong that's easy to notice and fix."""
        result = {}
        for row in range(self.table.rowCount()):
            key_item = self.table.item(row, 0)
            key = key_item.text().strip() if key_item is not None else ""
            if not key:
                continue
            level_item = self.table.item(row, 1)
            level_text = level_item.text().strip() if level_item is not None else "0"
            try:
                level = int(level_text)
            except ValueError:
                level = 0
            result[key] = level
        return result


class _ConfigForm:
    """One tool's form: builds a QFormLayout of (field label -> widget) pairs
    off a live config dataclass instance, and reports back only the fields
    whose widget value actually changed (`collect_updates`) - so a Save never
    sends a no-op update for every untouched field."""

    def __init__(self, cfg: Any, enum_options: Optional[dict[str, tuple]] = None):
        self.cfg = cfg
        self.enum_options = enum_options or {}
        self.layout = QFormLayout()
        self._getters: dict[str, Callable[[], Any]] = {}
        self._widgets: dict[str, Any] = {}  # field name -> its widget, mainly for tests
        self._originals: dict[str, Any] = {}

        hints = typing.get_type_hints(type(cfg))
        for f in dataclasses.fields(cfg):
            name = f.name
            value = getattr(cfg, name)
            hint = hints.get(name, type(value))
            self._originals[name] = value
            widget, getter = self._make_widget(name, value, hint)
            self.layout.addRow(name, widget)
            self._widgets[name] = widget
            if getter is not None:
                self._getters[name] = getter

    def _make_widget(self, name: str, value: Any, hint: Any):
        if name in self.enum_options:
            combo = QComboBox()
            options = list(self.enum_options[name])
            combo.addItems(options)
            if value in options:
                combo.setCurrentText(value)
            return combo, combo.currentText

        kind, optional = _classify_field(hint)

        if kind == "bool":
            box = QCheckBox()
            box.setChecked(bool(value))
            return box, box.isChecked

        if optional:
            edit = QLineEdit("" if value is None else str(value))
            edit.setPlaceholderText("(not set)")

            def _get_optional(kind=kind, edit=edit):
                text = edit.text().strip()
                if text == "":
                    return None
                if kind == "int":
                    return int(text)
                if kind == "float":
                    return float(text)
                return text

            return edit, _get_optional

        if kind == "int":
            spin = QSpinBox()
            lo, hi = _FIELD_RANGES.get(name, (None, None))
            lo = int(lo) if lo is not None else _INT_FALLBACK[0]
            hi = int(hi) if hi is not None else _INT_FALLBACK[1]
            spin.setRange(lo, hi)
            spin.setValue(int(value))
            return spin, spin.value

        if kind == "float":
            spin = QDoubleSpinBox()
            lo, hi = _FIELD_RANGES.get(name, (None, None))
            spin.setDecimals(6)
            spin.setRange(float(lo) if lo is not None else _FLOAT_FALLBACK[0],
                         float(hi) if hi is not None else _FLOAT_FALLBACK[1])
            spin.setValue(float(value))
            return spin, spin.value

        if kind == "str":
            edit = QLineEdit(value or "")
            return edit, edit.text

        if kind == "tuple":
            text_edit = QPlainTextEdit("\n".join(str(v) for v in value))
            text_edit.setMaximumHeight(100)
            text_edit.setPlaceholderText("One entry per line.")

            def _get_tuple(text_edit=text_edit):
                return tuple(line.strip() for line in text_edit.toPlainText().splitlines() if line.strip())

            return text_edit, _get_tuple

        if kind == "dict":
            table_editor = _DictIntTableEditor(dict(value))
            return table_editor, table_editor.value

        # Anything else - see module docstring's "anything else" case.
        edit = QLineEdit(repr(value))
        edit.setReadOnly(True)
        edit.setToolTip("Not editable from this dialog yet - edit config.yaml directly.")
        return edit, None

    def collect_updates(self) -> dict[str, Any]:
        updates = {}
        for name, getter in self._getters.items():
            new_value = getter()
            if new_value != self._originals[name]:
                updates[name] = new_value
        return updates

    def mark_saved(self, updates: dict[str, Any]) -> None:
        """Called after a successful save so the next `collect_updates` diffs
        against the just-saved values, not the dialog's original snapshot."""
        self._originals.update(updates)


class SettingsDialog(QDialog, BusyMixin):
    """Non-modal-friendly (works either way - `main_window.py` opens it modal
    via `exec()`) tabbed Settings dialog, one tab per tool. See module
    docstring for the field-to-widget mapping and `workers.BusyMixin` for why
    this mixes that in directly rather than subclassing `views.base.BaseView`."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(760, 640)
        self._init_busy()
        self._forms: dict[str, tuple[_ConfigForm, Callable, Any]] = {}

        layout = QVBoxLayout(self)
        theme.apply_layout_rhythm(layout)
        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget)
        layout.addWidget(self.status_row)

        self._add_tab("Trading", trading_config.reload(), actions.do_update_settings)
        self._add_tab("Production", production_config.reload(), production_actions.do_update_settings,
                     enum_options={
                         "reaction_structure_type": STRUCTURE_TYPES,
                         "component_structure_type": STRUCTURE_TYPES,
                         "manufacturing_structure_type": STRUCTURE_TYPES,
                         "reaction_rig_tier": RIG_TIERS,
                         "component_rig_tier": RIG_TIERS,
                         "manufacturing_rig_tier": RIG_TIERS,
                     })
        self._add_tab("Doctrine", doctrine_config.reload(), doctrine_actions.do_update_settings)
        self._add_tab("Ore & Minerals", refining_config.reload(), refining_actions.do_update_settings,
                     enum_options={
                         "structure_type": structure_options(),
                         "rig_tier": rig_options(),
                         "implant": implant_options(),
                     })
        self._add_tab("Station Trading", station_trading_config.reload(),
                     station_trading_actions.do_update_settings)

    def _add_tab(self, title: str, cfg: Any, do_update_settings: Callable,
                 enum_options: Optional[dict[str, tuple]] = None) -> None:
        form = _ConfigForm(cfg, enum_options)

        page = QWidget()
        page_layout = QVBoxLayout(page)
        form_widget = QWidget()
        form_widget.setLayout(form.layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)
        page_layout.addWidget(scroll)

        save_btn = QPushButton(icons.icon("save", color="#06222b"), f"Save {title} Settings")
        save_btn.setProperty("cssClass", "primary")
        save_btn.clicked.connect(functools.partial(self._save_tab, title))
        page_layout.addWidget(save_btn)

        self.tab_widget.addTab(page, title)
        self._forms[title] = (form, do_update_settings, cfg)

    def _save_tab(self, title: str) -> None:
        form, do_update_settings, cfg = self._forms[title]
        updates = form.collect_updates()
        if not updates:
            self.show_info(f"{title}: no changes to save.")
            return

        def _on_success(result, form=form, updates=updates, title=title):
            form.mark_saved(updates)
            self.show_info(f"{title}: saved {', '.join(sorted(updates))}.")

        self.run_action(functools.partial(do_update_settings, updates, cfg), _on_success,
                        busy_message=f"Saving {title} settings...")
