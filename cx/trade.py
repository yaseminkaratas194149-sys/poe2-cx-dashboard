"""Open pathofexile.com/trade2 tabs pre-filled with curated filter presets.

Why a browser hand-off instead of a direct POST from cx: the trade2 API sits
behind Cloudflare, and a request needs a valid ``cf_clearance`` cookie that only
a real browser can mint (it solves Cloudflare's JS challenge). So cx never POSTs.
It builds the search-query JSON, base64url-encodes it into the URL *fragment*
(``#cxq=...`` — a fragment is never sent to the server), and opens the trade2
page in the user's logged-in browser. The companion userscript
(``poe2-trade-presets.user.js``) reads the fragment, POSTs it in-browser (where
clearance + session are always valid), and redirects the tab to the stored
search. See memory: poe2-trade-preset-architecture.

cx is the *launcher* (builds the query from preset pre-fields, opens the tabs);
the browser is the *executor*. CLI:  ``python -m cx.trade [preset ...]``.
"""
import base64
import json
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

TRADE2_SEARCH = "https://www.pathofexile.com/trade2/search/poe2"
DATA_STATS_URL = "https://www.pathofexile.com/api/trade2/data/stats"
# The data/* dictionaries are open GETs (no auth, no Cloudflare challenge) —
# only the search POST needs a browser. Identify as one anyway.
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def build_search_query(preset: dict) -> dict:
    """Assemble a trade2 search body from a preset's pre-fields.

    Pre-fields (all optional):
      status    : "available" (default) | "securable" | "onlineleague" |
                  "online" | "any"   (see STATUS_LABELS for the trade2 names)
      stats     : list of {"id": <stat id>, "min": .., "max": ..}  (min/max optional;
                  omit both -> "mod present, any roll")
      category  : str, e.g. "armour.helmet"            -> filters.type_filters.category
      rarity    : "normal"|"magic"|"rare"|"unique"|"nonunique" -> type_filters.rarity
      req_level : int (=max) or {"min": .., "max": ..} -> filters.req_filters.lvl
      price     : {"option": "chaos", "max": 15} etc.  -> filters.trade_filters.price
      sort      : dict, default {"price": "asc"}
    """
    q = {"status": {"option": preset.get("status", "available")}}

    # the site always sends a stats group, even empty — mirror it exactly
    group = []
    for s in preset.get("stats") or []:
        f = {"id": s["id"]}
        value = {}
        if s.get("min") is not None:
            value["min"] = s["min"]
        if s.get("max") is not None:
            value["max"] = s["max"]
        if value:
            f["value"] = value
        group.append(f)
    q["stats"] = [{"type": "and", "filters": group}]

    fblock = {}
    tf = {}
    if preset.get("category"):
        tf["category"] = {"option": preset["category"]}
    if preset.get("rarity"):
        tf["rarity"] = {"option": preset["rarity"]}
    if tf:
        fblock["type_filters"] = {"filters": tf}
    rl = preset.get("req_level")
    if rl is not None:
        lvl = {"max": rl} if isinstance(rl, int) else {
            k: rl[k] for k in ("min", "max") if rl.get(k) is not None
        }
        fblock["req_filters"] = {"filters": {"lvl": lvl}}
    if preset.get("price"):
        fblock["trade_filters"] = {"filters": {"price": preset["price"]}}
    if fblock:
        q["filters"] = fblock

    return {"query": q, "sort": preset.get("sort", {"price": "asc"})}


