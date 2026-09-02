"""Invention cost/probability tests.

Nothing here touches the network: the SDE side is a real, tiny SQLite cache
written through storage.replace_sde_data (same pattern as
test_production_engine.py), and the price side is the plain
{type_id: CurrentPrice} maps pricing.buy_price already takes as arguments, so
no market client is ever constructed.

The config used throughout zeroes the broker fee and haul cost so that a
material's landed price equals its listed sell price exactly - the point here
is the invention math, and pricing.py has its own tests for the sourcing rule.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.config import validate_config_overrides
from eve_trader_local.errors import ActionError, ConfigError
from eve_trader_local.goonmetrics_client import CurrentPrice
from eve_trader_local.production import invention
from eve_trader_local.production.config import ProductionConfig
from eve_trader_local.production.constants import DECRYPTORS

# Tech II chain: a T1 blueprint invents the T2 blueprint of a T2 module.
T1_BLUEPRINT = 1002
T2_BLUEPRINT = 1001
T2_MODULE = 2048
DATACORE_A = 20410
DATACORE_B = 20424
# Tech III chain: three relic grades all invent the same subsystem blueprint.
T3_BLUEPRINT = 1008
T3_SUBSYSTEM = 45591
INTACT_RELIC = 1010
MALFUNCTIONING_RELIC = 1011
WRECKED_RELIC = 1012
# Manufacturing materials of the T2 module (for the ME-savings half).
TRITANIUM = 34
MORPHITE = 11399

ACCELERANT = DECRYPTORS["Accelerant"]

BASE_PROBABILITY = 0.34
BASE_RUNS = 10
# 4/4/4 skills: 1 + (4+4)/30 + 4/40.
SKILL_MULTIPLIER = 1 + 8 / 30 + 4 / 40


def _cfg(**overrides) -> ProductionConfig:
    return ProductionConfig(jita_buy_broker_fee=0.0, haul_cost_per_m3=0.0,
                            encryption_skill_level=4,
                            datacore_skill_1_level=4, datacore_skill_2_level=4,
                            **overrides)


def _prices(**by_type_id: float) -> dict[int, CurrentPrice]:
    return {int(tid): CurrentPrice(type_id=int(tid), updated="", buy=0.0, sell=sell)
            for tid, sell in by_type_id.items()}


DECRYPTOR_PRICES = {
    "Accelerant": 500_000.0, "Attainment": 400_000.0, "Augmentation": 1_000_000.0,
    "Parity": 600_000.0, "Process": 300_000.0, "Symmetry": 450_000.0,
    "Optimized Attainment": 900_000.0, "Optimized Augmentation": 1_200_000.0,
}

JITA = _prices(**{
    str(DATACORE_A): 1_000.0,
    str(DATACORE_B): 2_000.0,
    **{str(DECRYPTORS[name].type_id): price for name, price in DECRYPTOR_PRICES.items()},
    str(INTACT_RELIC): 30_000_000.0,
    str(MALFUNCTIONING_RELIC): 8_000_000.0,
    str(WRECKED_RELIC): 1_000_000.0,
    str(TRITANIUM): 5.0,
    str(MORPHITE): 10_000.0,
})
NO_HOME: dict[int, CurrentPrice] = {}


def _seed_sde():
    storage.replace_sde_data(
        types=[
            # type_id, group_id, name, volume, published, market_group_id,
            # meta_level, meta_group_id, portion_size
            (TRITANIUM, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (MORPHITE, 18, "Morphite", 0.01, 1, 100, 0, 1, 1),
            (DATACORE_A, 333, "Datacore - Mechanical Engineering", 0.1, 1, 100, 0, None, 1),
            (DATACORE_B, 333, "Datacore - Molecular Engineering", 0.1, 1, 100, 0, None, 1),
            (T2_MODULE, 60, "Damage Control II", 5.0, 1, 200, 5, 2, 1),
            (T3_SUBSYSTEM, 963, "Loki Core - Augmented Nuclear Reactor", 50.0, 1, 400, 1, 2, 1),
            (T1_BLUEPRINT, 9, "Damage Control I Blueprint", 0.01, 1, None, 0, None, 1),
            (T2_BLUEPRINT, 9, "Damage Control II Blueprint", 0.01, 1, None, 0, None, 1),
            (T3_BLUEPRINT, 9, "Loki Core Blueprint", 0.01, 1, None, 0, None, 1),
            (INTACT_RELIC, 34, "Intact Armor Nanobot", 10.0, 1, 500, 0, None, 1),
            (MALFUNCTIONING_RELIC, 34, "Malfunctioning Armor Nanobot", 10.0, 1, 500, 0, None, 1),
            (WRECKED_RELIC, 34, "Wrecked Armor Nanobot", 10.0, 1, 500, 0, None, 1),
        ]
        # The decryptors themselves, so their volume/name lookups resolve.
        + [(d.type_id, 314, name, 0.1, 1, 100, 0, None, 1)
           for name, d in DECRYPTORS.items() if d.type_id],
        groups=[(18, 4, "Mineral"), (60, 7, "Damage Control"), (963, 6, "Strategic Cruiser"),
                (9, 9, "Blueprint"), (34, 34, "Ancient Relics"), (333, 4, "Datacores"),
                (314, 4, "Decryptors")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment"),
                       (400, None, "Ships"), (500, None, "Ancient Relics")],
        blueprint_time=[(T1_BLUEPRINT, 8, 3600.0), (INTACT_RELIC, 8, 3600.0)],
        blueprint_materials=[
            # Invention (activity 8) "materials" are the datacores consumed.
            (T1_BLUEPRINT, 8, DATACORE_A, 2),
            (T1_BLUEPRINT, 8, DATACORE_B, 2),
            (INTACT_RELIC, 8, DATACORE_A, 3),
            (MALFUNCTIONING_RELIC, 8, DATACORE_A, 3),
            (WRECKED_RELIC, 8, DATACORE_A, 3),
            # The T2 module's own manufacturing materials: only the qty>1 one
            # can ever benefit from ME.
            (T2_BLUEPRINT, 1, TRITANIUM, 1000),
            (T2_BLUEPRINT, 1, MORPHITE, 1),
        ],
        blueprint_products=[
            (T2_BLUEPRINT, 1, T2_MODULE, 1),
            (T3_BLUEPRINT, 1, T3_SUBSYSTEM, 1),
            (T1_BLUEPRINT, 8, T2_BLUEPRINT, BASE_RUNS),
            (INTACT_RELIC, 8, T3_BLUEPRINT, 20),
            (MALFUNCTIONING_RELIC, 8, T3_BLUEPRINT, 10),
            (WRECKED_RELIC, 8, T3_BLUEPRINT, 3),
        ],
        invention_probability=[
            (T1_BLUEPRINT, T2_BLUEPRINT, BASE_PROBABILITY),
            (INTACT_RELIC, T3_BLUEPRINT, 0.26),
            (MALFUNCTIONING_RELIC, T3_BLUEPRINT, 0.21),
            (WRECKED_RELIC, T3_BLUEPRINT, 0.14),
        ],
        categories=[(4, "Material"), (7, "Module"), (6, "Ship"),
                    (9, "Blueprint"), (34, "Ancient Relics")],
    )


@pytest.fixture
def sde(db):
    _seed_sde()
    return db


# ------------------------------------------------------------- probability
def test_skill_multiplier_stacks_the_three_skills_additively():
    """Each datacore skill is worth 1/30 per level, encryption 1/40 - added
    together, never multiplied."""
    assert invention.skill_multiplier(_cfg()) == pytest.approx(SKILL_MULTIPLIER)
    assert invention.skill_multiplier(
        ProductionConfig(encryption_skill_level=0,
                         datacore_skill_1_level=0, datacore_skill_2_level=0)) == 1.0
    assert invention.skill_multiplier(
        ProductionConfig(encryption_skill_level=5,
                         datacore_skill_1_level=5, datacore_skill_2_level=5)
    ) == pytest.approx(1 + 10 / 30 + 5 / 40)


def test_probability_without_a_decryptor_is_base_times_skills(sde):
    result = invention.estimate(T1_BLUEPRINT, "None", NO_HOME, JITA, _cfg())

    assert result.probability == pytest.approx(BASE_PROBABILITY * SKILL_MULTIPLIER)
    # "None" is not a real EVE item, so it costs nothing and adds no runs.
    assert result.decryptor_cost == 0.0
    assert result.output_runs == BASE_RUNS
    # EVE's real no-decryptor invention baseline is ME2/TE4, not ME0/TE0.
    assert (result.me, result.te) == (2, 4)


def test_probability_with_a_decryptor_applies_its_multiplier_and_run_bonus(sde):
    result = invention.estimate(T1_BLUEPRINT, "Accelerant", NO_HOME, JITA, _cfg())

    assert result.probability == pytest.approx(
        BASE_PROBABILITY * SKILL_MULTIPLIER * ACCELERANT.probability_multiplier)
    assert result.output_runs == BASE_RUNS + ACCELERANT.run_bonus
    assert result.decryptor_cost == 500_000.0
    assert (result.me, result.te) == (ACCELERANT.me_bonus, ACCELERANT.te_bonus)


def test_probability_never_exceeds_one(sde):
    """A high-base-probability recipe plus good skills plus Optimized
    Attainment (x1.9) overshoots 100% arithmetically - EVE caps it."""
    with storage.connect() as conn:
        conn.execute("UPDATE sde_invention_probability SET probability = 0.9 "
                     "WHERE t1_blueprint_type_id = ?", (T1_BLUEPRINT,))

    result = invention.estimate(T1_BLUEPRINT, "Optimized Attainment", NO_HOME, JITA, _cfg())

    assert result.probability == 1.0


# ------------------------------------------------------------ expected cost
def test_expected_cost_per_run_divides_by_probability_then_by_output_runs(sde):
    result = invention.estimate(T1_BLUEPRINT, "None", NO_HOME, JITA, _cfg())

    # 2 x 1000 + 2 x 2000
    assert result.datacore_cost == pytest.approx(6_000.0)
    assert result.total_attempt_cost == pytest.approx(6_000.0)
    expected_per_success = 6_000.0 / (BASE_PROBABILITY * SKILL_MULTIPLIER)
    assert result.expected_cost_per_success == pytest.approx(expected_per_success)
    assert result.expected_cost_per_run == pytest.approx(expected_per_success / BASE_RUNS)
    # No reducible material cost passed, so no ME saving to net off.
    assert result.material_savings_per_run == 0.0
    assert result.net_cost_per_run == pytest.approx(result.expected_cost_per_run)


def test_decryptor_cost_is_part_of_the_attempt_cost(sde):
    result = invention.estimate(T1_BLUEPRINT, "Accelerant", NO_HOME, JITA, _cfg())

    assert result.total_attempt_cost == pytest.approx(6_000.0 + 500_000.0)
    assert result.expected_cost_per_success == pytest.approx(
        506_000.0 / result.probability)
    assert result.expected_cost_per_run == pytest.approx(
        506_000.0 / result.probability / (BASE_RUNS + ACCELERANT.run_bonus))


def test_me_bonus_is_netted_off_as_material_savings(sde):
    """A decryptor's ME is worth (me/100) x the item's ME-scaling material
    cost on every run the resulting BPC produces - that is what makes an
    expensive decryptor worth buying, so it belongs in the ranking figure."""
    result = invention.estimate(T1_BLUEPRINT, "Accelerant", NO_HOME, JITA, _cfg(),
                                reducible_material_cost_per_run=100_000.0)

    assert result.material_savings_per_run == pytest.approx(
        ACCELERANT.me_bonus / 100 * 100_000.0)
    assert result.net_cost_per_run == pytest.approx(
        result.expected_cost_per_run - result.material_savings_per_run)


def test_reducible_material_cost_skips_quantity_one_materials(sde):
    """EVE never reduces a material below 1 unit per run, so a qty-1 material
    (here Morphite, deliberately the expensive one) can never be saved on and
    must not inflate the ME bonus's apparent worth."""
    cost = invention.reducible_material_cost(T2_BLUEPRINT, 1, NO_HOME, JITA, _cfg())

    assert cost == pytest.approx(1000 * 5.0)


