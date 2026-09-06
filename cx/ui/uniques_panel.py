"""UniquesPanel — a unique-item browser in the cx visual language.

Drill-down like the poe2scout item browser, now three tiers: a row of fixed
META-category chips (Weapon · Offhands · Armour · Others) → the chosen meta's
groups opened in one line → a row of base chips for the chosen group → the
uniques in that base, sorted by required level (low → high; no requirement counts
as level 0, so those come first), each with its icon,
level and a mod. The meta split is a static system-level table (_META_GROUPS),
permanent and never derived from data; see meta_of().

Second row varies by group: Weapons split by hand (1H/2H); **armour groups (Body
Armours, Helmets, Gloves, Boots, Offhands) filter by ATTRIBUTE instead of base
name** — a multi-select Str/Dex/Int toggle (red/green/blue; dim when off), where
picking several shows bases that have *all* of them (Str+Dex = Armour/Evasion
hybrids). See attr_set() for how an item's attributes are derived. Other groups
keep base-name chips.

Clicking a row pops an in-game-style detail card (build_sections + _show_card):
one data-driven renderer for every class — the class marker is the null-valued
`properties` key, stat lines are the valued ones, then requirements / implicit /
explicit mods / flavour. Everything it needs is kept on the item dict at index
time, so the click needs no extra query.

Data: cx_<league>.unique_item (the Actualize cycle / `python -m cx.uniques`), read
from the league schema with the freshest pairs (derive.resolve_schema). A row
click pops the detail card; a DOUBLE click (and the card's "↗ Open trade2"
button) opens a trade2 tab for that unique in the tracked league, through the
same browser hand-off the Trade view uses (trade.unique_preset -> #cxq). The
category/base taxonomy is DERIVED from poe2scout's coarse category via classify()
below — poe2scout's unique data has only 7 coarse categories, so the
screenshot-style split (Body Armours / Helmets / Bow / Staff …) is reconstructed
from the item CLASS poe2scout keeps in `properties` (weapons and armour — the
same marker the detail card shows) and from base-type keywords (the rest, and as
the fallback). Tune the tables freely.
"""
import queue
import re
import threading
import traceback
import tkinter as tk
from tkinter import ttk

import psycopg2

from cx import config, derive, trade
from .theme import (BG, BG2, BG3, FG, FG_DIM, FG_MUTED, BORDER, DULL_GRN, RED,
                    HOVER_BG)
from .icons import IconCache
from .row_table import RowTable
from .colsort import ColumnSort
from .equipment_nav import EquipmentNav
from .chrome import work_area_at

ICON_SIZE = 22
STRIPE_A, STRIPE_B = BG3, "#272728"

# --- taxonomy: derive (group, subgroup) from category_api_id + base_type ------
# Heuristic keyword rules over the base type; first match wins. Unmatched falls
# back to the base type as its own subgroup, so nothing is hidden or misfiled.

# poe2scout's ItemMetadata.properties carries the real item CLASS as one of its
# keys (e.g. "One Hand Mace", "Crossbow", "Spear", "Gloves", "Boots") — far more
# reliable than guessing from the base-type name (Morning Star / Warpick are
# maces; a "Runic Fork" is actually a wand; "Aged Cuffs" / "Linen Wraps" are
# gloves, "Secured Leggings" boots, "Grand Visage" a helmet). The class key is
# the null-valued one (see build_sections); these keys are STATS, to be skipped
# when isolating it.
_STAT_KEYS = {
    "Physical Damage", "Elemental Damage", "Fire Damage", "Cold Damage",
    "Lightning Damage", "Chaos Damage", "Attacks per Second",
    "Critical Hit Chance", "Reload Time", "Quality", "Spirit",
    "Energy Shield", "Armour", "Evasion", "Block",
}
# class-keyword -> display label (substring match, specific first); folds
# culture-prefixed variants ("Ezomyte Wand"→Wand) and Trarthan Cannon→Crossbow.
_WEAPON_CLASSES = [
    ("One Hand Mace", "One Hand Mace"), ("Two Hand Mace", "Two Hand Mace"),
    ("One Hand Sword", "One Hand Sword"), ("Two Hand Sword", "Two Hand Sword"),
    ("One Hand Axe", "One Hand Axe"), ("Two Hand Axe", "Two Hand Axe"),
    ("Crossbow", "Crossbow"), ("Cannon", "Crossbow"),
    ("Quarterstaff", "Quarterstaff"), ("Warstaff", "Warstaff"),
    ("Bow", "Bow"), ("Spear", "Spear"), ("Wand", "Wand"),
    ("Sceptre", "Sceptre"), ("Staff", "Staff"), ("Talisman", "Talisman"),
    ("Flail", "Flail"), ("Dagger", "Dagger"), ("Claw", "Claw"),
    ("Mace", "Mace"), ("Sword", "Sword"), ("Axe", "Axe"),
]
_HELMET = ("Helm", "Mask", "Circlet", "Cap", "Crown", "Hood", "Burgonet",
           "Bascinet", "Sallet", "Coif", "Visor", "Pelt", "Crest", "Tiara", "Hat")
_GLOVES = ("Glove", "Mitt", "Gauntlet", "Bracer")
_BOOTS = ("Boot", "Sandal", "Greave", "Shoe", "Slipper")
_SHIELD = ("Shield", "Buckler")
# armour class-keyword -> group (substring match, so culture-prefixed variants
# fold: "Kalguuran Shield"→Offhands, "Ezomyte Gloves"→Gloves). Offhands = the
# off-hand slot: shields, bucklers, foci and quivers.
_ARMOUR_CLASSES = [
    ("Body Armour", "Body Armours"), ("Helmet", "Helmets"),
    ("Gloves", "Gloves"), ("Boots", "Boots"),
    ("Shield", "Offhands"), ("Buckler", "Offhands"), ("Focus", "Offhands"),
    ("Quiver", "Offhands"),
]


def _weapon_class(props, base_type):
    """Real weapon class — prefer poe2scout's properties (the class is a non-stat
    key there: 'One Hand Mace', 'Crossbow', …), fall back to base-type keywords."""
    keys = list(props.keys()) if isinstance(props, dict) else []
    for k in keys:                          # the class key in properties
        if k in _STAT_KEYS:
            continue
        for kw, label in _WEAPON_CLASSES:
            if kw.lower() in k.lower():
                return label
    for kw, label in _WEAPON_CLASSES:       # fall back to the base-type name
        if kw.lower() in (base_type or "").lower():
            return label
    for k in keys:                          # last resort: any non-stat prop key
        if k not in _STAT_KEYS:
            return k
    return base_type or "Other"


def _armour_group(props, base_type):
    """Armour group — prefer poe2scout's class marker (the null-valued property
    key: 'Gloves', 'Boots', 'Helmet', 'Body Armour', 'Shield', 'Quiver', …), fall
    back to base-type keywords when the marker is missing."""
    if isinstance(props, dict):
        for k, v in props.items():          # the class marker in properties
            if v is not None or k in _STAT_KEYS:
                continue
            for kw, group in _ARMOUR_CLASSES:
                if kw.lower() in k.lower():
                    return group
    btl = (base_type or "").lower()         # fall back to the base-type name
    if any(k.lower() in btl for k in _SHIELD) or "focus" in btl or "quiver" in btl:
        return "Offhands"
    if any(k.lower() in btl for k in _GLOVES):
        return "Gloves"
    if any(k.lower() in btl for k in _BOOTS):
        return "Boots"
    if any(k.lower() in btl for k in _HELMET):
        return "Helmets"
    return "Body Armours"


def classify(category, base_type, props=None):
    """-> (group, subgroup). Weapons and armour take their class from poe2scout
    properties (One Hand Mace, Crossbow, Gloves, Boots, Quiver, …); the rest
    derive from base_type.

    Non-weapon keyword matching is CASE-INSENSITIVE so compounds fold into their
    group instead of splintering into singletons."""
    bt = base_type or ""
    btl = bt.lower()
    cat = (category or "").lower()
    if cat == "weapon":
        return "Weapons", _weapon_class(props, bt)
    if cat == "armour":
        return _armour_group(props, bt), bt
    if cat == "accessory":
        if "ring" in btl:
            return "Jewellery", "Ring"
        if "amulet" in btl or "talisman" in btl:
            return "Jewellery", "Amulet"
        if "belt" in btl:
            return "Jewellery", "Belt"
        return "Jewellery", bt or "Other"
    if cat == "flask":
        if "charm" in btl:
            return "Charms", bt
        if "mana" in btl:
            return "Flasks", "Mana"
        if "life" in btl:
            return "Flasks", "Life"
        return "Flasks", bt or "Other"
    if cat == "jewel":
        return "Jewels", bt or "Other"
    if cat == "map":
        return ("Tablets" if "tablet" in btl else "Waystones"), bt or "Other"
    if cat == "sanctum":
        return "Relics", bt or "Other"
    return (category or "Other").title(), bt or "Other"