# ---- archetypes: the 4-6 stat bundles that actually matter, per slot --------
# The whole point of the trade builder: trade2 exposes ~600 stat ids, but for a
# given gear slot you almost always want the same handful of "bundles" — life or
# ES, the resistances, the attributes — plus a slot-specific stat (move speed on
# boots, +levels on a focus, etc.). An archetype is that curated shortlist for
# one case; the user circulates between archetypes instead of hand-picking stats.
#
# Each archetype is {category, label, bundles:[bundle-key ...]}. A bundle is a
# named group of pseudo stat ids (STAT_BUNDLES); flattening an archetype yields
# the stat list a preset wants, deduped in declared order.
STAT_BUNDLES = {
    "life":       ["pseudo.pseudo_total_life"],
    "es":         ["pseudo.pseudo_total_energy_shield",
                   "pseudo.pseudo_increased_energy_shield"],
    "mana":       ["pseudo.pseudo_total_mana"],
    # "defence" = pick whichever of life / ES the build runs on — both offered.
    "defence":    ["pseudo.pseudo_total_life",
                   "pseudo.pseudo_total_energy_shield"],
    "resistance": ["pseudo.pseudo_total_elemental_resistance",
                   "pseudo.pseudo_total_chaos_resistance"],
    "res_each":   ["pseudo.pseudo_total_fire_resistance",
                   "pseudo.pseudo_total_cold_resistance",
                   "pseudo.pseudo_total_lightning_resistance"],
    "attributes": ["pseudo.pseudo_total_strength",
                   "pseudo.pseudo_total_dexterity",
                   "pseudo.pseudo_total_intelligence"],
    "all_attr":   ["pseudo.pseudo_total_all_attributes"],
    "move_speed": ["pseudo.pseudo_increased_movement_speed"],
}

# slot -> the bundles that matter for that slot. Ordered so the most build-
# defining bundle leads. (label is what the archetype combo shows.)
ARCHETYPES = {
    "helmet":  {"category": "armour.helmet", "label": "Helmet",
                "bundles": ["defence", "resistance", "attributes"]},
    "chest":   {"category": "armour.chest", "label": "Body Armour",
                "bundles": ["defence", "resistance", "attributes"]},
    "gloves":  {"category": "armour.gloves", "label": "Gloves",
                "bundles": ["defence", "resistance", "attributes"]},
    # boots: the classic slot-specific case — move speed is always wanted.
    "boots":   {"category": "armour.boots", "label": "Boots",
                "bundles": ["move_speed", "defence", "resistance", "attributes"]},
    "shield":  {"category": "armour.shield", "label": "Shield",
                "bundles": ["defence", "resistance", "attributes"]},
    "amulet":  {"category": "accessory.amulet", "label": "Amulet",
                "bundles": ["defence", "resistance", "attributes"]},
    "ring":    {"category": "accessory.ring", "label": "Ring",
                "bundles": ["resistance", "attributes", "life"]},
    "belt":    {"category": "accessory.belt", "label": "Belt",
                "bundles": ["life", "resistance"]},
    "quiver":  {"category": "armour.quiver", "label": "Quiver",
                "bundles": ["attributes", "resistance"]},
}


def bundle_stats(bundle_key: str) -> list:
    """Stat ids for one bundle key (unknown key -> [])."""
    return list(STAT_BUNDLES.get(bundle_key, []))


def archetype_stats(name: str) -> list:
    """Flatten an archetype's bundles to a deduped [stat_id ...] in declared order."""
    arch = ARCHETYPES.get(name)
    if not arch:
        return []
    seen, out = set(), []
    for bkey in arch.get("bundles", []):
        for sid in bundle_stats(bkey):
            if sid not in seen:
                seen.add(sid)
                out.append(sid)
    return out


def archetype_preset(name: str, **overrides) -> dict:
    """Build a ready preset dict for an archetype: its category + the flattened
    stat shortlist (each stat "present, any roll" — the user tightens min/max in
    the builder). Extra pre-fields (status/price/req_level/rarity) via overrides."""
    arch = ARCHETYPES.get(name)
    if not arch:
        return dict(overrides)
    p = {"category": arch["category"],
         "stats": [{"id": sid} for sid in archetype_stats(name)]}
    p.update(overrides)
    return p


