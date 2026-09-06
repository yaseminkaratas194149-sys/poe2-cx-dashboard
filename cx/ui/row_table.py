"""RowTable — a canvas-drawn table that stands in for the slice of ``ttk.Treeview``
the uniques browser uses, with one capability ttk.Treeview can't offer: **per-cell
background colour**.

Why this exists
---------------
ttk.Treeview tags colour a *whole row*, never an individual cell, so the request
to tint each resistance (fire=red / cold=blue / lightning=yellow) is impossible in
a Treeview. The obvious alternative — a frame of ``tk.Label`` cells — would break
the tiket_master inspector: its per-cell ticket grain (``uniques.tree[row][col]``)
is resolved by *geometry* on a SINGLE widget (``identify_row`` / ``identify_column``
in ``inspector._grab`` and inspect-mode ``_hit``). Child-widget cells make
``event.widget`` the child, and the grain collapses to an amber best-effort ref.

A canvas threads both needles: it is one widget (so the inspector sees it exactly
like a Treeview), its rows are canvas *items* (so events always land on the canvas),
and a rectangle behind a cell gives a true per-cell background. So RowTable mimics
the Treeview method-subset the panel calls — ``heading`` / ``column`` /
``tag_configure`` / ``insert`` / ``delete`` / ``move`` / ``get_children`` / ``item`` /
``exists`` / ``identify_row`` / ``identify_column`` / ``cget("columns")`` — plus the
native canvas ``yview`` / ``yscrollcommand`` for the scrollbar. A click on a header
cell runs that column's ``heading(command=...)``, as a Treeview's does. It stays a
drop-in: ``self.tv = RowTable(...)`` and the rest of the panel is unchanged.

Cell values are plain strings, OR a list of ``(text, bg, fg)`` *segments* for a
coloured cell (the resist triad). Segments render left-to-right within the column,
each behind its own tinted rectangle — that is literally "split the field into 3
segments, each a slight background colour".

It is deliberately small and panel-agnostic: it knows nothing about resists, only
about columns, rows, tags and coloured segments. The resist semantics live in the
panel. Lists here are short (≤ a few hundred rows), so a full redraw per fill is
fine and there is no virtualisation to reason about.
"""
import tkinter as tk
import tkinter.font as tkfont

from .theme import BG2, BG3, FG, FG_DIM, BORDER


