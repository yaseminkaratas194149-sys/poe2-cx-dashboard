"""Derivation queries over the cx store (read-only).

Findings baked in (verified on live data; the field semantics exact on all
1074 pair-rows of 17 hours, 2026-09-06 -- see DECISIONS.md):
  - poe2scout's per-side fields: `value` = volume x P(X), where P(X) is the
    currency's hour-global price in base (the same in every pair of X;
    P(base) = 1). `rate` (RelativePrice) of X in pair X-Y = value(Y) /
    volume(X) = P(Y) x volume(Y) / volume(X): the base value one unit of X
    fetched in that pair, the counter valued at its global price. So `rate`
    is NOISY across pairs (thin pairs trade off the global price) and is ~1.0
    in every pair of the base currency by construction; a `value` floor is a
    plain volume floor, not inflated by that noise.
  - The realized exchange ratio of a pair is counter_volume / volume (both
    sides' VolumeTraded of the same hour's trades). rate / counter_rate is
    NOT it: it equals the realized ratio x (rate / P(X)), i.e. it carries the
    pair's deviation from the global price (up to 5x on thin pairs).
  - Cross-pair % "arbitrage" from this source is dominated by per-pair
    valuation noise, not real opportunity (real currency-exchange arbitrage
    is typically small). So we do NOT rank by a raw cross-pair premium.
  - Trustworthy outputs: per-currency CONSENSUS value (volume-weighted over
    both-sides-liquid pairs = sum(counter value) / sum(volume), the shape of
    poe2scout's own global price), LIQUIDITY (base value traded), and the
    per-pair LISTING for manual judgment. Precise within/cross-pair arbitrage
    needs the cxapi exact ratios (forward-capacity) or cross-hour persistence
    analysis.

CLI:
  python -m cx.derive            -> liquidity board (top currencies + consensus)
  python -m cx.derive divine     -> one currency's pairs (price / avg 6h / 7d / traded)
"""
import sys
import time

import psycopg2

from cx import config

# Base-value floor (exalted-equivalents), required on BOTH sides of a pair.
MIN_VALUE = 500


def resolve_schema(cur) -> str:
    r"""Pick the league cx_* schema with the most recent data (network-free).

    Only schemas that actually carry a `pair_snapshot` table count as league
    schemas — this skips reference-only schemas like cx_ref (which would
    otherwise raise 'relation cx_ref.pair_snapshot does not exist')."""
    cur.execute(
        "select table_schema from information_schema.tables "
        "where table_schema like 'cx\\_%' escape '\\' and table_name = 'pair_snapshot' "
        "order by table_schema"
    )
    schemas = [row[0] for row in cur.fetchall()]
    if not schemas:
        raise RuntimeError("no cx_* league schema found — run `python -m cx` first")
    if len(schemas) == 1:
        return schemas[0]
    best, best_e = schemas[0], -1
    for s in schemas:
        cur.execute(f"select coalesce(max(hour_epoch), -1) from {s}.pair_snapshot")
        e = cur.fetchone()[0]
        if e > best_e:
            best, best_e = s, e
    return best


def has_table(cur, schema: str, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"{schema}.{table}",))
    return cur.fetchone()[0] is not None


def ninja_epoch(cur, schema: str):
    """Hour of the latest poe.ninja fetch in the schema, or None (no table / empty)."""
    if not has_table(cur, schema, "ninja_price"):
        return None
    cur.execute(f"SELECT max(hour_epoch) FROM {schema}.ninja_price")
    return cur.fetchone()[0]