def all_archetype_stats() -> set:
    """Every stat id surfaced by some archetype quick-pick (across all slots).

    Used to spot the gap: stats you actually use (from presets) that NO archetype
    bundle offers as a one-click pick — the "what the Trade panel doesn't show"."""
    out = set()
    for name in ARCHETYPES:
        out.update(archetype_stats(name))
    return out


def used_stats(presets: dict) -> dict:
    """{stat id -> [preset names that use it]} across all saved presets.

    The "★ stats you use" surface is *derived, not stored*: a stat counts as one
    you use iff it appears in >=1 preset, and the value is exactly the stat→preset
    link the UI shows. Re-derived whenever presets change, so there is nothing to
    keep in sync (no separate library file). Presets are the single source of
    truth; capturing a live search and saving it as a preset is what earns the
    star."""
    out = {}
    for name, p in (presets or {}).items():
        for s in (p or {}).get("stats") or []:
            sid = s.get("id")
            if not sid:
                continue
            names = out.setdefault(sid, [])
            if name not in names:
                names.append(name)
    for names in out.values():
        names.sort(key=str.lower)
    return out


# ---- read a trade2 link back into a preset (reverse of preset_to_url) -------
def _query_to_preset(q: dict, sort: dict = None) -> dict:
    """Trade2 search *query* block -> preset pre-fields (best-effort, lossless
    for the fields the builder edits). `sort` is the body's sibling sort block;
    kept only when it differs from the trade2 default (price asc)."""
    p = {}
    status = (q.get("status") or {}).get("option")
    if status:
        p["status"] = status
    stats = []
    for grp in q.get("stats") or []:
        for f in grp.get("filters") or []:
            sid = f.get("id")
            if not sid:
                continue
            s = {"id": sid}
            val = f.get("value") or {}
            if val.get("min") is not None:
                s["min"] = val["min"]
            if val.get("max") is not None:
                s["max"] = val["max"]
            stats.append(s)
    if stats:
        p["stats"] = stats
    filters = q.get("filters") or {}
    tf = (filters.get("type_filters") or {}).get("filters") or {}
    if (tf.get("category") or {}).get("option"):
        p["category"] = tf["category"]["option"]
    if (tf.get("rarity") or {}).get("option"):
        p["rarity"] = tf["rarity"]["option"]
    lvl = ((filters.get("req_filters") or {}).get("filters") or {}).get("lvl") or {}
    rl = {k: lvl[k] for k in ("min", "max") if lvl.get(k) is not None}
    if rl:
        p["req_level"] = rl
    price = ((filters.get("trade_filters") or {}).get("filters") or {}).get("price")
    if price:
        p["price"] = price
    if sort and sort != SORT_DEFAULT:
        p["sort"] = sort
    return p


