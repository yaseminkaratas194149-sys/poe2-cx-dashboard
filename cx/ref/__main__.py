"""cx.ref CLI — provision the container, run source loaders, reconcile.

  python -m cx.ref provision         provision / upgrade the cx_ref schema
  python -m cx.ref load [source...]  run loader(s) (default: all), then relink
  python -m cx.ref reconcile         coverage + divergence report
  python -m cx.ref                   provision + load all + reconcile
"""
import sys

import psycopg2

from cx import config
from cx.ref import loaders, reconcile, store
from cx.ref.provision import ensure_ref_schema


def run_load(names):
    schema = config.REF_SCHEMA
    conn = psycopg2.connect(**config.DB_CONFIG)
    try:
        cur = conn.cursor()
        for name in names:
            loader = loaders.SOURCES.get(name)
            if loader is None:
                print(f"  ! unknown source: {name} (have: {', '.join(loaders.SOURCES)})")
                continue
            items, mods = loader()
            n_i = store.upsert_item_src(cur, schema, items)
            n_m = store.upsert_item_mods(cur, schema, mods)
            print(f"  {name}: {n_i} items, {n_m} mods")
        linked = store.relink(cur, schema)
        conn.commit()
        cur.close()
        print(f"  relinked: {linked} src rows")
    finally:
        conn.close()


def run_reconcile():
    schema = config.REF_SCHEMA
    conn = psycopg2.connect(**config.DB_CONFIG)
    try:
        cur = conn.cursor()
        items, ps, dump, dat, in2, in3 = reconcile.coverage(cur, schema)
        print(f"[{schema}] items={items}  poe2scout={ps} dump={dump} dat={dat}  "
              f">=2 sources={in2}  all 3={in3}")
        div = reconcile.divergences(cur, schema)
        if div:
            print(f"-- {len(div)} divergent fields (sources disagree):")
            for item_key, field, n, by in div:
                print(f"  {item_key:<40.40} {field:<11} {by}")
        else:
            print("-- no divergences (no overlapping items / fields yet)")
        cur.close()
    finally:
        conn.close()


def main(argv):
    cmd = argv[0] if argv else "all"
    if cmd == "provision":
        print(f"provisioned: {ensure_ref_schema()}")
    elif cmd == "load":
        ensure_ref_schema()
        run_load(argv[1:] or list(loaders.SOURCES))
    elif cmd == "reconcile":
        run_reconcile()
    elif cmd == "all":
        print(f"provisioned: {ensure_ref_schema()}")
        run_load(list(loaders.SOURCES))
        run_reconcile()
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
