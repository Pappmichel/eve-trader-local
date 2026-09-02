"""The seller's own open market orders, and what they're missing.

Four related live-ESI checks, none of them cached or persisted - each is a
one-shot "how do things stand right now" question:

- `fetch_own_sell_orders`: how much of each item the seller still has listed
  at the structure (the "Own Orders Remaining" column of the original
  spreadsheet: SUM(volume_remain) WHERE type_id = x AND not a buy order AND
  location_id = structure_id).
- `check_undercut`/`_pooled`: which of those listed orders a competitor has
  since beaten on price.
- `fetch_seller_stock_without_order`/`_pooled`: stock physically sitting at
  the structure with no sell order on it at all - listable inventory that
  was apparently forgotten.
- `fetch_buyer_already_covered`: items the buyer needn't import more of,
  because they're already on order or already in inventory.

Storage: this module calls storage directly, like trade_reconciliation.py
and unlike candidate_discovery/history_backtest/shortlist - because the
parent's own own_orders.py does (it reads the SDE station list for the
buyer-side "is this in Jita" check). The SQL itself stays in storage.py,
this repo's single persistence module; nothing here is written anywhere -
resolving item names and building the display rows (see models.
UnlistedStockRow/UndercutRow) is the caller's job, exactly as in the
parent's actions.do_check_seller_unlisted_stock/do_check_undercut.
"""
from __future__ import annotations

from collections import defaultdict

from . import storage
from .config import TRADING_CONFIG, TradingConfig
from .esi_client import NON_STOCK_LOCATION_FLAGS, ESIClient

JITA_SOLAR_SYSTEM_ID = 30000142  # stable, never changes - distinct from cfg.jita_region_id (The Forge region)


def fetch_own_sell_orders(character_id: int, auth_role: str, client: ESIClient,
                          cfg: TradingConfig = TRADING_CONFIG) -> dict[int, float]:
    """Returns {type_id: remaining_volume} for open SELL orders at
    cfg.structure_id - one pass over the character's whole order list rather
    than one filtered query per shortlist row."""
    orders = client.character_orders(character_id, auth_role=auth_role)
    remaining: dict[int, float] = defaultdict(float)
    for o in orders:
        if o.get("is_buy_order"):
            continue
        if o.get("location_id") != cfg.structure_id:
            continue
        remaining[o["type_id"]] += o.get("volume_remain", 0)
    return dict(remaining)


def check_undercut_pooled(sellers: list[tuple[int, str]], client: ESIClient,
                          cfg: TradingConfig = TRADING_CONFIG) -> list[dict]:
    """Which of the sellers' own sell orders are currently beaten by a
    cheaper competing order at cfg.structure_id. `sellers` is a list of
    (character_id, auth_role) pairs.

    Pooled across every registered seller character (parent repo's GitHub
    issue #46: several seller characters share the same structure's order
    slots). A cheaper order belonging to one of *your own* other sellers
    must not count as being undercut - my_order_ids below is the union over
    every seller passed in, so all of them are excluded from the competitor
    comparison, not just whichever one is being checked.

    ESI's structure order book (structure_orders_raw) carries no owning-
    character field per order (a real ESI limitation, confirmed against the
    endpoint's response schema - order_id/price/type_id/volume_remain etc,
    no character_id), so "which of these are mine" can only be decided by
    cross-referencing order_id against character_orders (which does return
    your own order_id per order) - never by type_id/price matching, which
    would false-positive on a coincidentally identical price from another
    seller.

    Returns one dict per undercut order: {"type_id", "my_price",
    "competitor_price", "difference"}, only for orders actually beaten
    (competitor_price < my_price) - same "only flag real problems" shape as
    fetch_seller_stock_without_order. Item names aren't resolved here; the
    caller has the SDE-backed lookup."""
    my_orders = []
    for character_id, auth_role in sellers:
        my_orders.extend(
            o for o in client.character_orders(character_id, auth_role=auth_role)
            if not o.get("is_buy_order") and o.get("location_id") == cfg.structure_id
        )
    if not my_orders:
        return []
    my_order_ids = {o["order_id"] for o in my_orders}
    my_best_price: dict[int, float] = {}
    for o in my_orders:
        type_id = o["type_id"]
        if type_id not in my_best_price or o["price"] < my_best_price[type_id]:
            my_best_price[type_id] = o["price"]

    # The structure's order book is one shared/global fetch - any one of the
    # registered sellers with docking access can retrieve it, so the first
    # one passed in is enough.
    all_orders = client.structure_orders_raw(cfg.structure_id, auth_role=sellers[0][1])
    competitor_best: dict[int, float] = {}
    for o in all_orders:
        if o.get("is_buy_order") or o.get("order_id") in my_order_ids:
            continue
        type_id = o.get("type_id")
        if type_id not in my_best_price:
            continue  # not one of my listed items - irrelevant to this check
        price = o.get("price")
        if type_id not in competitor_best or price < competitor_best[type_id]:
            competitor_best[type_id] = price

    results = []
    for type_id, my_price in my_best_price.items():
        competitor_price = competitor_best.get(type_id)
        if competitor_price is not None and competitor_price < my_price:
            results.append({
                "type_id": type_id, "my_price": my_price, "competitor_price": competitor_price,
                "difference": my_price - competitor_price,
            })
    results.sort(key=lambda r: r["difference"], reverse=True)
    return results