def parse_trade_url(url: str) -> dict:
    """Reverse a trade2 link into preset pre-fields, so the builder can show
    "what characteristics this link cares about".

    Handles the cx hand-off form ``…/search/poe2/<league>#cxq=<base64url(body)>``
    — the self-contained format cx itself produces. (A bare stored-search URL
    ``…/search/poe2/<league>/<id>`` carries no filters in the URL — only the
    server holds them — so it can't be decoded offline; we raise for it.)

    Returns the preset dict; raises ValueError if no decodable query is present.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("empty URL")
    frag = ""
    if "#" in url:
        frag = url.split("#", 1)[1]
    m = None
    if frag:
        import re
        m = re.search(r"(?:^|[#&])cxq=([^&]+)", "#" + frag)
    if not m:
        # Tell the user exactly what went wrong. The usual cause is pasting a
        # *resolved* stored-search URL (…/search/poe2/<league>/<id>) copied from
        # the address bar — by then the browser has dropped the #cxq fragment.
        import re
        path = url.split("#", 1)[0].split("?", 1)[0]
        ss = re.search(r"/search/poe2/[^/]+/([^/]+)/?$", path)
        if ss:
            raise ValueError(
                f"this is a stored-search link (server id '{ss.group(1)}') — its "
                "filters live on GGG's server and can't be decoded offline. Paste "
                "the cx #cxq hand-off link instead — it's printed in the console "
                "and dropped into the From-link box when you Open a preset.")
        raise ValueError(
            "no #cxq= payload in this link — only cx hand-off links (…#cxq=…) "
            "carry their filters in the URL. Open a preset to generate one (it's "
            "printed in the console and put in the From-link box).")
    raw = m.group(1)
    pad = "=" * (-len(raw) % 4)
    try:
        body = json.loads(base64.urlsafe_b64decode(raw + pad).decode("utf-8"))
    except Exception as e:
        raise ValueError(f"bad #cxq payload: {e}")
    q = body.get("query") if isinstance(body, dict) else None
    if not isinstance(q, dict):
        raise ValueError("payload has no query block")
    return _query_to_preset(q, body.get("sort") if isinstance(body, dict) else None)


def preset_to_url(preset: dict, league: str) -> str:
    """Build the trade2 hand-off URL: base + ``#cxq=<base64url(query JSON)>``.

    `league` is the league *display name* (e.g. "HC Runes of Aldur") — exactly
    what source.current_league()["league"] returns and what the trade URL uses.
    """
    body = build_search_query(preset)
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    frag = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{TRADE2_SEARCH}/{urllib.parse.quote(league)}#cxq={frag}"


def open_preset(preset: dict, league: str = None) -> str:
    """Open one preset as a new browser tab; returns the URL opened."""
    if league is None:
        from cx.source import current_league  # lazy: avoids pulling cx.config/DATA at import
        league = current_league()["league"]
    url = preset_to_url(preset, league)
    webbrowser.open(url, new=2)  # new=2 -> new tab when possible
    return url


# ---- dictionaries (verified against the open data/* endpoints, 2026-06) ----
# type_filters.category option ids. ("", "Any") = no category filter.
CATEGORIES = [
    ("", "Any"),
    ("weapon", "Any Weapon"), ("weapon.onemelee", "Any One-Handed Melee Weapon"),
    ("weapon.unarmed", "Unarmed"), ("weapon.claw", "Claw"), ("weapon.dagger", "Dagger"),
    ("weapon.onesword", "One-Handed Sword"), ("weapon.oneaxe", "One-Handed Axe"),
    ("weapon.onemace", "One-Handed Mace"), ("weapon.spear", "Spear"), ("weapon.flail", "Flail"),
    ("weapon.twomelee", "Any Two-Handed Melee Weapon"), ("weapon.twosword", "Two-Handed Sword"),
    ("weapon.twoaxe", "Two-Handed Axe"), ("weapon.twomace", "Two-Handed Mace"),
    ("weapon.warstaff", "Quarterstaff"), ("weapon.talisman", "Talisman"),
    ("weapon.ranged", "Any Ranged Weapon"), ("weapon.bow", "Bow"), ("weapon.crossbow", "Crossbow"),
    ("weapon.caster", "Any Caster Weapon"), ("weapon.wand", "Wand"),
    ("weapon.sceptre", "Sceptre"), ("weapon.staff", "Staff"), ("weapon.rod", "Fishing Rod"),
    ("armour", "Any Armour"), ("armour.helmet", "Helmet"), ("armour.chest", "Body Armour"),
    ("armour.gloves", "Gloves"), ("armour.boots", "Boots"), ("armour.quiver", "Quiver"),
    ("armour.shield", "Shield"), ("armour.focus", "Focus"), ("armour.buckler", "Buckler"),
    ("accessory", "Any Accessory"), ("accessory.amulet", "Amulet"),
    ("accessory.belt", "Belt"), ("accessory.ring", "Ring"),
    ("gem", "Any Gem"), ("gem.activegem", "Skill Gem"),
    ("gem.supportgem", "Support Gem"), ("gem.metagem", "Meta Gem"),
    ("jewel", "Any Jewel"), ("flask", "Any Flask"),
    ("flask.life", "Life Flask"), ("flask.mana", "Mana Flask"),
    ("map", "Any Endgame Item"), ("map.waystone", "Waystone"),
    ("map.fragment", "Map Fragment"), ("map.logbook", "Logbook"),
    ("map.breachstone", "Breachstone"), ("map.barya", "Barya"),
    ("map.bosskey", "Pinnacle Key"), ("map.ultimatum", "Ultimatum Key"),
    ("map.tablet", "Tablet"), ("card", "Divination Card"), ("sanctum.relic", "Relic"),
    ("currency", "Any Currency"), ("currency.omen", "Omen"),
    ("currency.socketable", "Any Augment"), ("currency.rune", "Rune"),
    ("currency.soulcore", "Soul Core"), ("currency.idol", "Idol"),
]
RARITIES = ["", "normal", "magic", "rare", "unique", "uniquefoil", "nonunique"]
# trade2 status.option values, in the site's dropdown order. First entry is the
# site default. Labels mirror the trade2 dropdown exactly.
STATUS_OPTIONS = ["available", "securable", "onlineleague", "online", "any"]
STATUS_LABELS = {
    "available":    "Instant Buyout and In Person",
    "securable":    "Instant Buyout",
    "onlineleague": "In Person (Online in League)",
    "online":       "In Person (Online)",
    "any":          "Any",
}
STATUS_DEFAULT = "available"
# labels in dropdown order, and the reverse label -> option lookup
STATUS_LABEL_LIST = [STATUS_LABELS[o] for o in STATUS_OPTIONS]
STATUS_BY_LABEL = {label: opt for opt, label in STATUS_LABELS.items()}
PRICE_OPTIONS = ["exalted", "divine", "chaos", "annul", "regal", "alch"]

# ---- result sorting ---------------------------------------------------------
# trade2 sorts inside the POST body's top-level "sort" key, NOT in the URL — the
# stored-search id is the same regardless of sort, which is why clicking a column
# never changes the address bar. Two shapes (captured live 2026-06):
#   item property column -> {"<data-field>": "desc"}   e.g. {"ar":"desc"} (Armour)
#   a filtered stat      -> {"stat.<id>":  "desc"}      e.g. {"stat.pseudo.pseudo_total_cold_resistance":"desc"}
#   price (site default) -> {"price": "asc"}
SORT_DEFAULT = {"price": "asc"}
# (label, sort) for the fixed item-property columns the dropdown always offers.
SORT_PROPERTIES = [
    ("Price (cheapest first)", {"price": "asc"}),
    ("Armour ▼",               {"ar": "desc"}),
    ("Evasion ▼",              {"ev": "desc"}),
    ("Energy Shield ▼",        {"es": "desc"}),
    ("Total DPS ▼",            {"dps": "desc"}),
    ("Physical DPS ▼",         {"pdps": "desc"}),
    ("Elemental DPS ▼",        {"edps": "desc"}),
]
SORT_DEFAULT_LABEL = SORT_PROPERTIES[0][0]


def sort_for_stat(stat_id: str, direction: str = "desc") -> dict:
    """Sort key for one of the filter's stats (highest roll first by default).

    Mirrors what the trade2 site sends when you click a filtered-mod column:
    ``{"stat.<id>": "desc"}``."""
    return {"stat." + stat_id: direction}

# Display labels for the category chip navigator's meta row (EquipmentNav). The
# dotted category ids give the tree for free: the segment before the first dot is
# the meta, and the bare "weapon"/"armour"/… entries are each meta's "Any X" leaf.
_CAT_META_LABEL = {
    "weapon": "Weapon", "armour": "Armour", "accessory": "Accessory",
    "gem": "Gem", "jewel": "Jewel", "flask": "Flask", "map": "Endgame",
    "card": "Cards", "sanctum": "Sanctum", "currency": "Currency",
}


def category_taxonomy():
    """CATEGORIES -> the EquipmentNav meta→leaf taxonomy (a chip tree replacing the
    old category dropdown). Each top-level segment ('weapon', 'armour', …) becomes a
    meta whose leaves are its dotted members plus the bare 'Any <meta>' id; a meta
    with a single id (Jewel, Cards) collapses to a terminal meta chip; the lone
    ('', 'Any') entry is a standalone 'Any' meta that clears the category filter.
    Pure restructuring of CATEGORIES — the same option ids come back out as leaf
    ``value``s, so the picked value drops straight into a preset's ``category``."""
    order, buckets = [], {}
    for cid, label in CATEGORIES:
        meta = cid.split(".", 1)[0] if cid else ""
        if meta not in buckets:
            buckets[meta] = []
            order.append(meta)
        buckets[meta].append((cid, label))
    metas = []
    for meta in order:
        entries = buckets[meta]
        if meta == "":                       # the lone ("", "Any") -> clear filter
            metas.append({"key": "any", "label": "Any", "value": ""})
            continue
        leaves = [{"key": cid or meta, "label": label, "value": cid}
                  for cid, label in entries]
        meta_label = _CAT_META_LABEL.get(meta, meta.title())
        if len(leaves) == 1:                 # single id (Jewel, Cards) -> terminal
            metas.append({"key": meta, "label": meta_label,
                          "value": leaves[0]["value"]})
        else:
            metas.append({"key": meta, "label": meta_label, "children": leaves})
    return metas

