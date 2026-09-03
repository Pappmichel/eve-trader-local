from __future__ import annotations

from eve_trader_local.station_trading.constants import (
    SKILL_ACCOUNTING,
    SKILL_ADVANCED_BROKER_RELATIONS,
    SKILL_BROKER_RELATIONS,
    SKILL_RETAIL,
    SKILL_TRADE,
    SKILL_TYCOON,
    SKILL_WHOLESALE,
    order_slots_from_skills,
)


def test_no_skills_gives_the_base_slot_count():
    assert order_slots_from_skills({}) == 5


def test_each_slot_skill_adds_its_own_per_level_bonus():
    assert order_slots_from_skills({SKILL_TRADE: 5}) == 5 + 4 * 5
    assert order_slots_from_skills({SKILL_RETAIL: 5}) == 5 + 8 * 5
    assert order_slots_from_skills({SKILL_WHOLESALE: 5}) == 5 + 16 * 5
    assert order_slots_from_skills({SKILL_TYCOON: 5}) == 5 + 32 * 5


def test_slot_skills_stack_additively():
    levels = {SKILL_TRADE: 5, SKILL_RETAIL: 5, SKILL_WHOLESALE: 5, SKILL_TYCOON: 5}
    assert order_slots_from_skills(levels) == 5 + 4 * 5 + 8 * 5 + 16 * 5 + 32 * 5


def test_fee_discount_skills_never_affect_order_slots():
    """Accounting/Broker Relations/Advanced Broker Relations reduce fees, not
    order slots - confirming they're absent from the slot formula entirely."""
    levels = {SKILL_ACCOUNTING: 5, SKILL_BROKER_RELATIONS: 5, SKILL_ADVANCED_BROKER_RELATIONS: 5}
    assert order_slots_from_skills(levels) == 5


def test_unlisted_skills_default_to_zero():
    assert order_slots_from_skills({999999: 5}) == 5
