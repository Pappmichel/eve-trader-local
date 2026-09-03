"""Ore & Minerals tool: ore/ice import-refine-sell, reprocessing quotes,
mineral shopping list.

A separate tool from Trading/Production/Doctrine (which share only the
SDE/ESI/config foundation with this one, exactly as in the parent
`eve-trader` repo, see its own CLAUDE.md's "Two tools, one backend" section
and GitHub issue #90). Only the fixed reprocessing-yield constants/math and
the inventory paste parser are ported so far; see SYNC.md for what's still
outstanding (pricing, candidate discovery, the shopping-list optimizer, the
orchestration layer).
"""