def check_undercut(character_id: int, auth_role: str, client: ESIClient,
                   cfg: TradingConfig = TRADING_CONFIG) -> list[dict]:
    """Single-seller convenience wrapper around check_undercut_pooled - see
    that function's docstring for the real logic."""
    return check_undercut_pooled([(character_id, auth_role)], client, cfg)


def fetch_seller_stock_without_order_pooled(sellers: list[tuple[int, str]], client: ESIClient,
                                            shortlist_item_ids: set[int],
                                            cfg: TradingConfig = TRADING_CONFIG) -> list[dict]:
    """Pooled version of fetch_seller_stock_without_order (see that function
    for the scope rules and limitations) - `sellers` is a list of
    (character_id, auth_role) pairs. "No sell order at all" means none of the
    registered sellers' orders cover it, not just one particular character's,
    and asset_quantity is the combined total across every seller's hangar,
    since the structure's hangar is shared regardless of which character
    happens to hold the stock."""
    asset_qty: dict[int, float] = defaultdict(float)
    sell_remaining: dict[int, float] = defaultdict(float)
    for character_id, auth_role in sellers:
        assets = client.character_assets(character_id, auth_role=auth_role)
        for a in assets:
            type_id = a.get("type_id")
            if type_id not in shortlist_item_ids:
                continue
            if a.get("location_id") != cfg.structure_id:
                continue
            if a.get("location_flag") in NON_STOCK_LOCATION_FLAGS:
                continue
            asset_qty[type_id] += a.get("quantity", 0)
        for type_id, remaining in fetch_own_sell_orders(character_id, auth_role, client, cfg).items():
            sell_remaining[type_id] += remaining

    rows = []
    for type_id, qty in asset_qty.items():
        if sell_remaining.get(type_id, 0.0) <= 0:
            rows.append({
                "type_id": type_id, "asset_quantity": qty,
                "sell_order_remaining": 0.0, "unlisted_quantity": qty,
            })
    return rows


def fetch_seller_stock_without_order(character_id: int, auth_role: str, client: ESIClient,
                                     shortlist_item_ids: set[int],
                                     cfg: TradingConfig = TRADING_CONFIG) -> list[dict]:
    """Shortlist items the seller physically has sitting at the structure
    (personal assets, location_id == cfg.structure_id, excluding
    NON_STOCK_LOCATION_FLAGS - AssetSafety/Deliveries/etc. aren't actually
    available to list) with NO open sell order there at all right now -
    stock that could be listed for sale but apparently was forgotten.

    Two deliberate scope limits, both confirmed in the parent repo: only
    `shortlist_item_ids` are considered (not every asset at the structure,
    just what you're tracking as tradeable), and only a *complete* absence
    of a sell order counts (a partially listed item is not flagged).

    Returns one dict per flagged type_id: {"type_id", "asset_quantity",
    "sell_order_remaining" (always 0), "unlisted_quantity"}
    (unlisted_quantity == asset_quantity here, kept as its own field for a
    stable row shape).

    Requires esi-assets.read_assets.v1 on the seller's token in addition to
    esi-markets.read_character_orders.v1 - a seller authorized before that
    scope was added has to log in again.

    Known limitation: only sees items sitting directly in the structure's own
    location_id (e.g. "Hangar"). Items nested inside a container or a parked
    ship there carry that container's/ship's own item_id as their
    location_id, the same one-level-flat limitation fetch_own_sell_orders
    has - these are a personal character's assets, so there is no corp office
    to unwrap either.

    Single-seller convenience wrapper around
    fetch_seller_stock_without_order_pooled."""
    return fetch_seller_stock_without_order_pooled([(character_id, auth_role)], client,
                                                   shortlist_item_ids, cfg)


def fetch_buyer_already_covered(character_id: int, auth_role: str, client: ESIClient,
                                cfg: TradingConfig = TRADING_CONFIG) -> set[int]:
    """The set of type_ids the buyer either already has an open BUY order for
    (in the Jita region or at cfg.structure_id) or already holds in inventory
    (at a Jita station or at cfg.structure_id) - all of which mean "don't
    import more of this right now", extending the seller-sell-order-based
    "Already ordered" check.

    Requires esi-assets.read_assets.v1 on the buyer's token in addition to
    esi-markets.read_character_orders.v1."""
    covered: set[int] = set()

    orders = client.character_orders(character_id, auth_role=auth_role)
    for o in orders:
        if not o.get("is_buy_order"):
            continue
        if o.get("region_id") == cfg.jita_region_id or o.get("location_id") == cfg.structure_id:
            covered.add(o["type_id"])

    jita_stations = storage.get_station_ids_in_system(JITA_SOLAR_SYSTEM_ID)
    assets = client.character_assets(character_id, auth_role=auth_role)
    for a in assets:
        if a.get("location_id") in jita_stations or a.get("location_id") == cfg.structure_id:
            covered.add(a["type_id"])

    return covered
