"""Source loaders for the cx_ref item container — one per source.

Contract: a loader is a callable `load() -> (items, mods)` where
  items: list of {source, src_key, item_class?, name_en?, name_ru?, icon_url?, raw?}
  mods : list of {source, src_key, ordinal, stat_id?, text_en?, text_ru?}
It does NO db writes and NO merging — it just maps its source to layer-1 rows
tagged with its own `source`. The runner (cx.ref.__main__) persists them via
cx.ref.store and then relink()s.

These are intentionally STUBS: the container is the deliverable; the three
sources get filled independently and then compared (item_coverage /
item_divergence). Implement a body, run `python -m cx.ref load <source>`, watch
the reconciliation report change.

Source roles (verified — see concepts/items-reference.md):
  poe2scout  EN names + icons + tradeable set + prices; NO Russian.
  dump       adainrivers/poe2-data JSON: EN+RU names; stat text EN-only.
  dat        pathofexile-dat extraction (translations ['English','Russian']):
             EN+RU names AND EN+RU stat text. The only RU-stats source.
"""
import json
import urllib.request
from pathlib import Path

try:
    from cx.config import USER_AGENT
except Exception:
    USER_AGENT = "poe2cx/0.2"

SOURCE_POE2SCOUT = "poe2scout"
SOURCE_DUMP = "dump"
SOURCE_DAT = "dat"


def load_poe2scout():
    """EN names + icons + tradeable set, from the poe2scout API.

    src_key = api_id (slug). Currency is already in cx_<league>.currency
    (reuse store.upsert_currencies' embeds); uniques via the items endpoints.
    """
    # TODO(fill): map poe2scout currency/uniques -> item_src rows
    #   item_class from CategoryApiId; icon_url from IconUrl; name_en from Text.
    return [], []


# --- dump source (adainrivers/poe2-data) -------------------------------------
DUMP_REPO = "adainrivers/poe2-data"
DUMP_BRANCH = "main"
DUMP_RAW = f"https://raw.githubusercontent.com/{DUMP_REPO}/{DUMP_BRANCH}"
DUMP_CACHE = Path(__file__).resolve().parent / "_dump_cache"
# currency bases live under this metadata-path prefix (uniques: see load_dump)
DUMP_CURRENCY_PREFIX = "Metadata/Items/Currency/"


def _dump_fetch(rel_path):
    """Download a dump JSON (cached on disk under _dump_cache/) and parse it."""
    DUMP_CACHE.mkdir(parents=True, exist_ok=True)
    cache = DUMP_CACHE / rel_path.replace("/", "__")
    if not cache.exists():
        req = urllib.request.Request(f"{DUMP_RAW}/{rel_path}",
                                     headers={"User-Agent": USER_AGENT})
        cache.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    return json.loads(cache.read_text(encoding="utf-8"))


def load_dump():
    """EN+RU item names from the adainrivers/poe2-data JSON dump.

    Currency (this pass): baseitemtypes.json filtered to the currency
    metadata-path prefix; RU name joined by the stable `Id`. src_key = Id.
    Uniques: TODO — names are in words.json (needs the unique Wordlist filter);
    unique *mods* are NOT in this dump (that's the `dat` source).
    """
    en = _dump_fetch("data/baseitemtypes.json")
    ru = _dump_fetch("data/russian/baseitemtypes.json")
    ru_name = {r["Id"]: r.get("Name") for r in ru}

    items = []
    for r in en:
        rid = r.get("Id") or ""
        name_en = r.get("Name")
        if not name_en or not rid.startswith(DUMP_CURRENCY_PREFIX):
            continue
        cls = (r.get("ItemClassesKey") or {}).get("Id")
        items.append({
            "source": SOURCE_DUMP,
            "src_key": rid,
            "item_class": cls,
            "name_en": name_en,
            "name_ru": ru_name.get(rid),
            "icon_url": None,           # dump carries DDS art refs, not CDN urls
            "raw": {"Id": rid, "class": cls,
                    "name_en": name_en, "name_ru": ru_name.get(rid)},
        })
    return items, []


def load_dat():
    """EN+RU names AND EN+RU stat text via pathofexile-dat extraction.

    Pin a patch; config translations ['English','Russian']. src_key = metadata
    Id. mods from Mods + stat_descriptions (text_en/text_ru both populated).
    """
    # TODO(fill): run/parse pathofexile-dat output -> item_src + item_mod_src rows
    return [], []


SOURCES = {
    SOURCE_POE2SCOUT: load_poe2scout,
    SOURCE_DUMP: load_dump,
    SOURCE_DAT: load_dat,
}
