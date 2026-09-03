"""Tests for eve_trader_local/refining/optimizer.py - the Mineral Shopping
List's buy-vs-refine linear program (parent repo's GitHub issue #93).

Fully pure: `optimize_shopping_list` takes pre-priced, pre-refined inputs, so
every case here is hand-constructed with numbers whose optimum can be checked
by arithmetic in the docstring of each test. No network, no SQLite.
"""
from __future__ import annotations

import itertools
import random

import pytest

from eve_trader_local.errors import ActionError
from eve_trader_local.refining.models import MineralOption, MineralRequirement, OreOption
from eve_trader_local.refining.optimizer import optimize_shopping_list

TRIT, PYE, MEX = 34, 35, 36
NAMES = {TRIT: "Tritanium", PYE: "Pyerite", MEX: "Mexallon"}


def _req(type_id, qty):
    return MineralRequirement(type_id=type_id, name=NAMES[type_id], required_qty=qty)


def _ore(type_id, cost_per_portion, yields, portion_size=100, volume_m3=0.01, family="Veldspar"):
    """cost_per_portion is the readable number in each test's arithmetic; the
    dataclass itself stores a per-unit cost, so divide it back out."""
    return OreOption(type_id=type_id, item=f"Compressed Ore {type_id}", family=family, is_ice=False,
                     volume_m3=volume_m3, portion_size=portion_size,
                     landed_cost_per_unit=cost_per_portion / portion_size,
                     yield_per_portion=dict(yields))


def _minerals(prices):
    return {m: MineralOption(type_id=m, name=NAMES[m], landed_cost_per_unit=p,
                             source=None if p is None else "Jita")
            for m, p in prices.items()}


def _by_id(purchases):
    return {p.type_id: p for p in purchases}


def _coverage(plan):
    return {c.type_id: c for c in plan.coverage}


# --------------------------------------------------------------- basic picks
def test_prefers_ore_when_refining_is_cheaper():
    """1000 Tritanium. One ore: 500 ISK/portion -> 400 Trit = 1.25 ISK/unit,
    against 5.00 ISK/unit direct. LP optimum is 2.5 portions = 1250 ISK;
    rounded up to 3 whole portions = 1500 ISK, which still beats trimming to
    2 portions + 200 Trit direct (1000 + 1000 = 2000)."""
    plan = optimize_shopping_list([_req(TRIT, 1000)],
                                  [_ore(1, 500, {TRIT: 400})],
                                  _minerals({TRIT: 5.0}))

    assert len(plan.ore_purchases) == 1
    buy = plan.ore_purchases[0]
    assert (buy.portions, buy.units) == (3, 300)
    assert plan.direct_purchases == []
    assert plan.ore_cost == pytest.approx(1500.0)
    assert plan.total_cost == pytest.approx(1500.0)
    assert plan.lp_cost == pytest.approx(1250.0)
    assert plan.all_direct_cost == pytest.approx(5000.0)
    assert plan.savings_vs_all_direct == pytest.approx(3500.0)
    assert plan.total_volume_m3 == pytest.approx(3.0)

    cov = _coverage(plan)[TRIT]
    assert (cov.from_ore, cov.from_direct, cov.delivered) == (1200, 0, 1200)
    assert cov.surplus == pytest.approx(200.0)


def test_prefers_direct_purchase_when_ore_is_dearer():
    """Same ore (1.25 ISK/unit refined) but Tritanium lists at 1.00 direct,
    so the whole 1000 units is bought outright for 1000 ISK and no ore is
    touched at all."""
    plan = optimize_shopping_list([_req(TRIT, 1000)],
                                  [_ore(1, 500, {TRIT: 400})],
                                  _minerals({TRIT: 1.0}))

    assert plan.ore_purchases == []
    assert len(plan.direct_purchases) == 1
    assert plan.direct_purchases[0].quantity == 1000
    assert plan.direct_purchases[0].source == "Jita"
    assert plan.total_cost == pytest.approx(1000.0)
    assert plan.lp_cost == pytest.approx(1000.0)
    assert plan.savings_vs_all_direct == pytest.approx(0.0)
    assert plan.total_volume_m3 == pytest.approx(0.0)


