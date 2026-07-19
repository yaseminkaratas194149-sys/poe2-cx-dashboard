"""EquipmentNav — the reusable equipment-selection chip navigator.

Lifted out of UniquesPanel (shared meta→group→base body) so the same widget drives
two callers: a three-tier row of chips — a fixed META row, the chosen meta's GROUP
row, then the group's BASE/leaf chips — with NO data source baked in. The host
feeds a taxonomy (``set_taxonomy``) and reacts to picks via the ``on_meta`` /
``on_group`` / ``on_leaf`` callbacks; a group may delegate its leaf row to a host
hook (UniquesPanel plugs its weapon scope rows and its Str/Dex/Int attribute
toggles in this way — those stay data-coupled and live with the panel).

  • UniquesPanel feeds it the local unique DB → picks FILTER the item list.
  • TradePanel feeds it trade.CATEGORIES → a pick IS the chosen category.

Same body, two callers, one copy of the chip/selection/highlight/tagging code.

Taxonomy shape — a list of *meta* node dicts; every node is::

    {"key": str, "label": str, "count": int | None,
     "children": [ ...sub-nodes... ],     # groups under a meta, or leaves
     "leaf_mode": "plain" | "<hook name>",# groups only; default "plain"
     "value": <opaque>}                   # what on_leaf hands back for a leaf

A node is a GROUP (opens a further chip row) when it has children or a non-plain
``leaf_mode``; otherwise it's a terminal LEAF. A meta whose children are leaves
(no intermediate groups) opens its base bar directly, skipping the group row — the
same path a single-member meta takes. ``count is None`` renders the label bare (no
count suffix), so a count-less caller (Trade) and a counted one (Uniques) share it.
"""
import tkinter as tk

from .theme import BG, BG2, BG3, FG, FG_DIM, FG_MUTED, DULL_GRN, HOVER_BG

_LEAVES_PER_ROW = 12


def is_group(node):
    """A node opens a further chip row (a GROUP) iff it has children or a custom
    leaf-bar hook; otherwise it is a terminal LEAF (a final pick)."""
    return bool(node.get("children")) or node.get("leaf_mode", "plain") != "plain"


