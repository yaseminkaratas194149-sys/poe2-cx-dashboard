"""cx11 backfill — pull recent per-pair history into pair_snapshot.

The live `cx10_pairs` stage writes only the newest hour. This backfills the last
N hours of each currently-active pair's hourly history (poe2scout, keyed by
`item_id`) so the board / consensus reflect a day rather than one noisy hour.

It first runs provision + the live pairs pull (so the schema exists and the
currency reference + icons are loaded — history rows carry only CurrencyItemIds,
not the full currency objects), then fetches each active pair's history in a
small thread pool and UPSERTs the in-window hours via the same `store.upsert_pairs`
path. Idempotent (UPSERT on (cur1,cur2,hour_epoch)); no-trade (all-zero) hours
are skipped, so the store stays sparse.

    python -m cx.backfill                  # last 24h, all active pairs
    python -m cx.backfill --hours 48
    python -m cx.backfill --max-pairs 10   # quick test on the busiest pairs
"""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2

from cx import config, source, store
from cx.stages.provision import EnsureSchemaStage
from cx.stages.api_stages import PairsStage


def _active_pairs(short):
    """Latest SnapshotPairs -> (pairs, itemid_to_cid).

    pairs = [(item_id_1, item_id_2, value)] busiest first. itemid_to_cid maps the
    per-pair history key (ItemId) to the currency table PK (currency_item_id).
    The history endpoint mislabels its per-side id as 'CurrencyItemId', but it is
    really the ItemId (chaos 287, not currency_item_id 10) — see DECISIONS — so
    backfill must translate it back before writing FK-checked pair rows."""
    pairs, itemid_to_cid = [], {}
    for p in source.snapshot_pairs(short):
        for side in ("CurrencyOne", "CurrencyTwo"):
            c = p[side]
            itemid_to_cid[c["ItemId"]] = c["CurrencyItemId"]
        pairs.append((p["CurrencyOne"]["ItemId"], p["CurrencyTwo"]["ItemId"],
                      float(p["CurrencyOneData"].get("ValueTraded") or 0)))
    pairs.sort(key=lambda t: t[2], reverse=True)
    return pairs, itemid_to_cid


def _history_rows(short, i1, i2, cutoff, limit, itemid_to_cid):
    """pair_history -> [(epoch, pseudo_pair)] for in-window, traded hours.

    A history row is {Epoch, Data:{CurrencyOneData, CurrencyTwoData}} — the same
    per-side shape SnapshotPairs carries, minus the currency objects. We rebuild
    the minimal pair dict `store.upsert_pairs` consumes (currency_item_id per side
    + the data blocks), translating the mislabeled ItemId back to the real
    currency_item_id; snapshot_id is null on backfill."""
    h = source.pair_history(short, i1, i2, limit=limit)
    rows = h.get("History", []) if isinstance(h, dict) else (h or [])
    out = []
    for r in rows:
        ep = r.get("Epoch")
        if ep is None or int(ep) < cutoff:
            continue
        data = r.get("Data") or {}
        d1, d2 = data.get("CurrencyOneData"), data.get("CurrencyTwoData")
        if not d1 or not d2:
            continue
        if int(d1.get("VolumeTraded") or 0) == 0 and int(d2.get("VolumeTraded") or 0) == 0:
            continue                                   # no-trade hour -> skip
        cid1 = itemid_to_cid.get(d1.get("CurrencyItemId"))   # really an ItemId
        cid2 = itemid_to_cid.get(d2.get("CurrencyItemId"))
        if cid1 is None or cid2 is None:
            continue                                   # currency outside active set
        out.append((int(ep), {
            "CurrencyOne": {"CurrencyItemId": cid1},
            "CurrencyTwo": {"CurrencyItemId": cid2},
            "CurrencyOneData": d1, "CurrencyTwoData": d2,
            "CurrencyExchangeSnapshotId": None,
        }))
    return out


def backfill(hours=24, max_pairs=0, workers=6):
    # provision + live pull: schema, league row, currency reference, newest hour
    prov = EnsureSchemaStage().run({})
    schema, short = prov["schema"], prov["short_name"]
    PairsStage().run({"cx00_provision": prov})
    print(f"[backfill] {prov['league']} -> {schema}")

    pairs, itemid_to_cid = _active_pairs(short)
    if max_pairs:
        pairs = pairs[:max_pairs]
    cutoff = int(time.time()) - hours * 3600
    limit = max(hours + 24, 48)
    print(f"[backfill] {len(pairs)} active pairs, last {hours}h "
          f"(cutoff {time.strftime('%m-%d %H:%M', time.localtime(cutoff))})")

    by_epoch = {}                # epoch -> [pseudo_pair, ...]
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_history_rows, short, i1, i2, cutoff, limit, itemid_to_cid): (i1, i2)
                for i1, i2, _v in pairs}
        for fut in as_completed(futs):
            try:
                for ep, pseudo in fut.result():
                    by_epoch.setdefault(ep, []).append(pseudo)
                ok += 1
            except Exception:
                fail += 1
            done = ok + fail
            if done % 100 == 0 or done == len(pairs):
                print(f"  fetched {done}/{len(pairs)} (fail {fail})")

    conn = psycopg2.connect(**config.DB_CONFIG)
    written = 0
    try:
        cur = conn.cursor()
        for ep in sorted(by_epoch):
            written += store.upsert_pairs(cur, schema, by_epoch[ep], ep)
        conn.commit()
        cur.close()
    finally:
        conn.close()

    hrs = sorted(by_epoch)
    span = ((hrs[-1] - hrs[0]) / 3600) if hrs else 0
    print(f"[backfill] pairs ok={ok} fail={fail}; "
          f"hours={len(by_epoch)} (span {span:.0f}h); rows upserted={written}")
    return {"schema": schema, "hours": len(by_epoch), "rows": written,
            "ok": ok, "fail": fail}


def main():
    ap = argparse.ArgumentParser(description="Backfill recent per-pair history.")
    ap.add_argument("--hours", type=int, default=24, help="window to backfill")
    ap.add_argument("--max-pairs", type=int, default=0, help="cap pairs (0=all; for testing)")
    ap.add_argument("--workers", type=int, default=6, help="concurrent history fetches")
    a = ap.parse_args()
    backfill(a.hours, a.max_pairs, a.workers)


if __name__ == "__main__":
    main()
