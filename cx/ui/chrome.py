"""Frameless-window chrome for the cx app.

Small, dependency-light ports of the launcher / doc_nav idioms — segmented
action cells, single-instance mutex, relaunch interpreter, and a bottom-right
resize grip (a frameless window has no native resize border). `launcher.app`
itself can't be imported (it drags in process_runner / hotkeys / persistence),
so the ~40 lines we actually want live here.
"""

import ctypes
import sys
import tkinter as tk
from pathlib import Path

from .theme import BG, BG2, BORDER, FG, FG_MUTED, HOVER_BG

# Segmented-control geometry (matches launcher/app.py)
CELL_H = 22
CELL_W_GLYPH = 24
CELL_W_RUN = 84


def seg_cell(parent, text, *, width=CELL_W_GLYPH, primary=False, first=False,
             font=None, fg=None):
    """One segmented-control cell. Returns (outer_frame, inner_label).

    Outer Frame is `bg=BORDER`; the inner Label is `place`-d so 1px of the outer
    shows through as a hairline on all sides except left (left border drawn only
    on the first cell — neighbours share borders). The label stores `_base_bg` /
    `_hover_bg` / `_active` so `bind_seg_hover` can coexist with sticky states.
    """
    base_bg = HOVER_BG if primary else BG2
    outer = tk.Frame(parent, bg=BORDER, width=width, height=CELL_H)
    outer.pack_propagate(False)
    lbl = tk.Label(outer, text=text, bg=base_bg, fg=fg or FG,
                   font=font or ("Segoe UI", 9), cursor="hand2")
    if first:
        lbl.place(x=1, y=1, width=width - 2, height=CELL_H - 2)
    else:
        lbl.place(x=0, y=1, width=width - 1, height=CELL_H - 2)
    lbl._base_bg = base_bg
    lbl._hover_bg = BORDER if primary else HOVER_BG
    lbl._active = False
    return outer, lbl


def bind_seg_hover(lbl):
    """Hover bg flip that respects the cell's `_active` sticky state."""
    def enter(_e):
        if not lbl._active:
            lbl.configure(bg=lbl._hover_bg)

    def leave(_e):
        if not lbl._active:
            lbl.configure(bg=lbl._base_bg)

    lbl.bind("<Enter>", enter)
    lbl.bind("<Leave>", leave)


# ---------------------------------------------------------------------------
# Frameless lifecycle (ported from doc_nav/app.py)
# ---------------------------------------------------------------------------

_mutex_handle = None


def pythonw_exe():
    """The console-less interpreter for relaunch: pythonw.exe beside python.exe
    when started via python.exe, else sys.executable as-is."""
    py = Path(sys.executable)
    if py.stem.lower() == "python":
        pw = py.with_name("pythonw.exe")
        if pw.exists():
            return pw
    return py


def mutex_exists(name):
    """True if a named mutex is already held (another instance is running)."""
    k = ctypes.windll.kernel32
    h = k.OpenMutexW(0x00100000, False, name)   # SYNCHRONIZE
    if h:
        k.CloseHandle(h)
        return True
    return False


def try_single_instance(name):
    """True if we are the only instance; False if one is already running."""
    global _mutex_handle
    if mutex_exists(name):
        return False
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    return True


def release_instance():
    """Drop the single-instance lock so a relaunch replacement can claim it."""
    global _mutex_handle
    if _mutex_handle:
        try:
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        except Exception:
            pass
        _mutex_handle = None


# ---------------------------------------------------------------------------
# Resize grip — a frameless window has no native resize border
# ---------------------------------------------------------------------------

class ResizeGrip(tk.Canvas):
    """Bottom-right corner hatch; drag to resize the (frameless) root window."""

    def __init__(self, parent, root, size=16, bg=BG, min_w=620, min_h=380):
        super().__init__(parent, width=size, height=size, bg=bg,
                         highlightthickness=0, cursor="sizing")
        self._root = root
        self._size = size
        self._min_w = min_w
        self._min_h = min_h
        for off in (4, 8, 12):
            self.create_line(size - off, size - 2, size - 2, size - off,
                             fill=FG_MUTED)
        self.bind("<ButtonPress-1>", self._start)
        self.bind("<B1-Motion>", self._drag)

    def _start(self, e):
        self._ox, self._oy = e.x_root, e.y_root
        self._ow = self._root.winfo_width()
        self._oh = self._root.winfo_height()

    def _drag(self, e):
        w = max(self._min_w, self._ow + (e.x_root - self._ox))
        h = max(self._min_h, self._oh + (e.y_root - self._oy))
        self._root.geometry(f"{w}x{h}")
