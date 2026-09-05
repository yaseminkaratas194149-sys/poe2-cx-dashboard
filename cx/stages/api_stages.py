"""poe2scout pull stages (mirrors DATA/pipeline/stages/api_stages.py).

`PairsStage` is the hourly cycle; the other three join it in the Actualize cycle
(`build_stages(full=True)`): backfill the recent history, merge the unique-item
reference, refresh the trade2 stat dictionary. Their cores live in `cx.backfill`,
`cx.uniques`, `cx.trade` and are imported lazily -- those modules import the
stage package for their CLIs.
"""
import psycopg2

from DATA.pipeline.stage import Stage
from cx import config, source, store


class PairsStage(Stage):
    """Pull the latest SnapshotPairs (+ ExchangeSnapshot for the hour stamp),
    UPSERT the currency reference (from embeds) and the pair facts."""

    name = "cx10_pairs"
    label = "Pairs"
    deps = ["cx00_provision"]

    def run(self, ctx):
        prov = ctx["cx00_provision"]
        schema, short = prov["schema"], prov["short_name"]

        snap = source.exchange_snapshot(short)
        epoch = int(snap["Epoch"])
        pairs = source.snapshot_pairs(short)
        self.log(f"epoch={epoch} pairs={len(pairs)}")

        conn = psycopg2.connect(**config.DB_CONFIG)
        try:
            cur = conn.cursor()
            n_cur = store.upsert_currencies(cur, schema, pairs)
            store.upsert_market(cur, schema, epoch, snap)
            n_pairs = store.upsert_pairs(cur, schema, pairs, epoch)
            conn.commit()
            cur.close()
        finally:
            conn.close()

        self.log(f"currencies={n_cur} pairs={n_pairs} @ {epoch}")
        return {"count": n_pairs, "last_slot": epoch * 1000}


class BackfillStage(Stage):
    """Backfill the last `config.BACKFILL_HOURS` of per-pair history (cx.backfill)
    after the live pull, so a fresh schema shows a day of consensus, not one hour."""

    name = "cx11_backfill"
    label = "Backfill"
    deps = ["cx00_provision", "cx10_pairs"]

    def run(self, ctx):
        from cx.backfill import backfill_history
        prov = ctx["cx00_provision"]
        res = backfill_history(prov["schema"], prov["short_name"],
                               hours=config.BACKFILL_HOURS, log=self.log,
                               progress=lambda done, total: self.progress(done, total))
        return {"count": res["rows"], "hours": res["hours"], "fail": res["fail"]}


class UniquesStage(Stage):
    """Merge the unique-item reference into the tracked schema (cx.uniques): the most
    complete populated poe2scout list, then the tracked league's softcore twin on top."""

    name = "cx20_uniques"
    label = "Uniques"
    deps = ["cx00_provision"]

    def run(self, ctx):
        from cx.uniques import refresh
        prov = ctx["cx00_provision"]
        sources = source.uniques_sources(prov["short_name"], log=self.log)
        self.log(f"sources: {' + '.join(sources)}")
        conn = psycopg2.connect(**config.DB_CONFIG)
        try:
            cur = conn.cursor()
            res = refresh(cur, prov["schema"], sources, log=self.log)
            conn.commit()
            cur.close()
        finally:
            conn.close()
        return {"count": res["count"], "sources": sources}


class TradeDictStage(Stage):
    """Re-fetch the open trade2 data/stats dictionary into cx/_trade_dict_cache
    (cx.trade): patches add stat ids, and the Trade view's picker reads the cache."""

    name = "cx30_trade_dict"
    label = "Trade dict"
    deps = []

    def run(self, ctx):
        from cx import trade
        before, flat = trade.refresh_stats()
        self.log(f"stats {before} -> {len(flat)}")
        return {"count": len(flat)}