# Weapon hand split. poe2scout's class only carries hand for Mace/Sword/Axe
# ("One/Two Hand …"); the rest are fixed by game design. VERIFY these — esp.
# Spear / Flail / Quarterstaff (best guess from PoE2 EA, easy to move).
_TWO_HAND = {"Bow", "Crossbow", "Staff", "Warstaff", "Quarterstaff", "Talisman"}
_ONE_HAND = {"Wand", "Sceptre", "Spear", "Flail", "Dagger", "Claw"}


def weapon_hand_and_type(weapon_class):
    """(hand, short_type) from a poe2scout weapon class; hand in {'1H','2H',None}.
    'One Hand Mace'->('1H','Mace'), 'Bow'->('2H','Bow'), 'Wand'->('1H','Wand')."""
    wc = weapon_class or ""
    if wc.startswith("One Hand "):
        return "1H", wc[len("One Hand "):]
    if wc.startswith("Two Hand "):
        return "2H", wc[len("Two Hand "):]
    if wc in _TWO_HAND:
        return "2H", wc
    if wc in _ONE_HAND:
        return "1H", wc
    return None, wc


# Weapon CATEGORY split for the scope bar (row 1, to the right of All/1H/2H). Range
# = bows & crossbows; Melee = the martial classes (incl. Talisman, per request);
# Caster = everything else (Wand/Sceptre/Staff/Warstaff). Driven off the short
# weapon type (wtype); the catch-all into Caster keeps any new class reachable.
_RANGE_TYPES = {"Bow", "Crossbow"}
_MELEE_TYPES = {"Mace", "Sword", "Axe", "Quarterstaff", "Spear", "Talisman",
                "Flail", "Dagger", "Claw"}
# row-1 scope chips, in order: hand split then category split.
_WSCOPE_ORDER = ["All", "1H", "2H", "Melee", "Range", "Caster"]


def weapon_category(short_type):
    """Melee / Range / Caster bucket for a short weapon type (wtype). Range = bows &
    crossbows; Melee = the martial classes (incl. Talisman); Caster = the rest."""
    if short_type in _RANGE_TYPES:
        return "Range"
    if short_type in _MELEE_TYPES:
        return "Melee"
    return "Caster"


_GROUP_ORDER = ["Weapons", "Offhands", "Body Armours", "Helmets", "Gloves",
                "Boots", "Jewellery", "Charms", "Flasks", "Jewels", "Tablets",
                "Waystones", "Relics"]
_MAX_SUBCHIPS = 30          # cap base chips; the rest stay reachable under "All"

# Top tier: fixed system-level META-categories over the groups (ticket 1). The
# group bar is two rows -- row 1 is these four meta-chips, row 2 opens the chosen
# meta's member groups in a single line. The split is permanent (a static table,
# not derived from data) so it never duplicates into the per-group sub-taxonomy.
# Any group not named here folds into "Others" via _meta_of, so nothing is hidden.
_META_ORDER = ["Weapon", "Offhands", "Armour", "Others"]
_META_GROUPS = {
    "Weapon":   ["Weapons"],
    "Offhands": ["Offhands"],
    "Armour":   ["Body Armours", "Helmets", "Gloves", "Boots"],
    "Others":   ["Jewellery", "Charms", "Flasks", "Jewels", "Tablets",
                 "Waystones", "Relics"],
}
# group -> meta (inverted once); the catch-all keeps unknown groups reachable.
_GROUP_META = {g: m for m, gs in _META_GROUPS.items() for g in gs}
_META_FALLBACK = "Others"


def meta_of(group):
    """The fixed meta-category a group belongs to; unknown groups -> 'Others'."""
    return _GROUP_META.get(group, _META_FALLBACK)

# Armour groups filter by ATTRIBUTE (Str/Dex/Int), not by base name: their second
# chip row is the multi-select Str/Dex/Int toggle instead of base chips. Other
# groups keep base chips (Weapons keep the 1H/2H split).
_ATTR_GROUPS = {"Body Armours", "Helmets", "Gloves", "Boots", "Offhands"}
_ATTRS = ("Str", "Dex", "Int")

# An item "has" an attribute if it REQUIRES it (requirements->Str/Dex/Int) or if it
# carries the matching DEFENSE (PoE: Armour↔Str, Evasion↔Dex, Energy Shield↔Int) --
# the defense fallback classifies low-level bases whose requirements are empty but
# which still provide a defense. requirements also has rare long-form keys.
_ATTR_REQ_KEYS = {"Str": ("Str", "Strength"), "Dex": ("Dex", "Dexterity"),
                  "Int": ("Int", "Intelligence")}
_DEFENSE_ATTR = {"Armour": "Str", "Evasion Rating": "Dex", "Energy Shield": "Int"}

# Str/Dex/Int chip colors (PoE convention). Each chip always carries its hue:
# (on_bg vivid when selected, off_bg dim tint + off_fg pale hue when not) -- the
# "блеклый соответствующий цвет" the unselected state should read as.
ATTR_COLORS = {
    "Str": ("#be3a30", "#3a2422", "#c98b86"),   # red    — Armour
    "Dex": ("#3a9d4e", "#233a29", "#8fc89a"),   # green  — Evasion
    "Int": ("#3f86cc", "#23304a", "#8fb4dd"),   # blue   — Energy Shield
}
ATTR_FG_ON = "#f5f5f5"


def attr_set(requirements, props):
    """The {Str,Dex,Int} an armour item has -- union of its attribute REQUIREMENTS
    and its DEFENSE properties (see _DEFENSE_ATTR). Empty for items with neither."""
    req = requirements or {}
    pk = set(props.keys()) if isinstance(props, dict) else set()
    out = set()
    for attr, keys in _ATTR_REQ_KEYS.items():
        if any(req.get(k) for k in keys):
            out.add(attr)
    for prop, attr in _DEFENSE_ATTR.items():
        if prop in pk:
            out.add(attr)
    return out


# ---- detail card (in-game-style item tooltip) -------------------------------
# Colors rhyme with the PoE item tooltip; the body is data-driven (no per-class
# template) — the layout differences between a weapon/armour/jewel ARE the data.
CARD_GOLD = "#c79653"        # unique name
CARD_VAL = "#a6c8e6"         # property values (Armour: 15, Crit: 10.94% …)
CARD_MOD = "#8aa0ff"         # explicit mods (PoE blue)
CARD_IMPL = "#7fb4c4"        # implicit mods (muted teal-blue)

# Game-like display order for common property stats. jsonb returns keys sorted by
# length, not by game order, so we impose this; the class marker (the null-valued
# property key) is lifted to its own line; unknown stats are appended after these.
_PROP_ORDER = [
    "Physical Damage", "Fire Damage", "Cold Damage", "Lightning Damage",
    "Chaos Damage", "Elemental Damage", "Critical Hit Chance",
    "Attacks per Second", "Reload Time", "Weapon Range",
    "Armour", "Evasion Rating", "Energy Shield", "Spirit", "Block chance",
    "Limited to", "Radius", "Quality",
]
_PROP_ORD = {k: i for i, k in enumerate(_PROP_ORDER)}


def _clean(text):
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def _lvl_key(it):
    """Sort key for a unique: required level ascending, then name. A unique with
    no level requirement is wearable from the start, so it counts as level 0 and
    sorts FIRST -- not last, as a bare ``None`` would."""
    return (it["lvl"] or 0, it["name"])


