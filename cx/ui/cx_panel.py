"""CxPanel — liquidity board + currency lookup, in the launcher / doc_nav
visual language.

Two bordered cards (board + currency view) over the cx store, plus the pair
chart card a click on a counter row opens (pair_chart.PairChart). Read-only:
queries go through cx.derive. The currency name rides in each tree's #0 column
with its icon; rows stripe and highlight on hover; icons stream in from a disk
cache in the background. Empty / no-schema states render a friendly placeholder,
not a red error. Widgets are tagged through the universal ticket+inspector handle
(`tm`) when present, so any element greps back to this source. A click on a
column header sorts that card by the column — high → low, click again for
low → high (ColumnSort); the pinned key-pair row stays on top.
"""
import queue
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk

import psycopg2

from cx import config, derive
from .theme import (BG, BG2, BG3, FG, FG_DIM, FG_MUTED, BORDER, GREEN, RED,
                    HOVER_BG)
from .chrome import seg_cell, bind_seg_hover
from .colsort import ColumnSort
from .icons import IconCache
from .pair_chart import PairChart

# Subtle row striping (over the BG3 field): alternate rows a hair darker.
STRIPE_A = BG3          # "#2d2d2d"
STRIPE_B = "#272728"

# The key pair. Every currency is read against Divine Orb first, so its leg is
# pinned to the top of the currency view — or a "no data" stub sits there when
# the latest hour carries no divine leg for that currency.
KEY_COUNTER = "divine"
KEY_BG = "#37373d"      # pinned-row tint (same as the uniques section rows)
PINNED_TAGS = {"key", "missing"}   # the rows a header sort leaves on top

PLACEHOLDER = "currency…"
ICON_SIZE = 20          # per-row currency icon (px)
HDR_ICON_SIZE = 26      # currency-card header icon (px)