def currency_view(cur, schema: str, api_id: str):
    """Traded legs of one currency X at the latest hour, most traded first.

    Rows: (counter_api_id, price, avg6h, change_7d, traded,
           volume, counter_volume, rate, counter_value).
      price      -- X per 1 counter this hour: volume / counter_volume (realized).
      avg6h      -- the same in poe.ninja's smoothed terms: ninja(counter) /
                    ninja(X), each a 6h VWAP in base; None without ninja data.
      change_7d  -- poe.ninja 7-day change of the counter, percent; None if unknown.
      traded     -- base value of X traded in the pair this hour (= volume x P(X)).
    The tail (volume, counter_volume, rate, counter_value) is the raw leg.
    Without a ninja_price table (schema not yet re-provisioned) the two ninja
    columns are NULL and the view still works.
    """
    with_ninja = has_table(cur, schema, "ninja_price")
    ninja_cte = (f", np AS (SELECT DISTINCT ON (api_id) api_id, value, change_7d "
                 f"FROM {schema}.ninja_price ORDER BY api_id, hour_epoch DESC)"
                 if with_ninja else "")
    ninja_cols = ("nc.value / NULLIF(nx.value, 0) AS avg6h, nc.change_7d"
                  if with_ninja else "NULL::numeric AS avg6h, NULL::numeric AS change_7d")
    ninja_joins = ("LEFT JOIN np nc ON nc.api_id = co.api_id "
                   "LEFT JOIN np nx ON nx.api_id = cc.api_id"
                   if with_ninja else "")
    cur.execute(
        f"""
        WITH h AS (SELECT max(hour_epoch) e FROM {schema}.pair_snapshot){ninja_cte}
        SELECT co.api_id,
               l.volume::numeric / NULLIF(l.counter_volume, 0) AS price,
               {ninja_cols},
               l.value AS traded,
               l.volume, l.counter_volume, l.rate, l.counter_value
        FROM {schema}.pair_leg l
        JOIN h ON l.hour_epoch = h.e
        JOIN {schema}.currency cc ON cc.currency_item_id = l.currency
        JOIN {schema}.currency co ON co.currency_item_id = l.counter
        {ninja_joins}
        WHERE cc.api_id = %s AND l.volume > 0
        ORDER BY l.value DESC
        """,
        (api_id,),
    )
    return cur.fetchall()


def league_short(cur, schema: str):
    """The league's poe2scout short name stored in the schema, or None."""
    cur.execute(f"SELECT short_name FROM {schema}.league LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else None


def pair_series(cur, schema: str, api_x: str, api_y: str):
    """Hourly series of one pair, oldest first:
    (hour_epoch, price, volume_x, volume_y, value_x), price = volume_x /
    volume_y (X per 1 Y, the currency view's `price`). Hours where either side
    did not trade are left out -- a gap, not a zero."""
    cur.execute(
        f"""
        SELECT l.hour_epoch, l.volume::numeric / l.counter_volume,
               l.volume, l.counter_volume, l.value
        FROM {schema}.pair_leg l
        JOIN {schema}.currency cc ON cc.currency_item_id = l.currency
        JOIN {schema}.currency co ON co.currency_item_id = l.counter
        WHERE cc.api_id = %s AND co.api_id = %s AND l.volume > 0 AND l.counter_volume > 0
        ORDER BY l.hour_epoch
        """,
        (api_x, api_y),
    )
    return cur.fetchall()


def fmt_price(x) -> str:
    """Adaptive decimals, poe.ninja style: 8,833 / 282 / 54.2 / 5.50 / 0.376 / 0.0185;
    '' for None."""
    if x is None:
        return ""
    x = float(x)
    if x >= 1000:
        return f"{x:,.0f}"
    if x >= 100:
        return f"{x:.0f}"
    if x >= 10:
        return f"{x:.1f}"
    if x >= 1:
        return f"{x:.2f}"
    if x >= 0.1:
        return f"{x:.3f}"
    return f"{x:.4f}"


def fmt_pct(x) -> str:
    """Signed whole percent ('+36%'); '' for None."""
    return "" if x is None else f"{float(x):+.0f}%"


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
            ne = ninja_epoch(cur, schema)
            print(f"[{schema}] {arg}: pairs by traded value; price = {arg} per 1 counter "
                  f"(this hour) | avg 6h / 7d from poe.ninja @ "
                  f"{time.strftime('%m-%d %H:%M', time.gmtime(ne)) + ' UTC' if ne else 'no data'}")
            print(f"{'counter':<30}{'price':>10}{'avg 6h':>10}{'7d':>7}{'traded':>10}"
                  f"{'vol':>8}{'c_vol':>8}")
            for counter, price, avg6h, ch7, traded, vol, cvol, rate, cval in rows:
                print(f"{counter:<30}{fmt_price(price):>10}{fmt_price(avg6h):>10}{fmt_pct(ch7):>7}"
                      f"{_f(traded):>10,.0f}{vol or 0:>8}{cvol or 0:>8}")
            print(f"-- {len(rows)} pairs | {sum(_f(r[4]) for r in rows):,.0f} base traded this hour")
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
