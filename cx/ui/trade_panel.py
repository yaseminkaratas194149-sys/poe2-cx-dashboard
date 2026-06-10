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
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

import psycopg2

from cx import config, trade
from .theme import (BG, BG2, BG3, FG, FG_DIM, FG_MUTED, BORDER_DARK,
                    DULL_GRN, RED)
from .chrome import seg_cell, bind_seg_hover

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
        self._presets = {}
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
        except queue.Empty:
            pass
        self.after(400, self._poll)

    # ------------------------------------------------------------------ build
    def _card(self, title):
        outer = tk.Frame(self, bg=BORDER_DARK)
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
        right_outer, rf = self._card("presets")
        left_outer.pack(side="left", fill="both", expand=True)
        right_outer.pack(side="left", fill="y", padx=(8, 0))

        f = tk.Frame(lf, bg=BG2)
        f.pack(fill="both", expand=True, padx=10, pady=8)

        # row 0 — league (resolved async) + status
        self.lbl_league = tk.Label(f, text="league: …", bg=BG2, fg=FG_DIM, font=_FONT)
        self.lbl_league.grid(row=0, column=0, columnspan=3, sticky="w")
        self._lbl(f, "Status").grid(row=0, column=3, sticky="e", padx=(8, 4))
        self.cb_status = self._combo(f, trade.STATUS_OPTIONS, "available", 10)
        self.cb_status.grid(row=0, column=4, columnspan=2, sticky="w")

        # row 1 — category + rarity
        self._lbl(f, "Category").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self._cat_by_label = {lab: cid for cid, lab in trade.CATEGORIES}
        self._label_by_cat = {cid: lab for cid, lab in trade.CATEGORIES}
        self.cb_cat = self._combo(f, [lab for _, lab in trade.CATEGORIES], "Any", 26)
        self.cb_cat.grid(row=1, column=1, columnspan=2, sticky="w",
                         padx=(8, 0), pady=(8, 0))
        self._lbl(f, "Rarity").grid(row=1, column=3, sticky="e", padx=(8, 4), pady=(8, 0))
        self.cb_rar = self._combo(f, ["Any"] + [r for r in trade.RARITIES if r], "Any", 10)
        self.cb_rar.grid(row=1, column=4, columnspan=2, sticky="w", pady=(8, 0))

        # row 2 — req level min/max + price cap
        self._lbl(f, "Req level").grid(row=2, column=0, sticky="w", pady=(6, 0))
        lv = tk.Frame(f, bg=BG2)
        lv.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        self.e_lvl_min = self._entry(lv, 5)
        self.e_lvl_min.pack(side="left")
        tk.Label(lv, text="–", bg=BG2, fg=FG_MUTED).pack(side="left", padx=3)
        self.e_lvl_max = self._entry(lv, 5)
        self.e_lvl_max.pack(side="left")
        self._lbl(f, "Price ≤").grid(row=2, column=3, sticky="e", padx=(8, 4), pady=(6, 0))
        pr = tk.Frame(f, bg=BG2)
        pr.grid(row=2, column=4, columnspan=2, sticky="w", pady=(6, 0))
        self.e_price = self._entry(pr, 6)
        self.e_price.pack(side="left")
        self.cb_price = self._combo(pr, trade.PRICE_OPTIONS, "exalted", 9, readonly=False)
        self.cb_price.pack(side="left", padx=(4, 0))

        # row 3 — stat search; row 4 — suggestion list (hidden until matches)
        self._lbl(f, "Add stat").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.e_search = self._entry(f, 52)
        self.e_search.grid(row=3, column=1, columnspan=5, sticky="we",
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

        # row 5 — chosen stat filters (one row each: text · min · max · ✕)
        self.stats_frame = tk.Frame(f, bg=BG2)
        self.stats_frame.grid(row=5, column=0, columnspan=6, sticky="we", pady=(6, 0))

        # row 6 — actions: open + save-as
        act = tk.Frame(f, bg=BG2)
        act.grid(row=6, column=0, columnspan=6, sticky="we", pady=(12, 0))
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

        # row 7 — status line
        self.status = tk.Label(f, text="", bg=BG2, fg=FG_DIM, font=_MONO, anchor="w")
        self.status.grid(row=7, column=0, columnspan=6, sticky="we", pady=(10, 0))
        f.columnconfigure(2, weight=1)

        # presets card — list + load / open / delete
        self.plist = tk.Listbox(rf, bg=BG3, fg=FG, selectbackground=DULL_GRN,
                                selectforeground=FG, activestyle="none",
                                font=_FONT, width=28, relief="flat",
                                highlightthickness=1,
                                highlightbackground=BORDER_DARK)
        self.plist.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.plist.bind("<Double-Button-1>", lambda e: self._open_selected())
        pb = tk.Frame(rf, bg=BG2)
        pb.pack(fill="x", padx=8, pady=(0, 8))
        for text, w, cmd, first in (("Load", 52, self._load_selected, True),
                                    ("Open", 52, self._open_selected, False),
                                    ("✕", 24, self._delete_selected, False)):
            outer, btn = seg_cell(pb, text, width=w, first=first, font=_FONT)
            outer.pack(side="left")
            btn.bind("<Button-1>", lambda e, c=cmd: c())
            bind_seg_hover(btn)

    # ---------------------------------------------------------------- tagging
    # Inspector eids (greppable stems, see cx/CLAUDE.md): trade.* lives here.
    def _tag_widgets(self):
        tm = self.tm
        if tm is None:
            return
        tm.tag(self.lbl_league, "trade.league")
        tm.tag(self.cb_status, "trade.form.status")
        tm.tag(self.cb_cat, "trade.form.category")
        tm.tag(self.cb_rar, "trade.form.rarity")
        tm.tag(self.e_search, "trade.form.statsearch")
        tm.tag(self.stats_frame, "trade.form.stats")
        tm.tag(self.btn_open, "trade.open")
        tm.tag(self.btn_save, "trade.save")
        tm.tag(self.plist, "trade.presets")
        tm.tag(self.status, "trade.status")

    # ------------------------------------------------------------ stat picker
    def _filter_sugg(self, _e=None):
        q = self.e_search.get().strip().lower()
        if len(q) < 2:
            self._hide_sugg()
            return
        words = q.split()
        self._sugg_items = [(i, t) for i, t in self._stat_opts
                            if all(w in t.lower() or w in i for w in words)][:50]
        self.sugg.delete(0, "end")
        for _i, t in self._sugg_items:
            self.sugg.insert("end", t)
        if self._sugg_items:
            self.sugg.config(height=min(8, len(self._sugg_items)))
            self.sugg.grid(row=4, column=1, columnspan=5, sticky="we", padx=(8, 0))
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

    def _remove_stat_row(self, entry):
        self._stat_rows.remove(entry)
        entry[3].destroy()

    def _clear_stat_rows(self):
        for _sid, _mn, _mx, row in self._stat_rows:
            row.destroy()
        self._stat_rows = []

    # ------------------------------------------------------- form <-> preset
    def _collect_preset(self) -> dict:
        p = {"status": self.cb_status.get() or "available"}
        cat = self._cat_by_label.get(self.cb_cat.get(), "")
        if cat:
            p["category"] = cat
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
        return p

    def _load_into_form(self, p: dict):
        self.cb_status.set(p.get("status", "available"))
        self.cb_cat.set(self._label_by_cat.get(p.get("category", ""), "Any"))
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

    # ---------------------------------------------------------------- actions
    def _set_status(self, text, err=False):
        self.status.config(text=text, fg=RED if err else FG_DIM)

    def _open_preset_dict(self, p: dict, label: str):
        if not self._league:
            self._set_status("league unknown — run a cycle (DB) or check network", err=True)
            return
        url = trade.preset_to_url(p, self._league)
        webbrowser.open(url, new=2)
        self._set_status(f"→ tab opened: {label} (userscript finishes the POST)")

    def _open_form(self):
        self._open_preset_dict(self._collect_preset(), "form")

    def _save_preset(self):
        name = self.e_name.get().strip()
        if not name:
            self._set_status("preset name is empty", err=True)
            return
        presets = trade.load_presets()
        presets[name] = self._collect_preset()
        trade.save_presets(presets)
        self._reload_presets()
        self._set_status(f"saved: {name}")

    def _reload_presets(self):
        self._presets = trade.load_presets()
        self.plist.delete(0, "end")
        for name in sorted(self._presets):
            self.plist.insert("end", name)

    def _selected_name(self):
        sel = self.plist.curselection()
        return self.plist.get(sel[0]) if sel else None

    def _load_selected(self):
        name = self._selected_name()
        if name:
            self._load_into_form(self._presets[name])
            self.e_name.delete(0, "end")
            self.e_name.insert(0, name)
            self._set_status(f"loaded: {name}")

    def _open_selected(self):
        name = self._selected_name()
        if name:
            self._open_preset_dict(self._presets[name], name)

    def _delete_selected(self):
        name = self._selected_name()
        if name:
            presets = trade.load_presets()
            presets.pop(name, None)
            trade.save_presets(presets)
            self._reload_presets()
            self._set_status(f"deleted: {name}")
