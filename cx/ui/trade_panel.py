"""TradePanel — build a trade2 search in cx and open it as a browser tab.

The form's output IS a preset dict (cx/trade.py pre-fields): category / rarity /
req-level / price plus any number of stat filters picked from the stats
dictionary (full open ``data/stats`` when reachable, embedded pseudo set
offline). Opening = ``trade.preset_to_url`` + ``webbrowser`` — the POST that
mints the short search id runs in the logged-in browser via the companion
userscript (#cxq hand-off), never here. Named presets persist in
``cx_trade_presets.json`` at the repo root (gitignored — user data).

League: the display name in the trade URL comes from the ``cx_<league>.league``
row (local, no network), with poe2scout as the backup resolver.
"""
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

import psycopg2

from cx import config, trade
from .theme import (BG, BG2, BG3, FG, FG_DIM, FG_MUTED, BORDER_DARK,
                    DULL_GRN, RED)
from .chrome import seg_cell, bind_seg_hover
from .equipment_nav import EquipmentNav

_FONT = ("Segoe UI", 9)
_MONO = ("Consolas", 9)


def _num(s: str):
    """Entry text -> int | float | None (empty / unparsable -> None)."""
    s = (s or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return None


class TradePanel(tk.Frame):
    def __init__(self, parent, tm=None):
        super().__init__(parent, bg=BG)
        self.tm = tm
        self._league = None
        self._stat_opts = [(i, f"[pseudo] {t}") for i, t in trade.PSEUDO_STATS]
        self._text_by_id = dict(self._stat_opts)
        self._stat_rows = []          # [(stat_id, min Entry, max Entry, row Frame)]
        self._mystat_ids = []         # stat id per row in the ★-stats list
        self._presets = {}
        self._used = {}               # stat id -> [preset names]  (derived)
        self._editing = None          # name of the preset last Loaded (edit hint)
        self._q = queue.Queue()
        self._build()
        self._tag_widgets()
        self._reload_presets()
        threading.Thread(target=self._bg_init, daemon=True).start()
        self.after(150, self._poll)

    # ------------------------------------------------------------------ data
    def _conn(self):
        return psycopg2.connect(**config.DB_CONFIG)

    def _bg_init(self):
        """Resolve the league display name (DB first, poe2scout backup) and the
        full stat dictionary (disk cache / network / embedded) off the UI thread."""
        league = None
        try:
            schema = config.schema_name(config.LEAGUE_SHORT)
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(f"select league from {schema}.league limit 1")
                row = cur.fetchone()
                league = row[0] if row else None
        except Exception:
            pass
        if not league:
            try:
                from cx.source import current_league
                league = current_league()["league"]
            except Exception:
                pass
        self._q.put(("league", league))
        self._q.put(("stats", trade.stat_options()))

    def _poll(self):
        try:
            while True:
                kind, val = self._q.get_nowait()
                if kind == "league":
                    self._league = val
                    self.lbl_league.config(
                        text=val or "league: unknown (run a cycle once)",
                        fg=FG if val else RED)
                elif kind == "stats" and val:
                    self._stat_opts = val
                    self._text_by_id = dict(val)
                    self._set_status(f"stat dictionary: {len(val)} entries")
                    self._reload_mystats()   # relabel ★-stats with full dict text
        except queue.Empty:
            pass
        self.after(400, self._poll)

    # ------------------------------------------------------------------ build
    def _card(self, title, parent=None):
        outer = tk.Frame(parent or self, bg=BORDER_DARK)
        head = tk.Label(outer, text=title.upper(), bg=BG2, fg=FG_MUTED,
                        font=("Segoe UI", 8, "bold"), anchor="w", padx=8, pady=3)
        body = tk.Frame(outer, bg=BG2)
        head.pack(fill="x", padx=1, pady=(1, 0))
        body.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        return outer, body

    def _lbl(self, parent, text):
        return tk.Label(parent, text=text, bg=BG2, fg=FG_DIM, font=_FONT)

    def _entry(self, parent, width):
        return tk.Entry(parent, width=width, bg=BG3, fg=FG, insertbackground=FG,
                        relief="flat", font=_MONO, highlightthickness=1,
                        highlightbackground=BORDER_DARK, highlightcolor=DULL_GRN)

    def _combo(self, parent, values, default, width, readonly=True):
        cb = ttk.Combobox(parent, values=values, width=width,
                          state="readonly" if readonly else "normal",
                          style="Trade.TCombobox", font=_FONT)
        cb.set(default)
        return cb

    def _build(self):
        style = ttk.Style(self)
        style.configure("Trade.TCombobox", fieldbackground=BG3, background=BG2,
                        foreground=FG, arrowcolor=FG_DIM, bordercolor=BORDER_DARK,
                        lightcolor=BG3, darkcolor=BG3, selectbackground=BG3,
                        selectforeground=FG)
        style.map("Trade.TCombobox", fieldbackground=[("readonly", BG3)])
        # dropdown list colors (plain tk Listbox inside the combobox popup)
        for opt, val in (("background", BG3), ("foreground", FG),
                         ("selectBackground", DULL_GRN), ("selectForeground", FG)):
            self.option_add(f"*TCombobox*Listbox.{opt}", val)

        left_outer, lf = self._card("search builder")
        right_col = tk.Frame(self, bg=BG)
        left_outer.pack(side="left", fill="both", expand=True)
        right_col.pack(side="left", fill="y", padx=(8, 0))
        presets_outer, rf = self._card("presets", right_col)
        presets_outer.pack(fill="both", expand=True)
        mystats_outer, msf = self._card("★ stats you use", right_col)
        mystats_outer.pack(fill="both", expand=True, pady=(8, 0))

        f = tk.Frame(lf, bg=BG2)
        f.pack(fill="both", expand=True, padx=10, pady=8)

        # row 0 — league (resolved async) + status
        self.lbl_league = tk.Label(f, text="league: …", bg=BG2, fg=FG_DIM, font=_FONT)
        self.lbl_league.grid(row=0, column=0, columnspan=3, sticky="w")
        self._lbl(f, "Status").grid(row=0, column=3, sticky="e", padx=(8, 4))
        self.cb_status = self._combo(
            f, trade.STATUS_LABEL_LIST,
            trade.STATUS_LABELS[trade.STATUS_DEFAULT], 26)
        self.cb_status.grid(row=0, column=4, columnspan=2, sticky="w")

        # row 1 — category: the SAME chip navigator the Uniques tab uses (meta →
        # group → base), built from trade.CATEGORIES instead of the unique DB. This
        # replaces the old Category + Archetype dropdowns — one click down the tree
        # instead of hunting a ~50-entry list. Rarity stays its own field below, so
        # "unique" is just a rarity here, never a separate branch of the tree.
        self._lbl(f, "Category").grid(row=1, column=0, sticky="nw", pady=(8, 0))
        self._sel_category = ""
        self._arch_by_category = {trade.ARCHETYPES[k]["category"]: k
                                  for k in trade.ARCHETYPES}
        self.nav = EquipmentNav(f, tm=self.tm, eid_prefix="trade.form.category",
                                bg=BG2, on_leaf=self._on_cat_leaf)
        self.nav.grid(row=1, column=1, columnspan=5, sticky="we",
                      padx=(8, 0), pady=(8, 0))
        self.nav.set_taxonomy(trade.category_taxonomy())

        # row 2 — rarity + "Load case". Load case fills the stat bundles for the
        # picked gear slot (Helmet / Boots / Ring …) — the old Archetype quick-pick,
        # now keyed off whichever category chip is live (no separate dropdown). One
        # click instead of hand-picking ~600 stats; bundles live in trade.ARCHETYPES.
        self._lbl(f, "Rarity").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.cb_rar = self._combo(f, ["Any"] + [r for r in trade.RARITIES if r], "Any", 10)
        self.cb_rar.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        cr = tk.Frame(f, bg=BG2)
        cr.grid(row=2, column=2, columnspan=4, sticky="w", pady=(8, 0))
        o_load_arch, self.btn_arch = seg_cell(cr, "Load case", width=78,
                                              first=True, font=_FONT)
        o_load_arch.pack(side="left")
        self.btn_arch.bind("<Button-1>", lambda e: self._load_archetype())
        bind_seg_hover(self.btn_arch)
        tk.Label(cr, text="HP/ES · Resist · Attrs (+ slot-specific)",
                 bg=BG2, fg=FG_MUTED, font=_FONT).pack(side="left", padx=(10, 0))

        # row 3 — import: paste a cx trade link, read back its stat shortlist.
        self._lbl(f, "From link").grid(row=3, column=0, sticky="w", pady=(6, 0))
        il = tk.Frame(f, bg=BG2)
        il.grid(row=3, column=1, columnspan=5, sticky="we", padx=(8, 0), pady=(6, 0))
        self.e_link = self._entry(il, 44)
        self.e_link.pack(side="left", fill="x", expand=True)
        o_imp, self.btn_import = seg_cell(il, "Read", width=52, first=True, font=_FONT)
        o_imp.pack(side="left", padx=(6, 0))
        self.btn_import.bind("<Button-1>", lambda e: self._import_link())
        bind_seg_hover(self.btn_import)

        # row 4 — req level min/max + price cap
        self._lbl(f, "Req level").grid(row=4, column=0, sticky="w", pady=(6, 0))
        lv = tk.Frame(f, bg=BG2)
        lv.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        self.e_lvl_min = self._entry(lv, 5)
        self.e_lvl_min.pack(side="left")
        tk.Label(lv, text="–", bg=BG2, fg=FG_MUTED).pack(side="left", padx=3)
        self.e_lvl_max = self._entry(lv, 5)
        self.e_lvl_max.pack(side="left")
        self._lbl(f, "Price ≤").grid(row=4, column=3, sticky="e", padx=(8, 4), pady=(6, 0))
        pr = tk.Frame(f, bg=BG2)
        pr.grid(row=4, column=4, columnspan=2, sticky="w", pady=(6, 0))
        self.e_price = self._entry(pr, 6)
        self.e_price.pack(side="left")
        self.cb_price = self._combo(pr, trade.PRICE_OPTIONS, "exalted", 9, readonly=False)
        self.cb_price.pack(side="left", padx=(4, 0))

        # row 5 — stat search; row 6 — suggestion list (hidden until matches)
        self._lbl(f, "Add stat").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.e_search = self._entry(f, 52)
        self.e_search.grid(row=5, column=1, columnspan=5, sticky="we",
                           padx=(8, 0), pady=(10, 0))
        self.e_search.bind("<KeyRelease>", self._filter_sugg)
        self.e_search.bind("<Return>", lambda e: self._pick_sugg())
        self.e_search.bind("<Escape>", lambda e: self._hide_sugg())
        self.sugg = tk.Listbox(f, bg=BG3, fg=FG, selectbackground=DULL_GRN,
                               selectforeground=FG, activestyle="none",
                               font=_MONO, height=8, relief="flat",
                               highlightthickness=1,
                               highlightbackground=BORDER_DARK)
        self.sugg.bind("<Double-Button-1>", lambda e: self._pick_sugg())
        self.sugg.bind("<Return>", lambda e: self._pick_sugg())
        self._sugg_items = []

        # row 7 — chosen stat filters (one row each: text · min · max · ✕)
        self.stats_frame = tk.Frame(f, bg=BG2)
        self.stats_frame.grid(row=7, column=0, columnspan=6, sticky="we", pady=(6, 0))

        # row 8 — actions: open + save-as
        act = tk.Frame(f, bg=BG2)
        act.grid(row=8, column=0, columnspan=6, sticky="we", pady=(12, 0))
        o_open, self.btn_open = seg_cell(act, "↗ Open trade2", width=110,
                                         primary=True, first=True, font=_FONT)
        o_open.pack(side="left")
        self.btn_open.bind("<Button-1>", lambda e: self._open_form())
        bind_seg_hover(self.btn_open)
        self.e_name = self._entry(act, 16)
        self.e_name.pack(side="left", padx=(16, 0))
        o_save, self.btn_save = seg_cell(act, "Save preset", width=86,
                                         first=True, font=_FONT)
        o_save.pack(side="left", padx=(4, 0))
        self.btn_save.bind("<Button-1>", lambda e: self._save_preset())
        bind_seg_hover(self.btn_save)
        # Sort-by (far right of the actions row): trade2 sorts in the POST body,
        # so opening with a sort picked here lands the tab already ordered. The
        # list = fixed property columns + one entry per stat now in the builder.
        self._sort_by_label = {}
        self.cb_sort = self._combo(act, [trade.SORT_DEFAULT_LABEL],
                                   trade.SORT_DEFAULT_LABEL, 24)
        self.cb_sort.pack(side="right")
        self._lbl(act, "Sort by").pack(side="right", padx=(0, 4))
        self._refresh_sort_options()

        # row 9 — status line
        self.status = tk.Label(f, text="", bg=BG2, fg=FG_DIM, font=_MONO, anchor="w")
        self.status.grid(row=9, column=0, columnspan=6, sticky="we", pady=(10, 0))
        f.columnconfigure(2, weight=1)

        # presets card — list + load / open / delete
        self.plist = tk.Listbox(rf, bg=BG3, fg=FG, selectbackground=DULL_GRN,
                                selectforeground=FG, activestyle="none",
                                font=_FONT, width=28, relief="flat",
                                highlightthickness=1,
                                highlightbackground=BORDER_DARK)
        self.plist.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.plist.bind("<Double-Button-1>", lambda e: self._open_selected())
        # two rows: Load (-> edit in the form) / Open (-> browser);
        #           Rename (re-key to the name box) / Delete.
        pb = tk.Frame(rf, bg=BG2)
        pb.pack(fill="x", padx=8, pady=(0, 8))
        for rowspec in (
            (("Load", 56, self._load_selected), ("Open", 56, self._open_selected)),
            (("Rename", 56, self._rename_selected), ("Delete", 56, self._delete_selected)),
        ):
            rowf = tk.Frame(pb, bg=BG2)
            rowf.pack(fill="x", pady=(0, 2))
            for j, (text, w, cmd) in enumerate(rowspec):
                outer, btn = seg_cell(rowf, text, width=w, first=(j == 0), font=_FONT)
                outer.pack(side="left")
                btn.bind("<Button-1>", lambda e, c=cmd: c())
                bind_seg_hover(btn)

        # ★ my-stats card — the stats that appear in your presets (derived from
        # them, never stored separately). Double-click adds one to the builder;
        # a leading "+" flags stats no archetype quick-pick covers, and the gap
        # line counts them — the "filters you use that the panel doesn't show".
        self.slist = tk.Listbox(msf, bg=BG3, fg=FG, selectbackground=DULL_GRN,
                                selectforeground=FG, activestyle="none",
                                font=_MONO, width=28, height=8, relief="flat",
                                highlightthickness=1,
                                highlightbackground=BORDER_DARK)
        self.slist.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.slist.bind("<Double-Button-1>", lambda e: self._add_mystat())
        self.slist.bind("<<ListboxSelect>>", lambda e: self._mystat_selected())
        self.lbl_gap = tk.Label(msf, text="", bg=BG2, fg=FG_MUTED, font=_FONT,
                                anchor="w", justify="left")
        self.lbl_gap.pack(fill="x", padx=8, pady=(0, 8))

    # ---------------------------------------------------------------- tagging
    # Inspector eids (greppable stems, see cx/CLAUDE.md): trade.* lives here.
    def _tag_widgets(self):
        tm = self.tm
        if tm is None:
            return
        tm.tag(self.lbl_league, "trade.league")
        tm.tag(self.cb_status, "trade.form.status")
        tm.tag(self.cb_sort, "trade.form.sort")
        # category is now the chip navigator (it self-tags trade.form.category.*);
        # point the bare stem at the whole picker. The archetype dropdown is gone —
        # "Load case" keeps its eid and is driven by the live category chip.
        tm.tag(self.nav, "trade.form.category")
        tm.tag(self.btn_arch, "trade.form.archetype.load")
        tm.tag(self.e_link, "trade.form.fromlink")
        tm.tag(self.btn_import, "trade.form.fromlink.read")
        tm.tag(self.cb_rar, "trade.form.rarity")
        tm.tag(self.e_search, "trade.form.statsearch")
        tm.tag(self.stats_frame, "trade.form.stats")
        tm.tag(self.btn_open, "trade.open")
        tm.tag(self.btn_save, "trade.save")
        tm.tag(self.plist, "trade.presets")
        tm.tag(self.slist, "trade.mystats")
        tm.tag(self.lbl_gap, "trade.mystats.gap")
        tm.tag(self.status, "trade.status")

    # ------------------------------------------------------------ stat picker
    def _filter_sugg(self, _e=None):
        q = self.e_search.get().strip().lower()
        if len(q) < 2:
            self._hide_sugg()
            return
        words = q.split()
        matches = [(i, t) for i, t in self._stat_opts
                   if all(w in t.lower() or w in i for w in words)]
        # ★ first: stats you already use (appear in a preset) float to the top.
        # Stable sort keeps the dictionary order within each group.
        used = self._used
        matches.sort(key=lambda it: it[0] not in used)
        self._sugg_items = matches[:50]
        self.sugg.delete(0, "end")
        for i, t in self._sugg_items:
            self.sugg.insert("end", ("★ " if i in used else "   ") + t)
        if self._sugg_items:
            self.sugg.config(height=min(8, len(self._sugg_items)))
            self.sugg.grid(row=6, column=1, columnspan=5, sticky="we", padx=(8, 0))
        else:
            self._hide_sugg()

    def _hide_sugg(self):
        self.sugg.grid_remove()

    def _pick_sugg(self):
        if not self._sugg_items:
            return
        sel = self.sugg.curselection()
        idx = sel[0] if sel else 0
        stat_id, text = self._sugg_items[idx]
        self._add_stat_row(stat_id, text)
        self.e_search.delete(0, "end")
        self._hide_sugg()

    def _add_stat_row(self, stat_id, text, vmin=None, vmax=None):
        row = tk.Frame(self.stats_frame, bg=BG2)
        row.pack(fill="x", pady=1)
        lab = tk.Label(row, text=text[:64], bg=BG2, fg=FG, font=_MONO, anchor="w")
        lab.pack(side="left", fill="x", expand=True)
        e_min = self._entry(row, 5)
        e_min.pack(side="left", padx=(6, 0))
        e_max = self._entry(row, 5)
        e_max.pack(side="left", padx=(3, 0))
        if vmin is not None:
            e_min.insert(0, str(vmin))
        if vmax is not None:
            e_max.insert(0, str(vmax))
        entry = (stat_id, e_min, e_max, row)
        x = tk.Label(row, text="✕", bg=BG2, fg=FG_MUTED, cursor="hand2", padx=6)
        x.pack(side="left")
        x.bind("<Button-1>", lambda e: self._remove_stat_row(entry))
        x.bind("<Enter>", lambda e: x.config(fg=RED))
        x.bind("<Leave>", lambda e: x.config(fg=FG_MUTED))
        self._stat_rows.append(entry)
        self._refresh_sort_options()

    def _remove_stat_row(self, entry):
        self._stat_rows.remove(entry)
        entry[3].destroy()
        self._refresh_sort_options()

    def _clear_stat_rows(self):
        for _sid, _mn, _mx, row in self._stat_rows:
            row.destroy()
        self._stat_rows = []
        self._refresh_sort_options()

    # ------------------------------------------------------------ sort options
    def _refresh_sort_options(self):
        """Rebuild the Sort-by list: fixed property columns + one entry per stat
        currently in the builder (sort that stat high->low). Keeps the current
        pick if it still exists, else falls back to the default (price asc)."""
        if not hasattr(self, "cb_sort"):
            return
        labels, self._sort_by_label = [], {}
        for label, sort in trade.SORT_PROPERTIES:
            labels.append(label)
            self._sort_by_label[label] = sort
        for sid, _mn, _mx, _row in self._stat_rows:
            text = self._text_by_id.get(sid, sid)
            label = "▼ " + text[:38]
            if label in self._sort_by_label:        # same mod text twice
                label = f"{label} [{sid[-6:]}]"
            self._sort_by_label[label] = trade.sort_for_stat(sid)
            labels.append(label)
        cur = self.cb_sort.get()
        self.cb_sort.config(values=labels)
        if cur not in self._sort_by_label:
            self.cb_sort.set(trade.SORT_DEFAULT_LABEL)

    def _select_sort(self, sort):
        """Point the Sort-by combo at `sort` (a sort dict from a loaded preset).
        Unknown sorts (e.g. a property column we don't list) are added verbatim
        so a loaded link's sort is never silently dropped."""
        if not sort or sort == trade.SORT_DEFAULT:
            self.cb_sort.set(trade.SORT_DEFAULT_LABEL)
            return
        for label, s in self._sort_by_label.items():
            if s == sort:
                self.cb_sort.set(label)
                return
        key = next(iter(sort))
        label = f"(raw) {key} {sort[key]}"
        self._sort_by_label[label] = sort
        self.cb_sort.config(values=list(self.cb_sort.cget("values")) + [label])
        self.cb_sort.set(label)

    # ------------------------------------------------------- form <-> preset
    def _collect_preset(self) -> dict:
        p = {"status": trade.STATUS_BY_LABEL.get(
            self.cb_status.get(), trade.STATUS_DEFAULT)}
        if self._sel_category:
            p["category"] = self._sel_category
        rar = self.cb_rar.get()
        if rar and rar != "Any":
            p["rarity"] = rar
        mn, mx = _num(self.e_lvl_min.get()), _num(self.e_lvl_max.get())
        if mn is not None or mx is not None:
            p["req_level"] = {k: v for k, v in (("min", mn), ("max", mx))
                              if v is not None}
        pmax = _num(self.e_price.get())
        if pmax is not None:
            p["price"] = {"option": (self.cb_price.get() or "exalted").strip(),
                          "max": pmax}
        stats = []
        for sid, e_min, e_max, _row in self._stat_rows:
            s = {"id": sid}
            if _num(e_min.get()) is not None:
                s["min"] = _num(e_min.get())
            if _num(e_max.get()) is not None:
                s["max"] = _num(e_max.get())
            stats.append(s)
        if stats:
            p["stats"] = stats
        sort = self._sort_by_label.get(self.cb_sort.get())
        if sort and sort != trade.SORT_DEFAULT:
            p["sort"] = sort
        return p

    def _load_into_form(self, p: dict):
        self.cb_status.set(trade.STATUS_LABELS.get(
            p.get("status", trade.STATUS_DEFAULT),
            trade.STATUS_LABELS[trade.STATUS_DEFAULT]))
        cid = p.get("category", "") or ""
        self._sel_category = cid
        self.nav.select_value(cid)           # replay the chip path to this category
        self.cb_rar.set(p.get("rarity") or "Any")
        self.e_lvl_min.delete(0, "end")
        self.e_lvl_max.delete(0, "end")
        rl = p.get("req_level")
        if isinstance(rl, dict):
            if rl.get("min") is not None:
                self.e_lvl_min.insert(0, str(rl["min"]))
            if rl.get("max") is not None:
                self.e_lvl_max.insert(0, str(rl["max"]))
        elif rl is not None:
            self.e_lvl_max.insert(0, str(rl))
        self.e_price.delete(0, "end")
        price = p.get("price") or {}
        if price.get("max") is not None:
            self.e_price.insert(0, str(price["max"]))
        self.cb_price.set(price.get("option", "exalted"))
        self._clear_stat_rows()
        for s in p.get("stats") or []:
            self._add_stat_row(s["id"], self._text_by_id.get(s["id"], s["id"]),
                               s.get("min"), s.get("max"))
        # stat rows are now in place, so their sort entries exist — select last.
        self._select_sort(p.get("sort"))

    # ----------------------------------------------------- archetype / import
    def _on_cat_leaf(self, leaf):
        """A category chip pick -> remember its id (it drops straight into the
        preset's ``category``) and echo the choice to the status line."""
        self._sel_category = leaf.get("value", "") or ""
        self._set_status(f"category: {leaf['label']}")

    def _load_archetype(self):
        """Fill the builder with the stat bundles for the LIVE category chip — the
        quick-pick "case" for a gear slot (Helmet / Boots / Ring …). Driven by the
        category navigator now (no separate Archetype dropdown); leaves status /
        price / req-level untouched so you can flick between slots. Each stat lands
        "present, any roll" — tighten min/max afterwards."""
        key = self._arch_by_category.get(self._sel_category)
        if not key:
            self._set_status("pick a gear slot category first (Helmet / Boots / "
                             "Ring …) — no quick-pick case for this one", err=True)
            return
        arch = trade.ARCHETYPES[key]
        self._clear_stat_rows()
        for sid in trade.archetype_stats(key):
            self._add_stat_row(sid, self._text_by_id.get(sid, sid))
        n = len(self._stat_rows)
        self._set_status(f"case loaded: {arch['label']} — {n} stat bundle(s)")

    def _import_link(self):
        """Read a pasted cx trade link back into the builder: shows exactly which
        characteristics that link cares about (reverse of Open trade2)."""
        url = self.e_link.get().strip()
        if not url:
            self._set_status("paste a cx trade link first", err=True)
            return
        try:
            p = trade.parse_trade_url(url)
        except ValueError as e:
            self._set_status(f"can't read link: {e}", err=True)
            return
        self._load_into_form(p)
        n = len(p.get("stats") or [])
        self._set_status(f"read link → {n} stat(s) loaded into the builder")

    # ---------------------------------------------------------------- actions
    def _set_status(self, text, err=False):
        self.status.config(text=text, fg=RED if err else FG_DIM)
        # mirror every status line to the terminal so failures are visible in
        # the console (python -m cx) without having to screenshot the GUI.
        stream = sys.stderr if err else sys.stdout
        print(f"[trade] {'ERROR: ' if err else ''}{text}", file=stream, flush=True)

    def _open_preset_dict(self, p: dict, label: str):
        if not self._league:
            self._set_status("league unknown — run a cycle (DB) or check network", err=True)
            return
        url = trade.preset_to_url(p, self._league)
        # Surface the #cxq hand-off link: this is the ONLY place the decodable
        # link exists — the browser redirects to a stored-search URL (no #cxq)
        # the instant the userscript's POST lands. Drop it into the From-link
        # box so "Read" round-trips it straight back, and echo it to the
        # terminal so it can be grabbed from the console.
        self.e_link.delete(0, "end")
        self.e_link.insert(0, url)
        print(f"[trade] opened ({label}): {url}", flush=True)
        webbrowser.open(url, new=2)
        self._set_status(
            f"→ tab opened: {label} — #cxq link put in the From-link box "
            "(hit Read to load it back)")

    def _open_form(self):
        self._open_preset_dict(self._collect_preset(), "form")

    def _save_preset(self):
        name = self.e_name.get().strip()
        if not name:
            self._set_status("preset name is empty", err=True)
            return
        presets = trade.load_presets()
        verb = "updated" if name in presets else "saved"  # overwrite vs new
        presets[name] = self._collect_preset()
        trade.save_presets(presets)
        self._editing = name
        self._reload_presets()
        self._set_status(f"{verb}: {name}")

    def _reload_presets(self):
        self._presets = trade.load_presets()
        self._used = trade.used_stats(self._presets)   # derive the ★ set
        self.plist.delete(0, "end")
        for name in sorted(self._presets):
            self.plist.insert("end", name)
        self._reload_mystats()

    def _reload_mystats(self):
        """Fill the ★-stats list from self._used (stat -> presets), most-used
        first. Marks stats no archetype quick-pick covers with a leading "+" and
        tallies that gap — the filters you use that the panel doesn't surface."""
        if not hasattr(self, "slist"):
            return
        arch = trade.all_archetype_stats()
        self.slist.delete(0, "end")
        self._mystat_ids = []
        items = sorted(
            self._used.items(),
            key=lambda kv: (-len(kv[1]), self._text_by_id.get(kv[0], kv[0]).lower()))
        gap = 0
        for sid, _names in items:
            label = self._text_by_id.get(sid, sid)
            covered = sid in arch
            if not covered:
                gap += 1
            self.slist.insert("end", ("  " if covered else "+ ") + label[:38])
            self._mystat_ids.append(sid)
        if items:
            self.lbl_gap.config(
                text=f"{len(items)} stat(s) you use · {gap} not in quick-picks (+)")
        else:
            self.lbl_gap.config(text="empty — capture a search, Save a preset")

    def _mystat_selected(self):
        """Show the stat→preset link (which presets use the picked ★-stat)."""
        sel = self.slist.curselection()
        if not sel:
            return
        sid = self._mystat_ids[sel[0]]
        names = self._used.get(sid, [])
        self._set_status(f"{self._text_by_id.get(sid, sid)} — in: {', '.join(names)}")

    def _add_mystat(self):
        """Double-click a ★-stat -> drop it into the builder (no duplicate row)."""
        sel = self.slist.curselection()
        if not sel:
            return
        sid = self._mystat_ids[sel[0]]
        if any(r[0] == sid for r in self._stat_rows):
            self._set_status("already in the builder")
            return
        self._add_stat_row(sid, self._text_by_id.get(sid, sid))
        self._set_status(f"added: {self._text_by_id.get(sid, sid)}")

    def _selected_name(self):
        sel = self.plist.curselection()
        return self.plist.get(sel[0]) if sel else None

    def _load_selected(self):
        name = self._selected_name()
        if name:
            self._load_into_form(self._presets[name])
            self.e_name.delete(0, "end")
            self.e_name.insert(0, name)
            self._editing = name
            self._set_status(f"loaded: {name} — edit the form, then Save to update")

    def _open_selected(self):
        name = self._selected_name()
        if name:
            self._open_preset_dict(self._presets[name], name)

    def _rename_selected(self):
        """Re-key the selected preset to the name in the name box (content kept).

        Pairs with Save: Save writes the form under the name box (new name -> a
        copy), Rename moves the existing preset to that name. Load fills the name
        box with the current name, so the flow is: select -> Load -> edit name ->
        Rename."""
        old = self._selected_name()
        if not old:
            self._set_status("select a preset to rename", err=True)
            return
        new = self.e_name.get().strip()
        if not new:
            self._set_status("type the new name in the name box first", err=True)
            return
        if new == old:
            self._set_status("name unchanged")
            return
        presets = trade.load_presets()
        if new in presets:
            self._set_status(f"'{new}' already exists — pick another name", err=True)
            return
        presets[new] = presets.pop(old)
        trade.save_presets(presets)
        self._editing = new
        self._reload_presets()
        self._set_status(f"renamed: {old} → {new}")

    def _delete_selected(self):
        name = self._selected_name()
        if name:
            presets = trade.load_presets()
            presets.pop(name, None)
            trade.save_presets(presets)
            if name == self._editing:
                self._editing = None
            self._reload_presets()
            self._set_status(f"deleted: {name}")
