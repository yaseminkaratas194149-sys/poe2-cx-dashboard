"""UniquesPanel — a unique-item browser in the cx visual language.

Drill-down like the poe2scout item browser: a row of global-category chips → a
row of base chips for the chosen category → the uniques in that base, sorted by
required level (low → high), each with its icon, level and a mod.

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

Data: cx_<league>.unique_item (pulled by `python -m cx.uniques`). The
category/base taxonomy is DERIVED from poe2scout's coarse category + the base
type via the heuristic classify() below — poe2scout's unique data has only 7
coarse categories, so the screenshot-style split (Body Armours / Helmets / Bow /
Staff …) is reconstructed from base-type keywords. Tune the tables freely.
"""
import queue
import threading
import tkinter as tk
from tkinter import ttk

import psycopg2

from cx import config
from .theme import (BG, BG2, BG3, FG, FG_DIM, FG_MUTED, BORDER, DULL_GRN, RED,
                    HOVER_BG)
from .icons import IconCache

ICON_SIZE = 22
STRIPE_A, STRIPE_B = BG3, "#272728"

# --- taxonomy: derive (group, subgroup) from category_api_id + base_type ------
# Heuristic keyword rules over the base type; first match wins. Unmatched falls
# back to the base type as its own subgroup, so nothing is hidden or misfiled.

# poe2scout's ItemMetadata.properties carries the real item CLASS as one of its
# keys (e.g. "One Hand Mace", "Crossbow", "Spear") — far more reliable than
# guessing from the base-type name (Morning Star / Warpick are maces; a "Runic
# Fork" is actually a wand). These keys are STATS, to be skipped when isolating
# the class key.
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


def classify(category, base_type, props=None):
    """-> (group, subgroup). Weapons take their class from poe2scout properties
    (One Hand Mace, Two Hand Mace, Crossbow, …); the rest derive from base_type.

    Non-weapon keyword matching is CASE-INSENSITIVE so compounds fold into their
    group instead of splintering into singletons."""
    bt = base_type or ""
    btl = bt.lower()
    cat = (category or "").lower()
    if cat == "weapon":
        return "Weapons", _weapon_class(props, bt)
    if cat == "armour":
        if any(k.lower() in btl for k in _SHIELD) or "focus" in btl:
            return "Offhands", bt
        if any(k.lower() in btl for k in _GLOVES):
            return "Gloves", bt
        if any(k.lower() in btl for k in _BOOTS):
            return "Boots", bt
        if any(k.lower() in btl for k in _HELMET):
            return "Helmets", bt
        return "Body Armours", bt
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


_GROUP_ORDER = ["Weapons", "Offhands", "Body Armours", "Helmets", "Gloves",
                "Boots", "Jewellery", "Charms", "Flasks", "Jewels", "Tablets",
                "Waystones", "Relics"]
_MAX_SUBCHIPS = 30          # cap base chips; the rest stay reachable under "All"

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