def test_an_unpriced_input_makes_the_derived_figures_none_not_cheap(sde):
    """A datacore with no sell order anywhere used to be silently priced at 0
    ISK, understating the attempt and letting an expensive decryptor look
    cheap. The raw cost fields still report what *is* priced; the
    decision-driving ones report "unknown"."""
    partial = {k: v for k, v in JITA.items() if k != DATACORE_B}

    result = invention.estimate(T1_BLUEPRINT, "None", NO_HOME, partial, _cfg())

    assert result.datacore_cost == pytest.approx(2_000.0)  # only DATACORE_A priced
    assert result.expected_cost_per_success is None
    assert result.expected_cost_per_run is None
    assert result.net_cost_per_run is None


# ------------------------------------------------------------------ Tech III
def test_a_relic_is_priced_into_the_attempt_but_a_t1_blueprint_is_not(sde):
    """A Sleeper relic must be bought fresh for every attempt, unlike a T1 BPC
    reprinted from an owned BPO - so its cost counts, and the T1 case's
    deliberate omission is unchanged."""
    relic = invention.estimate(INTACT_RELIC, "None", NO_HOME, JITA, _cfg())
    tech_ii = invention.estimate(T1_BLUEPRINT, "None", NO_HOME, JITA, _cfg())

    assert relic.relic_cost == pytest.approx(30_000_000.0)
    assert relic.total_attempt_cost == pytest.approx(3 * 1_000.0 + 30_000_000.0)
    assert tech_ii.relic_cost == 0.0
    assert tech_ii.total_attempt_cost == pytest.approx(tech_ii.datacore_cost)


