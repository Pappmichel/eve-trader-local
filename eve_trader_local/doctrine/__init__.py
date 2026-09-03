"""Doctrine tool: fitted-ship contract/stockpile tracking against EFT
fittings.

A separate tool from Trading/Production (which share only the SDE/ESI/config
foundation with this one, exactly as in the parent `eve-trader` repo). Only
the EFT-fitting parser and its supporting constants/dataclasses are ported so
far; see SYNC.md for what's still outstanding (matching/validation, contract
sync, the orchestration layer).
"""
