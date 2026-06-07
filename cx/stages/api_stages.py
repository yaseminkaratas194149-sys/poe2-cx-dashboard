"""poe2scout pull stages (mirrors DATA/pipeline/stages/api_stages.py)."""
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