def build_sections(name, base, req, implicits, explicits, flavour, props):
    """A stored unique -> the ordered tooltip sections, as ``(style, text)`` pairs.
    style ∈ {name, base, class, prop, req, implicit, explicit, flavour, sep}. One
    renderer for every item class: the class marker is the null-valued property
    key; stat lines are the valued ones (ordered by _PROP_ORDER); then requirements,
    implicit mods, explicit mods, flavour -- each preceded by a ``sep`` divider."""
    props = props or {}
    out = [("name", name)]
    if base and base != name:
        out.append(("base", base))

    cls = [k for k, v in props.items() if v is None]          # class / slot marker(s)
    stats = {k: v for k, v in props.items() if v is not None}
    block = [("class", ", ".join(cls))] if cls else []
    for k in sorted(stats, key=lambda k: (_PROP_ORD.get(k, 999), k)):
        # templated keys ("Recovers {0} Mana every {1} Seconds") fill {0} with the
        # stored value; we only have one number, so any {1}+ stays a placeholder.
        block.append(("prop", k.replace("{0}", str(stats[k])) if "{0}" in k
                      else f"{k}: {stats[k]}"))
    if block:
        out.append(("sep", "")); out += block

    if req:
        parts = ([f"Level {req['Level']}"] if req.get("Level") else []) + \
                [f"{req[a]} {a}" for a in ("Str", "Dex", "Int") if req.get(a)]
        if parts:
            out += [("sep", ""), ("req", "Requires: " + ", ".join(parts))]
    if implicits:
        out.append(("sep", "")); out += [("implicit", m) for m in implicits]
    if explicits:
        out.append(("sep", "")); out += [("explicit", m) for m in explicits]
    if flavour:
        out += [("sep", ""), ("flavour", flavour)]
    return [(style, _clean(text)) for style, text in out]


# ---- tree "info" column: elemental resistances (armour) / DPS (weapons) ------
# The uniques tree drops the base/mod columns for two derived summaries: armour
# shows its elemental resistances as 3 cells "fire cold lightning" (e.g. "16 0 10"
# = +16% Fire, 0 Cold, +10% Lightning; blank when the item grants none); weapons
# show phys DPS and elemental DPS. Both are read off the stored mods / properties.

# A stored numeric is a roll RANGE ("(50-75)", "8-12") or a plain number; we take
# the HIGH roll (what a max-rolled copy gives) as the representative value.
_NUM = r"[+-]?\d+(?:\.\d+)?"
_RANGE_RE = re.compile(r"\(?\s*(%s)\s*-\s*(%s)\s*\)?" % (_NUM, _NUM))


def _hi_roll(text):
    """High end of the first roll range in *text* ("(50-75)"->75.0, "12"->12.0),
    or None. Handles the '(-5--1)' double-minus form via the signed number regex."""
    if text is None:
        return None
    m = _RANGE_RE.search(str(text))
    if m:
        try:
            return max(float(m.group(1)), float(m.group(2)))
        except ValueError:
            return None
    m = re.search(_NUM, str(text))
    return float(m.group(0)) if m else None


def _roll_range(text):
    """The (lo, hi) of the first roll range in *text*: "(50-75)"->(50.0, 75.0),
    "(-20--10)"->(-20.0, -10.0), a bare "12"->(12.0, 12.0), or None. Mirror of
    _hi_roll that keeps both bounds, so a fixed value (lo==hi) is distinguishable
    from a rolled range."""
    if text is None:
        return None
    m = _RANGE_RE.search(str(text))
    if m:
        try:
            a, b = float(m.group(1)), float(m.group(2))
        except ValueError:
            return None
        return (min(a, b), max(a, b))
    m = re.search(_NUM, str(text))
    return (float(m.group(0)), float(m.group(0))) if m else None


# Per-element resistance mod templates: "...to Fire Resistance" etc. "all
# Elemental Resistances" feeds all three. MAXIMUM-resistance and penetration lines
# are deliberately excluded — this column is the flat elemental resist a piece adds.
_RESIST_RE = {
    "fire": re.compile(r"to Fire Resistance\b", re.I),
    "cold": re.compile(r"to Cold Resistance\b", re.I),
    "lightning": re.compile(r"to Lightning Resistance\b", re.I),
}
_ALL_ELE_RE = re.compile(r"to all Elemental Resistances\b", re.I)
_MAX_RES_RE = re.compile(r"Maximum", re.I)


def elemental_resists(explicits):
    """The flat Fire/Cold/Lightning resistance an armour piece grants, as a dict
    {fire,cold,lightning} -> (lo, hi) roll range (lo==hi for a fixed value), or {}
    if it grants none. 'to all Elemental Resistances' counts for all three;
    Maximum-resistance lines skip; several mods on one element add interval-wise."""
    out = {}
    for m in (explicits or []):
        if _MAX_RES_RE.search(m):
            continue
        rng = _roll_range(m)
        if rng is None:
            continue
        if _ALL_ELE_RE.search(m):
            elems = ("fire", "cold", "lightning")
        else:
            elems = tuple(e for e, rx in _RESIST_RE.items() if rx.search(m))
        for e in elems:
            lo, hi = out.get(e, (0.0, 0.0))
            out[e] = (lo + rng[0], hi + rng[1])
    return {e: (int(round(lo)), int(round(hi))) for e, (lo, hi) in out.items()}


# Resist cell tints — slight dark backgrounds over the dark rows, one hue per
# element (fire=red, cold=blue, lightning=yellow); kept subtle so the row striping
# still reads through. The collapsed 'all elements' case gets a neutral tint, since
# no single element owns it. (bg, fg) pairs.
RES_TINT = {
    "fire":      ("#3a2020", "#e6a0a0"),
    "cold":      ("#1e2742", "#9cc0ee"),
    "lightning": ("#3a3520", "#e6d79a"),
}
RES_ALL_TINT = ("#2b2b30", "#d0d0d0")


def resist_segments(explicits):
    """The armour resist cell as RowTable colour segments — a list of (text, bg, fg),
    one tinted box per element (Fire/Cold/Lightning), or [] when the piece grants
    none. Text mirrors the old text form: a fixed value is a plain int, a rolled
    range is "lo-hi" (no parens — the colour tint already sets the cell apart); an
    'all Elemental Resistances' piece (every element identical) collapses to a
    single neutral-tinted segment."""
    res = elemental_resists(explicits)
    if not res:
        return []
    def txt(lo, hi):
        return str(lo) if lo == hi else f"{lo}-{hi}"
    triad = [res.get(e, (0, 0)) for e in ("fire", "cold", "lightning")]
    if triad[0] == triad[1] == triad[2]:
        return [(txt(*triad[0]), RES_ALL_TINT[0], RES_ALL_TINT[1])]
    return [(txt(lo, hi), RES_TINT[e][0], RES_TINT[e][1])
            for e, (lo, hi) in zip(("fire", "cold", "lightning"), triad)]


def resist_total(explicits):
    """The resist cell as one number, for the header sort: the three elements'
    mid-rolls summed (a fixed value is its own mid; 'all elemental' counts three
    times, as it grants), or None when the piece grants none."""
    res = elemental_resists(explicits)
    if not res:
        return None
    return sum((lo + hi) / 2.0 for lo, hi in res.values())


# ---- boots: movement speed --------------------------------------------------
# Boots carry one more derived cell, MS, and it is PINNED for the whole group:
# movement speed is what a pair is judged by, so the column stays on even for the
# few uniques that grant none (they read "—") rather than appearing per item.
# Only the unconditional line counts — "(15-25)% increased Movement Speed" — so
# the number is what the boots always give; conditional riders ("per Frenzy
# Charge", "while affected by an Ailment", "when on Full Life", "at random when
# Hit") and "less Movement and Skill Speed" are excluded, the way the resist
# column excludes MAXIMUM-resistance lines. The regex therefore demands the mod
# be the roll and nothing else: no letters before the '%', nothing after "Speed".
# 'reduced' rolls are stored already-negative ("(-10--10)% reduced Movement
# Speed"), so the sign carries as-is.
_MS_RE = re.compile(r"^[^A-Za-z]*%\s*(?:increased|reduced)\s+Movement\s+Speed$", re.I)


def movement_speed(explicits):
    """The flat movement speed a boot grants, as an (lo, hi) percent roll range
    (lo == hi for a fixed roll), or None when it grants none. Several flat lines
    add interval-wise, as in elemental_resists()."""
    lo = hi = None
    for m in (explicits or []):
        if not _MS_RE.match(str(m).strip()):
            continue
        rng = _roll_range(m)
        if rng is None:
            continue
        lo = rng[0] if lo is None else lo + rng[0]
        hi = rng[1] if hi is None else hi + rng[1]
    if lo is None:
        return None
    return (int(round(lo)), int(round(hi)))


def ms_text(explicits):
    """The MS cell: "30" for a fixed roll, "10-20" for a range, "—" when the pair
    grants no movement speed at all (the column is pinned, so the cell has to say
    'none' rather than go blank)."""
    ms = movement_speed(explicits)
    if ms is None:
        return "—"
    lo, hi = ms
    return str(lo) if lo == hi else f"{lo}-{hi}"


