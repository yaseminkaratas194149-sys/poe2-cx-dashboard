"""poe2scout feed client (read-only).

The single programmatic source of in-game Currency Exchange data. Hourly
digests; no auth (a User-Agent with contact is sent). League path segment is
the short name (e.g. 'forbiddenriteshc').

League resolution lives here too: which league the store tracks
(`current_league`) and which leagues the unique-item reference is merged from
(`uniques_sources`). Both are rules over `/Leagues`, overridable by the pins in
`cx.config`.
"""
import json
import urllib.parse
import urllib.request

from cx import config
from cx.config import POE2SCOUT_BASE, REALM, USER_AGENT


def get(path: str):
    """GET {POE2SCOUT_BASE}{path} and parse JSON."""
    req = urllib.request.Request(POE2SCOUT_BASE + path, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---- leagues ----------------------------------------------------------------

def leagues() -> list:
    """Every league poe2scout knows, newest first ({Value, ShortName, IsCurrent, ...})."""
    lgs = get(f"/{REALM}/Leagues")
    if not lgs:
        raise RuntimeError("poe2scout returned no leagues")
    return lgs


def softcore_twin(short: str) -> str:
    """'forbiddenriteshc' -> 'forbiddenrites'; a softcore short name is its own twin."""
    return short[:-2] if short.endswith("hc") else short


def hc_twin(lg: dict, lgs: list):
    """The HC league of a softcore one: ShortName + 'hc' (Value 'HC ' + name), or None."""
    short, name = lg.get("ShortName", ""), lg.get("Value", "")
    return next((x for x in lgs if x.get("ShortName") == short + "hc"
                 or x.get("Value") == "HC " + name), None)


def current_league(lgs: list = None) -> dict:
    """Resolve the tracked league -> {league, short_name, realm, base_currency_api_id}.

    `config.LEAGUE_SHORT` pins one league by ShortName. Otherwise the rule: the
    first IsCurrent softcore entry is the newest league -- several leagues carry
    IsCurrent at once (poe2scout keeps the previous league current for a while),
    but the newest is listed first -- and with `config.HARDCORE` the tracked
    league is its HC twin (the softcore league itself when no twin is listed).
    """
    lgs = lgs if lgs is not None else leagues()
    if config.LEAGUE_SHORT:
        cur = next((lg for lg in lgs if lg.get("ShortName") == config.LEAGUE_SHORT), None)
        if cur is None:
            raise RuntimeError(f"league ShortName={config.LEAGUE_SHORT!r} not found on poe2scout")
    else:
        current = [lg for lg in lgs if lg.get("IsCurrent")]
        cur = next((lg for lg in current if not lg.get("ShortName", "").endswith("hc")),
                   current[0] if current else lgs[0])
        if config.HARDCORE:
            cur = hc_twin(cur, lgs) or cur
    return {
        "league": cur["Value"],
        "short_name": cur["ShortName"],
        "realm": REALM,
        "base_currency_api_id": cur["BaseCurrencyApiId"],
    }


def uniques_sources(tracked_short: str, lgs: list = None, log=None) -> list:
    """Leagues to pull the unique-item reference from, in UPSERT order.

    `config.UNIQUES_LEAGUE_SHORT` pins a single source. Otherwise: the populated
    list with the most uniques first -- uniques only accumulate, so the biggest
    list is the most complete one (2026-09-05: 449 in 'runes' vs 387 in 'hunt',
    and 0 in the two-day-old 'forbiddenrites') -- then the tracked league's
    softcore twin when it is populated and different, so its prices and mod
    text land on top. UniqueItemId is the same in every league, so the merge is
    a plain UPSERT. Probes the live lists first (the twin + every IsCurrent
    league) and the older leagues only when none of those is populated; one
    Items/Categories call per league, plus one tiny call per category for the
    leagues that have categories at all (~3 s live, ~13 s for the full scan)."""
    if config.UNIQUES_LEAGUE_SHORT:
        return [config.UNIQUES_LEAGUE_SHORT]
    lgs = lgs if lgs is not None else leagues()
    twin = softcore_twin(tracked_short)
    live = [twin] + [lg["ShortName"] for lg in lgs
                     if lg.get("IsCurrent") and lg.get("ShortName") and lg["ShortName"] != twin]
    rest = [lg["ShortName"] for lg in lgs if lg.get("ShortName") and lg["ShortName"] not in live]
    totals = {}
    for tier in (live, rest):
        for short in tier:
            cats = [c.get("ApiId") for c in (items_categories(short).get("UniqueCategories") or [])]
            cats = [c for c in cats if c]
            if not cats:
                continue
            totals[short] = sum(int(uniques_by_category(short, c, page=1, per_page=1).get("Total") or 0)
                                for c in cats)
            if log:
                log(f"{short}: {totals[short]} uniques")
        if totals:
            break
    if not totals:
        raise RuntimeError("no league on poe2scout carries unique categories")
    best = max(totals, key=lambda s: (totals[s], s == twin))
    return [best] + ([twin] if twin in totals and twin != best else [])


# ---- market data ------------------------------------------------------------

def exchange_snapshot(short: str) -> dict:
    """Latest market-wide snapshot: {Epoch, Volume, MarketCap, BaseCurrency...}."""
    return get(f"/{REALM}/Leagues/{short}/ExchangeSnapshot")


def snapshot_pairs(short: str) -> list:
    """Latest per-pair rows (no hour field; stamp with ExchangeSnapshot.Epoch)."""
    return get(f"/{REALM}/Leagues/{short}/SnapshotPairs")


def pair_history(short: str, item_id_1: int, item_id_2: int, limit: int = 2000) -> dict:
    """Per-pair hourly history (keyed by ItemId). Returns {History:[...], Meta, ...}."""
    return get(f"/{REALM}/Leagues/{short}/Currencies/Pairs/{item_id_1}/{item_id_2}/History?Limit={limit}")


# ---- items ------------------------------------------------------------------

def items_categories(short: str) -> dict:
    """Item taxonomy: {UniqueCategories:[{ApiId,Label,Icon}], CurrencyCategories:[...]}."""
    return get(f"/{REALM}/Leagues/{short}/Items/Categories")


def uniques_by_category(short: str, category: str, page: int = 1, per_page: int = 250) -> dict:
    """One page of uniques in a category -> {CurrentPage, Pages, Total, Items:[...]}.
    NOTE: poe2scout populates uniques for SOFTCORE leagues mostly (HC categories are empty)."""
    q = urllib.parse.urlencode({"Category": category, "Page": page, "PerPage": per_page})
    return get(f"/{REALM}/Leagues/{short}/Uniques/ByCategory?{q}")
