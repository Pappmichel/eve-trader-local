"""Tests for eve_trader_local/refining/optimizer.py - the Mineral Shopping
List's buy-vs-refine linear program (parent repo's GitHub issue #93).

Fully pure: `optimize_shopping_list` takes pre-priced, pre-refined inputs, so
every case here is hand-constructed with numbers whose optimum can be checked
by arithmetic in the docstring of each test. No network, no SQLite.
"""
from __future__ import annotations

import itertools
import math
import random
import time

import pytest

from eve_trader_local.errors import ActionError
from eve_trader_local.refining.models import MineralOption, MineralRequirement, OreOption
from eve_trader_local.refining.optimizer import optimize_shopping_list

TRIT, PYE, MEX, ISO = 34, 35, 36, 37
NAMES = {TRIT: "Tritanium", PYE: "Pyerite", MEX: "Mexallon", ISO: "Isogen"}


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


# ---------------------------------------------------------------------------
# Regression test for the relax-then-round optimality gap (fixed by solving
# with `linprog`'s `integrality` param instead - see optimizer.py's module
# docstring, decision 1; ported from the parent's commit 90c669b, which fixed
# this same bug there first). The old code only ever *dropped* portions from
# the ore set the continuous relaxation gave non-zero weight to, so it could
# never discover that a single portion of a relaxation-excluded ore was the
# true whole-unit optimum. This brute-forces every small case's real answer
# independently (by enumerating every whole-portion/whole-unit combination,
# using the exact same "ceil the direct-buy gap" pricing the real app uses)
# and asserts the solver actually finds it - not just a feasible plan.

def _real_cost(ores, portions, mineral_ids, required, direct_price):
    """Mirrors optimizer.py's own real-world costing: portions*ore price plus
    a whole-unit ceil'd direct purchase for whatever gap is left - the same
    arithmetic `optimize_shopping_list` uses to build the plan it returns."""
    delivered = {m: 0 for m in mineral_ids}
    cost = 0.0
    for qty, ore in zip(portions, ores):
        cost += qty * ore.landed_cost_per_portion
        for m in mineral_ids:
            delivered[m] += qty * ore.yield_per_portion.get(m, 0)
    for m in mineral_ids:
        gap = required[m] - delivered[m]
        if gap > 1e-9:
            if m not in direct_price:
                return None  # infeasible: nothing left to cover this mineral
            cost += math.ceil(gap - 1e-9) * direct_price[m]
    return cost


def _brute_force_optimum(ores, mineral_ids, required, direct_price):
    # A single portion can yield as little as 1 unit of a mineral (see
    # _random_case), so the ceiling on portions needed has to scale with the
    # largest requirement, not a fixed small constant - otherwise brute force
    # itself misses the true (and only) feasible combination.
    max_portion = max(1, math.ceil(max(required.values())))
    best = None
    for combo in itertools.product(range(max_portion + 1), repeat=len(ores)):
        cost = _real_cost(ores, combo, mineral_ids, required, direct_price)
        if cost is None:
            continue
        if best is None or cost < best:
            best = cost
    return best


def _random_case(rng):
    """A small synthetic ore/requirement scenario, deliberately sized so
    `_brute_force_optimum` can exhaustively enumerate it (<=3 ores, each
    portion size 1 so "portions" and "real units" coincide)."""
    minerals = rng.sample([TRIT, PYE, MEX, ISO], rng.randint(1, 2))
    ores = []
    for i in range(rng.randint(1, 3)):
        yields = {m: rng.randint(1, 20) for m in minerals if rng.random() < 0.8}
        if not yields:
            yields = {minerals[0]: rng.randint(1, 20)}
        ores.append(_ore(i + 1, rng.uniform(1, 50), yields, portion_size=1))
    # Kept small deliberately: `_brute_force_optimum` enumerates every
    # portions^n_ores combination, so this needs to stay cheap enough to run
    # 200 times in a normal test suite while still being large enough that a
    # relaxation-excluded ore can plausibly be the true optimum.
    required = {m: rng.uniform(1, 20) for m in minerals}
    direct_price = {m: rng.uniform(0.5, 10) for m in minerals if rng.random() < 0.7}
    reachable = all(m in direct_price or any(o.yield_per_portion.get(m, 0) > 0 for o in ores)
                     for m in minerals)
    if not reachable:
        return None
    return ores, minerals, required, direct_price