class CxPanel(tk.Frame):
    def __init__(self, parent, tm=None):
        super().__init__(parent, bg=BG)
        self.tm = tm                 # universal ticket+inspector handle (or None)
        self._schema = None
        self._placeholder_on = True
        self.icons = IconCache()
        self._icon_urls = {}         # api_id -> icon_url (loaded per schema)
        self._icon_gen = {}          # tree -> generation int (stale-write guard)
        self._hdr_gen = 0
        self._icon_q = queue.Queue()  # (kind, ...) results from loader threads
        self._icon_active = 0         # running loaders (main-thread counter)
        self._icon_poll = None        # after-job id of the drain poller
        self._chart = None            # the open pair-chart card, or None
        self._cur_api = None          # currency the currency view shows
        self._short = None            # league short name (poe2scout path segment)
        self._build()
        self._tag_widgets()
        self.refresh()

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    def _conn(self):
        return psycopg2.connect(**config.DB_CONFIG)

    def _build(self):
        self._build_search_row()

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        # left card — liquidity board (currency name + icon in #0)
        (_l, _bi, self.board_title, self.board_sub, self.board_tv, self.board_ph) = self._make_card(
            body, "left", "Liquidity leaders",
            ("currency", 180), [("pairs", 48), ("consensus", 92), ("tot_val", 116)],
            pad=(0, 4))
        self.board_sub.config(text="top 20 · by value")
        self.board_tv.bind("<Double-1>", self._on_board_click)

        # right card — currency view (counter name + icon in #0; header icon)
        (_r, self.cur_icon, self.cur_title, self.cur_sub, self.cur_tv, self.cur_ph) = self._make_card(
            body, "left", "Currency",
            ("counter", 158), [("price", 78), ("avg 6h", 78), ("7d", 56), ("traded", 84)],
            pad=(4, 0), with_icon=True)
        self.cur_sub.config(text="")
        self._show_placeholder(self.cur_ph, "double-click a currency\nor type one and press Enter")
        self.cur_tv.bind("<Button-1>", self._on_cur_click, add="+")   # counter row -> pair chart

    def _build_search_row(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", pady=(0, 8))

        # search field — Entry on BG3 inside a 1px hairline, with an inline ✕
        sf_outer = tk.Frame(top, bg=BORDER)
        sf_outer.pack(side="left")
        sf = tk.Frame(sf_outer, bg=BG3)
        sf.pack(padx=1, pady=1)
        self.entry = tk.Entry(sf, bg=BG3, fg=FG_MUTED, insertbackground=FG,
                              bd=0, width=22, font=("Consolas", 9))
        self.entry.pack(side="left", padx=(7, 2), pady=4)
        self.entry.insert(0, PLACEHOLDER)
        self.entry.bind("<FocusIn>", self._entry_focus_in)
        self.entry.bind("<FocusOut>", self._entry_focus_out)
        self.entry.bind("<Return>", lambda e: self.lookup())
        self._clear = tk.Label(sf, text="✕", bg=BG3, fg=FG_MUTED,
                               font=("Consolas", 8), cursor="hand2")
        self._clear.pack(side="left", padx=(0, 7))
        self._clear.bind("<Button-1>", lambda e: self._clear_entry())

        # Lookup (primary) · ↻ refresh — segmented group
        seg = tk.Frame(top, bg=BG)
        seg.pack(side="left", padx=(6, 0))
        o1, self.lookup_btn = seg_cell(seg, "Lookup", width=64, primary=True,
                                       first=True, font=("Segoe UI", 9))
        o1.pack(side="left")
        self.lookup_btn.bind("<Button-1>", lambda e: self.lookup())
        bind_seg_hover(self.lookup_btn)
        o2, self.refresh_btn = seg_cell(seg, "↻", width=28, font=("Consolas", 11))
        o2.pack(side="left")
        self.refresh_btn.bind("<Button-1>", lambda e: self.refresh())
        bind_seg_hover(self.refresh_btn)

        # status / freshness chip, right-aligned
        self.status = tk.Label(top, text="", bg=BG, fg=FG_DIM,
                               font=("Consolas", 8))
        self.status.pack(side="right", padx=4)

    def _make_card(self, parent, side, title, tree_col, cols, pad, with_icon=False):
        """A bordered card: hairline frame → BG2 body → BG3 header strip +
        Treeview + centered placeholder. The currency name + icon ride in the #0
        tree column (`tree_col` = (heading, width)); `cols` are the numeric data
        columns. `with_icon` adds a header icon Label left of the title. A click
        on a column header sorts the card by it (see _sort_click).
        Returns (inner, head_icon, title_lbl, sub_lbl, tree, placeholder)."""
        outer = tk.Frame(parent, bg=BORDER)
        outer.pack(side=side, fill="both", expand=True, padx=pad)
        inner = tk.Frame(outer, bg=BG2)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        head = tk.Frame(inner, bg=BG3)
        head.pack(fill="x")
        head_icon = None
        if with_icon:
            head_icon = tk.Label(head, bg=BG3)
            head_icon.pack(side="left", padx=(8, 2), pady=2)
            title_lbl = tk.Label(head, text=title, bg=BG3, fg=FG,
                                 font=("Segoe UI", 9, "bold"))
            title_lbl.pack(side="left", padx=(0, 6), pady=4)
        else:
            title_lbl = tk.Label(head, text=title, bg=BG3, fg=FG,
                                 font=("Segoe UI", 9, "bold"))
            title_lbl.pack(side="left", padx=8, pady=4)
        sub_lbl = tk.Label(head, text="", bg=BG3, fg=FG_DIM, font=("Consolas", 8))
        sub_lbl.pack(side="right", padx=8)

        bodyf = tk.Frame(inner, bg=BG2)
        bodyf.pack(fill="both", expand=True, padx=6, pady=(4, 6))
        tcol_name, tcol_w = tree_col
        tv = ttk.Treeview(bodyf, columns=[c for c, _ in cols], show="tree headings")
        tv.heading("#0", text=tcol_name)
        tv.column("#0", width=tcol_w, minwidth=90, anchor="w", stretch=True)
        for c, w in cols:
            tv.heading(c, text=c)
            tv.column(c, width=w, anchor="e", stretch=False)
        # header click -> sort by that column: ColumnSort keeps (col, direction),
        # _fill stores each row's raw keys, _apply_sort moves the rows in place
        tv._colids = ["#0"] + [c for c, _ in cols]
        tv._titles = dict(zip(tv._colids, [tcol_name] + [c for c, _ in cols]))
        tv._sort = ColumnSort()
        tv._keys = {}
        for c in tv._colids:
            tv.heading(c, command=lambda c=c, tv=tv: self._sort_click(tv, c))
        sb = ttk.Scrollbar(bodyf, orient="vertical", command=tv.yview,
                           style="Subtle.Vertical.TScrollbar")
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tv.pack(side="left", fill="both", expand=True)

        tv.tag_configure("even", background=STRIPE_A)
        tv.tag_configure("odd", background=STRIPE_B)
        tv.tag_configure("hover", background=HOVER_BG)
        tv.tag_configure("key", background=KEY_BG)                      # pinned key pair
        tv.tag_configure("missing", background=KEY_BG, foreground=FG_MUTED)  # ...absent
        tv._hover_row = ""
        tv._hover_tags = ()
        tv.bind("<Motion>", self._hover_motion)
        tv.bind("<Leave>", lambda e: self._hover_restore(e.widget))

        placeholder = tk.Label(bodyf, text="", bg=BG3, fg=FG_MUTED,
                               font=("Segoe UI", 9), justify="center")
        return inner, head_icon, title_lbl, sub_lbl, tv, placeholder

    # ------------------------------------------------------------------
    # inspector tagging
    # ------------------------------------------------------------------

    def _tag_widgets(self):
        """Tag the panel's meaningful widgets so the inspector can reference them
        by a stable _eid that is literal in this source. No-op without a
        TiketMaster handle, so the panel still runs standalone. The two data
        surfaces are ttk.Treeviews -- tag_table gives them per-ROW grain
        (board.tree[divine]); the row label is the #0 text (the currency name),
        since the name now lives there with its icon."""
        tm = self.tm
        if tm is None:
            return
        name_of = lambda t, r: t.item(r, "text")
        tm.tag(self.entry, "search.entry")
        tm.no_grab(self.entry)               # ...but Ctrl+click stays normal text
        tm.tag(self._clear, "search.clear")
        tm.tag(self.lookup_btn, "search.lookup")
        tm.tag(self.refresh_btn, "search.refresh")
        tm.tag(self.status, "search.status")
        tm.tag(self.board_title, "board.title")
        tm.tag(self.board_sub, "board.subtitle")
        tm.tag_table(self.board_tv, "board.tree", row_label=name_of)
        tm.tag(self.cur_icon, "cur.icon")
        tm.tag(self.cur_title, "cur.title")
        tm.tag(self.cur_sub, "cur.subtitle")
        tm.tag_table(self.cur_tv, "cur.tree", row_label=name_of)

    # ------------------------------------------------------------------
    # hover highlight
    # ------------------------------------------------------------------

    def _hover_motion(self, ev):
        tv = ev.widget
        rowid = tv.identify_row(ev.y)
        if rowid == getattr(tv, "_hover_row", ""):
            return
        self._hover_restore(tv)
        if rowid:
            tv._hover_row = rowid
            tv._hover_tags = tv.item(rowid, "tags")
            tv.item(rowid, tags=("hover",))

    @staticmethod
    def _hover_restore(tv):
        rid = getattr(tv, "_hover_row", "")
        if rid and tv.exists(rid):
            tv.item(rid, tags=getattr(tv, "_hover_tags", ()))
        tv._hover_row = ""
        tv._hover_tags = ()

    # ------------------------------------------------------------------
    # placeholder / rows helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _show_placeholder(ph, text):
        ph.config(text=text)
        ph.place(relx=0.5, rely=0.42, anchor="center")

    @staticmethod
    def _hide_placeholder(ph):
        ph.place_forget()

    def _fill(self, tv, rows, keys=None):
        """Fill a tree. rows: (api_id, name_text, (col values...)[, tags]). The
        name goes in #0 (with the icon, loaded later); a row without its own
        tags gets the stripe. `keys`, aligned with rows, are each row's raw sort
        values -- (name, col1, col2, ...) as numbers, not cell text -- kept for
        the header sort; None for a row that never sorts (the pinned stub). The
        card's active sort, if any, is re-applied. Returns [(rowid, api_id)]."""
        self._hover_restore(tv)
        tv.delete(*tv.get_children())
        tv._keys = {}
        ids = []
        for i, row in enumerate(rows):
            api, text, vals = row[:3]
            tags = row[3] if len(row) > 3 else ("even" if i % 2 == 0 else "odd",)
            rid = tv.insert("", "end", text=text, values=vals, tags=tags)
            ids.append((rid, api))
            if keys is not None and keys[i] is not None:
                tv._keys[rid] = keys[i]
        self._apply_sort(tv)
        return ids

    # ------------------------------------------------------------------
    # header sort (click: high -> low, again: low -> high; #0 opens A -> Z)
    # ------------------------------------------------------------------

    def _sort_click(self, tv, col):
        tv._sort.click(col)
        self._apply_sort(tv)

    def _apply_sort(self, tv):
        """Reorder *tv* in place by its ColumnSort: rows move (icons ride along),
        the pinned key-pair rows stay on top, the stripe is redone in the new
        order, and the active header carries the arrow. With no active column
        the fill order stands (only the stripe and titles are touched)."""
        srt = tv._sort
        self._hover_restore(tv)
        rows = tv.get_children("")
        def tags(r):                        # Tk hands back a tuple, or a bare str
            t = tv.item(r, "tags")
            return {t} if isinstance(t, str) else set(t)
        pinned = [r for r in rows if PINNED_TAGS & tags(r)]
        rest = [r for r in rows if r not in pinned]
        if srt.col is not None:
            idx = tv._colids.index(srt.col)
            keys = tv._keys
            rest = srt.order(rest, lambda r: keys[r][idx] if r in keys else None)
        for i, r in enumerate(pinned + rest):
            tv.move(r, "", i)
            if r not in pinned:
                tv.item(r, tags=("even" if i % 2 == 0 else "odd",))
        for c, base in tv._titles.items():
            tv.heading(c, text=srt.title(c, base))

    # ------------------------------------------------------------------
    # icons (background load from disk cache / CDN, progressive)
    # ------------------------------------------------------------------

    def _load_icon_urls(self, cur, schema):
        try:
            cur.execute(f"select api_id, icon_url from {schema}.currency")
            return {a: u for a, u in cur.fetchall()}
        except Exception:
            return {}

    # Tkinter isn't thread-safe: loader threads only put results on a Queue;
    # a MAIN-thread poller (_drain_icons) creates the PhotoImage and sets it on
    # the row. (Mirrors the pipeline queue-drain in app.py.) A per-tree
    # generation guards stale writes when a tree is refilled (ids get reused).

    def _ensure_poll(self):
        if self._icon_poll is None:
            self._icon_poll = self.after(60, self._drain_icons)

    def _drain_icons(self):
        self._icon_poll = None
        try:
            while True:
                msg = self._icon_q.get_nowait()
                kind = msg[0]
                if kind == "row":
                    _, tv, rid, api, pil, gen = msg
                    self._apply_icon(tv, rid, api, pil, gen)
                elif kind == "hdr":
                    _, api, pil, gen = msg
                    self._apply_header_icon(api, pil, gen)
                elif kind == "done":
                    self._icon_active = max(0, self._icon_active - 1)
        except queue.Empty:
            pass
        if self._icon_active > 0:
            self._icon_poll = self.after(60, self._drain_icons)

    def _load_icons(self, tv, ids):
        """Fetch row icons in a background pool; results stream back via the queue
        and are applied by the main-thread poller, so icons pop in progressively."""
        if not ids:
            return
        gen = self._icon_gen.get(tv, 0) + 1
        self._icon_gen[tv] = gen
        urls = self._icon_urls
        items = list(ids)
        self._icon_active += 1
        self._ensure_poll()

        def work():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            try:
                with ThreadPoolExecutor(max_workers=8) as ex:
                    futs = {ex.submit(self.icons.get_pil, api, urls.get(api), ICON_SIZE):
                            (rid, api) for rid, api in items}
                    for fut in as_completed(futs):
                        rid, api = futs[fut]
                        try:
                            pil = fut.result()
                        except Exception:
                            pil = None
                        if pil is not None:
                            self._icon_q.put(("row", tv, rid, api, pil, gen))
            finally:
                self._icon_q.put(("done",))

        threading.Thread(target=work, daemon=True).start()

    def _apply_icon(self, tv, rid, api, pil, gen):
        if self._icon_gen.get(tv) != gen:
            return                       # a newer fill superseded this one
        try:
            if not tv.exists(rid):
                return
            ph = self.icons.get_photo(api, ICON_SIZE, pil, self)
            tv.item(rid, image=ph)
        except tk.TclError:
            pass

    def _load_header_icon(self, api):
        self._hdr_gen += 1
        gen = self._hdr_gen
        url = self._icon_urls.get(api)
        self._icon_active += 1
        self._ensure_poll()

        def work():
            try:
                pil = self.icons.get_pil(api, url, HDR_ICON_SIZE)
                if pil is not None:
                    self._icon_q.put(("hdr", api, pil, gen))
            finally:
                self._icon_q.put(("done",))

        threading.Thread(target=work, daemon=True).start()

    def _apply_header_icon(self, api, pil, gen):
        if gen != self._hdr_gen:
            return
        try:
            ph = self.icons.get_photo(api, HDR_ICON_SIZE, pil, self)
            self.cur_icon.config(image=ph)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # search field placeholder
    # ------------------------------------------------------------------

    def _entry_focus_in(self, _e):
        if self._placeholder_on:
            self.entry.delete(0, "end")
            self.entry.config(fg=FG)
            self._placeholder_on = False

    def _entry_focus_out(self, _e):
        if not self.entry.get().strip():
            self._set_placeholder()

    def _set_placeholder(self):
        self.entry.delete(0, "end")
        self.entry.insert(0, PLACEHOLDER)
        self.entry.config(fg=FG_MUTED)
        self._placeholder_on = True

    def _clear_entry(self):
        self.entry.delete(0, "end")
        self._set_placeholder()

    def _entry_value(self):
        return "" if self._placeholder_on else self.entry.get().strip()

    # ------------------------------------------------------------------
    # data
    # ------------------------------------------------------------------

    def refresh(self):
        conn = None
        try:
            conn = self._conn()
            cur = conn.cursor()
            try:
                self._schema = derive.resolve_schema(cur)
                self._short = derive.league_short(cur, self._schema)
            except RuntimeError:
                self._schema = None
                self._icon_urls = {}
                self._fill(self.board_tv, [])
                self._show_placeholder(self.board_ph, "no data yet — run a cycle  ▷")
                self.status.config(text="no cx_* schema", fg=FG_MUTED)
                return
            self._icon_urls = self._load_icon_urls(cur, self._schema)
            rows = derive.liquidity_board(cur, self._schema)
            ids = self._fill(
                self.board_tv,
                [(api, api, (n, f"{derive._f(vwap):.4f}", f"{derive._f(tv):,.0f}"))
                 for api, n, vwap, tv in rows],
                keys=[(api.lower(), n, derive._f(vwap), derive._f(tv))
                      for api, n, vwap, tv in rows])
            self._load_icons(self.board_tv, ids)
            if rows:
                self._hide_placeholder(self.board_ph)
            else:
                self._show_placeholder(self.board_ph, "no liquid pairs this hour")
            self._set_freshness(cur, self._schema)
        except Exception as e:
            traceback.print_exc()        # also surface in the terminal log
            self.status.config(text=f"err: {e}", fg=RED)
        finally:
            if conn is not None:
                conn.close()

    def lookup(self):
        api = self._entry_value()
        if not api:
            return
        conn = None
        try:
            conn = self._conn()
            cur = conn.cursor()
            schema = self._schema or derive.resolve_schema(cur)
            rows = derive.currency_view(cur, schema, api)
            self._cur_api = api
            ids = self._fill(self.cur_tv, *self._cur_rows(api, rows))
            self._load_icons(self.cur_tv, ids)
            self.cur_title.config(text=api, fg=FG)
            if rows:
                self._hide_placeholder(self.cur_ph)
                self.cur_sub.config(text=self._summary(rows, derive.ninja_epoch(cur, schema)))
                self._load_header_icon(api)
            else:
                self._show_placeholder(self.cur_ph, f"no traded pairs for “{api}”")
                self.cur_sub.config(text="")
                self.cur_icon.config(image="")
        except Exception as e:
            traceback.print_exc()        # also surface in the terminal log
            self.cur_title.config(text="error", fg=RED)
            self.cur_sub.config(text=str(e)[:60])
            self.cur_icon.config(image="")
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _cur_rows(api, rows):
        """Currency-view fill list + its sort keys: the key-pair leg (counter ==
        KEY_COUNTER) pinned first, then the rest in rank order (traded desc).
        Columns: price (X per 1 counter, this hour) · avg 6h (poe.ninja) · 7d
        (poe.ninja) · traded (base value this hour). When the currency has legs
        but none against the key pair, a muted "no data" stub holds the top slot
        instead — except for the key currency itself, which has no leg against
        itself. Empty `rows` stays empty (the placeholder speaks). The keys
        mirror the columns as raw numbers (None where the cell is blank, so such
        rows trail a sort); the stub has none -- it is pinned, never sorted."""
        num = lambda x: None if x is None else float(x)
        key = [r for r in rows if r[0] == KEY_COUNTER]
        rest = [r for r in rows if r[0] != KEY_COUNTER]
        fill, keys = [], []
        for counter, price, avg6h, ch7, traded, *_raw in (key + rest)[:300]:
            row = (counter, counter,
                   (derive.fmt_price(price), derive.fmt_price(avg6h),
                    derive.fmt_pct(ch7), f"{derive._f(traded):,.0f}"))
            fill.append(row + (("key",),) if counter == KEY_COUNTER else row)
            keys.append((counter.lower(), num(price), num(avg6h), num(ch7),
                         derive._f(traded)))
        if rows and not key and api != KEY_COUNTER:
            fill.insert(0, (KEY_COUNTER, f"{KEY_COUNTER} · no data",
                            ("—", "—", "—", "—"), ("missing",)))
            keys.insert(0, None)
        return fill, keys

    @classmethod
    def _summary(cls, rows, ninja_epoch=None):
        """One-line readout: pairs · base value traded this hour · age of the
        poe.ninja snapshot behind avg 6h / 7d (mirrors cx.derive.main)."""
        traded = sum(derive._f(r[4]) for r in rows)
        ninja = f"ninja {cls._ago(ninja_epoch)}" if ninja_epoch else "ninja: no data"
        return f"{len(rows)} pairs · {traded:,.0f} traded · {ninja}"

    def _set_freshness(self, cur, schema):
        try:
            cur.execute(f"select max(hour_epoch) from {schema}.pair_snapshot")
            epoch = cur.fetchone()[0]
        except Exception:
            epoch = None
        if epoch:
            self.status.config(text=f"{schema} · {self._ago(epoch)}", fg=FG_DIM)
        else:
            self.status.config(text=schema, fg=FG_DIM)

    @staticmethod
    def _ago(epoch):
        d = max(0.0, time.time() - float(epoch))
        if d < 3600:
            return f"{int(d // 60)}m ago"
        if d < 86400:
            return f"{int(d // 3600)}h ago"
        return f"{int(d // 86400)}d ago"

    # ------------------------------------------------------------------

    def _on_cur_click(self, ev):
        """A click on a counter row pops the pair chart (the currency shown vs
        that counter). The header and the muted "no data" stub are ignored; a
        new row replaces the open card."""
        tv = self.cur_tv
        rowid = tv.identify_row(ev.y)
        if not rowid or "missing" in tv.item(rowid, "tags") or not self._cur_api:
            return
        counter = tv.item(rowid, "text")
        self._close_chart()
        try:
            self._chart = PairChart(self, self._schema, self._short, self._cur_api,
                                    counter, ev, tm=self.tm)
        except Exception:
            traceback.print_exc()

    def _close_chart(self):
        if self._chart is not None:
            try:
                self._chart.destroy()
            except Exception:
                pass
            self._chart = None

    def _on_board_click(self, _event):
        sel = self.board_tv.selection()
        if not sel:
            return
        api = self.board_tv.item(sel[0], "text")    # currency name lives in #0
        self._placeholder_on = False
        self.entry.config(fg=FG)
        self.entry.delete(0, "end")
        self.entry.insert(0, api)
        self.lookup()
