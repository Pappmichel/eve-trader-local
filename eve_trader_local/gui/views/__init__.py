"""One module per GUI view. A view is a QWidget subclass of `base.BaseView`
(or its `base.TableView` specialization for the common "a table plus a
refresh/action toolbar" shape) that `main_window.MainWindow` opens as a tab
on demand - see the `gui` package docstring for the overall navigation model.

Naming/grouping convention: a view's *file* here groups several of the CLI's
separate commands where they belong together as one on-screen unit (e.g.
`trading_shortlist.py` covers the shortlist table, refresh, and prune/trend
actions in one tab) - see each view module's own docstring for exactly which
CLI/`do_*` surface it covers and why it's grouped the way it is.
"""
