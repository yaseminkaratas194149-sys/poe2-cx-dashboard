"""UniquesPanel — a unique-item browser in the cx visual language.

Drill-down like the poe2scout item browser: a row of global-category chips → a
row of base chips for the chosen category → the uniques in that base, sorted by
required level (low → high), each with its icon, level and a mod.

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
        self._icon_q = queue.Queue()
        self._icon_active = 0
        self._icon_poll = None
        self._icon_gen = 0
        self._build()
        self.refresh()

    def _conn(self):
        return psycopg2.connect(**config.DB_CONFIG)

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
        self.ph = tk.Label(bodyf, text="", bg=BG3, fg=FG_MUTED,
                           font=("Segoe UI", 9), justify="center")

    def _chip(self, parent, text, active, cmd):
        bg = DULL_GRN if active else BG3
        fg = FG if active else FG_DIM
        lab = tk.Label(parent, text=text, bg=bg, fg=fg, font=("Segoe UI", 9),
                       padx=10, pady=4, cursor="hand2")
        lab._active = active
        lab.bind("<Button-1>", lambda e: cmd())
        lab.bind("<Enter>", lambda e: lab.config(bg=(DULL_GRN if lab._active else HOVER_BG)))
        lab.bind("<Leave>", lambda e: lab.config(bg=(DULL_GRN if lab._active else BG3)))
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
                               explicit_mods, implicit_mods, metadata->'properties'
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
        for uid, name, base, cat, icon, lvl, emods, imods, props in rows:
            group, sub = classify(cat, base, props)
            key = f"u{uid}"
            urls[key] = icon
            try:
                lvl_i = int(lvl) if lvl is not None else None
            except (TypeError, ValueError):
                lvl_i = None
            mods = emods or imods or []
            item = {"key": key, "name": name, "base": base or "",
                    "lvl": lvl_i, "mod": (mods[0] if mods else "")}
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
                              lambda gg=g: self._select_group(gg))
            chip.grid(row=i // 11, column=i % 11, padx=2, pady=2, sticky="w")
        # hidden by default: categories only — nothing picked, list empty (on request)
        self._sel_group = None
        self._sel_sub = None
        for w in self.sub_bar.winfo_children():
            w.destroy()
        self.sub_label.config(text="")
        self._fill_items([])
        self._show_ph("выбери категорию ↑")

    def _select_group(self, group):
        self._sel_group = group
        self._sel_sub = None
        for w in self.group_bar.winfo_children():
            on = w.cget("text").rsplit("  ", 1)[0] == group
            w._active = on
            w.config(bg=DULL_GRN if on else BG3, fg=FG if on else FG_DIM)
        for w in self.sub_bar.winfo_children():
            w.destroy()
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
                              lambda ss=s: self._select_sub(ss))
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

    # ------------------------------------------------------------------ items
    def _fill_items(self, items):
        self._hover_restore(self.tv)
        self.tv.delete(*self.tv.get_children())
        ids = []
        for i, it in enumerate(items):
            lvl = "—" if it["lvl"] is None else str(it["lvl"])
            rid = self.tv.insert("", "end", text=it["name"],
                                 values=(lvl, it["base"], it["mod"]),
                                 tags=("even" if i % 2 == 0 else "odd",))
            ids.append((rid, it["key"]))
        if items:
            self.ph.place_forget()
        else:
            self._show_ph("nothing here")
        self._load_icons(ids)

    def _fill_sectioned(self, sections):
        """Grouped view: one parent header row per section, items nested under it."""
        self._hover_restore(self.tv)
        self.tv.delete(*self.tv.get_children())
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
        if sections:
            self.ph.place_forget()
        else:
            self._show_ph("nothing here")
        self._load_icons(ids)

    def _show_ph(self, text):
        self.ph.config(text=text)
        self.ph.place(relx=0.5, rely=0.42, anchor="center")

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