# ---------------------------------------------------------------- real mixes
def test_mixes_ore_and_direct_when_the_last_portion_is_not_worth_it():
    """1000 Tritanium; ore is 900 ISK/portion -> 900 Trit (1.00 ISK/unit)
    against 1.20 direct. The continuous LP wants 1.111 portions (1111.1 ISK);
    the honest whole-portion answers are 2 portions = 1800, or 1 portion + 100
    direct = 900 + 120 = 1020. The trim step has to find the latter."""
    plan = optimize_shopping_list([_req(TRIT, 1000)],
                                  [_ore(1, 900, {TRIT: 900})],
                                  _minerals({TRIT: 1.2}))

    assert len(plan.ore_purchases) == 1 and plan.ore_purchases[0].portions == 1
    assert len(plan.direct_purchases) == 1 and plan.direct_purchases[0].quantity == 100
    assert plan.ore_cost == pytest.approx(900.0)
    assert plan.direct_cost == pytest.approx(120.0)
    assert plan.total_cost == pytest.approx(1020.0)
    assert plan.lp_cost == pytest.approx(1000 / 900 * 900)  # 1111.11 -> the relaxation's own optimum

    cov = _coverage(plan)[TRIT]
    assert (cov.from_ore, cov.from_direct, cov.delivered) == (900, 100, 1000)
    assert cov.surplus == pytest.approx(0.0)


def test_picks_ore_for_one_mineral_and_direct_for_another():
    """1000 Trit + 1000 Pyerite. Ore A: 800/portion -> 1000 Trit (0.80/unit)
    vs 1.50 direct -> ore wins. Ore B: 900/portion -> 1000 Pyerite (0.90/unit)
    vs 0.85 direct -> direct wins. Optimum 800 + 850 = 1650, and the solver
    must not be fooled into taking Ore B just because it's an ore."""
    plan = optimize_shopping_list([_req(TRIT, 1000), _req(PYE, 1000)],
                                  [_ore(1, 800, {TRIT: 1000}), _ore(2, 900, {PYE: 1000})],
                                  _minerals({TRIT: 1.5, PYE: 0.85}))

    assert [p.type_id for p in plan.ore_purchases] == [1]
    assert [p.type_id for p in plan.direct_purchases] == [PYE]
    assert plan.total_cost == pytest.approx(1650.0)
    assert plan.all_direct_cost == pytest.approx(1500 + 850)
    assert plan.savings_vs_all_direct == pytest.approx(700.0)


def test_greedy_per_mineral_ranking_would_lose_to_the_lp():
    """The case the LP exists for. 1000 Trit + 1000 Mexallon.

    Ore A (2000/portion -> 1000 Trit + 1000 Mex) covers both at once for 2000.
    Ore B (600/portion -> 1000 Trit) is the cheapest *Tritanium* source per
    unit (0.60), and Mexallon direct is 3.00/unit. A per-mineral greedy pick
    would take Ore B for Trit (600) and then still owe 3000 for Mexallon =
    3600. Buying the one combined portion of Ore A is 2000."""
    plan = optimize_shopping_list([_req(TRIT, 1000), _req(MEX, 1000)],
                                  [_ore(1, 2000, {TRIT: 1000, MEX: 1000}), _ore(2, 600, {TRIT: 1000})],
                                  _minerals({TRIT: 1.0, MEX: 3.0}))

    assert [p.type_id for p in plan.ore_purchases] == [1]
    assert plan.ore_purchases[0].portions == 1
    assert plan.direct_purchases == []
    assert plan.total_cost == pytest.approx(2000.0)
    assert plan.lp_cost == pytest.approx(2000.0)


