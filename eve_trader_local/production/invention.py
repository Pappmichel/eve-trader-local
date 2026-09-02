"""Invention cost/probability math: for one invention source (a T1 blueprint
for Tech II, a Sleeper relic for Tech III), estimate the datacore + decryptor
cost, the success probability, and the resulting BPC's run count for each
decryptor choice, and pick the "best" one by minimising *net* cost per BPC run
after accounting for the material savings the resulting ME gives you on every
subsequent build:
`((datacore+decryptor cost)/probability)/output_runs - (ME/100)*reducible_material_cost`.

The probability formula is EVE's actual invention mechanic (per
https://wiki.eveuniversity.org/Invention) - the real game reads two separate
datacore/science skills plus one encryption skill, contributing at different
rates and stacking additively, not multiplicatively:

    probability = base_probability
                  * (1 + (datacore_skill_1 + datacore_skill_2)/30 + encryption_skill/40)
                  * decryptor_probability_multiplier

Tech III hulls/subsystems use the exact same activity_id=8 Invention mechanic
as Tech II (CCP removed the old relic-based "Reverse Engineering" years ago -
confirmed against real SDE data, see engine.classify_activity's docstring), so
probability/runs/materials work identically either way. The *input* consumed
does not: Tech II consumes a real, ownable/reprintable T1 BPO/BPC, while Tech
III consumes a Sleeper relic (Intact/Malfunctioning/Wrecked -
constants.ANCIENT_RELIC_CATEGORY_ID), which can only ever be bought or looted,
never manufactured. That distinction is why estimate() prices the relic itself
into the attempt cost but not a T1 blueprint - see the known simplifications
below and estimate()'s own relic_cost handling.

Known simplifications: the invention job's own installation fee (facility fee)
is ignored, and - for a genuine T1 blueprint only, never a Tech III relic -
so is the T1 BPC's own copy cost (copy time + copy job fee for the blueprint
copy an attempt consumes). Both are real EVE costs, neither is modeled. Since
both are omitted consistently across every decryptor choice for the same item,
they don't change *which* decryptor is recommended unless two decryptors are
close enough that this gap could plausibly flip the ranking - worth keeping in
mind for genuinely marginal calls. The T1-BPC-copy-cost omission deliberately
does NOT extend to a Tech III relic: unlike a T1 BPO you already own and can
reprint near-for-free, a relic must be bought or looted fresh for every single
attempt, so omitting its cost the same way would be a real, not-minor
understatement of Tech III's true cost (confirmed real in the parent repo,
reported by a user, 2026-08-30).
"""
from __future__ import annotations

from typing import Optional

from .. import storage
from ..errors import ActionError
from ..goonmetrics_client import CurrentPrice
from . import pricing
from .config import PRODUCTION_CONFIG, ProductionConfig
from .constants import ANCIENT_RELIC_CATEGORY_ID, DECRYPTORS
from .models import InventionResult

PriceMap = dict[int, CurrentPrice]


class NoInventionRecipe(ActionError):
    """`t1_blueprint_type_id` invents nothing, or the SDE has no success
    probability for it. Its own exception type (rather than the parent repo's
    bare ValueError) so the multi-candidate helpers below can skip an
    unusable candidate without also swallowing a genuine pricing/auth failure,
    while still printing as a plain message if it reaches the user - it
    subclasses ActionError."""


def reducible_material_cost(manufacturing_blueprint_id: int, activity_id: int,
                            home: PriceMap, jita: PriceMap,
                            cfg: ProductionConfig = PRODUCTION_CONFIG) -> float:
    """Cost of a blueprint's own build materials that actually scale with ME.
    Materials with a base quantity of 1/run are excluded: EVE never reduces a
    material below 1 unit per run, so no ME bonus can ever save anything on
    them. Used to weigh a decryptor's ME bonus against its price - see the
    module docstring."""
    total = 0.0
    for material_id, base_qty in storage.get_blueprint_materials(manufacturing_blueprint_id, activity_id):
        if base_qty <= 1:
            continue
        sde_type = storage.get_sde_type(material_id)
        volume = sde_type[3] if sde_type else None
        price = pricing.buy_price(material_id, home, jita, volume, cfg)
        total += base_qty * (price or 0.0)
    return total


def skill_multiplier(cfg: ProductionConfig = PRODUCTION_CONFIG) -> float:
    """EVE's real invention skill bonus: each of the two datacore/science
    skills contributes 1/30 (~3.33%) per level and the encryption skill 1/40
    (2.5%) per level - all three additive, not multiplied together."""
    return (1
            + (cfg.datacore_skill_1_level + cfg.datacore_skill_2_level) / 30
            + cfg.encryption_skill_level / 40)