def test_every_relic_grade_is_compared_not_just_the_best_odds_one(sde):
    results = invention.compare_recipes_and_decryptors(T3_BLUEPRINT, NO_HOME, JITA, _cfg())

    assert {r.t1_blueprint_type_id for r in results} == {
        INTACT_RELIC, MALFUNCTIONING_RELIC, WRECKED_RELIC}
    assert len(results) == 3 * len(DECRYPTORS)


def test_best_recipe_and_decryptor_can_prefer_a_cheaper_lower_odds_grade(sde):
    """The Intact relic has the best odds (0.26 vs 0.14) and the most runs, but
    at 30x the Wrecked relic's price - the point of comparing grades at all is
    that the best odds are not automatically the cheapest per run."""
    best = invention.best_recipe_and_decryptor(T3_BLUEPRINT, NO_HOME, JITA, _cfg())

    assert best is not None
    assert best.t1_blueprint_type_id == WRECKED_RELIC
    assert best.product_type_id == T3_BLUEPRINT
    assert best.t1_blueprint_name == "Wrecked Armor Nanobot"
    per_run = [(r.t1_blueprint_type_id, r.net_cost_per_run)
               for r in invention.compare_recipes_and_decryptors(T3_BLUEPRINT, NO_HOME, JITA, _cfg())]
    assert best.net_cost_per_run == pytest.approx(min(c for _, c in per_run))