def ms_total(explicits):
    """The MS cell as one number for the header sort — the mid-roll — or None for
    a pair that grants none (those trail either way)."""
    ms = movement_speed(explicits)
    return None if ms is None else (ms[0] + ms[1]) / 2.0


# Weapon DPS = average hit * attacks/sec. The damage props are ranges ("6-9"); the
# average is (lo+hi)/2, so DPS for a band = mid(range)*APS. Phys is its own band;
# "elemental" sums Fire/Cold/Lightning AND the pre-summed "Elemental Damage" key
# (poe2scout stores one OR the other, never both, so this can't double-count).
_ELE_DMG_KEYS = ("Fire Damage", "Cold Damage", "Lightning Damage", "Elemental Damage")


def _band_mid(text):
    """Average of a damage band: "6-9"->7.5, "1 or 102"->51.5, "5"->5.0, else None.
    Damage bands are non-negative "lo-hi" / "lo or hi"; we match UNSIGNED integers so
    the '-' stays a range separator (a signed regex would read "6-9" as 6 and -9)."""
    if text is None:
        return None
    nums = re.findall(r"\d+(?:\.\d+)?", str(text))
    try:
        vals = [float(n) for n in nums]
    except ValueError:
        return None
    if not vals:
        return None
    return (min(vals) + max(vals)) / 2.0


def weapon_dps(props):
    """(phys_dps, ele_dps) rounded ints, or (None, None) where a band/APS is absent
    so the cell stays blank rather than showing a bogus 0. ele sums every elemental
    damage band; APS-less rows (Sceptres/Wands with no attack) yield (None, None)."""
    props = props or {}
    aps = _hi_roll(props.get("Attacks per Second"))
    if not aps:
        return None, None
    phys = _band_mid(props.get("Physical Damage"))
    ele = sum(v for v in (_band_mid(props.get(k)) for k in _ELE_DMG_KEYS) if v)
    phys_dps = int(round(phys * aps)) if phys else None
    ele_dps = int(round(ele * aps)) if ele else None
    return phys_dps, ele_dps