class EquipmentNav(tk.Frame):
    def __init__(self, parent, *, tm=None, eid_prefix="equip", carded=True,
                 bg=None, on_meta=None, on_group=None, on_leaf=None,
                 leaf_hooks=None):
        self._bg = bg or BG
        super().__init__(parent, bg=self._bg)
        self.tm = tm
        self._prefix = eid_prefix
        self._on_meta = on_meta
        self._on_group = on_group
        self._on_leaf = on_leaf
        self._hooks = leaf_hooks or {}
        self._metas = []
        self.sel_meta = None
        self.sel_group = None
        self.sel_leaf = None
        self._build(carded)
        self._tag()

    # ------------------------------------------------------------------ build
    def _build(self, carded):
        # Top tier is two rows: meta_bar (fixed metas) over group_row (the chosen
        # meta's groups, opened on demand). group_bar wraps both so the inspector
        # can point at the whole top block.
        self.group_bar = tk.Frame(self, bg=self._bg)
        self.group_bar.pack(fill="x", pady=(0, 4))
        self.meta_bar = tk.Frame(self.group_bar, bg=self._bg)
        self.meta_bar.pack(fill="x")
        self.group_row = tk.Frame(self.group_bar, bg=self._bg)   # packed on demand

        # base bar — a labelled BG2 card (carded) so the leaf chips read as their
        # own surface, matching the uniques look.
        wrap = tk.Frame(self, bg=BG2 if carded else self._bg)
        wrap.pack(fill="x", pady=(0, 6))
        self.sub_label = tk.Label(wrap, text="", bg=BG2 if carded else self._bg,
                                  fg=FG_MUTED, font=("Segoe UI", 8))
        self.sub_label.pack(anchor="w", padx=8, pady=(4, 0))
        self.sub_bar = tk.Frame(wrap, bg=BG2 if carded else self._bg)
        self.sub_bar.pack(fill="x", padx=6, pady=(2, 6))

    def _tag(self):
        # Inspector eids mirror the old uniques stems under a caller-supplied prefix
        # (uniques.* unchanged; trade.form.category.* for the trade picker).
        tm = self.tm
        if tm is None:
            return
        p = self._prefix
        tm.tag(self.group_bar, f"{p}.groupbar")
        tm.tag(self.meta_bar, f"{p}.metabar")
        tm.tag(self.group_row, f"{p}.grouprow")
        tm.tag(self.sub_bar, f"{p}.basebar")
        tm.tag(self.sub_label, f"{p}.basebar.label")

    # ---------------------------------------------------------- chip primitive
    def make_chip(self, parent, text, active, cmd, eid_key=None, key=None):
        """The shared chip: DULL_GRN when active, BG3 otherwise; hover previews the
        active fill so the off state stays clearly off. ``key`` lets _highlight pick
        the live chip without parsing its (count-suffixed) text. Exposed publicly so
        host leaf-bar hooks build chips in the same visual language."""
        bg = DULL_GRN if active else BG3
        fg = FG if active else FG_DIM
        lab = tk.Label(parent, text=text, bg=bg, fg=fg, font=("Segoe UI", 9),
                       padx=10, pady=4, cursor="hand2")
        lab._active = active
        lab._key = key
        lab.bind("<Button-1>", lambda e: cmd())
        lab.bind("<Enter>", lambda e: lab.config(bg=(DULL_GRN if lab._active else HOVER_BG)))
        lab.bind("<Leave>", lambda e: lab.config(bg=(DULL_GRN if lab._active else BG3)))
        if self.tm is not None and eid_key:
            self.tm.tag(lab, eid_key)
        return lab

    @staticmethod
    def _text(node):
        c = node.get("count")
        return f"{node['label']}  {c}" if c is not None else node["label"]

    @staticmethod
    def _highlight(frame, key):
        """Light the chip whose ``_key`` matches; dim the rest. Skips chips that
        carry no key (custom hook chips manage their own highlight)."""
        for w in frame.winfo_children():
            wk = getattr(w, "_key", None)
            if wk is None:
                continue
            on = wk == key
            w._active = on
            try:
                w.config(bg=DULL_GRN if on else BG3, fg=FG if on else FG_DIM)
            except tk.TclError:
                pass

    # --------------------------------------------------------------- helpers
    def clear_subbar(self):
        for w in self.sub_bar.winfo_children():
            w.destroy()

    def set_sub_label(self, text):
        """Show *text* above the base bar (re-packing the label if a hook hid it)."""
        self.sub_label.config(text=text)
        if not self.sub_label.winfo_ismapped():
            self.sub_label.pack(anchor="w", padx=8, pady=(4, 0), before=self.sub_bar)

    def hide_sub_label(self):
        self.sub_label.pack_forget()

    # --------------------------------------------------------------- taxonomy
    def set_taxonomy(self, metas):
        """(Re)build the meta row from *metas*; reset every selection below it."""
        self._metas = metas or []
        self.sel_meta = self.sel_group = self.sel_leaf = None
        for w in self.meta_bar.winfo_children():
            w.destroy()
        for w in self.group_row.winfo_children():
            w.destroy()
        self.group_row.pack_forget()
        self.clear_subbar()
        self.sub_label.config(text="")
        for i, m in enumerate(self._metas):
            chip = self.make_chip(self.meta_bar, self._text(m), False,
                                  lambda mm=m: self.select_meta(mm),
                                  eid_key=f"{self._prefix}.meta[{m['key']}]",
                                  key=m["key"])
            chip.grid(row=0, column=i, padx=2, pady=2, sticky="w")

    # --------------------------------------------------------------- selection
    def select_meta(self, meta):
        """Top-row pick: highlight the meta, then open its groups (multi-member),
        its base bar directly (single member / leaf children), or — for a terminal
        meta with no children — treat the meta itself as the pick."""
        self.sel_meta = meta
        self.sel_group = None
        self.sel_leaf = None
        self._highlight(self.meta_bar, meta["key"])
        for w in self.group_row.winfo_children():
            w.destroy()
        self.clear_subbar()
        if self._on_meta:
            self._on_meta(meta)
        children = meta.get("children") or []
        # terminal meta — the meta chip IS the pick (e.g. Trade's "Any" / Jewel)
        if not children and meta.get("leaf_mode", "plain") == "plain":
            self.group_row.pack_forget()
            self._fire_leaf(meta)
            return
        if children and is_group(children[0]):
            members = children
            # a single-member meta IS that group — skip the redundant 1-chip row
            if len(members) == 1:
                self.group_row.pack_forget()
                self.select_group(members[0])
                return
            self.group_row.pack(fill="x", pady=(3, 0))
            for i, g in enumerate(members):
                chip = self.make_chip(self.group_row, self._text(g), False,
                                      lambda gg=g: self.select_group(gg),
                                      eid_key=f"{self._prefix}.group[{g['key']}]",
                                      key=g["key"])
                chip.grid(row=0, column=i, padx=2, pady=2, sticky="w")
        else:
            # children are leaves — open them straight in the base bar (no group row)
            self.group_row.pack_forget()
            self._render_leaves(children)

    def select_group(self, group):
        """Second-row pick: highlight the group, then render its base bar — plain
        leaf chips, or a host hook's custom bar (weapon / attribute).
        A terminal node (value, no children, plain mode) fires directly without
        opening a sub-bar — supports mixed group_row (e.g. Trade's 'Others')."""
        self.sel_group = group
        self.sel_leaf = None
        self._highlight(self.group_row, group["key"])
        self.clear_subbar()
        if self._on_group:
            self._on_group(group)
        mode = group.get("leaf_mode", "plain")
        if mode != "plain":
            hook = self._hooks.get(mode)
            if hook:
                hook(self, self.sub_bar, group)
            return
        if not group.get("children") and "value" in group:
            self._fire_leaf(group)
            return
        self._render_leaves(group.get("children") or [])

    def _render_leaves(self, leaves):
        for i, lf in enumerate(leaves):
            chip = self.make_chip(self.sub_bar, self._text(lf), False,
                                  lambda ll=lf: self._fire_leaf(ll),
                                  eid_key=f"{self._prefix}.base[{lf['key']}]",
                                  key=lf["key"])
            chip.grid(row=i // _LEAVES_PER_ROW, column=i % _LEAVES_PER_ROW,
                      padx=2, pady=2, sticky="w")

    def _fire_leaf(self, leaf):
        self.sel_leaf = leaf
        self._highlight(self.sub_bar, leaf.get("key"))
        if self._on_leaf:
            self._on_leaf(leaf)

    # ----------------------------------------------------- programmatic select
    def select_value(self, value):
        """Replay meta→(group)→leaf to land on the leaf whose ``value`` == *value*
        (used to restore a saved pick). Returns True if found."""
        for m in self._metas:
            if not m.get("children") and m.get("leaf_mode", "plain") == "plain":
                if m.get("value") == value:
                    self.select_meta(m)
                    return True
                continue
            for child in (m.get("children") or []):
                if is_group(child):
                    for lf in (child.get("children") or []):
                        if lf.get("value") == value:
                            self.select_meta(m)
                            self.select_group(child)
                            self._fire_leaf(lf)
                            return True
                elif child.get("value") == value:
                    self.select_meta(m)
                    children_list = m.get("children") or []
                    if children_list and is_group(children_list[0]):
                        # terminal chip lives in group_row; select_group highlights + fires
                        self.select_group(child)
                    else:
                        self._fire_leaf(child)
                    return True
        return False