def test_matches_brute_force_optimum_on_random_small_cases():
    rng = random.Random(20260903)  # fixed seed: deterministic, reproducible failures
    checked = 0
    while checked < 200:
        case = _random_case(rng)
        if case is None:
            continue
        ores, minerals, required, direct_price = case
        checked += 1

        requirements = [_req(m, required[m]) for m in minerals]
        mineral_options = _minerals({m: direct_price.get(m) for m in minerals})
        plan = optimize_shopping_list(requirements, ores, mineral_options)

        # Every requirement must still be covered - the one thing that must
        # never regress, fix or no fix.
        for coverage in plan.coverage:
            assert coverage.delivered + 1e-6 >= coverage.required

        optimum = _brute_force_optimum(ores, minerals, required, direct_price)
        assert optimum is not None, "brute force found no feasible combination at all"
        # Within float tolerance of the TRUE discrete optimum, not merely
        # feasible - this is what the old relax-then-round approach failed at
        # roughly 8% of the time (see optimizer.py's module docstring).
        assert plan.total_cost <= optimum + 1e-6, (
            f"plan cost {plan.total_cost} exceeds true optimum {optimum} "
            f"(case: ores={ores}, required={required}, direct_price={direct_price})"
        )


def test_realistic_scale_solves_quickly():
    """Sanity-checks solve time at the tool's realistic scale: dozens of ore
    candidates, requirements never exceeding the 8 real EVE minerals. Called
    at request time (refining/actions.py's do_optimize_mineral_shopping_list),
    so it needs to stay fast even though it's now a real MIP rather than a
    relaxed LP."""
    rng = random.Random(1)
    minerals = [34, 35, 36, 37, 38, 39, 40, 11399]  # the 8 real EVE minerals
    mineral_price = {m: rng.uniform(1, 80) for m in minerals}
    ores = []
    for i in range(60):
        yields = {m: rng.randint(50, 3000) for m in minerals if rng.random() < 0.4}
        if not yields:
            yields = {rng.choice(minerals): rng.randint(50, 3000)}
        # Ore price roughly tracks its refined mineral value (as real market
        # prices do) plus noise - the structure that actually stresses a MIP
        # solver (many near-tied choices), not pure uniform randomness.
        base_value = sum(qty * mineral_price[m] for m, qty in yields.items())
        markup = rng.uniform(0.85, 1.15)
        ores.append(_ore(i + 1, base_value * markup, yields, portion_size=100))
    required = {m: rng.uniform(10_000, 2_000_000) for m in minerals}
    requirements = [MineralRequirement(type_id=m, name=str(m), required_qty=required[m]) for m in minerals]
    mineral_options = {m: MineralOption(type_id=m, name=str(m),
                                        landed_cost_per_unit=mineral_price[m] * rng.uniform(0.95, 1.3),
                                        source="Jita")
                       for m in minerals}

    start = time.monotonic()
    plan = optimize_shopping_list(requirements, ores, mineral_options)
    elapsed = time.monotonic() - start

    for coverage in plan.coverage:
        assert coverage.delivered + 1e-6 >= coverage.required
    # Generous ceiling - a bit above optimizer.py's own `_MIP_TIME_LIMIT_SECONDS`
    # (5s) worst-case solver cutoff, so this fails loudly rather than the
    # solver silently eating its own timeout every run. Typical real solves
    # are well under 1s; this only guards against the realistic-scale case
    # regressing to multi-second territory unnoticed.
    assert elapsed < 8.0, (
        f"realistic-scale solve took {elapsed:.2f}s - see optimizer.py's "
        "module docstring on MIP solve time before assuming this is fine"
    )