def estimate(t1_blueprint_type_id: int, decryptor_name: str, home: PriceMap, jita: PriceMap,
             cfg: ProductionConfig = PRODUCTION_CONFIG,
             reducible_material_cost_per_run: float = 0.0) -> InventionResult:
    """One invention source x one decryptor, priced end to end.

    `reducible_material_cost_per_run` is the invented item's own ME-scaling
    build-material cost (see reducible_material_cost) - pass 0 to ignore
    build-side ME savings entirely, e.g. when pricing invention itself rather
    than deciding which decryptor to build with.
    """
    if decryptor_name not in DECRYPTORS:
        raise ActionError(f"Unknown decryptor '{decryptor_name}'. "
                          f"Options: {', '.join(DECRYPTORS)}")
    recipe = storage.get_invention_recipe(t1_blueprint_type_id)
    if recipe is None:
        raise NoInventionRecipe(f"No invention recipe found for type ID {t1_blueprint_type_id}.")
    if recipe["base_probability"] is None:
        raise NoInventionRecipe(
            f"No base success probability found for type ID {t1_blueprint_type_id}.")

    decryptor = DECRYPTORS[decryptor_name]
    probability = min(1.0, recipe["base_probability"] * skill_multiplier(cfg)
                      * decryptor.probability_multiplier)
    output_runs = max(0, recipe["base_runs"] + decryptor.run_bonus)

    # A datacore/decryptor with genuinely no sell order anywhere must not be
    # silently priced at 0 ISK (`price or 0.0`) - that understates the
    # attempt's real cost and can make an actually-expensive decryptor look
    # cheaper than it is (confirmed real bug in the parent repo, 2026-08-29).
    # The raw cost fields below still sum whatever *is* priced (useful partial
    # information), but this flag gates the decision-driving derived fields, so
    # a missing price surfaces as an honest "can't estimate" (None) instead of
    # a too-low number quietly winning compare_decryptors' ranking.
    all_prices_known = True

    def _priced(type_id: int) -> float:
        nonlocal all_prices_known
        sde_type = storage.get_sde_type(type_id)
        volume = sde_type[3] if sde_type else None
        price = pricing.buy_price(type_id, home, jita, volume, cfg)
        if price is None:
            all_prices_known = False
        return price or 0.0

    datacore_cost = sum(qty * _priced(material_id) for material_id, qty in recipe["datacores"])
    decryptor_cost = _priced(decryptor.type_id) if decryptor.type_id else 0.0

    # A Tech III relic is the "blueprint" being consumed here, exactly as a T1
    # BPC is for Tech II - but unlike a T1 BPC (usually a near-free reprint of
    # an already-owned BPO, the documented reason its cost is deliberately not
    # modeled at all), a relic must be bought or looted fresh for every attempt
    # and is often the single most expensive input. Omitting it the same way
    # would materially overstate Tech III profitability rather than round it
    # slightly, so it IS priced, under the same all_prices_known gate as every
    # other input. A genuine T1 blueprint (category 9) never reaches this
    # branch, so Tech II's behavior is unchanged.
    relic_cost = (_priced(t1_blueprint_type_id)
                  if storage.get_type_category(t1_blueprint_type_id) == ANCIENT_RELIC_CATEGORY_ID
                  else 0.0)

    total_attempt_cost = datacore_cost + decryptor_cost + relic_cost
    expected_cost_per_success = (
        (total_attempt_cost / probability) if probability > 0 and all_prices_known else None
    )
    expected_cost_per_run = (
        (expected_cost_per_success / output_runs)
        if expected_cost_per_success is not None and output_runs > 0 else None
    )
    material_savings_per_run = (decryptor.me_bonus / 100) * reducible_material_cost_per_run
    net_cost_per_run = (
        (expected_cost_per_run - material_savings_per_run)
        if expected_cost_per_run is not None else None
    )

    t1_type = storage.get_sde_type(t1_blueprint_type_id)
    product_type = storage.get_sde_type(recipe["product_type_id"])

    return InventionResult(
        t1_blueprint_type_id=t1_blueprint_type_id,
        t1_blueprint_name=t1_type[2] if t1_type else str(t1_blueprint_type_id),
        product_type_id=recipe["product_type_id"],
        product_name=product_type[2] if product_type else str(recipe["product_type_id"]),
        decryptor=decryptor_name, probability=probability, output_runs=output_runs,
        datacore_cost=datacore_cost, decryptor_cost=decryptor_cost, relic_cost=relic_cost,
        total_attempt_cost=total_attempt_cost,
        expected_cost_per_success=expected_cost_per_success,
        expected_cost_per_run=expected_cost_per_run,
        me=decryptor.me_bonus, te=decryptor.te_bonus,
        material_savings_per_run=material_savings_per_run,
        net_cost_per_run=net_cost_per_run,
    )


