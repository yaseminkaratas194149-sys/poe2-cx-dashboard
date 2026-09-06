"""PairChart — a frameless card with the exchange-rate history of one pair.

Opened by a click on a counter row of the currency view (cx_panel): the pair is
the selected currency X and the row's counter Y. The line is the realized price
of Y in X per hour (volume_X / volume_Y — the table's `price` column), the bars
underneath the base value traded that hour; hovering reads one hour out; the
range cells pick the last N days or everything; the stats line gives last /
min / max / VWAP / change over the window. Drawn on a plain Tk Canvas in the
app's palette (no matplotlib: lighter, and it stays in the theme). Untraded
hours are gaps, not zeros — the line breaks across them.

Data: the pair's series from pair_snapshot (derive.pair_series). On open the
card draws what the store holds, then a background thread pulls the pair's
WHOLE history from poe2scout (backfill.pair_full_history — league start to
now, one call per ~5000 hours), UPSERTs it into pair_snapshot and the card
redraws. So the second open is instant and offline, the pull repeats at most
hourly per pair, and the hourly cycle keeps appending hours from there.

Closes on Esc, the ✕, or losing focus; a click on another row replaces it.
Inspector eids: chart.title / chart.canvas / chart.stats / chart.range.<N>.
"""
import math
import threading
import time
import tkinter as tk
import traceback

import psycopg2

from cx import backfill, config, derive
from .chrome import bind_seg_hover, seg_cell, work_area_at
from .theme import BG2, BG3, BORDER, FG, FG_DIM, FG_MUTED, RED, WARM_YLW

RANGES = [("1d", 1), ("2d", 2), ("3d", 3), ("5d", 5), ("7d", 7), ("14d", 14), ("all", 0)]
DEFAULT_DAYS = 3
CANVAS_W, CANVAS_H = 640, 260
PAD_L, PAD_R, PAD_T, PAD_B = 58, 14, 12, 26
VOL_FRAC = 0.22                     # bottom share of the plot given to the volume bars
LINE = WARM_YLW
BARS = "#3d3d42"
GRID = BORDER
REFETCH_AFTER = 3600                # s: a pair's full history is re-pulled at most hourly
_last_fetch = {}                    # (schema, x, y) -> time of the last full pull

fmt = derive.fmt_price


def _ticks(lo, hi, n=4):
    """Round y-axis tick values between lo and hi (about n of them)."""
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = mag
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    v = math.ceil(lo / step) * step
    out = []
    while v <= hi + 1e-9:
        out.append(v)
        v += step
    return out


def _time_ticks(t0, t1):
    """[(epoch, label)] for the x axis, in local time: 6-hour marks up to two
    days, then days, then every k days so at most ~8 labels appear."""
    span = max(1, t1 - t0)
    if span <= 2 * 86400:
        step, fmt_ = 6 * 3600, "%H:%M"
    elif span <= 16 * 86400:
        step, fmt_ = 86400, "%m-%d"
    else:
        step, fmt_ = math.ceil(span / 86400 / 8) * 86400, "%m-%d"
    lt = time.localtime(t0)
    t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))   # local midnight <= t0
    out = []
    while t <= t1:
        if t >= t0:
            lab = time.strftime(fmt_, time.localtime(t))
            if step < 86400 and time.localtime(t).tm_hour == 0:
                lab = time.strftime("%m-%d", time.localtime(t))
            out.append((t, lab))
        t += step
    return out


