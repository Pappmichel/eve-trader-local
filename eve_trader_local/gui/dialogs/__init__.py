"""App-level dialogs - reachable from `main_window.MainWindow`'s new top-level
"App" menu, not from any per-tool menu (see that menu's own docstring for
why: these are cross-tool, not another tool view).

- `settings_dialog.SettingsDialog`: one tab per tool's config dataclass,
  the first real caller of any tool's `do_update_settings` (see that
  module's own docstring for why there was no CLI precedent to mirror).
- `characters_dialog.CharactersDialog`: lists every authorized character
  (`auth.TokenManager.list_records`) and lets the user start a new SSO
  login or remove a registered one.

Both are `QDialog`s, not `views.base.BaseView` tabs - they use
`workers.BusyMixin` directly (the same busy/status/run_action machinery
`BaseView` itself is built on) rather than subclassing `BaseView`, since a
`QDialog` and a `QWidget`-based view can't share a common Qt base class
(see `workers.BusyMixin`'s own docstring)."""