class UniquesPanel(tk.Frame):
    def __init__(self, parent, tm=None):
        super().__init__(parent, bg=BG)
        self.tm = tm
        self.icons = IconCache()
        self._schema = config.schema_name(config.LEAGUE_SHORT)
        self._data = {}              # group -> {subgroup -> [item dict]}
        self._icon_urls = {}         # icon key -> url
        self._sel_group = None
        self._sel_sub = None
        self._sel_attrs = set()      # armour groups: chosen {Str,Dex,Int} (AND filter)
        self._attr_all = False       # armour groups: the neutral "All" mode
        self._row_item = {}          # tree rowid -> item dict (for the detail card)
        self._card = None            # the open detail-card Toplevel, or None
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
    # The chip rows are repeated widgets: each chip is tagged with the stem
    # `uniques.group[<name>]` / `uniques.base[<name>]` (in _chip) or, for armour
    # groups, `uniques.attr[<Str|Dex|Int|All>]` (in _attr_chip), so a non-Ctrl pick
    # greps back to the chip-builder and Ctrl pins one chip. No-op without a
    # TiketMaster handle, so the panel still runs standalone.
    def _tag_widgets(self):
        tm = self.tm
        if tm is None:
            return
        tm.tag(self.group_bar, "uniques.groupbar")
        tm.tag(self.sub_bar, "uniques.basebar")
        tm.tag(self.sub_label, "uniques.basebar.label")
        tm.tag(self.list_title, "uniques.title")
        tm.tag(self.list_sub, "uniques.subtitle")
        tm.tag_table(self.tv, "uniques.tree", row_label=lambda t, r: t.item(r, "text"))

    # ------------------------------------------------------------------ build
    def _build(self):
        self.group_bar = tk.Frame(self, bg=BG)
        self.group_bar.pack(fill="x", pady=(0, 4))

        sub_wrap = tk.Frame(self, bg=BG2)
        sub_wrap.pack(fill="x", pady=(0, 6))
        self.sub_label = tk.Label(sub_wrap, text="", bg=BG2, fg=FG_MUTED,
                                  font=("Segoe UI", 8))
        self.sub_label.pack(anchor="w", padx=8, pady=(4, 0))
        self.sub_bar = tk.Frame(sub_wrap, bg=BG2)
        self.sub_bar.pack(fill="x", padx=6, pady=(2, 6))

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
        self.tv = ttk.Treeview(bodyf, columns=["lvl", "base", "mod"],
                               show="tree headings")
        self.tv.heading("#0", text="unique")
        self.tv.column("#0", width=210, minwidth=120, anchor="w", stretch=True)
        self.tv.heading("lvl", text="lvl")
        self.tv.column("lvl", width=42, anchor="e", stretch=False)
        self.tv.heading("base", text="base")
        self.tv.column("base", width=148, anchor="w", stretch=False)
        self.tv.heading("mod", text="mod")
        self.tv.column("mod", width=330, anchor="w", stretch=True)
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
        # (Ctrl+click stays the inspector capture: it fires <Control-Button-1>, the
        #  more specific binding, so this plain handler does not run for it.)
        self.ph = tk.Label(bodyf, text="", bg=BG3, fg=FG_MUTED,
                           font=("Segoe UI", 9), justify="center")

    def _chip(self, parent, text, active, cmd, eid=None):
        bg = DULL_GRN if active else BG3
        fg = FG if active else FG_DIM
        lab = tk.Label(parent, text=text, bg=bg, fg=fg, font=("Segoe UI", 9),
                       padx=10, pady=4, cursor="hand2")
        lab._active = active
        lab.bind("<Button-1>", lambda e: cmd())
        lab.bind("<Enter>", lambda e: lab.config(bg=(DULL_GRN if lab._active else HOVER_BG)))
        lab.bind("<Leave>", lambda e: lab.config(bg=(DULL_GRN if lab._active else BG3)))
        if self.tm is not None and eid:          # greppable: the [name] stem -> _chip
            self.tm.tag(lab, eid)
        return lab

    # ------------------------------------------------------------------- data
    def refresh(self):
        conn = None
        try:
            conn = self._conn()
            cur = conn.cursor()
            try:
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
            self._render_groups()
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
                data[g][s].sort(key=lambda it: (it["lvl"] is None, it["lvl"] or 0, it["name"]))
        self._data, self._icon_urls = data, urls

    def _ordered_groups(self):
        known = [g for g in _GROUP_ORDER if g in self._data]
        rest = sorted(g for g in self._data if g not in _GROUP_ORDER)
        return known + rest

    def _render_groups(self):
        for w in self.group_bar.winfo_children():
            w.destroy()
        groups = self._ordered_groups()
        if not groups:
            self.sub_label.config(text="")
            for w in self.sub_bar.winfo_children():
                w.destroy()
            self._fill_items([])
            self._show_ph(f"no uniques in {self._schema}\nrun  python -m cx.uniques")
            return
        for i, g in enumerate(groups):
            n = sum(len(v) for v in self._data[g].values())
            chip = self._chip(self.group_bar, f"{g}  {n}", False,
                              lambda gg=g: self._select_group(gg),
                              eid=f"uniques.group[{g}]")
            chip.grid(row=i // 11, column=i % 11, padx=2, pady=2, sticky="w")
        # hidden by default: categories only — nothing picked, list empty (on request)
        self._sel_group = None
        self._sel_sub = None
        self._sel_attrs = set()
        self._attr_all = False
        for w in self.sub_bar.winfo_children():
            w.destroy()
        self.sub_label.config(text="")
        self._fill_items([])
        self._show_ph("выбери категорию ↑")

    def _select_group(self, group):
        self._sel_group = group
        self._sel_sub = None
        self._sel_attrs = set()
        self._attr_all = False
        for w in self.group_bar.winfo_children():
            on = w.cget("text").rsplit("  ", 1)[0] == group
            w._active = on
            w.config(bg=DULL_GRN if on else BG3, fg=FG if on else FG_DIM)
        for w in self.sub_bar.winfo_children():
            w.destroy()
        if group in _ATTR_GROUPS:                # armour: Str/Dex/Int, not base names
            self.sub_label.config(text=f"Filter by attribute   ·   {group.upper()}"
                                       f"   ·   multi-select, Str+Dex = has both")
            self._build_attr_chips(group)
            self._fill_items([])
            self._show_ph("← Str · Dex · Int  (можно несколько)     или  All")
            return
        if group == "Weapons":
            self.sub_label.config(text="Choose hand   ·   ALL · 1H · 2H")
            counts = self._weapon_chip_counts()
            order, cnt_of = ["All", "1H", "2H"], counts.get
        else:
            self.sub_label.config(text=f"Choose a base   ·   {group.upper()}")
            subs = self._data.get(group, {})
            total = sum(len(v) for v in subs.values())
            order = ["All"] + [s for s, _ in sorted(
                subs.items(), key=lambda kv: (-len(kv[1]), kv[0]))][:_MAX_SUBCHIPS]
            cnt_of = lambda s: total if s == "All" else len(subs[s])
        for i, s in enumerate(order):
            chip = self._chip(self.sub_bar, f"{s}  {cnt_of(s) or 0}", False,
                              lambda ss=s: self._select_sub(ss),
                              eid=f"uniques.base[{s}]")
            chip.grid(row=i // 12, column=i % 12, padx=2, pady=2, sticky="w")
        # on request only: do NOT auto-fill — wait for a sub-chip click
        self._fill_items([])
        self._show_ph("← 1H / 2H / All" if group == "Weapons" else "← выбери базу")

    def _select_sub(self, sub):
        self._sel_sub = sub
        for w in self.sub_bar.winfo_children():
            on = w.cget("text").rsplit("  ", 1)[0] == sub
            w._active = on
            w.config(bg=DULL_GRN if on else BG3, fg=FG if on else FG_DIM)

        if self._sel_group == "Weapons":
            sections = self._weapon_sections(sub)
            total = sum(len(it) for _, it in sections)
            self.list_title.config(text=f"Weapons · {sub}")
            self.list_sub.config(text=f"{total} uniques · {len(sections)} types · by level ↑")
            self._fill_sectioned(sections)
            return
        subs = self._data.get(self._sel_group, {})
        if sub == "All":
            items = [it for lst in subs.values() for it in lst]
            items.sort(key=lambda it: (it["lvl"] is None, it["lvl"] or 0, it["name"]))
        else:
            items = subs.get(sub, [])
        self.list_title.config(text=f"{self._sel_group} · {sub}")
        self.list_sub.config(text=f"{len(items)} uniques · by level ↑")
        self._fill_items(items)

    def _weapon_chip_counts(self):
        weapons = [it for lst in self._data.get("Weapons", {}).values() for it in lst]
        return {"All": len(weapons),
                "1H": sum(1 for it in weapons if it.get("hand") == "1H"),
                "2H": sum(1 for it in weapons if it.get("hand") == "2H")}

    def _weapon_sections(self, which):
        """-> [(label, [items])]. 'All' groups by full class; '1H'/'2H' filter by
        hand and group by short type ('Mace'). Sorted by size, items by level."""
        weapons = [it for lst in self._data.get("Weapons", {}).values() for it in lst]
        if which in ("1H", "2H"):
            weapons = [it for it in weapons if it.get("hand") == which]
            keyf = lambda it: it.get("wtype") or "Other"
        else:
            keyf = lambda it: it.get("wclass") or "Other"
        buckets = {}
        for it in weapons:
            buckets.setdefault(keyf(it), []).append(it)
        out = []
        for label in sorted(buckets, key=lambda k: (-len(buckets[k]), k)):
            items = sorted(buckets[label],
                           key=lambda it: (it["lvl"] is None, it["lvl"] or 0, it["name"]))
            out.append((label, items))
        return out

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
            self.list_sub.config(text="")
            self._show_ph("← Str · Dex · Int  (можно несколько)     или  All")
            return
        sel = sorted(sel, key=lambda it: (it["lvl"] is None, it["lvl"] or 0, it["name"]))
        self.list_title.config(text=f"{group} · {label}")
        self.list_sub.config(text=f"{len(sel)} uniques · by level ↑")
        self._fill_items(sel)

    # ------------------------------------------------------------------ items
    def _fill_items(self, items):
        self._hover_restore(self.tv)
        self._close_card()
        self.tv.delete(*self.tv.get_children())
        self._row_item = {}
        ids = []
        for i, it in enumerate(items):
            lvl = "—" if it["lvl"] is None else str(it["lvl"])
            rid = self.tv.insert("", "end", text=it["name"],
                                 values=(lvl, it["base"], it["mod"]),
                                 tags=("even" if i % 2 == 0 else "odd",))
            ids.append((rid, it["key"]))
            self._row_item[rid] = it
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
                rid = self.tv.insert(pid, "end", text=it["name"],
                                     values=(lvl, it["base"], it["mod"]),
                                     tags=("even" if i % 2 == 0 else "odd",))
                ids.append((rid, it["key"]))
                self._row_item[rid] = it          # section header rows excluded
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
        """A frameless tooltip near the cursor, built by build_sections(). Closes on
        Esc / click / losing focus; a new row replaces it. Topmost, like the app."""
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
        sw, sh = card.winfo_screenwidth(), card.winfo_screenheight()
        x = min(ev.x_root + 16, sw - cw - 8)
        y = min(ev.y_root + 10, sh - ch - 8)
        card.geometry(f"+{max(8, x)}+{max(8, y)}")
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