# Embedded fallback for the stat picker: the full pseudo group. The complete
# dictionary (explicit ~600 ids etc.) comes from stat_options() at runtime.
PSEUDO_STATS = [
    ("pseudo.pseudo_total_cold_resistance", "+#% total to Cold Resistance"),
    ("pseudo.pseudo_total_fire_resistance", "+#% total to Fire Resistance"),
    ("pseudo.pseudo_total_lightning_resistance", "+#% total to Lightning Resistance"),
    ("pseudo.pseudo_total_elemental_resistance", "+#% total Elemental Resistance"),
    ("pseudo.pseudo_total_chaos_resistance", "+#% total to Chaos Resistance"),
    ("pseudo.pseudo_total_resistance", "+#% total Resistance"),
    ("pseudo.pseudo_count_resistances", "# total Resistances"),
    ("pseudo.pseudo_count_elemental_resistances", "# total Elemental Resistances"),
    ("pseudo.pseudo_total_all_elemental_resistances", "+#% total to all Elemental Resistances"),
    ("pseudo.pseudo_total_strength", "+# total to Strength"),
    ("pseudo.pseudo_total_dexterity", "+# total to Dexterity"),
    ("pseudo.pseudo_total_intelligence", "+# total to Intelligence"),
    ("pseudo.pseudo_total_all_attributes", "+# total to all Attributes"),
    ("pseudo.pseudo_total_attributes", "+# total to Attributes"),
    ("pseudo.pseudo_total_life", "+# total maximum Life"),
    ("pseudo.pseudo_total_mana", "+# total maximum Mana"),
    ("pseudo.pseudo_total_energy_shield", "+# total maximum Energy Shield"),
    ("pseudo.pseudo_increased_energy_shield", "#% total increased maximum Energy Shield"),
    ("pseudo.pseudo_increased_movement_speed", "#% increased Movement Speed"),
    ("pseudo.pseudo_number_of_enchant_mods", "# Enchant Modifiers"),
    ("pseudo.pseudo_number_of_implicit_mods", "# Implicit Modifiers"),
    ("pseudo.pseudo_number_of_prefix_mods", "# Prefix Modifiers"),
    ("pseudo.pseudo_number_of_suffix_mods", "# Suffix Modifiers"),
    ("pseudo.pseudo_number_of_affix_mods", "# Modifiers"),
    ("pseudo.pseudo_number_of_desecrated_prefix_mods", "# Desecrated Prefix Modifiers"),
    ("pseudo.pseudo_number_of_desecrated_suffix_mods", "# Desecrated Suffix Modifiers"),
    ("pseudo.pseudo_number_of_desecrated_mods", "# Desecrated Modifiers"),
    ("pseudo.pseudo_number_of_unrevealed_prefix_mods", "# Unrevealed Prefix Modifiers"),
    ("pseudo.pseudo_number_of_unrevealed_suffix_mods", "# Unrevealed Suffix Modifiers"),
    ("pseudo.pseudo_number_of_unrevealed_mods", "# Unrevealed Modifiers"),
    ("pseudo.pseudo_number_of_empty_prefix_mods", "# Empty Prefix Modifiers"),
    ("pseudo.pseudo_number_of_empty_suffix_mods", "# Empty Suffix Modifiers"),
    ("pseudo.pseudo_number_of_empty_affix_mods", "# Empty Modifiers"),
    ("pseudo.pseudo_number_of_fractured_mods", "# Fractured Modifiers"),
    ("pseudo.pseudo_number_of_crafted_mods", "# Crafted Modifiers"),
    ("pseudo.pseudo_number_of_uses_remaining", "# uses remaining (Tablets)"),
]