class PairChart(tk.Toplevel):
    def __init__(self, parent, schema, short, x_api, y_api, ev, tm=None):
        super().__init__(parent)
        self.schema, self.short, self.x, self.y = schema, short, x_api, y_api
        self.tm = tm
        self.series = []            # [(epoch, price, vol_x, vol_y, value_x)], oldest first
        self.days = DEFAULT_DAYS
        self._cells = {}            # days -> range cell label
        self._pts = []              # [(px, py, row)] of the drawn points, for hover
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BORDER)                          # 1px hairline
        inner = tk.Frame(self, bg=BG2)
        inner.pack(padx=1, pady=1)
        self._build(inner)
        self._tag_widgets()
        self._place(ev)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<FocusOut>", self._on_focus_out)        # click elsewhere closes
        self.focus_force()
        self.reload()
        self._start_fetch()

    # ------------------------------------------------------------------ build

    def _build(self, inner):
        pad = tk.Frame(inner, bg=BG2)
        pad.pack(padx=10, pady=8)

        head = tk.Frame(pad, bg=BG2)
        head.pack(fill="x")
        self.title_lbl = tk.Label(head, text=f"{self.y} / {self.x}", bg=BG2, fg=FG,
                                  font=("Segoe UI", 11, "bold"))
        self.title_lbl.pack(side="left")
        self.sub_lbl = tk.Label(head, text=f"{self.x} per 1 {self.y} · hourly · local time",
                                bg=BG2, fg=FG_DIM, font=("Consolas", 8))
        self.sub_lbl.pack(side="left", padx=(8, 0), pady=(3, 0))
        close = tk.Label(head, text="✕", bg=BG2, fg=FG_MUTED, font=("Consolas", 9),
                         cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        self.status_lbl = tk.Label(head, text="", bg=BG2, fg=FG_MUTED, font=("Consolas", 8))
        self.status_lbl.pack(side="right", padx=(0, 10))

        rng = tk.Frame(pad, bg=BG2)
        rng.pack(fill="x", pady=(6, 4))
        seg = tk.Frame(rng, bg=BG2)
        seg.pack(side="left")
        for i, (label, days) in enumerate(RANGES):
            outer, lbl = seg_cell(seg, label, width=36, first=(i == 0), font=("Segoe UI", 8))
            outer.pack(side="left")
            lbl.bind("<Button-1>", lambda e, d=days: self.set_range(d))
            bind_seg_hover(lbl)
            self._cells[days] = lbl
        self.stats_lbl = tk.Label(rng, text="", bg=BG2, fg=FG_DIM, font=("Consolas", 8))
        self.stats_lbl.pack(side="right")

        self.canvas = tk.Canvas(pad, width=CANVAS_W, height=CANVAS_H, bg=BG3,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("hover"))
        self._mark_range()

    def _tag_widgets(self):
        tm = self.tm
        if tm is None:
            return
        try:
            tm.tag(self.title_lbl, "chart.title")
            tm.tag(self.canvas, "chart.canvas")
            tm.tag(self.stats_lbl, "chart.stats")
            for days, lbl in self._cells.items():
                tm.tag(lbl, f"chart.range.{days or 'all'}")
        except Exception:
            pass

    def _place(self, ev):
        """Near the cursor, on the cursor's monitor (same rule as the uniques card)."""
        self.update_idletasks()
        cw, ch = self.winfo_reqwidth(), self.winfo_reqheight()
        area = work_area_at(ev.x_root, ev.y_root)
        if area is None:
            area = (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())
        left, top, right, bottom = area
        x = max(left + 8, min(ev.x_root + 16, right - cw - 8))
        y = max(top + 8, min(ev.y_root + 10, bottom - ch - 8))
        self.geometry(f"+{x}+{y}")

    def _on_focus_out(self, _e):
        """Close when focus leaves the card (a click elsewhere, another window);
        a focus move between the card's own widgets is not a leave."""
        try:
            w = self.focus_get()
        except Exception:
            w = None
        if w is not None and str(w).startswith(str(self)):
            return
        self.close()

    def close(self):
        try:
            self.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------- data

    def reload(self):
        """Re-read the pair's series from the store and redraw."""
        conn = None
        try:
            conn = psycopg2.connect(**config.DB_CONFIG)
            cur = conn.cursor()
            self.series = [(int(e), float(p), int(vx), int(vy), float(v))
                           for e, p, vx, vy, v in derive.pair_series(cur, self.schema, self.x, self.y)]
            cur.close()
        except Exception as e:
            traceback.print_exc()
            self.series = []
            self.status_lbl.config(text=f"err: {e}"[:48], fg=RED)
        finally:
            if conn is not None:
                conn.close()
        self.draw()

    def _start_fetch(self):
        """Pull the pair's whole history off the UI thread (at most hourly per
        pair), then reload on the main thread if the card is still open."""
        key = (self.schema, self.x, self.y)
        if not self.short or time.time() - _last_fetch.get(key, 0) < REFETCH_AFTER:
            return
        self.status_lbl.config(text="history: fetching…", fg=FG_MUTED)

        def work():
            try:
                res = backfill.pair_full_history(self.schema, self.short, self.x, self.y)
                _last_fetch[key] = time.time()
                msg, fg = f"history: {res['hours']} h · {res['calls']} call(s)", FG_MUTED
            except Exception as e:
                traceback.print_exc()
                msg, fg = f"history: {str(e)[:40]}", RED
            try:
                self.after(0, lambda: self._after_fetch(msg, fg))
            except Exception:
                pass                                   # card closed meanwhile

        threading.Thread(target=work, daemon=True).start()

    def _after_fetch(self, msg, fg):
        if not self.winfo_exists():
            return
        self.status_lbl.config(text=msg, fg=fg)
        self.reload()

    # ------------------------------------------------------------------ range

    def set_range(self, days):
        self.days = days
        self._mark_range()
        self.draw()

    def _mark_range(self):
        for days, lbl in self._cells.items():
            on = (days == self.days)
            lbl._active = on
            lbl.configure(bg=lbl._hover_bg if on else lbl._base_bg)

    def _window(self):
        """The rows in the selected range, anchored on the newest stored hour
        (so a stale store still shows its last N days)."""
        if not self.series or not self.days:
            return list(self.series)
        cut = self.series[-1][0] - self.days * 86400 + 3600
        return [r for r in self.series if r[0] >= cut]

    # ------------------------------------------------------------------- draw

    def draw(self):
        c = self.canvas
        c.delete("all")
        self._pts = []
        rows = self._window()
        if not rows:
            c.create_text(CANVAS_W / 2, CANVAS_H / 2, fill=FG_MUTED, font=("Segoe UI", 9),
                          text=("no traded hours in the store yet" if not self.series
                                else "no trades in this range"))
            self.stats_lbl.config(text="")
            return
        x0, x1 = PAD_L, CANVAS_W - PAD_R
        y0, y1 = PAD_T, CANVAS_H - PAD_B
        vol_h = (y1 - y0) * VOL_FRAC
        ly1 = y1 - vol_h - 4                               # bottom of the line band
        t0, t1 = rows[0][0], rows[-1][0]
        if t1 == t0:
            t1 = t0 + 3600
        prices = [r[1] for r in rows]
        pmin, pmax = min(prices), max(prices)
        if pmax == pmin:
            pmin, pmax = pmin * 0.95, pmax * 1.05 or 1.0
        padv = (pmax - pmin) * 0.06
        pmin, pmax = pmin - padv, pmax + padv
        vmax = max(r[4] for r in rows) or 1.0

        def sx(t):
            return x0 + (t - t0) / (t1 - t0) * (x1 - x0)

        def sy(p):
            return ly1 - (p - pmin) / (pmax - pmin) * (ly1 - y0)

        for v in _ticks(pmin, pmax):
            y = sy(v)
            c.create_line(x0, y, x1, y, fill=GRID)
            c.create_text(x0 - 6, y, text=fmt(v), fill=FG_DIM, anchor="e", font=("Consolas", 8))
        for t, label in _time_ticks(t0, t1):
            x = sx(t)
            c.create_line(x, y0, x, y1, fill=GRID, dash=(1, 3))
            c.create_text(x, y1 + 4, text=label, fill=FG_DIM, anchor="n", font=("Consolas", 8))

        hours = max(1, (t1 - t0) / 3600)
        bw = max(1.0, min(8.0, (x1 - x0) / hours - 1))
        for r in rows:
            x = sx(r[0])
            h = r[4] / vmax * vol_h
            c.create_rectangle(x - bw / 2, y1 - h, x + bw / 2, y1, fill=BARS, outline="")

        pts = [(sx(r[0]), sy(r[1]), r) for r in rows]
        self._pts = pts
        seg, prev = [], None
        for x, y, r in pts:
            if prev is not None and r[0] - prev[0] > 3600:     # untraded hour(s): break
                self._draw_seg(seg)
                seg = []
            seg.append((x, y))
            prev = r
        self._draw_seg(seg)
        if len(pts) <= 200:
            for x, y, _r in pts:
                c.create_oval(x - 1.5, y - 1.5, x + 1.5, y + 1.5, fill=LINE, outline="")

        last, first = rows[-1][1], rows[0][1]
        vwap = sum(r[2] for r in rows) / (sum(r[3] for r in rows) or 1)
        chg = (last / first - 1) * 100 if first else 0.0
        self.stats_lbl.config(
            text=f"last {fmt(last)} · min {fmt(min(prices))} · max {fmt(max(prices))} · "
                 f"vwap {fmt(vwap)} · {chg:+.0f}% · {len(rows)} h")

    def _draw_seg(self, seg):
        if len(seg) >= 2:
            self.canvas.create_line(*[v for xy in seg for v in xy], fill=LINE, width=1.5)
        elif len(seg) == 1:
            x, y = seg[0]
            self.canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=LINE, outline="")

    def _hover(self, ev):
        c = self.canvas
        c.delete("hover")
        if not self._pts:
            return
        x, y, r = min(self._pts, key=lambda p: abs(p[0] - ev.x))
        if abs(x - ev.x) > 30:
            return
        c.create_line(x, PAD_T, x, CANVAS_H - PAD_B, fill=FG_MUTED, tags="hover")
        c.create_oval(x - 3, y - 3, x + 3, y + 3, outline=LINE, fill=BG3, tags="hover")
        when = time.strftime("%m-%d %H:%M", time.localtime(r[0]))
        txt = f"{when}   {fmt(r[1])}   {r[2]:,} {self.x} ↔ {r[3]:,} {self.y}"
        anchor = "nw" if x < CANVAS_W / 2 else "ne"
        tx = x + 8 if anchor == "nw" else x - 8
        t = c.create_text(tx, PAD_T + 2, text=txt, fill=FG, anchor=anchor,
                          font=("Consolas", 8), tags="hover")
        bb = c.bbox(t)
        c.create_rectangle(bb[0] - 3, bb[1] - 2, bb[2] + 3, bb[3] + 2, fill=BG2, outline=BORDER,
                           tags="hover")
        c.tag_raise(t)
