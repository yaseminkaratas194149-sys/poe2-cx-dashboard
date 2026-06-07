"""poe2scout feed client (read-only).

The single programmatic source of in-game Currency Exchange data. Hourly
digests; no auth (a User-Agent with contact is sent). League path segment is
the short name (e.g. 'runes').
"""
import json
import urllib.request

from cx.config import POE2SCOUT_BASE, REALM, USER_AGENT, LEAGUE_SHORT


def get(path: str):
    """GET {POE2SCOUT_BASE}{path} and parse JSON."""
    req = urllib.request.Request(POE2SCOUT_BASE + path, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def current_league() -> dict:
    """Resolve the tracked league -> {league, short_name, realm, base}.

    `config.LEAGUE_SHORT` pins a specific league by ShortName (we track HC Runes,
    'runeshc'); when None, fall back to the first IsCurrent (the softcore default).
    """
    leagues = get(f"/{REALM}/Leagues")
    if not leagues:
        raise RuntimeError("poe2scout returned no leagues")
    if LEAGUE_SHORT:
        cur = next((lg for lg in leagues if lg.get("ShortName") == LEAGUE_SHORT), None)
        if cur is None:
            raise RuntimeError(f"league ShortName={LEAGUE_SHORT!r} not found on poe2scout")
    else:
        cur = next((lg for lg in leagues if lg.get("IsCurrent")), leagues[0])
    return {
        "league": cur["Value"],
        "short_name": cur["ShortName"],
        "realm": REALM,
        "base_currency_api_id": cur["BaseCurrencyApiId"],
    }


def exchange_snapshot(short: str) -> dict:
    """Latest market-wide snapshot: {Epoch, Volume, MarketCap, BaseCurrency...}."""
    return get(f"/{REALM}/Leagues/{short}/ExchangeSnapshot")


def snapshot_pairs(short: str) -> list:
    """Latest per-pair rows (no hour field; stamp with ExchangeSnapshot.Epoch)."""
    return get(f"/{REALM}/Leagues/{short}/SnapshotPairs")


def pair_history(short: str, item_id_1: int, item_id_2: int, limit: int = 2000) -> dict:
    """Per-pair hourly history (keyed by ItemId). Returns {History:[...], Meta, ...}."""
    return get(f"/{REALM}/Leagues/{short}/Currencies/Pairs/{item_id_1}/{item_id_2}/History?Limit={limit}")
