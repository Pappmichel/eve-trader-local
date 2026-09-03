"""Native desktop GUI (PySide6), replacing the CLI as the primary interface.

Navigation model (see ROADMAP.md's "Deferred" section for the full jEveAssets
research this is based on): one top-level menu per tool (Trading, Production,
Doctrine, Ore & Minerals, Station Trading), each listing its sub-views as menu
items. Picking one opens it as a closable tab in a shared workspace
(`main_window.MainWindow`) - nothing is a mandatory always-visible page the
way the parent eve-trader's React frontend works. Several of the parent's/
CLI's separate pages are deliberately grouped into one combined view per menu
item where that makes sense, rather than mirroring every CLI command 1:1.

This package only ever calls the same `do_*` functions the CLI calls
(`actions.py`, `production/actions.py`, etc.) - never storage or the network
clients directly - so the CLI and GUI can never drift apart, same rule the
parent eve-trader's CLI/API split already follows.

Every `do_*` call that touches the network (ESI/Goonmetrics) runs on a
worker thread (`workers.ActionWorker`), never the Qt UI thread - some of
these calls take several seconds to minutes (a full candidate search), and
blocking the UI thread for that long would freeze the whole window.
"""