def test_best_recipe_for_decryptor_keeps_the_decryptor_but_optimises_the_grade(sde):
    best = invention.best_recipe_for_decryptor(T3_BLUEPRINT, "Attainment", NO_HOME, JITA, _cfg())

    assert best is not None
    assert best.decryptor == "Attainment"
    assert best.t1_blueprint_type_id == WRECKED_RELIC


# --------------------------------------------------------------- selection
def test_compare_decryptors_covers_every_option_cheapest_first(sde):
    results = invention.compare_decryptors(T1_BLUEPRINT, NO_HOME, JITA, _cfg())

    assert [r.decryptor for r in results] and len(results) == len(DECRYPTORS)
    assert {r.decryptor for r in results} == set(DECRYPTORS)
    costs = [r.net_cost_per_run for r in results]
    assert costs == sorted(costs)


def test_unpriceable_combinations_sort_last_rather_than_disappearing(sde):
    """An option nobody is selling is still a real option to show; it just
    must never outrank a priced one."""
    partial = {k: v for k, v in JITA.items() if k != ACCELERANT.type_id}

    results = invention.compare_decryptors(T1_BLUEPRINT, NO_HOME, partial, _cfg())

    assert results[-1].decryptor == "Accelerant"
    assert results[-1].net_cost_per_run is None
    assert results[0].net_cost_per_run is not None


