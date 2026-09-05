"""Unique-item reference: poe2scout Uniques -> cx_<league>.unique_item.

poe2scout tracks uniques mostly for softcore leagues (the HC lists are empty),
and a new league's list can stay empty for days after launch -- while a
unique's name / base / mods / icon are league-invariant and its UniqueItemId
is the same in every league. So the reference is MERGED into the tracked
league's schema: the most complete populated list first, then the tracked
league's softcore twin on top for current prices / mod text
(`source.uniques_sources` picks the order). Uniques change per patch, not per
hour, so this is not part of the hourly cycle: it is the `cx20_uniques` stage
of the Actualize cycle, and an on-demand CLI:

  python -m cx.uniques        resolve the league + sources, pull, UPSERT
"""
import psycopg2

from cx import config, source, store


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


def refresh(cur, schema: str, sources: list, log=print) -> dict:
    """Pull each source league in order and UPSERT into `schema`.unique_item.

    A later source overwrites an earlier one row by row (same UniqueItemId),
    which is the point of the order. The caller owns the transaction.
    -> {count: distinct items seen, per_source: {short: rows upserted}}."""
    seen, per = set(), {}
    for src in sources:
        items, per_cat = pull_all(src)
        per[src] = store.upsert_uniques(cur, schema, items)
        seen.update(it["UniqueItemId"] for it in items)
        log(f"{src}: {len(items)} uniques {per_cat}")
    return {"count": len(seen), "per_source": per}


def main():
    from cx.stages.provision import EnsureSchemaStage   # lazy: cx.stages imports this module
    prov = EnsureSchemaStage().run({})                  # tracked league + schema, idempotent
    schema, short = prov["schema"], prov["short_name"]
    print(f"uniques: {prov['league']} -> {schema}")
    sources = source.uniques_sources(short, log=lambda m: print("  probe", m))
    print(f"  sources: {' + '.join(sources)}")
    conn = psycopg2.connect(**config.DB_CONFIG)
    try:
        cur = conn.cursor()
        res = refresh(cur, schema, sources, log=lambda m: print("  " + m))
        conn.commit()
        cur.close()
    finally:
        conn.close()
    print(f"  {res['count']} distinct uniques -> {schema}.unique_item")


if __name__ == "__main__":
    main()
