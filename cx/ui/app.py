"""CxApp — the cx window: a frameless floating dashboard in the launcher /
doc_nav visual language.

`python -m cx` launches this. A custom toolbar doubles as the title bar (drag to
move); "▷ Run cycle" pulls fresh data through the pipeline in a thread while a
status ring sweeps the live stage progress, then refreshes the view;
"⇊ Actualize" runs the full cycle (league resolve, pairs, backfill, uniques,
trade dict) and refreshes every view. The pin holds the window above everything
else (on by default — click it to let other windows cover cx; starting cx again
calls the window back to the front). ↻ relaunches, ✕ quits. Single-instance;
Ctrl+Q quits.
"""
import json
import os
import queue as _queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk

from .theme import (BG, BG2, BG3, FG, FG_DIM, FG_MUTED, GRAY, BORDER,
                    BORDER_DARK, GREEN, DULL_GRN, RED, setup_styles)
from .chrome import (seg_cell, bind_seg_hover, draw_pin, ResizeGrip,
                     CELL_W_GLYPH, PIN_SIZE, pythonw_exe, try_single_instance,
                     release_instance, mutex_exists, signal_summon)
from .cx_panel import CxPanel
from .trade_panel import TradePanel
from .uniques_panel import UniquesPanel

# Universal ticket+inspector kit (C:\TP3\tiket_master). cx/__init__ already put
# C:\TP3 on sys.path, so this resolves like the launcher / DATA imports above.
from tiket_master import mount

# ProgressRing is a clean, standalone launcher widget (imports only its theme).
# Path to C:\TP3 is already on sys.path via .theme; fall back to a plain dot.
try:
    from launcher.progress_ring import ProgressRing
except Exception:
    class ProgressRing:  # minimal fallback: a solid status dot
        def __init__(self, canvas, bg_color, ring_color):
            self.canvas = canvas
            self._id = canvas.create_oval(3, 3, 15, 15, fill=ring_color, outline="")

        def reset(self):
            pass

        def set_color(self, color, is_running=False):
            try:
                self.canvas.itemconfig(self._id, fill=color)
            except Exception:
                pass

        def parse_line(self, text):
            pass


_MUTEX_NAME = "CxPoe2_Running"
# Raised by a second `python -m cx` to call the running window to the front —
# the way back for an unpinned window that slid behind the game (frameless, so
# it is in neither the taskbar nor Alt-Tab). Polled below.
_SUMMON_NAME = "CxPoe2_Summon"
_SUMMON_POLL_MS = 700
_RUN_TEXT = "▷ Run cycle"
_ACT_TEXT = "⇊ Actualize"


class CxApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self._pinned = True                # topmost, until the pin says otherwise
        self.attributes("-topmost", self._pinned)
        self.configure(bg=BORDER)          # 1px hairline border
        self._running = False
        self._poll_job = None
        self._cycle_error = False
        self._full = False
        self._summon_seen = False
        self._drag = (0, 0, 0, 0)

        style = ttk.Style(self)
        setup_styles(style)                # clam + subtle scrollbars
        self._style_trees(style)

        self.body = tk.Frame(self, bg=BG)
        self.body.pack(padx=1, pady=1, fill="both", expand=True)

        # Mount the universal ticket+inspector before building chrome, so the
        # toolbar can wire its ≡ button and tag widgets through the handle.
        self.tm = mount(self, ticket_path=r"C:\POE\POE2\cx_tickets.md",
                        header="# cx tickets")

        self._build_toolbar()
        sep = tk.Frame(self.body, bg=BORDER_DARK, height=1)
        sep.pack(side="top", fill="x")
        self._build_footer()

        self.panel = CxPanel(self.body, tm=self.tm)
        self.uniques = UniquesPanel(self.body, tm=self.tm)
        self.trade = TradePanel(self.body, tm=self.tm)
        self._views = {"currency": self.panel, "uniques": self.uniques,
                       "trade": self.trade}
        self._view = "currency"
        self._show_view("currency")

        self.bind("<Control-q>", lambda e: self._quit())
        self.geometry("1000x640")
        self.after_idle(self._place_top_right)
        self.after(_SUMMON_POLL_MS, self._poll_summon)

    # ------------------------------------------------------------------
    # styling
    # ------------------------------------------------------------------

    def _style_trees(self, style):
        style.configure("Treeview", background=BG3, fieldbackground=BG3,
                        foreground=FG, borderwidth=0, rowheight=24,
                        font=("Consolas", 9))
        style.configure("Treeview.Heading", background=BG2, foreground=FG_DIM,
                        borderwidth=0, font=("Segoe UI", 8, "bold"))
        style.map("Treeview", background=[("selected", DULL_GRN)],
                  foreground=[("selected", FG)])
        style.map("Treeview.Heading", background=[("active", BG3)])

    # ------------------------------------------------------------------
    # chrome
    # ------------------------------------------------------------------

    def _build_toolbar(self):
        tb = tk.Frame(self.body, bg=BG)
        tb.pack(side="top", fill="x")

        title = tk.Label(tb, text="cx", bg=BG, fg=FG,
                         font=("Segoe UI", 10, "bold"))
        title.pack(side="left", padx=(8, 6), pady=4)
        sub = tk.Label(tb, text="PoE2 Currency Exchange", bg=BG, fg=FG_MUTED,
                       font=("Segoe UI", 8))
        sub.pack(side="left", pady=4)

        # view toggle — Currency | Uniques (sticky active state on DULL_GRN)
        vseg = tk.Frame(tb, bg=BG)
        vseg.pack(side="left", padx=(12, 0), pady=3)
        o_cv, self.btn_view_cur = seg_cell(vseg, "Currency", width=76, first=True,
                                           font=("Segoe UI", 9))
        o_cv.pack(side="left")
        self.btn_view_cur.bind("<Button-1>", lambda e: self._show_view("currency"))
        bind_seg_hover(self.btn_view_cur)
        o_uq, self.btn_view_uniq = seg_cell(vseg, "Uniques", width=68,
                                            font=("Segoe UI", 9))
        o_uq.pack(side="left")
        self.btn_view_uniq.bind("<Button-1>", lambda e: self._show_view("uniques"))
        bind_seg_hover(self.btn_view_uniq)
        o_tr, self.btn_view_trade = seg_cell(vseg, "Trade", width=56,
                                             font=("Segoe UI", 9))
        o_tr.pack(side="left")
        self.btn_view_trade.bind("<Button-1>", lambda e: self._show_view("trade"))
        bind_seg_hover(self.btn_view_trade)

        # drag the window by the toolbar (title bar replacement)
        for w in (tb, title, sub):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

        # status ring (multi-segment during a cycle; idle dot otherwise)
        self.ring_canvas = tk.Canvas(tb, width=18, height=18, bg=BG,
                                     highlightthickness=0)
        self.ring_canvas.pack(side="left", padx=(8, 0), pady=2)
        self.ring = ProgressRing(self.ring_canvas, bg_color=BG, ring_color=GREEN)
        self.ring.set_color(GRAY)

        # right: segmented group  ≡ · ▷ Run cycle · ↻ · ✕
        seg = tk.Frame(tb, bg=BG)
        seg.pack(side="right", padx=(0, 6), pady=3)
        o_tk, self.btn_tickets = seg_cell(seg, "≡", width=CELL_W_GLYPH,
                                          first=True, font=("Consolas", 13))
        o_tk.pack(side="left")
        self.btn_tickets.bind("<Button-1>", lambda e: self.tm.toggle_tickets())
        bind_seg_hover(self.btn_tickets)

        o_run, self.btn_run = seg_cell(seg, _RUN_TEXT, width=96, primary=True,
                                       font=("Segoe UI", 9))
        o_run.pack(side="left")
        self.btn_run.bind("<Button-1>", lambda e: self._run_cycle())
        bind_seg_hover(self.btn_run)

        o_act, self.btn_actualize = seg_cell(seg, _ACT_TEXT, width=96, primary=True,
                                             font=("Segoe UI", 9))
        o_act.pack(side="left")
        self.btn_actualize.bind("<Button-1>", lambda e: self._run_cycle(full=True))
        bind_seg_hover(self.btn_actualize)

        o_rl, self.btn_relaunch = seg_cell(seg, "↻", width=CELL_W_GLYPH,
                                           font=("Consolas", 11))
        o_rl.pack(side="left")
        self.btn_relaunch.bind("<Button-1>", lambda e: self._relaunch())
        bind_seg_hover(self.btn_relaunch)

        o_cl, self.btn_close = seg_cell(seg, "✕", width=CELL_W_GLYPH,
                                        font=("Segoe UI", 10))
        o_cl.pack(side="left")
        self.btn_close._hover_bg = "#c42b1c"        # red on hover
        self.btn_close.bind("<Button-1>", lambda e: self._quit())
        bind_seg_hover(self.btn_close)

        # pin: bead+needle on a Canvas, left of the action group (the launcher
        # puts it in the same place). Bead interior is white while the window is
        # topmost and drops to BG — a hollow ring — once it is not.
        self.pin_btn = tk.Canvas(tb, width=PIN_SIZE, height=PIN_SIZE, bg=BG,
                                 highlightthickness=0, cursor="hand2")
        draw_pin(self.pin_btn, fill_color="#ffffff", outline_color=FG)
        self.pin_btn.pack(side="right", padx=(2, 2))
        self.pin_btn.bind("<Button-1>", self._toggle_topmost)

        self.lbl = tk.Label(tb, text="", bg=BG, fg=FG_DIM, font=("Consolas", 8))
        self.lbl.pack(side="right", padx=6)

        # Make the toolbar greppable to the inspector (eids literal in source).
        for w, eid in ((tb, "toolbar"), (title, "toolbar.title"),
                       (sub, "toolbar.subtitle"),
                       (self.btn_tickets, "toolbar.tickets"),
                       (self.btn_run, "toolbar.run"),
                       (self.btn_actualize, "toolbar.actualize"),
                       (self.btn_relaunch, "toolbar.relaunch"),
                       (self.btn_close, "toolbar.close"),
                       (self.pin_btn, "toolbar.pin"),
                       (self.lbl, "toolbar.status")):
            self.tm.tag(w, eid)

    def _build_footer(self):
        footer = tk.Frame(self.body, bg=BG)
        footer.pack(side="bottom", fill="x")
        ResizeGrip(footer, self, size=16, bg=BG).pack(side="right", padx=2, pady=2)

    def _show_view(self, name):
        """Swap the main area between currency / uniques / trade."""
        self._view = name
        for p in self._views.values():
            p.pack_forget()
        self._views[name].pack(side="top", fill="both", expand=True,
                               padx=8, pady=(6, 4))
        for b, key in ((self.btn_view_cur, "currency"),
                       (self.btn_view_uniq, "uniques"),
                       (self.btn_view_trade, "trade")):
            on = key == name
            b._active = on
            b.config(bg=(DULL_GRN if on else b._base_bg))

    # ------------------------------------------------------------------
    # window placement / drag / pin
    # ------------------------------------------------------------------

    def _toggle_topmost(self, event=None):
        """Pin ⇄ unpin: the only thing holding cx above other windows.

        Not persisted, like the launcher's — a relaunch comes back pinned. The
        detail card and other popups set their own topmost, so they still show
        over the game while the window itself is free to go behind it."""
        self._pinned = not self._pinned
        self.attributes("-topmost", self._pinned)
        draw_pin(self.pin_btn, fill_color=("#ffffff" if self._pinned else BG),
                 outline_color=FG)

    def _poll_summon(self):
        """Watch for the summon flag a second `python -m cx` raises, and come to
        the front once per raising (the flag stays up longer than one poll).

        This is the way back to an unpinned window: run cx again — the launcher's
        `cx` row, or the command — and the window that was behind the game lifts
        itself. Failing to read the flag is harmless: nothing happens."""
        try:
            here = mutex_exists(_SUMMON_NAME)
        except Exception:
            here = False
        if here and not self._summon_seen:
            self._raise_window()
        self._summon_seen = here
        self.after(_SUMMON_POLL_MS, self._poll_summon)

    def _raise_window(self):
        """Lift + take focus, then hand topmost back to whatever the pin says."""
        try:
            self.attributes("-topmost", True)
            self.lift()
            self.focus_force()
            self.after(200, lambda: self.attributes("-topmost", self._pinned))
        except Exception:
            pass

    def _place_top_right(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        w = self.winfo_width()
        self.geometry(f"+{sw - w - 48}+{64}")

    def _drag_start(self, e):
        self._drag = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y())

    def _drag_move(self, e):
        ox, oy, wx, wy = self._drag
        self.geometry(f"+{wx + e.x_root - ox}+{wy + e.y_root - oy}")

    # ------------------------------------------------------------------
    # run cycle — drain the pipeline queue into the status ring
    # ------------------------------------------------------------------

    def _run_cycle(self, full=False):
        """`full` = Actualize: the hourly DAG plus backfill / uniques / trade dict."""
        if self._running:
            return
        self._running = True
        self._full = full
        self._cycle_error = False
        btn = self.btn_actualize if full else self.btn_run
        btn._active = True
        btn.config(text="…", bg=btn._hover_bg)
        self.lbl.config(text="actualizing…" if full else "pulling…", fg=FG_DIM)

        from DATA.pipeline.runner import PipelineRunner
        from cx.stages import build_stages

        q = _queue.Queue()
        stages = build_stages(full)
        for s in stages:
            s._queue = q

        # Announce focal stages so the ring renders one segment per stage; the
        # queue dicts the pipeline emits already match ProgressRing's structured
        # event shape, so we just forward them as <<STAGE>> sentinels.
        self.ring.reset()
        focal = [{"id": s.name, "label": getattr(s, "label", "") or s.name}
                 for s in stages]
        self.ring.parse_line("<<STAGE>>" + json.dumps({"type": "announce",
                                                       "focal": focal}))

        def work():
            try:
                PipelineRunner(stages, q).run_cycle()      # emits its own cycle_done
            except Exception as e:
                q.put({"type": "cycle_done", "error": str(e)})

        threading.Thread(target=work, daemon=True).start()
        self._poll_job = self.after(80, lambda: self._drain(q))

    def _drain(self, q):
        done = None
        try:
            while True:
                msg = q.get_nowait()
                t = msg.get("type")
                if t in ("status", "progress"):
                    self.ring.parse_line("<<STAGE>>" + json.dumps(msg, default=str))
                    if t == "status":
                        st = msg.get("status")
                        if st == "ERROR":
                            self._cycle_error = True
                            self.lbl.config(text=f"{msg.get('stage')}: error", fg=RED)
                        elif st in ("RUNNING", "DONE"):
                            self.lbl.config(text=f"{msg.get('stage')} {st.lower()}",
                                            fg=FG_DIM)
                elif t == "log":
                    self.lbl.config(text=str(msg.get("msg", ""))[:42], fg=FG_DIM)
                elif t == "cycle_done":
                    done = msg
        except _queue.Empty:
            pass

        if done is not None:
            self._after_cycle(done.get("error"))
        else:
            self._poll_job = self.after(120, lambda: self._drain(q))

    def _after_cycle(self, err):
        self._running = False
        self._poll_job = None
        for btn, text in ((self.btn_run, _RUN_TEXT), (self.btn_actualize, _ACT_TEXT)):
            btn._active = False
            btn.config(text=text, bg=btn._base_bg)
        self.ring.reset()
        if err or self._cycle_error:
            self.ring.set_color(RED)
            if err:
                self.lbl.config(text="err: " + str(err)[:36], fg=RED)
        else:
            self.ring.set_color(DULL_GRN)
            self.lbl.config(text="done", fg=FG_DIM)
        self.panel.refresh()
        if self._full:                 # the league / uniques / dictionary may have moved
            self.uniques.refresh()
            self.trade.refresh()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def _relaunch(self):
        release_instance()         # let the replacement take the single-instance lock
        try:
            subprocess.Popen([str(pythonw_exe()), "-m", "cx"], cwd=r"c:\POE\POE2",
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        except Exception:
            pass
        os._exit(0)

    def _quit(self):
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        self.destroy()


def main():
    if not try_single_instance(_MUTEX_NAME):
        signal_summon(_SUMMON_NAME)   # ask the running window to come forward
        return                        # another instance already owns the window
    try:
        CxApp().mainloop()
    finally:
        release_instance()


if __name__ == "__main__":
    main()
