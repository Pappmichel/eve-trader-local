"""PyInstaller's entry point. A separate script, not `-m eve_trader_local.gui.main`,
because PyInstaller analyzes a concrete .py file to build its dependency
graph - pointing it at a package's `__main__`-less module needs this same
"thin script that imports and calls the real main()" indirection either way,
so it may as well live here in `packaging/` rather than inside the package
itself, where it would look like a second, redundant entry point next to
`gui/main.py`'s own `if __name__ == "__main__"` block.
"""
from eve_trader_local.gui.main import main

if __name__ == "__main__":
    raise SystemExit(main())
