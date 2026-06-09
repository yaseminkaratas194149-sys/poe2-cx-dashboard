"""Open pathofexile.com/trade2 tabs pre-filled with curated filter presets.

Why a browser hand-off instead of a direct POST from cx: the trade2 API sits
behind Cloudflare, and a request needs a valid ``cf_clearance`` cookie that only
a real browser can mint (it solves Cloudflare's JS challenge). So cx never POSTs.
It builds the search-query JSON, base64url-encodes it into the URL *fragment*
(``#cxq=...`` — a fragment is never sent to the server), and opens the trade2
page in the user's logged-in browser. The companion userscript
(``poe2-trade-presets.user.js``) reads the fragment, POSTs it in-browser (where
clearance + session are always valid), and redirects the tab to the stored
search. See memory: poe2-trade-preset-architecture.

cx is the *launcher* (builds the query from preset pre-fields, opens the tabs);
the browser is the *executor*. CLI:  ``python -m cx.trade [preset ...]``.
"""
import base64
import json
import urllib.parse
import webbrowser

TRADE2_SEARCH = "https://www.pathofexile.com/trade2/search/poe2"


def build_search_query(preset: dict) -> dict:
    """Assemble a trade2 search body from a preset's pre-fields.

    Pre-fields (all optional):
      status    : "available" (default) | "online" | "any"
      stats     : list of {"id": <stat id>, "min": .., "max": ..}  (min/max optional;
                  omit both -> "mod present, any roll")
      category  : str, e.g. "armour.helmet"            -> filters.type_filters.category
      req_level : int (=max) or {"min": .., "max": ..} -> filters.req_filters.lvl
      price     : {"option": "chaos", "max": 15} etc.  -> filters.trade_filters.price
      sort      : dict, default {"price": "asc"}
    """
    q = {"status": {"option": preset.get("status", "available")}}

    stats = preset.get("stats")
    if stats:
        group = []
        for s in stats:
            f = {"id": s["id"]}
            value = {}
            if s.get("min") is not None:
                value["min"] = s["min"]
            if s.get("max") is not None:
                value["max"] = s["max"]
            if value:
                f["value"] = value
            group.append(f)
        q["stats"] = [{"type": "and", "filters": group}]

    fblock = {}
    if preset.get("category"):
        fblock["type_filters"] = {"filters": {"category": {"option": preset["category"]}}}
    rl = preset.get("req_level")
    if rl is not None:
        lvl = {"max": rl} if isinstance(rl, int) else {
            k: rl[k] for k in ("min", "max") if rl.get(k) is not None
        }
        fblock["req_filters"] = {"filters": {"lvl": lvl}}
    if preset.get("price"):
        fblock["trade_filters"] = {"filters": {"price": preset["price"]}}
    if fblock:
        q["filters"] = fblock

    return {"query": q, "sort": preset.get("sort", {"price": "asc"})}


def preset_to_url(preset: dict, league: str) -> str:
    """Build the trade2 hand-off URL: base + ``#cxq=<base64url(query JSON)>``.

    `league` is the league *display name* (e.g. "HC Runes of Aldur") — exactly
    what source.current_league()["league"] returns and what the trade URL uses.
    """
    body = build_search_query(preset)
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    frag = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{TRADE2_SEARCH}/{urllib.parse.quote(league)}#cxq={frag}"


def open_preset(preset: dict, league: str = None) -> str:
    """Open one preset as a new browser tab; returns the URL opened."""
    if league is None:
        from cx.source import current_league  # lazy: avoids pulling cx.config/DATA at import
        league = current_league()["league"]
    url = preset_to_url(preset, league)
    webbrowser.open(url, new=2)  # new=2 -> new tab when possible
    return url


# ---- A few starter presets (grow this, or feed presets from the UI) --------
PRESETS = {
    "helmet": {
        "category": "armour.helmet",
        "stats": [
            {"id": "pseudo.pseudo_total_elemental_resistance"},
            {"id": "pseudo.pseudo_total_cold_resistance"},
        ],
        "req_level": 20,
        "price": {"option": "chaos", "max": 15},
    },
}


def main(argv=None) -> int:
    import sys
    names = list(argv if argv is not None else sys.argv[1:]) or ["helmet"]
    unknown = [n for n in names if n not in PRESETS]
    if unknown:
        print(f"unknown preset(s): {', '.join(unknown)}; have: {', '.join(PRESETS)}")
        return 2
    from cx.source import current_league
    league = current_league()["league"]
    for n in names:
        print("opened:", open_preset(PRESETS[n], league))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