def test_best_decryptor_for_item_is_the_cheapest_net_cost_option(sde):
    best = invention.best_decryptor_for_item(T1_BLUEPRINT, NO_HOME, JITA, _cfg())
    all_options = invention.compare_decryptors(T1_BLUEPRINT, NO_HOME, JITA, _cfg())

    assert best is not None
    assert best.decryptor == all_options[0].decryptor
    assert best.net_cost_per_run == pytest.approx(all_options[0].net_cost_per_run)


def test_a_big_me_bonus_can_beat_a_cheap_decryptor(sde):
    """With a large ME-scaling build cost, the ME bonus dominates the ranking -
    the whole reason net_cost_per_run exists instead of ranking on the raw
    invention cost."""
    cheap_only = invention.best_decryptor_for_item(T1_BLUEPRINT, NO_HOME, JITA, _cfg())
    with_savings = invention.best_decryptor_for_item(
        T1_BLUEPRINT, NO_HOME, JITA, _cfg(), reducible_material_cost_per_run=500_000_000.0)

    assert with_savings is not None and cheap_only is not None
    assert with_savings.me >= cheap_only.me
    assert with_savings.net_cost_per_run < 0  # savings exceed the invention cost


# ------------------------------------------------------------------ failures
def test_a_type_with_no_invention_recipe_raises(sde):
    with pytest.raises(invention.NoInventionRecipe):
        invention.estimate(T2_BLUEPRINT, "None", NO_HOME, JITA, _cfg())


def test_a_recipe_without_a_probability_row_raises(sde):
    """"No probability data" must be "can't estimate", never a silent 0."""
    with storage.connect() as conn:
        conn.execute("DELETE FROM sde_invention_probability WHERE t1_blueprint_type_id = ?",
                     (T1_BLUEPRINT,))

    with pytest.raises(invention.NoInventionRecipe):
        invention.estimate(T1_BLUEPRINT, "None", NO_HOME, JITA, _cfg())


def test_the_best_helpers_return_none_instead_of_raising(sde):
    assert invention.best_decryptor_for_item(T2_BLUEPRINT, NO_HOME, JITA, _cfg()) is None
    assert invention.best_recipe_and_decryptor(T2_MODULE, NO_HOME, JITA, _cfg()) is None
    assert invention.best_recipe_for_decryptor(T2_MODULE, "None", NO_HOME, JITA, _cfg()) is None


def test_an_unknown_decryptor_name_is_a_user_facing_error(sde):
    with pytest.raises(ActionError, match="Unknown decryptor"):
        invention.estimate(T1_BLUEPRINT, "Acclerant", NO_HOME, JITA, _cfg())


def test_skill_levels_are_range_checked_to_eves_own_zero_to_five():
    """EVE skills only ever run 0-5, so anything outside that is a typo, not a
    setting - rejected before it can quietly distort every probability."""
    validate_config_overrides(ProductionConfig(), {"encryption_skill_level": 5})
    with pytest.raises(ConfigError):
        validate_config_overrides(ProductionConfig(), {"encryption_skill_level": 6})
    with pytest.raises(ConfigError):
        validate_config_overrides(ProductionConfig(), {"datacore_skill_1_level": -1})