class RowTable(tk.Canvas):
    def __init__(self, parent, columns, name_title="", rowheight=26,
                 headerheight=23, icon_w=22, bg=BG2, header_bg=BG3,
                 fg=FG, fg_dim=FG_DIM, font=("Segoe UI", 9),
                 numfont=("Consolas", 9)):
        super().__init__(parent, highlightthickness=0, bd=0, bg=bg,
                         takefocus=0)
        self._defbg = bg
        self._deffg = fg
        self._fg_dim = fg_dim
        self._rh = rowheight
        self._icon_w = icon_w
        self._font = tkfont.Font(font=font)
        self._numfont = tkfont.Font(font=numfont)

        # column model: "#0" is the name/icon column, then the data columns
        self._cols = list(columns)
        self._cfg = {"#0": {"width": 200, "minwidth": 80, "anchor": "w",
                            "stretch": True, "title": name_title}}
        for c in self._cols:
            self._cfg[c] = {"width": 80, "minwidth": 20, "anchor": "w",
                            "stretch": False, "title": ""}
        self._colx = {}              # colid -> (x0, x1) after layout
        self._content_w = 0

        self._tagcfg = {}            # tag -> {"background":.., "foreground":..}
        self._rec = {}               # rowid -> record
        self._kids = {"": []}        # parent rowid -> [child rowids]
        self._rows = []              # flattened visible order
        self._index = {}             # rowid -> display index
        self._photos = {}            # rowid -> PhotoImage (GC anchor)
        self._seq = 0
        self._refresh_pending = False

        # header is a sibling canvas pinned above this (body) canvas; packing it
        # side=top in the shared parent reserves the strip, then the panel packs
        # the scrollbar (right) and this body (left) into the cavity below.
        self._header = tk.Canvas(parent, height=headerheight, bg=header_bg,
                                 highlightthickness=0, bd=0, takefocus=0)
        self._header.pack(side="top", fill="x")
        self._header.bind("<Button-1>", self._on_header_click)

        self.bind("<Configure>", lambda e: self._schedule())
        # the wheel: a Canvas has no default scroll (Treeview did). Bind it only
        # while the pointer is over the list, so it doesn't fight other regions.
        self.configure(yscrollincrement=rowheight)
        self.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self._on_wheel))
        self.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))

    def _on_wheel(self, e):
        self.yview_scroll(-3 if e.delta > 0 else 3, "units")
        return "break"

    def _on_header_click(self, e):
        """Header click -> that column's ``heading(command=...)`` (Treeview's
        contract); a column without one ignores the click. Resolved by x against
        the laid-out column boxes, so it tracks stretch and hidden (0-width) columns."""
        for c, (x0, x1) in self._colx.items():
            if x0 <= e.x < x1:
                cmd = self._cfg[c].get("command")
                if cmd is not None:
                    cmd()
                return

    # -- tiket_master needs these set by tag_table; nothing here references them,
    #    but identify_row/identify_column below are what it actually calls.

    # ------------------------------------------------------------------ columns
    def heading(self, col, text=None, command=None, **kw):
        if text is not None:
            self._cfg[col]["title"] = text
        if command is not None:
            self._cfg[col]["command"] = command
        self._schedule()

    def column(self, col, width=None, minwidth=None, anchor=None,
               stretch=None, **kw):
        c = self._cfg[col]
        if width is not None:
            c["width"] = width
        if minwidth is not None:
            c["minwidth"] = minwidth
        if anchor is not None:
            c["anchor"] = anchor
        if stretch is not None:
            c["stretch"] = bool(stretch)
        self._schedule()

    def tag_configure(self, tag, **kw):
        self._tagcfg.setdefault(tag, {}).update(
            {k: v for k, v in kw.items() if v is not None})

    def cget(self, option):
        if option == "columns":
            return tuple(self._cols)
        if option == "displaycolumns":
            return ("#all",)
        return super().cget(option)

    # ------------------------------------------------------------------- rows
    def insert(self, parent, index, text="", values=(), tags=(), image=None,
               open=True, **kw):
        self._seq += 1
        rid = f"I{self._seq:05d}"
        self._rec[rid] = {"id": rid, "parent": parent, "text": text,
                          "values": list(values), "tags": tuple(tags),
                          "image": image, "open": open}
        kids = self._kids.setdefault(parent, [])
        if index == "end" or index >= len(kids):
            kids.append(rid)
        else:
            kids.insert(index, rid)
        self._kids.setdefault(rid, [])
        self._schedule()
        return rid

    def delete(self, *ids):
        for rid in ids:
            self._drop(rid)
        self._schedule()

    def _drop(self, rid):
        rec = self._rec.pop(rid, None)
        if rec is None:
            return
        for child in list(self._kids.get(rid, ())):
            self._drop(child)
        self._kids.pop(rid, None)
        self._photos.pop(rid, None)
        siblings = self._kids.get(rec["parent"])
        if siblings and rid in siblings:
            siblings.remove(rid)

    def move(self, rid, parent, index):
        """Treeview's ``move``: put *rid* at *index* among *parent*'s children
        (re-parenting if needed). The row keeps its record, icon and id, so a
        sort moves rows without a refill."""
        rec = self._rec.get(rid)
        if rec is None:
            return
        old = self._kids.get(rec["parent"])
        if old and rid in old:
            old.remove(rid)
        rec["parent"] = parent
        kids = self._kids.setdefault(parent, [])
        pos = len(kids) if index == "end" else max(0, min(int(index), len(kids)))
        kids.insert(pos, rid)
        self._schedule()

    def get_children(self, item=""):
        return list(self._kids.get(item, ()))

    def exists(self, rid):
        return rid in self._rec

    def item(self, rid, option=None, **kw):
        rec = self._rec.get(rid)
        if rec is None:
            return "" if option else {}
        if kw:                                   # setter
            if "text" in kw:
                rec["text"] = kw["text"]
            if "values" in kw:
                rec["values"] = list(kw["values"])
            if "tags" in kw:
                rec["tags"] = tuple(kw["tags"])
            if "open" in kw:
                rec["open"] = kw["open"]
            if "image" in kw:
                rec["image"] = kw["image"]
                self._photos[rid] = kw["image"]
            if rid in self._index:               # laid out -> repaint just this row
                self._redraw_row(rid)
            else:
                self._schedule()
            return None
        if option == "text":
            return rec["text"]
        if option == "values":
            return list(rec["values"])
        if option == "tags":
            return rec["tags"]
        if option == "image":
            return rec["image"]
        return dict(rec)

    # ----------------------------------------------------------- geometry / hit
    def identify_row(self, y):
        return self._rowid_at(self.canvasy(y))

    def identify_column(self, x):
        return self._col_at(self.canvasx(x))

    def _rowid_at(self, content_y):
        if content_y < 0:
            return ""
        idx = int(content_y // self._rh)
        return self._rows[idx] if 0 <= idx < len(self._rows) else ""

    def _col_at(self, content_x):
        for c, (x0, x1) in self._colx.items():
            if x0 <= content_x < x1:
                return "#0" if c == "#0" else f"#{self._cols.index(c) + 1}"
        return ""

    # -------------------------------------------------------------- layout/draw
    def _schedule(self):
        if not self._refresh_pending:
            self._refresh_pending = True
            self.after_idle(self._relayout)

    def _layout_columns(self, total):
        order = ["#0"] + self._cols
        wid = {c: max(self._cfg[c]["width"], 0) for c in order}
        stretch = [c for c in order if self._cfg[c]["stretch"]
                   and self._cfg[c]["width"] > 0]
        extra = max(0, total - sum(wid.values()))
        if stretch and extra:
            share = extra // len(stretch)
            for c in stretch:
                wid[c] += share
        x = 0
        self._colx = {}
        for c in order:
            w = wid[c]
            if w > 0:
                self._colx[c] = (x, x + w)
                x += w
        self._content_w = x

    def _rebuild_order(self):
        self._rows = []

        def walk(parent):
            for rid in self._kids.get(parent, ()):
                self._rows.append(rid)
                if self._rec[rid]["open"]:
                    walk(rid)

        walk("")
        self._index = {rid: i for i, rid in enumerate(self._rows)}

    def _relayout(self):
        self._refresh_pending = False
        self._rebuild_order()
        self._layout_columns(max(self.winfo_width(), 1))
        h = max(len(self._rows) * self._rh, 1)
        self.configure(scrollregion=(0, 0, max(self._content_w,
                                               self.winfo_width()), h))
        self._redraw()
        self._draw_header()

    def _style(self, tags):
        bg, fg = self._defbg, self._deffg
        for t in tags:
            cfg = self._tagcfg.get(t)
            if cfg:
                bg = cfg.get("background", bg)
                fg = cfg.get("foreground", fg)
        return bg, fg

    def _fit(self, text, font, maxpx):
        if maxpx <= 0 or font.measure(text) <= maxpx:
            return text
        ell = "…"
        while text and font.measure(text + ell) > maxpx:
            text = text[:-1]
        return (text + ell) if text else ell

    def _redraw(self):
        tk.Canvas.delete(self, "all")
        for i, rid in enumerate(self._rows):
            self._draw_row(rid, i)

    def _redraw_row(self, rid):
        i = self._index.get(rid)
        if i is None:
            return
        tk.Canvas.delete(self, rid)
        self._draw_row(rid, i)

    def _draw_row(self, rid, i):
        rec = self._rec[rid]
        rh = self._rh
        y0 = i * rh
        ymid = y0 + rh // 2
        w = max(self._content_w, self.winfo_width())
        bg, fg = self._style(rec["tags"])
        is_section = "section" in rec["tags"]
        self.create_rectangle(0, y0, w, y0 + rh, fill=bg, outline="",
                              tags=(rid,))

        if is_section:                                   # a spanning header row
            self.create_text(self._colx["#0"][0] + 8, ymid, anchor="w",
                             text=rec["text"], fill=fg, font=self._font,
                             tags=(rid,))
            return

        nx0, nx1 = self._colx.get("#0", (0, 0))
        if rec.get("image") is not None:
            self.create_image(nx0 + 4, ymid, anchor="w", image=rec["image"],
                             tags=(rid,))
        tx = nx0 + 6 + self._icon_w
        name = self._fit(rec["text"], self._font, nx1 - tx - 4)
        self.create_text(tx, ymid, anchor="w", text=name, fill=fg,
                         font=self._font, tags=(rid,))

        for j, col in enumerate(self._cols):
            box = self._colx.get(col)
            if box is None:
                continue
            val = rec["values"][j] if j < len(rec["values"]) else ""
            if isinstance(val, (list, tuple)):
                if val and isinstance(val[0], (list, tuple)):   # coloured segments
                    self._draw_segments(box, y0, val, rid)
                # an empty list is an empty cell -> draw nothing
            elif val != "":
                self._draw_text_cell(box, ymid, str(val),
                                     self._cfg[col]["anchor"], fg, rid)

    def _draw_text_cell(self, box, ymid, text, anchor, fg, rid):
        x0, x1 = box
        text = self._fit(text, self._numfont, x1 - x0 - 8)
        if anchor == "e":
            self.create_text(x1 - 5, ymid, anchor="e", text=text, fill=fg,
                             font=self._numfont, tags=(rid,))
        else:
            self.create_text(x0 + 5, ymid, anchor="w", text=text, fill=fg,
                             font=self._numfont, tags=(rid,))

    def _draw_segments(self, box, y0, segments, rid):
        """Coloured cell: each (text, bg, fg) gets its own tinted box, left→right."""
        x0, x1 = box
        rh = self._rh
        x = x0 + 3
        ymid = y0 + rh // 2
        for seg in segments:
            text, sbg, sfg = seg
            text = str(text)
            tw = self._numfont.measure(text)
            bw = tw + 10
            if x + bw > x1:                              # clip to the column
                break
            if sbg:
                self.create_rectangle(x, y0 + 3, x + bw, y0 + rh - 3,
                                      fill=sbg, outline="", tags=(rid,))
            self.create_text(x + bw / 2, ymid, anchor="center", text=text,
                             fill=(sfg or self._deffg), font=self._numfont,
                             tags=(rid,))
            x += bw + 3

    def _draw_header(self):
        hc = self._header
        hc.delete("all")
        hh = int(hc.cget("height"))
        ymid = hh // 2
        # bottom hairline
        hw = max(self._content_w, self.winfo_width())
        hc.create_line(0, hh - 1, hw, hh - 1, fill=BORDER)
        for c, (x0, x1) in self._colx.items():
            title = self._cfg[c]["title"]
            if not title:
                continue
            if self._cfg[c]["anchor"] == "e":
                hc.create_text(x1 - 5, ymid, anchor="e", text=title,
                               fill=self._fg_dim, font=self._font)
            else:
                hc.create_text(x0 + (6 if c == "#0" else 5), ymid, anchor="w",
                               text=title, fill=self._fg_dim, font=self._font)