class UniquesPanel(tk.Frame):
    def __init__(self, parent, tm=None):
        super().__init__(parent, bg=BG)
        self.tm = tm
        self.icons = IconCache()
        self._schema = None          # the league schema, resolved from the store on refresh
        self._league = None          # its display name, for the trade2 URL
        self._data = {}              # group -> {subgroup -> [item dict]}
        self._icon_urls = {}         # icon key -> url
        self._sel_meta = None        # chosen top-tier meta-category (Weapon/Armour/…)
        self._sel_group = None
        self._sel_sub = None
        self._sel_attrs = set()      # armour groups: chosen {Str,Dex,Int} (AND filter)
        self._attr_all = False       # armour groups: the neutral "All" mode
        self._sel_wscope = None      # weapons: scope chip (All/1H/2H/Melee/Range/Caster)
        self._sel_wtype = None       # weapons: archetype chip within scope, None = all
        self._row_item = {}          # tree rowid -> item dict (for the detail card)
        self._card = None            # the open detail-card Toplevel, or None
        self._sort = ColumnSort()    # header-click sort (col, direction); None = by level ↑
        self._titles = {}            # column -> base header text (the arrow paints over it)
        self._info_kind = None       # what c1/c2 mean now: res / dps / mod (_set_info_headers)
        self._sub_prefix = ""        # subtitle text before its "· by …" order note
        self._icon_q = queue.Queue()
        self._icon_active = 0
        self._icon_poll = None
        self._icon_gen = 0
        self._build()
        self._tag_widgets()
        self.refresh()

    def _conn(self):
        return psycopg2.connect(**config.DB_CONFIG)

    # ------------------------------------------------------------------
    # inspector tagging — point at this browser's parts, not just describe them.
    # The meta / group / base chip rows live in EquipmentNav now, which tags both
    # the row frames (uniques.groupbar / metabar / grouprow / basebar) and each chip
    # (`uniques.meta[<name>]`, `uniques.group[<name>]`, `uniques.base[<name>]`) under
    # the "uniques" prefix — so the stems are unchanged. The two data-coupled bars
    # still tag here: `uniques.wtype[<name>]` (weapon types, via _chip) and
    # `uniques.attr[<Str|Dex|Int|All>]` (in _attr_chip). No-op without a TiketMaster
    # handle, so the panel still runs standalone.
    def _tag_widgets(self):
        tm = self.tm
        if tm is None:
            return
        # groupbar / metabar / grouprow / basebar(.label) are tagged by EquipmentNav
        # itself (eid_prefix="uniques"), so the inspector stems are unchanged.
        tm.tag(self.list_title, "uniques.title")
        tm.tag(self.list_sub, "uniques.subtitle")
        tm.tag_table(self.tv, "uniques.tree", row_label=lambda t, r: t.item(r, "text"))

    # the chip rows are now the shared EquipmentNav body (meta → group → base); this
    # panel keeps only the weapon scope rows and the Str/Dex/Int attribute toggles
    # (data-coupled to the unique DB), plugged in as leaf-bar hooks. sub_bar /
    # sub_label proxy the nav's so the weapon/attr builders read unchanged.
    @property
    def sub_bar(self):
        return self.nav.sub_bar

    @property
    def sub_label(self):
        return self.nav.sub_label

    def _chip(self, parent, text, active, cmd, eid=None):
        """Shared-styling chip (delegates to the nav) — used by the weapon scope
        rows; the attr toggles use the coloured _attr_chip instead."""
        return self.nav.make_chip(parent, text, active, cmd, eid_key=eid)

    # ------------------------------------------------------------------ build
    def _build(self):
        # The fixed meta row, the chosen meta's group row and the base bar are the
        # shared EquipmentNav (same body as the Trade tab). It drives FILTER here:
        # _on_meta/_on_group/_on_leaf react against the local unique DB, and the
        # Weapons / armour groups hand their base bar to the hooks below.
        self.nav = EquipmentNav(
            self, tm=self.tm, eid_prefix="uniques",
            on_meta=self._on_meta, on_group=self._on_group, on_leaf=self._on_leaf,
            leaf_hooks={"weapon": self._weapon_hook, "attr": self._attr_hook})
        self.nav.pack(fill="x")

        outer = tk.Frame(self, bg=BORDER)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=BG2)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        head = tk.Frame(inner, bg=BG3)
        head.pack(fill="x")
        self.list_title = tk.Label(head, text="Uniques", bg=BG3, fg=FG,
                                   font=("Segoe UI", 9, "bold"))
        self.list_title.pack(side="left", padx=8, pady=4)
        self.list_sub = tk.Label(head, text="", bg=BG3, fg=FG_DIM, font=("Consolas", 8))
        self.list_sub.pack(side="right", padx=8)

        bodyf = tk.Frame(inner, bg=BG2)
        bodyf.pack(fill="both", expand=True, padx=6, pady=(4, 6))
        # Two derived "info" columns (base/mod dropped): their meaning depends on the
        # selected group and is set by _set_info_headers() — armour shows elemental
        # resistances (fire cold lightning) in c1; weapons show phys/ele DPS in c1/c2;
        # everything else falls back to the first mod in c1. Headers default to mod.
        self.tv = RowTable(bodyf, ["lvl", "c1", "c2"],
                           rowheight=26, icon_w=ICON_SIZE)
        self._retitle("#0", "unique")
        self.tv.column("#0", width=210, minwidth=120, anchor="w", stretch=True)
        self._retitle("lvl", "lvl")
        self.tv.column("lvl", width=42, anchor="e", stretch=False)
        self._retitle("c1", "mod")
        self.tv.column("c1", width=330, anchor="w", stretch=True)
        self._retitle("c2", "")
        self.tv.column("c2", width=72, anchor="e", stretch=False)
        # a header click sorts the list by that column: first high → low, the
        # next click flips (the name column opens A → Z) — see _sort_click
        for c in ("#0", "lvl", "c1", "c2"):
            self.tv.heading(c, command=lambda c=c: self._sort_click(c))
        sb = ttk.Scrollbar(bodyf, orient="vertical", command=self.tv.yview,
                           style="Subtle.Vertical.TScrollbar")
        self.tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tv.pack(side="left", fill="both", expand=True)
        self.tv.tag_configure("even", background=STRIPE_A)
        self.tv.tag_configure("odd", background=STRIPE_B)
        self.tv.tag_configure("hover", background=HOVER_BG)
        self.tv.tag_configure("section", background="#37373d", foreground=FG)
        self.tv._hover_row = ""
        self.tv._hover_tags = ()
        self.tv.bind("<Motion>", self._hover_motion)
        self.tv.bind("<Leave>", lambda e: self._hover_restore(self.tv))
        self.tv.bind("<Button-1>", self._on_row_click, add="+")   # row -> detail card
        # A double click opens trade2 for that unique. Tk fires the more specific
        # <Double-Button-1> INSTEAD of <Button-1> for the second press, so the
        # card from the first press is all that opens — this closes it again.
        self.tv.bind("<Double-Button-1>", self._on_row_double, add="+")
        # (Ctrl+click stays the inspector capture: it fires <Control-Button-1>, the
        #  more specific binding, so this plain handler does not run for it.)
        self.ph = tk.Label(bodyf, text="", bg=BG3, fg=FG_MUTED,
                           font=("Segoe UI", 9), justify="center")

    # ------------------------------------------------------------------- data
    def refresh(self):
        conn = None
        try:
            conn = self._conn()
            cur = conn.cursor()
            try:
                self._schema = derive.resolve_schema(cur)   # the league with the freshest pairs
                cur.execute(f"select league from {self._schema}.league limit 1")
                row = cur.fetchone()
                self._league = row[0] if row else None       # for the trade2 URL
                cur.execute(
                    f"""select unique_item_id, name, base_type, category_api_id,
                               icon_url, (requirements->>'Level') as lvl,
                               explicit_mods, implicit_mods, metadata->'properties',
                               requirements, flavour_text
                        from {self._schema}.unique_item""")
                rows = cur.fetchall()
            except Exception:
                rows = []
            self._index(rows)
            self._render_nav()
        finally:
            if conn is not None:
                conn.close()

    def _index(self, rows):
        data, urls = {}, {}
        for uid, name, base, cat, icon, lvl, emods, imods, props, req, flav in rows:
            group, sub = classify(cat, base, props)
            key = f"u{uid}"
            urls[key] = icon
            try:
                lvl_i = int(lvl) if lvl is not None else None
            except (TypeError, ValueError):
                lvl_i = None
            mods = emods or imods or []
            # everything the detail card needs is kept on the item (446 rows, cheap),
            # so a row click renders with no extra query -- see build_sections().
            item = {"key": key, "name": name, "base": base or "",
                    "lvl": lvl_i, "mod": (mods[0] if mods else ""),
                    "attrs": attr_set(req, props),
                    "req": req, "implicits": imods, "explicits": emods,
                    "flavour": flav, "props": props}
            if group == "Weapons":
                item["hand"], item["wtype"] = weapon_hand_and_type(sub)
                item["wclass"] = sub
            data.setdefault(group, {}).setdefault(sub, []).append(item)
        for g in data:
            for s in data[g]:
                data[g][s].sort(key=_lvl_key)
        self._data, self._icon_urls = data, urls

    def _ordered_groups(self):
        known = [g for g in _GROUP_ORDER if g in self._data]
        rest = sorted(g for g in self._data if g not in _GROUP_ORDER)
        return known + rest

    def _meta_members(self, meta):
        """The present groups of *meta*, in _GROUP_ORDER. 'Others' also collects any
        group not named in _META_GROUPS, so unknown groups stay reachable."""
        ordered = self._ordered_groups()
        return [g for g in ordered if meta_of(g) == meta]

    def _meta_count(self, meta):
        return sum(sum(len(v) for v in self._data[g].values())
                   for g in self._meta_members(meta))

    def _render_nav(self):
        """(Re)build the chip navigator from the unique DB and reset the list to its
        hint. The nav owns the chip rows now; this just feeds it the taxonomy and
        handles the empty-DB case (no metas)."""
        self._sel_group = None
        metas = self._build_taxonomy()
        self.nav.set_taxonomy(metas)
        self._fill_items([])
        if not metas:
            self._show_ph(f"no uniques in {self._schema or 'the store'}\nrun  ⇊ Actualize")
        else:
            self._show_ph("выбери категорию ↑")

    def _build_taxonomy(self):
        """unique DB -> the EquipmentNav taxonomy: present metas (Weapon / Offhands /
        Armour / Others) → their member groups → base chips. The Weapons group and
        the armour (attribute) groups carry a ``leaf_mode`` so the nav hands their
        base bar to _weapon_hook / _attr_hook; every other group lists base-name
        leaves whose ``value`` is ("sub", group, base) for _on_leaf to fill from."""
        metas = []
        for m in _META_ORDER:
            members = self._meta_members(m)
            if not members:
                continue
            groups = []
            for g in members:
                gcount = sum(len(v) for v in self._data[g].values())
                if g == "Weapons":
                    groups.append({"key": g, "label": g, "count": gcount,
                                   "leaf_mode": "weapon"})
                elif g in _ATTR_GROUPS:
                    groups.append({"key": g, "label": g, "count": gcount,
                                   "leaf_mode": "attr"})
                else:
                    subs = self._data.get(g, {})
                    order = ["All"] + [s for s, _ in sorted(
                        subs.items(),
                        key=lambda kv: (-len(kv[1]), kv[0]))][:_MAX_SUBCHIPS]
                    leaves = [{"key": s, "label": s,
                               "count": (gcount if s == "All" else len(subs[s])) or 0,
                               "value": ("sub", g, s)} for s in order]
                    groups.append({"key": g, "label": g, "count": gcount,
                                   "children": leaves})
            metas.append({"key": m, "label": m, "count": self._meta_count(m),
                          "children": groups})
        return metas

    # ---- nav callbacks: a pick FILTERS the local unique list -------------------
    def _on_meta(self, meta):
        """Multi-member meta picked: wait for a group. (Single-member metas auto-open
        their group's base bar, so _on_group handles those.)"""
        if len(meta.get("children") or []) > 1:
            self._fill_items([])
            self._show_ph("← выбери категорию")

    def _on_group(self, group):
        """Group picked: retitle the info columns and prime the base bar. Weapons and
        the armour attribute groups are built by their hooks (the nav calls them right
        after this); plain groups get a label + 'pick a base' hint while the nav
        renders their base chips."""
        g = group["key"]
        self._sel_group = g
        self._sel_sub = None
        self._sel_attrs = set()
        self._attr_all = False
        self._sel_wscope = None
        self._sel_wtype = None
        self._set_info_headers(g)                 # retitle c1/c2 for this group's kind
        if g == "Weapons":
            # the scope/type chips speak for themselves — no text label
            self.nav.hide_sub_label()
        elif g in _ATTR_GROUPS:                    # armour: Str/Dex/Int, not base names
            self.nav.set_sub_label(f"Filter by attribute   ·   {g.upper()}"
                                   f"   ·   multi-select, Str+Dex = has both")
            self._fill_items([])
            self._show_ph("← Str · Dex · Int  (можно несколько)     или  All")
        else:
            self.nav.set_sub_label(f"Choose a base   ·   {g.upper()}")
            self._fill_items([])
            self._show_ph("← выбери базу")

    def _on_leaf(self, leaf):
        """A base chip picked (plain groups only — weapon/attr drive their own list):
        fill the unique list for (group, base). leaf value = ("sub", group, base)."""
        value = leaf.get("value")
        if not value or value[0] != "sub":
            return
        _, group, sub = value
        self._sel_sub = sub
        subs = self._data.get(group, {})
        if sub == "All":
            items = [it for lst in subs.values() for it in lst]
            items.sort(key=_lvl_key)
        else:
            items = subs.get(sub, [])
        self.list_title.config(text=f"{group} · {sub}")
        self._set_sub(f"{len(items)} uniques")
        self._fill_items(items)

    # ---- leaf-bar hooks: the two data-coupled base bars (built into nav.sub_bar) --
    def _weapon_hook(self, nav, parent, group):
        """Weapons base bar: the two-row scope/type weapon picker. Opens on the 'All'
        scope so the list shows at once (matches the old _select_group path)."""
        self._sel_wscope = "All"
        self._sel_wtype = None
        self._build_weapon_chips()
        self._show_weapons()

    def _attr_hook(self, nav, parent, group):
        """Armour base bar: the Str / Dex / Int (+ All) attribute toggles."""
        self._sel_attrs = set()
        self._attr_all = False
        self._build_attr_chips(group["key"])

    # ---- weapons: two-row base bar (scope row + archetype row) -----------------
    # Row 1 is the scope: hand split (All/1H/2H) AND category split (Melee/Range/
    # Caster), counted independently. Row 2 is the archetypes (Mace/Wand/Axe/…)
    # PRESENT IN the chosen scope, so it narrows the weapons selected above. State:
    # _sel_wscope (a row-1 chip) and _sel_wtype (a row-2 chip, None = the whole scope).
    def _scope_weapons(self, scope):
        """The weapons in *scope*: a hand filter (1H/2H), a category filter (Melee/
        Range/Caster), or everything (All)."""
        weapons = self._group_items("Weapons")
        if scope in ("1H", "2H"):
            return [it for it in weapons if it.get("hand") == scope]
        if scope in ("Melee", "Range", "Caster"):
            return [it for it in weapons
                    if weapon_category(it.get("wtype") or "") == scope]
        return weapons

    def _wscope_counts(self):
        weapons = self._group_items("Weapons")
        out = {"All": len(weapons)}
        for s in _WSCOPE_ORDER[1:]:
            out[s] = len(self._scope_weapons(s))
        return out

    def _build_weapon_chips(self):
        """Build the two weapon rows fresh: row 1 = the fixed scope chips, row 2 =
        the archetypes within the current scope (rebuilt by _build_wtype_chips)."""
        for w in self.sub_bar.winfo_children():
            w.destroy()
        self._wscope_row = tk.Frame(self.sub_bar, bg=BG2)
        self._wscope_row.pack(fill="x", anchor="w")
        self._wtype_row = tk.Frame(self.sub_bar, bg=BG2)
        self._wtype_row.pack(fill="x", anchor="w", pady=(4, 0))
        counts = self._wscope_counts()
        for i, s in enumerate(_WSCOPE_ORDER):
            chip = self._chip(self._wscope_row, f"{s}  {counts.get(s, 0)}",
                              self._sel_wscope == s,
                              lambda ss=s: self._select_wscope(ss),
                              eid=f"uniques.base[{s}]")
            chip.grid(row=0, column=i, padx=2, pady=2, sticky="w")
        self._build_wtype_chips()

    def _build_wtype_chips(self):
        """Row 2: one archetype chip per short weapon type present in the current
        scope, each with its count. No 'All' chip — the active scope chip in row 1
        already IS "all of this scope"; clicking the live archetype again clears it."""
        for w in self._wtype_row.winfo_children():
            w.destroy()
        weapons = self._scope_weapons(self._sel_wscope)
        buckets = {}
        for it in weapons:
            buckets.setdefault(it.get("wtype") or "Other", []).append(it)
        order = sorted(buckets, key=lambda k: (-len(buckets[k]), k))
        for i, t in enumerate(order):
            chip = self._chip(self._wtype_row, f"{t}  {len(buckets[t])}",
                              self._sel_wtype == t,
                              lambda tt=t: self._select_wtype(tt),
                              eid=f"uniques.wtype[{t}]")
            chip.grid(row=i // 12, column=i % 12, padx=2, pady=2, sticky="w")

    def _select_wscope(self, scope):
        """Row-1 pick: set the scope, reset the archetype filter, rebuild row 2."""
        self._sel_wscope = scope
        self._sel_wtype = None
        for w in self._wscope_row.winfo_children():
            on = w.cget("text").rsplit("  ", 1)[0] == scope
            w._active = on
            w.config(bg=DULL_GRN if on else BG3, fg=FG if on else FG_DIM)
        self._build_wtype_chips()
        self._show_weapons()

    def _select_wtype(self, wtype):
        """Row-2 pick: narrow the scope to one archetype; clicking the active chip
        again clears it (back to the whole scope, the sectioned view)."""
        self._sel_wtype = None if self._sel_wtype == wtype else wtype
        for w in self._wtype_row.winfo_children():
            on = w.cget("text").rsplit("  ", 1)[0] == self._sel_wtype
            w._active = on
            w.config(bg=DULL_GRN if on else BG3, fg=FG if on else FG_DIM)
        self._show_weapons()

    def _show_weapons(self):
        """Render the weapon list for the current (scope, wtype): no archetype -> the
        sectioned view grouped by archetype; an archetype -> that type's flat list."""
        weapons = self._scope_weapons(self._sel_wscope)
        scope = self._sel_wscope
        if self._sel_wtype is None:
            buckets = {}
            for it in weapons:
                buckets.setdefault(it.get("wtype") or "Other", []).append(it)
            sections = []
            for label in sorted(buckets, key=lambda k: (-len(buckets[k]), k)):
                items = sorted(buckets[label],
                               key=_lvl_key)
                sections.append((label, items))
            total = sum(len(it) for _, it in sections)
            self.list_title.config(text=f"Weapons · {scope}")
            self._set_sub(f"{total} uniques · {len(sections)} types")
            self._fill_sectioned(sections)
        else:
            items = [it for it in weapons
                     if (it.get("wtype") or "Other") == self._sel_wtype]
            items.sort(key=_lvl_key)
            self.list_title.config(text=f"Weapons · {scope} · {self._sel_wtype}")
            self._set_sub(f"{len(items)} uniques")
            self._fill_items(items)

    # ------------------------------------------------------------- attribute filter
    def _group_items(self, group):
        """Every item in *group*, flattened across its subgroups."""
        return [it for lst in self._data.get(group, {}).values() for it in lst]

    def _attr_chip(self, parent, text, attr, active, cmd, eid=None):
        """A colored toggle for the Str/Dex/Int filter. *attr* in {Str,Dex,Int}
        carries a hue (vivid when selected, dim tint + pale text when not); *attr*
        None is the neutral 'All' chip (green active style, like the base chips).
        Hover previews the selected look so the off-state stays clearly off."""
        if attr is None:
            on_bg, off_bg, on_fg, off_fg = DULL_GRN, BG3, FG, FG_DIM
        else:
            on_bg, off_bg, off_fg = ATTR_COLORS[attr]
            on_fg = ATTR_FG_ON
        lab = tk.Label(parent, text=text, font=("Segoe UI", 9), padx=10, pady=4,
                       cursor="hand2", bg=(on_bg if active else off_bg),
                       fg=(on_fg if active else off_fg))
        lab._active = active
        lab.bind("<Button-1>", lambda e: cmd())
        lab.bind("<Enter>", lambda e: None if lab._active else lab.config(bg=on_bg, fg=on_fg))
        lab.bind("<Leave>", lambda e: None if lab._active else lab.config(bg=off_bg, fg=off_fg))
        if self.tm is not None and eid:          # greppable: uniques.attr[] -> here
            self.tm.tag(lab, eid)
        return lab

    def _build_attr_chips(self, group):
        """(Re)build the All · Str · Dex · Int row reflecting the current selection.
        Per-attribute counts are standalone (how many items in the group have that
        attribute), so they stay stable as the multi-select set changes."""
        for w in self.sub_bar.winfo_children():
            w.destroy()
        items = self._group_items(group)
        cnt = {a: sum(1 for it in items if a in it["attrs"]) for a in _ATTRS}
        specs = [("All", None, len(items), self._select_attr_all, self._attr_all)]
        specs += [(a, a, cnt[a], (lambda aa=a: self._toggle_attr(aa)), a in self._sel_attrs)
                  for a in _ATTRS]
        for i, (text, attr, n, cmd, active) in enumerate(specs):
            chip = self._attr_chip(self.sub_bar, f"{text}  {n}", attr, active, cmd,
                                   eid=f"uniques.attr[{text}]")
            chip.grid(row=0, column=i, padx=2, pady=2, sticky="w")

    def _select_attr_all(self):
        self._attr_all = True
        self._sel_attrs = set()
        self._build_attr_chips(self._sel_group)
        self._apply_attr_filter()

    def _toggle_attr(self, attr):
        self._sel_attrs.symmetric_difference_update({attr})   # toggle membership
        self._attr_all = False
        self._build_attr_chips(self._sel_group)
        self._apply_attr_filter()

    def _apply_attr_filter(self):
        """Show the group's items under the current attribute selection: 'All' = no
        filter; one or more attrs = bases that have ALL of them (sel ⊆ item.attrs);
        nothing selected = back to the hint."""
        group = self._sel_group
        items = self._group_items(group)
        if self._attr_all:
            sel, label = items, "All"
        elif self._sel_attrs:
            sel = [it for it in items if self._sel_attrs <= it["attrs"]]
            label = "+".join(a for a in _ATTRS if a in self._sel_attrs)
        else:
            self._fill_items([])
            self.list_title.config(text=group)
            self._set_sub("")
            self._show_ph("← Str · Dex · Int  (можно несколько)     или  All")
            return
        sel = sorted(sel, key=_lvl_key)
        self.list_title.config(text=f"{group} · {label}")
        self._set_sub(f"{len(sel)} uniques")
        self._fill_items(sel)

    # ------------------------------------------------------------------ items
    # The two info columns are filled by item kind: armour -> elemental resists in c1
    # ("fire cold lightning"), weapons -> phys DPS in c1 / ele DPS in c2, everything
    # else -> first mod in c1. Boots additionally pin MS into c2 (see movement_speed).
    # _set_info_headers retitles them for the chosen group; _info_cells turns one item
    # into its (c1, c2). A weapon DPS of None reads blank.
    def _set_info_headers(self, group):
        """Retitle the two info columns for *group*: armour -> resist triad header in
        c1; BOOTS keep that and add the pinned 'ms' column in c2; weapons ->
        'phys'/'ele' DPS; otherwise the plain 'mod' fallback. When the columns
        change MEANING (res / res+ms / dps / mod) a header sort on them drops
        back to the level order; the name / lvl sorts carry over."""
        kind = ("res+ms" if group == "Boots" else
                "res" if group in _ATTR_GROUPS else
                "dps" if group == "Weapons" else "mod")
        if kind != self._info_kind and self._sort.col in ("c1", "c2"):
            self._sort.reset()
        self._info_kind = kind
        if group in _ATTR_GROUPS:                 # armour: elemental resistances
            self._retitle("c1", "res  (f c l)")
            self.tv.column("c1", width=160, anchor="w", stretch=False)
            # boots always show movement speed, even the pairs that grant none
            self._retitle("c2", "ms" if group == "Boots" else "")
            self.tv.column("c2", width=(56 if group == "Boots" else 0),
                           anchor="e", stretch=False)
        elif group == "Weapons":
            self._retitle("c1", "phys dps")
            self.tv.column("c1", width=84, anchor="e", stretch=False)
            self._retitle("c2", "ele dps")
            self.tv.column("c2", width=84, anchor="e", stretch=False)
        else:
            self._retitle("c1", "mod")
            self.tv.column("c1", width=330, anchor="w", stretch=True)
            self._retitle("c2", "")
            self.tv.column("c2", width=0, stretch=False)
        self._paint_headers()

    def _info_cells(self, it):
        """(c1, c2) for *it* by kind: armour -> ('16 0 10', ''), boots -> that plus
        the MS cell ('25' / '10-20' / '—') in c2; weapon -> ('123', '88') phys/ele
        DPS; other -> (first-mod, ''). Driven off the selected group so an item
        shows the same two values whichever sub-filter surfaced it."""
        group = self._sel_group
        if group in _ATTR_GROUPS:
            return (resist_segments(it.get("explicits")),
                    ms_text(it.get("explicits")) if group == "Boots" else "")
        if group == "Weapons":
            phys, ele = weapon_dps(it.get("props"))
            return ("" if phys is None else str(phys),
                    "" if ele is None else str(ele))
        return it.get("mod", ""), ""

    # ---- header sort: click a column -> high → low, click again -> low → high --
    # ColumnSort holds (column, direction). The keys below read the raw item, so
    # the order is numeric, not the cell text; _reorder moves the rows in place
    # (per section in the grouped weapon view). No active column = the level
    # order the fills arrive in ("by level ↑").
    def _retitle(self, col, text):
        """Set a column's base header text; the sort arrow is painted over it."""
        self._titles[col] = text
        self.tv.heading(col, text=self._sort.title(col, text))

    def _paint_headers(self):
        for c, base in self._titles.items():
            self.tv.heading(c, text=self._sort.title(c, base))

    def _sort_key(self, col):
        """item -> raw sort value for header column *col* under the selected group:
        name (text) · level (none = 0, as in _lvl_key) · the info columns by kind
        -- armour: total elemental resist (mid-rolls summed), boots also movement
        speed on c2; weapons: phys / ele DPS; otherwise the first mod's text.
        None = no value (such rows trail)."""
        if col == "#0":
            return lambda it: it["name"].lower()
        if col == "lvl":
            return lambda it: it["lvl"] or 0
        group = self._sel_group
        if group in _ATTR_GROUPS:
            if col == "c1":
                return lambda it: resist_total(it.get("explicits"))
            if col == "c2" and group == "Boots":
                return lambda it: ms_total(it.get("explicits"))
        elif group == "Weapons":
            i = 0 if col == "c1" else 1
            return lambda it: weapon_dps(it.get("props"))[i]
        elif col == "c1":
            return lambda it: (it.get("mod") or "").lower() or None
        return lambda it: None                   # a hidden / empty column

    def _sort_click(self, col):
        self._sort.click(col)
        self._reorder()
        self._set_sub(self._sub_prefix)

    def _reorder(self):
        """Apply the active header sort to the rows on screen, in place: rows move
        (their icons and _row_item entries ride along), within each section when
        the view is grouped; the stripe is redone in the new order; the active
        header gets its arrow. With no active column the fill order stands."""
        tv = self.tv
        if self._sort.col is not None:
            self._hover_restore(tv)
            self._close_card()
            key = self._sort_key(self._sort.col)
            parents = [""] + [r for r in tv.get_children("") if r not in self._row_item]
            for parent in parents:
                kids = [r for r in tv.get_children(parent) if r in self._row_item]
                kids = self._sort.order(kids, lambda r: key(self._row_item[r]))
                for i, r in enumerate(kids):
                    tv.move(r, parent, i)
                    tv.item(r, tags=("even" if i % 2 == 0 else "odd",))
        self._paint_headers()

    def _sort_label(self):
        """The subtitle's order note: 'by level ↑' (the default) or the active
        header, e.g. 'by phys dps ↓'."""
        s = self._sort
        if s.col is None:
            return "by level ↑"
        name = "name" if s.col == "#0" else self._titles.get(s.col, s.col)
        return f"by {name} {s.mark}"

    def _set_sub(self, prefix):
        """Subtitle = *prefix* · the order note; the prefix is kept so a header
        click can re-render it with the new order."""
        self._sub_prefix = prefix
        self.list_sub.config(text=f"{prefix} · {self._sort_label()}" if prefix else "")

    def _fill_items(self, items):
        self._hover_restore(self.tv)
        self._close_card()
        self.tv.delete(*self.tv.get_children())
        self._row_item = {}
        ids = []
        for i, it in enumerate(items):
            lvl = "—" if it["lvl"] is None else str(it["lvl"])
            c1, c2 = self._info_cells(it)
            rid = self.tv.insert("", "end", text=it["name"],
                                 values=(lvl, c1, c2),
                                 tags=("even" if i % 2 == 0 else "odd",))
            ids.append((rid, it["key"]))
            self._row_item[rid] = it
        self._reorder()                       # the header sort, if one is active
        if items:
            self.ph.place_forget()
        else:
            self._show_ph("nothing here")
        self._load_icons(ids)

    def _fill_sectioned(self, sections):
        """Grouped view: one parent header row per section, items nested under it."""
        self._hover_restore(self.tv)
        self._close_card()
        self.tv.delete(*self.tv.get_children())
        self._row_item = {}
        ids = []
        for label, items in sections:
            pid = self.tv.insert("", "end", text=f"{label}   ({len(items)})",
                                 open=True, tags=("section",))
            for i, it in enumerate(items):
                lvl = "—" if it["lvl"] is None else str(it["lvl"])
                c1, c2 = self._info_cells(it)
                rid = self.tv.insert(pid, "end", text=it["name"],
                                     values=(lvl, c1, c2),
                                     tags=("even" if i % 2 == 0 else "odd",))
                ids.append((rid, it["key"]))
                self._row_item[rid] = it          # section header rows excluded
        self._reorder()                       # the header sort, if one is active
        if sections:
            self.ph.place_forget()
        else:
            self._show_ph("nothing here")
        self._load_icons(ids)

    def _show_ph(self, text):
        self.ph.config(text=text)
        self.ph.place(relx=0.5, rely=0.42, anchor="center")

    # ----------------------------------------------------------- detail card
    def _on_row_click(self, ev):
        """A click on a real item row pops the in-game-style detail card. Section
        header rows (weapon view) aren't in _row_item, so they're ignored."""
        it = self._row_item.get(self.tv.identify_row(ev.y))
        if it is not None:
            self._show_card(it, ev)

    def _on_row_double(self, ev):
        """Double click on an item row -> a trade2 tab for that unique."""
        it = self._row_item.get(self.tv.identify_row(ev.y))
        if it is not None:
            self._close_card()              # the first click of the pair opened it
            self._open_trade(it)

    def _open_trade(self, it):
        """Open trade2 for one unique, off the UI thread.

        The league display name is read from the store on refresh; without it
        (an empty store) `open_preset` resolves it from poe2scout itself, which
        is exactly why this runs in a thread — that is a network call, and so is
        the browser hand-off."""
        name, league = it.get("name"), self._league

        def work():
            try:
                trade.open_preset(trade.unique_preset(name), league)
            except Exception:
                traceback.print_exc()       # surfaces in the terminal log

        threading.Thread(target=work, daemon=True).start()

    def _close_card(self):
        if self._card is not None:
            self._dismiss(self._card)

    def _dismiss(self, card):
        """Destroy *this* card. Closing is bound per-card (not via self._card) so a
        dying card's late FocusOut can't tear down a freshly-opened replacement."""
        try:
            card.destroy()
        except Exception:
            pass
        if self._card is card:
            self._card = None

    def _show_card(self, it, ev):
        """A frameless tooltip near the cursor, on the cursor's monitor, built by
        build_sections(). Closes on Esc / click / losing focus; a new row replaces
        it. Topmost, like the app."""
        self._close_card()
        secs = build_sections(it["name"], it["base"], it.get("req"),
                              it.get("implicits"), it.get("explicits"),
                              it.get("flavour"), it.get("props"))
        card = tk.Toplevel(self)
        card.overrideredirect(True)
        card.attributes("-topmost", True)
        card.configure(bg=BORDER)                         # 1px hairline
        inner = tk.Frame(card, bg=BG2)
        inner.pack(padx=1, pady=1)
        pad = tk.Frame(inner, bg=BG2)
        pad.pack(padx=12, pady=10)
        self._render_card(pad, secs, it)
        self._card = card

        card.update_idletasks()                           # measure before placing
        cw, ch = card.winfo_reqwidth(), card.winfo_reqheight()
        # Keep it on the monitor under the cursor (= the one cx is on: the click
        # was inside cx). Tk's winfo_screenwidth/height describe the PRIMARY
        # display only, so clamping against them dragged a card opened on the
        # second monitor back onto the first, at an x that moved with the card's
        # width. The old metrics stay as the fallback when the Win32 query fails.
        area = work_area_at(ev.x_root, ev.y_root)
        if area is None:
            area = (0, 0, card.winfo_screenwidth(), card.winfo_screenheight())
        left, top, right, bottom = area
        x = max(left + 8, min(ev.x_root + 16, right - cw - 8))
        y = max(top + 8, min(ev.y_root + 10, bottom - ch - 8))
        card.geometry(f"+{x}+{y}")
        card.bind("<Escape>", lambda e: self._dismiss(card))
        card.bind("<Button-1>", lambda e: self._dismiss(card))
        card.bind("<FocusOut>", lambda e: self._dismiss(card))  # click elsewhere closes
        card.focus_force()

    def _card_icon(self, it, size=44):
        """A PhotoImage for the card header (disk-cached; created on the main
        thread here, which is fine for a single icon), or None."""
        key = it.get("key")
        try:
            pil = self.icons.get_pil(key, self._icon_urls.get(key), size)
            if pil is not None:
                return self.icons.get_photo(key, size, pil, self)
        except Exception:
            pass
        return None

    def _render_card(self, pad, secs, it):
        """Lay out the (style, text) sections: icon + name/base header, then a
        divider-separated body (class, props, requirements, mods, flavour)."""
        WRAP = 340
        # header: icon (left) + name / base (stacked) — the leading name[, base]
        head = secs[0:2] if len(secs) > 1 and secs[1][0] == "base" else secs[0:1]
        body = secs[len(head):]
        hf = tk.Frame(pad, bg=BG2)
        hf.pack(fill="x", anchor="w")
        ph = self._card_icon(it)
        if ph is not None:
            ic = tk.Label(hf, image=ph, bg=BG2)
            ic.image = ph                                 # hold a ref (belt & braces)
            ic.pack(side="left", padx=(0, 8))
        ht = tk.Frame(hf, bg=BG2)
        ht.pack(side="left", fill="x", expand=True)
        for style, text in head:
            tk.Label(ht, text=text, bg=BG2,
                     fg=(CARD_GOLD if style == "name" else FG_DIM),
                     font=("Segoe UI", 12, "bold") if style == "name"
                     else ("Segoe UI", 9),
                     anchor="w", justify="left").pack(anchor="w")
        spec = {                                          # style -> (fg, font, wrap)
            "class":    (FG_DIM,    ("Segoe UI", 9), 0),
            "prop":     (CARD_VAL,  ("Consolas", 9), 0),
            "req":      (FG,        ("Segoe UI", 9), 0),
            "implicit": (CARD_IMPL, ("Segoe UI", 9), WRAP),
            "explicit": (CARD_MOD,  ("Segoe UI", 9), WRAP),
            "flavour":  (FG_MUTED,  ("Segoe UI", 9, "italic"), WRAP),
        }
        for style, text in body:
            if style == "sep":
                tk.Frame(pad, bg=BORDER, height=1).pack(fill="x", pady=5)
                continue
            fg, font, wrap = spec[style]
            tk.Label(pad, text=text, bg=BG2, fg=fg, font=font, wraplength=wrap or 0,
                     justify="center", anchor="center").pack(fill="x")
        # …and the one action the card offers: this unique on trade2 (the same
        # hand-off the row's double click uses). The card dismisses itself on any
        # click — a child's own binding runs before the toplevel's, so the tab is
        # opened first and the card closing after is the wanted feedback.
        tk.Frame(pad, bg=BORDER, height=1).pack(fill="x", pady=5)
        btn = tk.Label(pad, text="↗ Open trade2", bg=BG3, fg=FG,
                       font=("Segoe UI", 9), cursor="hand2", pady=3)
        btn.pack(fill="x")
        btn.bind("<Button-1>", lambda e, i=it: self._open_trade(i))
        btn.bind("<Enter>", lambda e: btn.config(bg=HOVER_BG))
        btn.bind("<Leave>", lambda e: btn.config(bg=BG3))
        if self.tm is not None:
            self.tm.tag(btn, "uniques.card.trade")

    # ------------------------------------------------------------------ hover
    def _hover_motion(self, ev):
        tv = ev.widget
        rid = tv.identify_row(ev.y)
        if rid == getattr(tv, "_hover_row", ""):
            return
        self._hover_restore(tv)
        if rid:
            tv._hover_row = rid
            tv._hover_tags = tv.item(rid, "tags")
            tv.item(rid, tags=("hover",))

    @staticmethod
    def _hover_restore(tv):
        rid = getattr(tv, "_hover_row", "")
        if rid and tv.exists(rid):
            tv.item(rid, tags=getattr(tv, "_hover_tags", ()))
        tv._hover_row = ""
        tv._hover_tags = ()

    # ------------------------------------------------------------------ icons
    def _ensure_poll(self):
        if self._icon_poll is None:
            self._icon_poll = self.after(60, self._drain_icons)

    def _drain_icons(self):
        self._icon_poll = None
        try:
            while True:
                kind, *rest = self._icon_q.get_nowait()
                if kind == "row":
                    rid, key, pil, gen = rest
                    if gen == self._icon_gen and self.tv.exists(rid):
                        try:
                            ph = self.icons.get_photo(key, ICON_SIZE, pil, self)
                            self.tv.item(rid, image=ph)
                        except tk.TclError:
                            pass
                elif kind == "done":
                    self._icon_active = max(0, self._icon_active - 1)
        except queue.Empty:
            pass
        if self._icon_active > 0:
            self._icon_poll = self.after(60, self._drain_icons)

    def _load_icons(self, ids):
        if not ids:
            return
        self._icon_gen += 1
        gen = self._icon_gen
        urls = self._icon_urls
        items = list(ids)
        self._icon_active += 1
        self._ensure_poll()

        def work():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            try:
                with ThreadPoolExecutor(max_workers=8) as ex:
                    futs = {ex.submit(self.icons.get_pil, key, urls.get(key), ICON_SIZE):
                            (rid, key) for rid, key in items}
                    for fut in as_completed(futs):
                        rid, key = futs[fut]
                        try:
                            pil = fut.result()
                        except Exception:
                            pil = None
                        if pil is not None:
                            self._icon_q.put(("row", rid, key, pil, gen))
            finally:
                self._icon_q.put(("done",))

        threading.Thread(target=work, daemon=True).start()