def test_shared_yield_is_not_double_counted_or_under_covered():
    """One ore yielding two wanted minerals at once: 2000/portion -> 1000 Trit
    + 500 Mex. Requirement is 1000 Trit + 500 Mex, i.e. exactly one portion -
    the plan must be one portion with zero surplus on both lines, not two
    portions (one "per mineral")."""
    plan = optimize_shopping_list([_req(TRIT, 1000), _req(MEX, 500)],
                                  [_ore(1, 2000, {TRIT: 1000, MEX: 500})],
                                  _minerals({TRIT: 1.0, MEX: 4.0}))

    assert len(plan.ore_purchases) == 1 and plan.ore_purchases[0].portions == 1
    assert plan.direct_purchases == []
    assert plan.total_cost == pytest.approx(2000.0)
    assert plan.all_direct_cost == pytest.approx(1000 + 2000)
    cov = _coverage(plan)
    assert cov[TRIT].surplus == pytest.approx(0.0)
    assert cov[MEX].surplus == pytest.approx(0.0)


def test_overlapping_yield_tops_up_the_shortfall_directly_rather_than_over_buying_ore():
    """Same overlapping ore (2000/portion -> 1000 Trit + 500 Mex), but the
    requirement is 2000 Trit + 500 Mex. Two portions = 4000 (and 500 surplus
    Mexallon, which earns no credit); one portion + 1000 Trit at 1.00 direct =
    3000. Mexallon is only worth taking from ore (5000 direct), so the first
    portion stays."""
    plan = optimize_shopping_list([_req(TRIT, 2000), _req(MEX, 500)],
                                  [_ore(1, 2000, {TRIT: 1000, MEX: 500})],
                                  _minerals({TRIT: 1.0, MEX: 10.0}))

    assert len(plan.ore_purchases) == 1 and plan.ore_purchases[0].portions == 1
    assert [p.type_id for p in plan.direct_purchases] == [TRIT]
    assert plan.direct_purchases[0].quantity == 1000
    assert plan.total_cost == pytest.approx(3000.0)

    cov = _coverage(plan)
    assert (cov[TRIT].from_ore, cov[TRIT].from_direct) == (1000, 1000)
    assert (cov[MEX].from_ore, cov[MEX].from_direct) == (500, 0)


# ------------------------------------------------------------------ failures
def test_unsourceable_mineral_raises_action_error():
    """Mexallon has no direct price and no ore yields it - there is no plan,
    and saying so beats returning one that quietly doesn't cover the build."""
    with pytest.raises(ActionError) as exc:
        optimize_shopping_list([_req(TRIT, 1000), _req(MEX, 100)],
                               [_ore(1, 500, {TRIT: 400})],
                               _minerals({TRIT: 5.0, MEX: None}))
    assert "Mexallon" in str(exc.value)


def test_mineral_missing_from_mineral_options_entirely_is_the_same_failure():
    with pytest.raises(ActionError):
        optimize_shopping_list([_req(MEX, 100)], [_ore(1, 500, {TRIT: 400})], _minerals({TRIT: 5.0}))


def test_no_requirements_raises_action_error():
    with pytest.raises(ActionError):
        optimize_shopping_list([], [_ore(1, 500, {TRIT: 400})], _minerals({TRIT: 5.0}))
    with pytest.raises(ActionError):
        optimize_shopping_list([_req(TRIT, 0)], [_ore(1, 500, {TRIT: 400})], _minerals({TRIT: 5.0}))


def test_unpriceable_mineral_is_still_solvable_from_ore_alone():
    """No direct price is not by itself an error: the ore path can carry it,
    and the plan must round up enough portions to cover it (1000 / 400 -> 3)."""
    plan = optimize_shopping_list([_req(TRIT, 1000)],
                                  [_ore(1, 500, {TRIT: 400})],
                                  _minerals({TRIT: None}))
    assert plan.ore_purchases[0].portions == 3
    assert plan.all_direct_cost is None
    assert plan.savings_vs_all_direct is None


