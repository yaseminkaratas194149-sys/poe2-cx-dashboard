"""UPSERT + relink helpers for the cx_ref item container (idempotent by key).

Each source loader hands raw rows to upsert_item_src / upsert_item_mods (layer
1). relink() rebuilds the cross-source identity map (layer 2). Reconciliation is
pure SQL (layer-3 views). No source-specific logic lives here — see cx.ref.loaders.
"""
import re

from psycopg2.extras import Json, execute_values

_WS = re.compile(r"\s+")


def name_norm(name):
    """Normalized EN name used as the cross-source matching key (or None)."""
    if not name:
        return None
    return _WS.sub(" ", name.strip().lower()) or None


def upsert_item_src(cur, schema: str, rows: list) -> int:
    """UPSERT layer-1 item rows. Each row is a dict:
    {source, src_key, item_class?, name_en?, name_ru?, icon_url?, raw?}.
    name_norm is derived from name_en."""
    tuples = [
        (r["source"], r["src_key"], r.get("item_class"),
         r.get("name_en"), r.get("name_ru"), r.get("icon_url"),
         name_norm(r.get("name_en")),
         Json(r["raw"]) if r.get("raw") is not None else None)
        for r in rows
    ]
    if not tuples:
        return 0
    execute_values(
        cur,
        f"INSERT INTO {schema}.item_src "
        f"(source, src_key, item_class, name_en, name_ru, icon_url, name_norm, raw) VALUES %s "
        f"ON CONFLICT (source, src_key) DO UPDATE SET "
        f"item_class = EXCLUDED.item_class, name_en = EXCLUDED.name_en, "
        f"name_ru = EXCLUDED.name_ru, icon_url = EXCLUDED.icon_url, "
        f"name_norm = EXCLUDED.name_norm, raw = EXCLUDED.raw, loaded_at = now()",
        tuples,
    )
    return len(tuples)


def upsert_item_mods(cur, schema: str, rows: list) -> int:
    """UPSERT layer-1 mod rows. Each row is a dict:
    {source, src_key, ordinal, stat_id?, text_en?, text_ru?}.
    The parent item_src row (source, src_key) must already exist (FK)."""
    tuples = [
        (r["source"], r["src_key"], r["ordinal"],
         r.get("stat_id"), r.get("text_en"), r.get("text_ru"))
        for r in rows
    ]
    if not tuples:
        return 0
    execute_values(
        cur,
        f"INSERT INTO {schema}.item_mod_src "
        f"(source, src_key, ordinal, stat_id, text_en, text_ru) VALUES %s "
        f"ON CONFLICT (source, src_key, ordinal) DO UPDATE SET "
        f"stat_id = EXCLUDED.stat_id, text_en = EXCLUDED.text_en, text_ru = EXCLUDED.text_ru",
        tuples,
    )
    return len(tuples)


def relink(cur, schema: str) -> int:
    """Rebuild layer-2 item_link from item_src (full recompute, idempotent).

    Matching baseline (the tunable knob): a row from a metadata-bearing source
    (dat/dump) keeps its OWN metadata Id as item_key — distinct Ids never merge,
    even when they share a display name. A metadata-less row (e.g. a poe2scout
    slug) attaches by name_norm to a dat/dump item_key when one matches, else
    falls back to 'name:'+name_norm (or its own src_key). Swap this query out to
    sharpen matching without touching ingestion or the views.
    """
    cur.execute(f"TRUNCATE {schema}.item_link")
    cur.execute(
        f"""
        INSERT INTO {schema}.item_link (source, src_key, item_key, match_method, confidence)
        WITH meta AS (   -- canonical metadata key per name (from metadata-bearing sources)
            SELECT DISTINCT ON (name_norm) name_norm, src_key AS meta_key
            FROM {schema}.item_src
            WHERE source IN ('dat','dump') AND name_norm IS NOT NULL
            ORDER BY name_norm, source, src_key
        )
        SELECT s.source, s.src_key,
               CASE WHEN s.source IN ('dat','dump') THEN s.src_key          -- own metadata id
                    WHEN m.meta_key IS NOT NULL     THEN m.meta_key          -- bridge by name
                    WHEN s.name_norm IS NOT NULL    THEN 'name:' || s.name_norm
                    ELSE s.src_key END,
               CASE WHEN s.source IN ('dat','dump') THEN 'metadata_id'
                    WHEN m.meta_key IS NOT NULL     THEN 'name_norm'
                    WHEN s.name_norm IS NOT NULL    THEN 'name_norm'
                    ELSE 'src_key' END,
               1.0
        FROM {schema}.item_src s
        LEFT JOIN meta m ON m.name_norm = s.name_norm
        """
    )
    return cur.rowcount
