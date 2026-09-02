"""The user-facing error types for this app.

Lives in its own module rather than in actions.py (where the parent
eve-trader repo keeps ActionError) so that storage.py/config.py/auth.py can
all raise it without any of them importing an actions layer that doesn't
exist yet at this foundation stage.
"""
from __future__ import annotations


class ActionError(RuntimeError):
    """Raised for expected/user-facing problems (missing auth, no such role,
    bad config value, ...). Every command-line entry point catches this and
    prints just the message - anything else escaping as a raw traceback is a
    bug, not a supported failure mode."""


class ConfigError(ActionError):
    """A config.yaml value (or a stored override) doesn't match its field's
    declared type/range. Subclasses ActionError deliberately - in the parent
    repo these were kept separate only to avoid a circular import between
    config.py and actions.py, which doesn't exist here."""