def test_ore_that_yields_nothing_wanted_is_never_bought():
    plan = optimize_shopping_list([_req(TRIT, 1000)],
                                  [_ore(1, 1.0, {PYE: 100000}), _ore(2, 500, {TRIT: 400})],
                                  _minerals({TRIT: 5.0}))
    assert [p.type_id for p in plan.ore_purchases] == [2]


# ------------------------------------------------- no under-covering, ever
@pytest.mark.parametrize("seed", range(25))
def test_random_plans_always_cover_every_requirement(seed):
    """The rounding guard the LP itself can't give: whatever mix of continuous
    solve, round-up, repair and trim runs, every requirement must come out at
    least met, the reported costs must add up, and the whole-portion plan can
    never be cheaper than the continuous relaxation it came from."""
    rng = random.Random(seed)
    minerals = [TRIT, PYE, MEX]
    requirements = [_req(m, rng.randint(1, 50_000)) for m in minerals]
    ores = []
    for i in range(rng.randint(1, 5)):
        yields = {m: rng.choice([0, rng.randint(1, 5_000)]) for m in minerals}
        if not any(yields.values()):
            yields[rng.choice(minerals)] = rng.randint(1, 5_000)
        ores.append(_ore(i + 1, rng.uniform(100, 20_000), yields,
                         portion_size=rng.choice([50, 100, 200])))
    # Every mineral keeps a direct price here so the case is always feasible;
    # the unsourceable path has its own test above.
    prices = _minerals({m: rng.uniform(0.5, 20.0) for m in minerals})

    plan = optimize_shopping_list(requirements, ores, prices)

    for cov in plan.coverage:
        assert cov.delivered >= cov.required - 1e-9, f"{cov.name} under-covered"
        assert cov.delivered == cov.from_ore + cov.from_direct
        assert cov.surplus == pytest.approx(cov.delivered - cov.required)
    assert plan.total_cost == pytest.approx(plan.ore_cost + plan.direct_cost)
    assert plan.ore_cost == pytest.approx(sum(p.total_cost for p in plan.ore_purchases))
    assert plan.direct_cost == pytest.approx(sum(p.total_cost for p in plan.direct_purchases))
    # A whole-portion plan can only ever cost more than the relaxed optimum.
    assert plan.total_cost >= plan.lp_cost - 1e-6
    # ...and never more than simply buying everything outright.
    assert plan.total_cost <= plan.all_direct_cost + 1e-6


@pytest.mark.parametrize("seed", range(15))
def test_small_plans_match_brute_force_over_whole_portions(seed):
    """The strongest available check on the round-up/trim heuristics: on a
    small enough problem, enumerate every whole-portion combination (with the
    leftover gap priced at the direct rate, exactly as `_plan_cost` does) and
    confirm the optimizer's plan costs no more than the true discrete
    optimum."""
    rng = random.Random(1000 + seed)
    minerals = [TRIT, PYE]
    requirements = [_req(m, rng.randint(500, 4_000)) for m in minerals]
    required = {r.type_id: r.required_qty for r in requirements}
    ores = [_ore(i + 1, rng.uniform(500, 5_000),
                 {m: rng.choice([0, rng.randint(200, 2_000)]) for m in minerals})
            for i in range(2)]
    for ore in ores:
        if not any(ore.yield_per_portion.values()):
            ore.yield_per_portion[TRIT] = 1_000
    prices = {m: rng.uniform(1.0, 8.0) for m in minerals}
    plan = optimize_shopping_list(requirements, ores, _minerals(prices))

    best = float("inf")
    for combo in itertools.product(range(0, 12), repeat=len(ores)):
        cost = sum(n * o.landed_cost_per_portion for n, o in zip(combo, ores))
        for m in minerals:
            got = sum(n * o.yield_per_portion.get(m, 0) for n, o in zip(combo, ores))
            gap = required[m] - got
            if gap > 0:
                cost += -(-gap // 1) * prices[m]  # ceil(gap) units bought outright
        best = min(best, cost)

    assert plan.total_cost <= best + 1e-6