# Disk cache for the full stats dictionary (regenerable; gitignored).
DICT_CACHE = Path(__file__).resolve().parent / "_trade_dict_cache" / "stats.json"


def _flatten_stats(raw) -> list:
    """data/stats JSON -> [(id, "[group] text")], picker-ready."""
    out = []
    for grp in (raw or {}).get("result") or []:
        label = (grp.get("label") or grp.get("id") or "").lower()
        for e in grp.get("entries") or []:
            if e.get("id") and e.get("text"):
                out.append((e["id"], f"[{label}] {e['text']}"))
    return out


def stat_options(refresh: bool = False) -> list:
    """[(id, text)] for the stat picker: full open dictionary when reachable
    (disk-cached across runs), embedded pseudo set as the offline fallback."""
    if not refresh:
        try:
            flat = _flatten_stats(json.loads(DICT_CACHE.read_text("utf-8")))
            if flat:
                return flat
        except Exception:
            pass
    try:
        req = urllib.request.Request(
            DATA_STATS_URL, headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        flat = _flatten_stats(raw)
        if flat:
            DICT_CACHE.parent.mkdir(parents=True, exist_ok=True)
            DICT_CACHE.write_text(json.dumps(raw), encoding="utf-8")
            return flat
    except Exception:
        pass
    return [(i, f"[pseudo] {t}") for i, t in PSEUDO_STATS]


# ---- presets ----------------------------------------------------------------
# User data, NOT published: lives at the repo root (the public repo ships only
# cx/ — see .gitignore), seeded with the builtin examples on first use.
PRESETS_PATH = Path(__file__).resolve().parent.parent / "cx_trade_presets.json"

PRESETS = {
    "helmet": {
        "category": "armour.helmet",
        "stats": [
            {"id": "pseudo.pseudo_total_elemental_resistance"},
            {"id": "pseudo.pseudo_total_cold_resistance"},
        ],
        "req_level": 20,
        "price": {"option": "chaos", "max": 15},
    },
}


def load_presets() -> dict:
    """All saved presets {name: pre-fields}; seeds the file with PRESETS once."""
    try:
        return json.loads(PRESETS_PATH.read_text("utf-8"))
    except Exception:
        save_presets(PRESETS)
        return dict(PRESETS)


def save_presets(presets: dict) -> None:
    PRESETS_PATH.write_text(
        json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    import sys
    presets = load_presets()
    names = list(argv if argv is not None else sys.argv[1:]) or ["helmet"]
    unknown = [n for n in names if n not in presets]
    if unknown:
        print(f"unknown preset(s): {', '.join(unknown)}; have: {', '.join(presets)}")
        return 2
    from cx.source import current_league
    league = current_league()["league"]
    for n in names:
        print("opened:", open_preset(presets[n], league))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
