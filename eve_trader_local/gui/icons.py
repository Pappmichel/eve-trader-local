"""Central icon lookup, built on `qtawesome` (bundled Font Awesome 5 Free /
Material Design Icons - both permissively licensed, no attribution/asset
files needed, matching this app's own "PySide6 over PyQt6" reasoning about
staying safely redistributable as a binary - see pyproject.toml).

Every call site asks for a *semantic* name (`icon("settings")`, not
`icon("fa5s.cog")`) so the visual language (which glyph means "settings"
everywhere) and the color (theme accents, not ad-hoc hex codes) both live
in exactly one place. Icons default to `theme.TEXT` (matching normal label/
button text) unless a semantic name has an obviously more fitting accent
(destructive actions in a dim red, the primary-action glyphs in cyan).
"""
from __future__ import annotations

import qtawesome as qta
from PySide6.QtGui import QIcon

from . import theme

# fa5s = Font Awesome 5 Solid, mdi6 = Material Design Icons 6 - mixed
# deliberately, picking whichever of the two has the clearer glyph for a
# given concept rather than forcing one icon set throughout.
_GLYPHS: dict[str, str] = {
    # -- Window/app identity --
    "app": "mdi6.rocket-launch-outline",
    # -- App menu --
    "settings": "fa5s.sliders-h",
    "characters": "fa5s.user-astronaut",
    "update-data": "mdi6.cloud-sync-outline",
    "refresh-sde": "mdi6.database-sync-outline",
    "check-updates": "fa5s.arrow-circle-up",
    # -- Tool menus --
    "trading": "fa5s.exchange-alt",
    "production": "fa5s.industry",
    "doctrine": "fa5s.shield-alt",
    "refining": "mdi6.hexagon-multiple-outline",
    "station-trading": "fa5s.store",
    "portfolio": "fa5s.chart-pie",
    # -- Common actions --
    "refresh": "fa5s.sync-alt",
    "reload": "fa5s.redo-alt",
    "add": "fa5s.plus",
    "remove": "fa5s.trash-alt",
    "login": "fa5s.sign-in-alt",
    "logout": "fa5s.sign-out-alt",
    "save": "fa5s.save",
    "search": "fa5s.search",
    "check": "fa5s.check-circle",
    "warning": "fa5s.exclamation-triangle",
    "info": "fa5s.info-circle",
    "close": "fa5s.times",
    "show": "fa5s.eye",
    "run": "fa5s.play",
    "discover": "fa5s.compass",
    "validate": "fa5s.check-double",
    "target": "fa5s.bullseye",
    "clear": "fa5s.eraser",
    "resolve": "fa5s.map-marker-alt",
    "calculate": "fa5s.calculator",
    "tree": "fa5s.sitemap",
    "quote": "fa5s.receipt",
    "undo": "fa5s.undo",
}

# Semantic names whose glyph should read as an alert/destructive/positive
# color rather than plain text color.
_COLOR_OVERRIDES: dict[str, str] = {
    "app": theme.ACCENT_CYAN,
    "remove": "#c0554a",
    "logout": "#c0554a",
    "warning": "#c0392b",
    "check": "#2f8f4e",
    "update-data": theme.ACCENT_CYAN,
    "refresh-sde": theme.ACCENT_CYAN,
    "check-updates": theme.ACCENT_CYAN,
}


def icon(name: str, color: str | None = None) -> QIcon:
    """`name` is one of `_GLYPHS`' keys. Raises `KeyError` (naming the bad
    key) rather than silently returning a blank icon - a typo here should
    fail loud at the call site, not render an invisible button."""
    glyph = _GLYPHS[name]
    return qta.icon(glyph, color=color or _COLOR_OVERRIDES.get(name, theme.TEXT))
