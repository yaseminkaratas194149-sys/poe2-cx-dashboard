"""Palette — shared design tokens, the single source for the cx UI.

Mirrors doc_nav/theme.py: imported from the launcher when reachable (one source
of truth across the TP3 tools), with an inline fallback so cx still runs off
that path. cx already gets C:\\TP3 on sys.path via DATA.config, but the explicit
insert + fallback keeps this module self-standing.
"""

import sys

sys.path.insert(0, r"C:\TP3")
try:
    from launcher.theme import (  # noqa: F401
        BG, BG2, BG3, FG, FG_DIM, FG_MUTED, GRAY,
        BORDER, BORDER_DIM, BORDER_DARK,
        GREEN, DULL_GRN, RED, HOVER_BG, WARM_YLW,
        setup_styles,
    )
except Exception:
    BG, BG2, BG3 = "#1e1e1e", "#252526", "#2d2d2d"
    FG, FG_DIM, FG_MUTED, GRAY = "#cccccc", "#888888", "#666666", "#555555"
    BORDER, BORDER_DIM, BORDER_DARK = "#3a3a3a", "#2a2a2a", "#0a0a0a"
    GREEN, DULL_GRN, RED = "#4ec94e", "#2d5a2d", "#f44747"
    HOVER_BG, WARM_YLW = "#333333", "#e0a020"

    def setup_styles(style):
        """Fallback when the launcher isn't importable: clam + the thin,
        arrowless 'Subtle' scrollbar the cards use (blends into BG2)."""
        style.theme_use("clam")
        for orient in ("Vertical", "Horizontal"):
            name = f"Subtle.{orient}.TScrollbar"
            sticky_outer = "ns" if orient == "Vertical" else "ew"
            style.layout(name, [
                (f"{orient}.Scrollbar.trough", {"children": [
                    (f"{orient}.Scrollbar.thumb",
                     {"expand": "1", "sticky": "nswe"})
                ], "sticky": sticky_outer})])
            style.configure(name, background=BORDER, troughcolor=BG2,
                            bordercolor=BG2, lightcolor=BG2, darkcolor=BG2,
                            arrowcolor=BG2, gripcount=0, width=8)
            style.map(name, background=[("active", "#4a4a4a")])
