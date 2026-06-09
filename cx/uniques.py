"""Pull the unique-item reference from poe2scout into cx_<league>.unique_item.

poe2scout tracks uniques only for SOFTCORE leagues (HC unique categories are
empty), but a unique's name / base / mods / icon are league-invariant — so we
pull from the softcore counterpart (config.UNIQUES_LEAGUE_SHORT) and store it as
reference in the active schema. On-demand CLI (like cx.backfill), not a per-cycle
DAG stage: uniques change per patch, not per hour.

  python -m cx.uniques            pull all unique categories -> cx_<league>.unique_item
"""
import psycopg2

from cx import config, source, store


def ensure_schema(cur, schema: str):
    """Create the active schema + tables if missing (idempotent; same DDL as provisioning)."""
    ddl = config.SCHEMA_SQL.read_text(encoding="utf-8").replace("{schema}", schema)
    cur.execute(ddl)


def pull_all(src_league: str):
    """Pull every unique category from poe2scout, paginated. -> (items, per_category_counts)."""
    cats = source.items_categories(src_league).get("UniqueCategories", [])
    items, per_cat = [], {}
    for c in cats:
        cat = c.get("ApiId")
        if not cat:
            continue
        page, pages, got = 1, 1, 0
        while page <= pages:
            resp = source.uniques_by_category(src_league, cat, page=page, per_page=250)
            pages = resp.get("Pages") or 1
            batch = resp.get("Items") or []
            items.extend(batch)
            got += len(batch)
            page += 1
        per_cat[cat] = got
    return items, per_cat


def main():
    src = config.UNIQUES_LEAGUE_SHORT
    schema = config.schema_name(config.LEAGUE_SHORT)
    print(f"uniques: source league '{src}' -> schema {schema}")

    items, per_cat = pull_all(src)
    print(f"  pulled {len(items)} uniques  {per_cat}")

    conn = psycopg2.connect(**config.DB_CONFIG)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        ensure_schema(cur, schema)          # idempotent
        n = store.upsert_uniques(cur, schema, items)
        cur.close()
    finally:
        conn.close()
    print(f"  upserted {n} uniques into {schema}.unique_item")


if __name__ == "__main__":
    main()
