"""cx11 backfill -- pull recent per-pair history into pair_snapshot.

The live `cx10_pairs` stage writes only the newest hour. This backfills the last
N hours of each currently-active pair's hourly history (poe2scout, keyed by
`item_id`) so the board / consensus reflect a day rather than one noisy hour.

`backfill_history` is the core: the `cx11_backfill` stage of the Actualize cycle
calls it after provision + the live pull, and the CLI wraps it with those two
steps -- so the schema exists and the currency reference + icons are loaded
(history rows carry only CurrencyItemIds, not the full currency objects). Each
active pair's history is fetched in a small thread pool and the in-window hours
UPSERTed via the same `store.upsert_pairs` path. Idempotent (UPSERT on
(cur1,cur2,hour_epoch)); no-trade (all-zero) hours are skipped, so the store
stays sparse.

    python -m cx.backfill                  # last 24h, all active pairs
    python -m cx.backfill --hours 48
    python -m cx.backfill --max-pairs 10   # quick test on the busiest pairs
    python -m cx.backfill --pair exalted divine   # ONE pair, its whole history

`pair_full_history` is the pair chart's pull: one pair, league start to now,
paged with EndEpoch, ids taken from the store's currency table.
"""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2

from cx import config, source, store


def _active_pairs(short):
    """Latest SnapshotPairs -> (pairs, itemid_to_cid).

    pairs = [(item_id_1, item_id_2, value)] busiest first. itemid_to_cid maps the
    per-pair history key (ItemId) to the currency table PK (currency_item_id).
    The history endpoint mislabels its per-side id as 'CurrencyItemId', but it is
    really the ItemId (chaos 287, not currency_item_id 10) -- see DECISIONS -- so
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
    """pair_history -> [(epoch, pseudo_pair)] for in-window, traded hours."""
    h = source.pair_history(short, i1, i2, limit=limit)
    rows = h.get("History", []) if isinstance(h, dict) else (h or [])
    return _pseudo_rows(rows, cutoff, itemid_to_cid)


def _pseudo_rows(rows, cutoff, itemid_to_cid):
    """History rows -> [(epoch, pseudo_pair)] for traded hours at/after `cutoff`.

    A history row is {Epoch, Data:{CurrencyOneData, CurrencyTwoData}} -- the same
    per-side shape SnapshotPairs carries, minus the currency objects. We rebuild
    the minimal pair dict `store.upsert_pairs` consumes (currency_item_id per side
    + the data blocks), translating the mislabeled ItemId back to the real
    currency_item_id; snapshot_id is null on backfill."""
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


def backfill_history(schema, short, hours=24, max_pairs=0, workers=6, log=print, progress=None):
    """Fetch + UPSERT the last `hours` of every active pair's history into `schema`.

    `progress(done, total)` is called every few pairs (the stage forwards it to
    the status ring). -> {schema, hours, rows, ok, fail}."""
    pairs, itemid_to_cid = _active_pairs(short)
    if max_pairs:
        pairs = pairs[:max_pairs]
    cutoff = int(time.time()) - hours * 3600
    limit = max(hours + 24, 48)
    log(f"{len(pairs)} active pairs, last {hours}h "
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
                log(f"fetched {done}/{len(pairs)} (fail {fail})")
            if progress and (done % 10 == 0 or done == len(pairs)):
                progress(done, len(pairs))

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
    log(f"pairs ok={ok} fail={fail}; hours={len(by_epoch)} (span {span:.0f}h); "
        f"rows upserted={written}")
    return {"schema": schema, "hours": len(by_epoch), "rows": written,
            "ok": ok, "fail": fail}


def pair_full_history(schema, short, api_x, api_y, log=None, page=5000, max_calls=20):
    """Pull ONE pair's whole hourly history (league start .. now) into pair_snapshot.

    The pair chart's pull. Ids come from the store's currency table (item_id for
    the endpoint, currency_item_id for the rows), so no live snapshot is needed;
    pages back with EndEpoch while the feed says HasMore (a three-month league
    fits one `page`). Idempotent UPSERT; untraded hours are skipped, so the
    series keeps its gaps. -> {rows, hours, calls}."""
    conn = psycopg2.connect(**config.DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute(f"select api_id, item_id, currency_item_id from {schema}.currency "
                    f"where api_id in (%s, %s)", (api_x, api_y))
        ids = {a: (it, cid) for a, it, cid in cur.fetchall()}
        if api_x not in ids or api_y not in ids:
            raise RuntimeError(f"unknown currency in {schema}: {api_x} / {api_y}")
        itemid_to_cid = {it: cid for it, cid in ids.values()}
        by_epoch, calls, end = {}, 0, None
        while True:
            h = source.pair_history(short, ids[api_x][0], ids[api_y][0], limit=page, end_epoch=end)
            rows = h.get("History", []) if isinstance(h, dict) else (h or [])
            calls += 1
            for ep, pseudo in _pseudo_rows(rows, 0, itemid_to_cid):
                by_epoch.setdefault(ep, []).append(pseudo)
            epochs = [int(r["Epoch"]) for r in rows if r.get("Epoch") is not None]
            more = isinstance(h, dict) and bool((h.get("Meta") or {}).get("HasMore"))
            if not more or not epochs or calls >= max_calls:
                break
            end = min(epochs) - 1
        written = 0
        for ep in sorted(by_epoch):
            written += store.upsert_pairs(cur, schema, by_epoch[ep], ep)
        conn.commit()
        cur.close()
    finally:
        conn.close()
    if log:
        log(f"{api_x}/{api_y}: {len(by_epoch)} traded hours, {written} rows, {calls} call(s)")
    return {"rows": written, "hours": len(by_epoch), "calls": calls}


def backfill(hours=24, max_pairs=0, workers=6):
    """CLI path: provision + live pull, then the history window."""
    from cx.stages.provision import EnsureSchemaStage   # lazy: cx.stages imports this module
    from cx.stages.api_stages import PairsStage
    prov = EnsureSchemaStage().run({})
    PairsStage().run({"cx00_provision": prov})
    print(f"[backfill] {prov['league']} -> {prov['schema']}")
    return backfill_history(prov["schema"], prov["short_name"], hours, max_pairs, workers,
                            log=lambda m: print(f"[backfill] {m}"))


def main():
    ap = argparse.ArgumentParser(description="Backfill recent per-pair history.")
    ap.add_argument("--hours", type=int, default=24, help="window to backfill")
    ap.add_argument("--max-pairs", type=int, default=0, help="cap pairs (0=all; for testing)")
    ap.add_argument("--workers", type=int, default=6, help="concurrent history fetches")
    ap.add_argument("--pair", nargs=2, metavar=("X", "Y"),
                    help="one pair's whole history (two api_ids), the chart's pull")
    a = ap.parse_args()
    if a.pair:
        from cx.stages.provision import EnsureSchemaStage
        prov = EnsureSchemaStage().run({})
        print(f"[backfill] {prov['league']} -> {prov['schema']}")
        pair_full_history(prov["schema"], prov["short_name"], a.pair[0], a.pair[1],
                          log=lambda m: print(f"[backfill] {m}"))
        return
    backfill(a.hours, a.max_pairs, a.workers)


if __name__ == "__main__":
    main()