def _net_cost_key(result: InventionResult) -> float:
    """Sort key: an unpriceable combination ranks last rather than being
    dropped, so the caller still sees it exists."""
    return result.net_cost_per_run if result.net_cost_per_run is not None else float("inf")


def compare_decryptors(t1_blueprint_type_id: int, home: PriceMap, jita: PriceMap,
                       cfg: ProductionConfig = PRODUCTION_CONFIG,
                       reducible_material_cost_per_run: float = 0.0) -> list[InventionResult]:
    """One InventionResult per decryptor option (including "None"), cheapest
    net cost per BPC run first - i.e. expected invention cost minus the ME
    material savings that decryptor buys on every subsequent build."""
    results = [
        estimate(t1_blueprint_type_id, name, home, jita, cfg, reducible_material_cost_per_run)
        for name in DECRYPTORS
    ]
    results.sort(key=_net_cost_key)
    return results


def best_decryptor_for_item(t1_blueprint_type_id: int, home: PriceMap, jita: PriceMap,
                            cfg: ProductionConfig = PRODUCTION_CONFIG,
                            reducible_material_cost_per_run: float = 0.0) -> Optional[InventionResult]:
    """The single cheapest decryptor choice for ONE fixed invention source, or
    None if no recipe/probability data exists for it. For Tech III the caller
    decides which of the (up to 3) relic grades this compares decryptors
    within - see best_recipe_and_decryptor to let the grade vary too, which is
    what Tech III actually needs."""
    try:
        results = compare_decryptors(t1_blueprint_type_id, home, jita, cfg,
                                     reducible_material_cost_per_run)
    except NoInventionRecipe:
        return None
    return results[0] if results else None


def compare_recipes_and_decryptors(product_blueprint_type_id: int, home: PriceMap, jita: PriceMap,
                                   cfg: ProductionConfig = PRODUCTION_CONFIG,
                                   reducible_material_cost_per_run: float = 0.0) -> list[InventionResult]:
    """Every (invention source, decryptor) combination for
    `product_blueprint_type_id`, cheapest net cost per run first - generalises
    compare_decryptors, which only ever varies the decryptor for one fixed
    source. For Tech II the two are identical (there is always exactly one
    candidate, a real T1 blueprint); the difference is Tech III, whose up-to-
    three relic grades (Intact/Malfunctioning/Wrecked) have materially
    different odds, output runs and cost, and must be compared side by side
    rather than silently always assuming the highest-probability (Intact) one
    (confirmed real gap in the parent repo, reported by a user, 2026-08-30)."""
    results: list[InventionResult] = []
    for candidate in storage.find_invention_recipe_candidates_by_product_type_id(product_blueprint_type_id):
        try:
            results.extend(compare_decryptors(candidate, home, jita, cfg,
                                              reducible_material_cost_per_run))
        except NoInventionRecipe:
            continue
    results.sort(key=_net_cost_key)
    return results


def best_recipe_and_decryptor(product_blueprint_type_id: int, home: PriceMap, jita: PriceMap,
                              cfg: ProductionConfig = PRODUCTION_CONFIG,
                              reducible_material_cost_per_run: float = 0.0) -> Optional[InventionResult]:
    """The single globally-cheapest (source, decryptor) combination - the first
    element of compare_recipes_and_decryptors, or None if no invention
    recipe/probability data exists for `product_blueprint_type_id` at all."""
    results = compare_recipes_and_decryptors(product_blueprint_type_id, home, jita, cfg,
                                             reducible_material_cost_per_run)
    return results[0] if results else None


def best_recipe_for_decryptor(product_blueprint_type_id: int, decryptor_name: str,
                              home: PriceMap, jita: PriceMap,
                              cfg: ProductionConfig = PRODUCTION_CONFIG,
                              reducible_material_cost_per_run: float = 0.0) -> Optional[InventionResult]:
    """Like best_recipe_and_decryptor, but with the decryptor fixed - still
    explores every source candidate (Tech III's three relic grades), picking
    whichever grade is cheapest with that one decryptor. For when a decryptor
    has been chosen by hand but the grade should still be auto-optimised."""
    best: Optional[InventionResult] = None
    for candidate in storage.find_invention_recipe_candidates_by_product_type_id(product_blueprint_type_id):
        try:
            result = estimate(candidate, decryptor_name, home, jita, cfg,
                              reducible_material_cost_per_run)
        except NoInventionRecipe:
            continue
        if best is None or _net_cost_key(result) < _net_cost_key(best):
            best = result
    return best
