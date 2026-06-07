"""Derivation queries over the cx store (read-only).

Findings baked in (verified on live data):
  - A currency's per-pair base value (RelativePrice) is NOISY across pairs.
    `value` = volume x rate, so an inflated rate also inflates `value` — a
    value floor can't exclude rate outliers. Cross-pair % "arbitrage" from this
    source is dominated by per-pair valuation noise, not real opportunity
    (real currency-exchange arbitrage is typically small). So we do NOT rank by
    a raw cross-pair premium.
  - Trustworthy outputs: per-currency CONSENSUS value (volume-weighted over
    both-sides-liquid pairs), LIQUIDITY (base value traded), and the per-pair
    LISTING for manual judgment. Precise within/cross-pair arbitrage needs the
    cxapi exact ratios (forward-capacity) or cross-hour persistence analysis.

CLI:
  python -m cx.derive            -> liquidity board (top currencies + consensus)
  python -m cx.derive divine     -> one currency's pairs (rate / per-X / vol / value)
"""
import sys

import psycopg2

from cx import config

# Base-value floor (exalted-equivalents), required on BOTH sides of a pair.
MIN_VALUE = 500


def resolve_schema(cur) -> str:
    r"""Pick the cx_* schema with the most recent data (network-free)."""
    cur.execute(
        "select schema_name from information_schema.schemata "
        "where schema_name like 'cx\\_%' escape '\\' order by 1"
    )
    schemas = [row[0] for row in cur.fetchall()]
    if not schemas:
        raise RuntimeError("no cx_* schema found — run `python -m cx` first")
    if len(schemas) == 1:
        return schemas[0]
    best, best_e = schemas[0], -1
    for s in schemas:
        cur.execute(f"select coalesce(max(hour_epoch), -1) from {s}.pair_snapshot")
        e = cur.fetchone()[0]
        if e > best_e:
            best, best_e = s, e
    return best


def currency_view(cur, schema: str, api_id: str):
    """Traded legs of one currency at the latest hour, ranked by base-value (sell-high first).

    Rows: (counter_api_id, rate, per_x, volume, counter_volume, value, counter_value).
    """
    cur.execute(
        f"""
        WITH h AS (SELECT max(hour_epoch) e FROM {schema}.pair_snapshot)
        SELECT co.api_id, l.rate,
               l.rate / NULLIF(l.counter_rate, 0) AS per_x,
               l.volume, l.counter_volume, l.value, l.counter_value
        FROM {schema}.pair_leg l
        JOIN h ON l.hour_epoch = h.e
        JOIN {schema}.currency cc ON cc.currency_item_id = l.currency
        JOIN {schema}.currency co ON co.currency_item_id = l.counter
        WHERE cc.api_id = %s AND l.volume > 0
        ORDER BY l.rate DESC
        """,
        (api_id,),
    )
    return cur.fetchall()


def liquidity_board(cur, schema: str, top: int = 20, min_value: int = MIN_VALUE):
    """Top currencies by base-value traded, with volume-weighted consensus value.

    Rows: (api_id, liquid_pairs, consensus_vwap, total_value).
    """
    cur.execute(
        f"""
        WITH h AS (SELECT max(hour_epoch) e FROM {schema}.pair_snapshot),
        legs AS (
            SELECT l.currency, l.rate, l.volume, l.value
            FROM {schema}.pair_leg l JOIN h ON l.hour_epoch = h.e
            WHERE l.value >= %s AND l.counter_value >= %s
        )
        SELECT cc.api_id, count(*) AS n,
               sum(rate * volume) / NULLIF(sum(volume), 0) AS vwap,
               sum(value) AS tv
        FROM legs JOIN {schema}.currency cc ON cc.currency_item_id = legs.currency
        GROUP BY cc.api_id HAVING count(*) >= 3
        ORDER BY sum(value) DESC
        LIMIT %s
        """,
        (min_value, min_value, top),
    )
    return cur.fetchall()


def _f(x):
    return float(x) if x is not None else 0.0


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    conn = psycopg2.connect(**config.DB_CONFIG)
    try:
        cur = conn.cursor()
        schema = resolve_schema(cur)
        if arg:
            rows = currency_view(cur, schema, arg)
            if not rows:
                print(f"[{schema}] no traded pairs for '{arg}' (check the api_id)")
                return
            print(f"[{schema}] {arg}: pairs ranked by X base-value (sell-high first)")
            print(f"{'counter':<28}{'rate(base)':>12}{'per 1 X':>12}{'vol(X)':>10}{'val(base)':>12}")
            for counter, rate, per_x, vol, cvol, value, cval in rows:
                print(f"{counter:<28}{_f(rate):>12.4f}{_f(per_x):>12.4f}{vol or 0:>10}{_f(value):>12.0f}")
            liquid = [r for r in rows if _f(r[5]) >= MIN_VALUE and _f(r[6]) >= MIN_VALUE]
            if liquid:
                wv = sum((r[3] or 0) for r in liquid)
                vwap = sum(_f(r[1]) * (r[3] or 0) for r in liquid) / wv if wv else 0.0
                deep = max(liquid, key=lambda r: _f(r[5]))
                print(f"-- {len(liquid)} liquid pairs | consensus {vwap:.4f} base | "
                      f"deepest market: {deep[0]} @ {_f(deep[1]):.4f} (val {_f(deep[5]):.0f})")
        else:
            rows = liquidity_board(cur, schema)
            print(f"[{schema}] liquidity leaders (latest hour, both-sides val>={MIN_VALUE})")
            print(f"{'currency':<26}{'pairs':>6}{'consensus':>13}{'tot_val(base)':>16}")
            for api, n, vwap, tv in rows:
                print(f"{api:<26}{n:>6}{_f(vwap):>13.4f}{_f(tv):>16.0f}")
        cur.close()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
