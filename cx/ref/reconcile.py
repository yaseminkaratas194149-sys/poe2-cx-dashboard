"""Read-only reconciliation report over cx_ref (coverage + divergence).

Pure queries over the layer-3 views — the "where do the sources agree / diverge"
answer the container exists to give.
"""


def coverage(cur, schema: str):
    """One row: (items, poe2scout, dump, dat, in_2plus, in_all3)."""
    cur.execute(
        f"""
        SELECT count(*) AS items,
               count(*) FILTER (WHERE in_poe2scout)   AS poe2scout,
               count(*) FILTER (WHERE in_dump)         AS dump,
               count(*) FILTER (WHERE in_dat)          AS dat,
               count(*) FILTER (WHERE n_sources >= 2)  AS in_2plus,
               count(*) FILTER (WHERE n_sources = 3)   AS in_all3
        FROM {schema}.item_coverage
        """
    )
    return cur.fetchone()


def divergences(cur, schema: str, limit: int = 30):
    """Rows: (item_key, field, n_distinct, by_source) where sources disagree."""
    cur.execute(
        f"""
        SELECT item_key, field, n_distinct, by_source
        FROM {schema}.item_divergence
        ORDER BY n_distinct DESC, item_key
        LIMIT %s
        """,
        (limit,),
    )
    return cur.fetchall()
