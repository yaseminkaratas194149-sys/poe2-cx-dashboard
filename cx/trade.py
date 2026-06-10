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
      status    : "available" (default) | "online" | "any"
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
STATUS_OPTIONS = ["available", "online", "any"]
PRICE_OPTIONS = ["exalted", "divine", "chaos", "annul", "regal", "alch"]

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
