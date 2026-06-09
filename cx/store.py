"""Postgres upsert helpers for the cx schema (idempotent by stable key).

Prices arrive as strings -> Decimal (preserve precision). Currency reference is
derived from the currency objects embedded in each SnapshotPairs row. Pair rows
are canonicalized to cur1 < cur2 (side data swapped to match) so a logical pair
maps to one row per hour regardless of the feed's emission order.
"""
from decimal import Decimal

from psycopg2.extras import Json, execute_values


def _num(x):
    return Decimal(str(x)) if x is not None else None


def _int(x):
    return int(x) if x is not None else None


def upsert_currencies(cur, schema: str, pairs: list) -> int:
    """UPSERT cx_currency from the currencies embedded in SnapshotPairs rows."""
    seen = {}
    for p in pairs:
        for side in ("CurrencyOne", "CurrencyTwo"):
            c = p[side]
            cid = c["CurrencyItemId"]
            if cid not in seen:
                seen[cid] = (cid, c["ItemId"], c["ApiId"], c["Text"],
                             c.get("CategoryApiId"), c.get("IconUrl"))
    rows = list(seen.values())
    execute_values(
        cur,
        f"INSERT INTO {schema}.currency "
        f"(currency_item_id, item_id, api_id, name, category_api_id, icon_url) VALUES %s "
        f"ON CONFLICT (currency_item_id) DO UPDATE SET "
        f"item_id = EXCLUDED.item_id, api_id = EXCLUDED.api_id, name = EXCLUDED.name, "
        f"category_api_id = EXCLUDED.category_api_id, icon_url = EXCLUDED.icon_url",
        rows,
    )
    return len(rows)


def upsert_market(cur, schema: str, epoch: int, snap: dict):
    cur.execute(
        f"INSERT INTO {schema}.market_snapshot (hour_epoch, volume, market_cap) "
        f"VALUES (%s, %s, %s) "
        f"ON CONFLICT (hour_epoch) DO UPDATE SET "
        f"volume = EXCLUDED.volume, market_cap = EXCLUDED.market_cap",
        (epoch, _num(snap.get("Volume")), _num(snap.get("MarketCap"))),
    )


def upsert_pairs(cur, schema: str, pairs: list, epoch: int) -> int:
    """UPSERT cx_pair_snapshot for one hour. Canonicalized + deduped by (cur1, cur2)."""
    by_key = {}
    for p in pairs:
        d1, d2 = p["CurrencyOneData"], p["CurrencyTwoData"]
        s1 = (p["CurrencyOne"]["CurrencyItemId"], _num(d1["RelativePrice"]),
              _int(d1["VolumeTraded"]), _num(d1["ValueTraded"]),
              _num(d1.get("StockValue")), _int(d1.get("HighestStock")))
        s2 = (p["CurrencyTwo"]["CurrencyItemId"], _num(d2["RelativePrice"]),
              _int(d2["VolumeTraded"]), _num(d2["ValueTraded"]),
              _num(d2.get("StockValue")), _int(d2.get("HighestStock")))
        if s1[0] > s2[0]:
            s1, s2 = s2, s1
        by_key[(s1[0], s2[0])] = (
            s1[0], s2[0], epoch, p.get("CurrencyExchangeSnapshotId"),
            s1[1], s1[2], s1[3], s1[4], s1[5],
            s2[1], s2[2], s2[3], s2[4], s2[5],
        )
    rows = list(by_key.values())
    execute_values(
        cur,
        f"INSERT INTO {schema}.pair_snapshot "
        f"(cur1, cur2, hour_epoch, snapshot_id, "
        f" rate1, volume1, value1, stock1, high_stock1, "
        f" rate2, volume2, value2, stock2, high_stock2) VALUES %s "
        f"ON CONFLICT (cur1, cur2, hour_epoch) DO UPDATE SET "
        f"snapshot_id = EXCLUDED.snapshot_id, "
        f"rate1 = EXCLUDED.rate1, volume1 = EXCLUDED.volume1, value1 = EXCLUDED.value1, "
        f"stock1 = EXCLUDED.stock1, high_stock1 = EXCLUDED.high_stock1, "
        f"rate2 = EXCLUDED.rate2, volume2 = EXCLUDED.volume2, value2 = EXCLUDED.value2, "
        f"stock2 = EXCLUDED.stock2, high_stock2 = EXCLUDED.high_stock2",
        rows,
    )
    return len(rows)


def upsert_uniques(cur, schema: str, items: list) -> int:
    """UPSERT cx_<league>.unique_item from poe2scout Uniques/ByCategory rows.

    Mod arrays are stored as text[]; empty -> NULL (avoids empty-array typing).
    requirements / full ItemMetadata kept as jsonb (forward capacity)."""
    rows = []
    for it in items:
        m = it.get("ItemMetadata") or {}
        rows.append((
            it["UniqueItemId"], _int(it.get("ItemId")), it.get("Name"),
            m.get("base_type") or it.get("Type"), it.get("CategoryApiId"),
            it.get("IconUrl"), _int(m.get("item_level")),
            m.get("implicit_mods") or None, m.get("explicit_mods") or None,
            m.get("flavor_text"),
            Json(m["requirements"]) if m.get("requirements") is not None else None,
            _num(it.get("CurrentPrice")),
            Json(m) if m else None,
        ))
    if not rows:
        return 0
    execute_values(
        cur,
        f"INSERT INTO {schema}.unique_item "
        f"(unique_item_id, item_id, name, base_type, category_api_id, icon_url, "
        f" item_level, implicit_mods, explicit_mods, flavour_text, requirements, "
        f" current_price, metadata) VALUES %s "
        f"ON CONFLICT (unique_item_id) DO UPDATE SET "
        f"item_id = EXCLUDED.item_id, name = EXCLUDED.name, base_type = EXCLUDED.base_type, "
        f"category_api_id = EXCLUDED.category_api_id, icon_url = EXCLUDED.icon_url, "
        f"item_level = EXCLUDED.item_level, implicit_mods = EXCLUDED.implicit_mods, "
        f"explicit_mods = EXCLUDED.explicit_mods, flavour_text = EXCLUDED.flavour_text, "
        f"requirements = EXCLUDED.requirements, current_price = EXCLUDED.current_price, "
        f"metadata = EXCLUDED.metadata",
        rows,
    )
    return len(rows)
