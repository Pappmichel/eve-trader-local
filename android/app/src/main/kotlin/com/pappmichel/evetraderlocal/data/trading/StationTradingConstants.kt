package com.pappmichel.evetraderlocal.data.trading

/** Static EVE-mechanic tables for Station Trading - real skill type_ids and
 * the order-slot-count formula, ported verbatim from the desktop build's
 * station_trading/constants.py (see that file's own docstring for why these
 * ids were checked directly against the SDE rather than trusted from memory
 * - e.g. Tycoon is 18580, not 16598, which is actually Marketing; a
 * "Margin Trading" skill some research names does not exist in the game and
 * is not modeled here).
 *
 * No overlap with Production's own job-slot skill set (Mass Production,
 * Advanced Mass Production, ...) - a different skill tree entirely. */
const val SKILL_TRADE = 3443
const val SKILL_RETAIL = 3444
const val SKILL_WHOLESALE = 16596
const val SKILL_TYCOON = 18580
const val SKILL_ACCOUNTING = 16622
const val SKILL_BROKER_RELATIONS = 3446
const val SKILL_ADVANCED_BROKER_RELATIONS = 16597

/** Every skill worth pulling a level for, name-labeled in the order a
 * Skills summary should show them - fee/tax-discount skills (Accounting,
 * Broker Relations, Advanced Broker Relations) are informational only and
 * deliberately do not feed order_slots_from_skills below, since those
 * discounts also depend on NPC corp standings this app has no way to read. */
val STATION_TRADING_SKILL_LABELS: LinkedHashMap<Int, String> = linkedMapOf(
    SKILL_TRADE to "Trade",
    SKILL_RETAIL to "Retail",
    SKILL_WHOLESALE to "Wholesale",
    SKILL_TYCOON to "Tycoon",
    SKILL_ACCOUNTING to "Accounting",
    SKILL_BROKER_RELATIONS to "Broker Relations",
    SKILL_ADVANCED_BROKER_RELATIONS to "Advanced Broker Relations",
)

private const val BASE_ORDER_SLOTS = 5

/** Base 5 concurrent orders + 4/level Trade + 8/level Retail + 16/level
 * Wholesale + 32/level Tycoon - the well-established, standings-independent
 * EVE order-slot mechanic (unlike the fee/tax discount skills, which also
 * depend on NPC corp standings and are therefore not modeled numerically at
 * all - shown as a raw skill level only). `skillLevels` maps skill type_id
 * to active_skill_level, same shape ESI's own `/characters/{id}/skills/`
 * response reduces to. */
fun orderSlotsFromSkills(skillLevels: Map<Int, Int>): Int =
    BASE_ORDER_SLOTS +
        4 * (skillLevels[SKILL_TRADE] ?: 0) +
        8 * (skillLevels[SKILL_RETAIL] ?: 0) +
        16 * (skillLevels[SKILL_WHOLESALE] ?: 0) +
        32 * (skillLevels[SKILL_TYCOON] ?: 0)
