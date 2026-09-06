"""poe.ninja PoE2 economy feed (read-only) -- the smoothed reference price.

poe.ninja re-serves the same hourly Currency Exchange digests poe2scout does,
as ONE number per currency: `primaryValue` = the volume-weighted average price
of the currency's max-volume pair over the last 6 completed hours, in the base
currency (verified exactly on 8 currencies, 2026-09-06 -- see DECISIONS.md),
plus that pair's mean hourly volume (`volumePrimaryValue`), its id and rate
(`maxVolumeCurrency` / `maxVolumeRate`, units of the currency per 1 of the
pair) and a 7-day sparkline (`totalChange`, percent). So it is not fresher
than the pair feed, it is smoother: the "approximate average price". Ids are
the same slugs as poe2scout's `api_id`.

API reference: https://poe.ninja/docs/api -- no auth, no versioning, no SLA;
send a descriptive User-Agent with a contact, respect the ETag cache, do not
poll faster than minutes (PoE2 refreshes roughly hourly).

`refresh_prices` is the core: the `cx12_ninja` stage of the hourly cycle calls
it after provision; the CLI wraps it with provision. One GET per exchange type
(`config.NINJA_TYPES`), all UPSERTed into `ninja_price` at the fetch hour.

    python -m cx.ninja            # fetch every exchange type into ninja_price
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import psycopg2

from cx import config, store

_etags = {}      # (league, type) -> ETag of the last payload seen (in-process)


def overview(league: str, type_: str):
    """GET the exchange overview for one type -> payload dict, or None when the
    server answers 304 to our If-None-Match (nothing changed since last fetch)."""
    q = urllib.parse.urlencode({"league": league, "type": type_})
    url = f"{config.NINJA_BASE}/poe2/api/economy/exchange/current/overview?{q}"
    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
    etag = _etags.get((league, type_))
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            _etags[(league, type_)] = resp.headers.get("ETag")
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None
        raise


def rows_of(payload: dict, type_: str) -> list:
    """Overview payload -> store.upsert_ninja rows:
    (api_id, value, volume, max_pair, max_rate, change_7d, category, name)."""
    names = {it.get("id"): it.get("name") for it in (payload.get("items") or [])}
    out = []
    for l in payload.get("lines") or []:
        api, value = l.get("id"), l.get("primaryValue")
        if not api or value is None:
            continue
        spark = l.get("sparkline") or {}
        out.append((api, value, l.get("volumePrimaryValue"), l.get("maxVolumeCurrency"),
                    l.get("maxVolumeRate"), spark.get("totalChange"), type_, names.get(api)))
    return out


def refresh_prices(schema: str, league: str, types=None, log=print, progress=None) -> dict:
    """Fetch every exchange type for `league` (the poe.ninja league id is our
    `league.league`, e.g. 'HC Forbidden Rites') and UPSERT into
    `{schema}.ninja_price` at the current hour. A type answering 304 is left as
    it was (its last rows stay the latest). -> {rows, ok, unchanged, fail, hour}."""
    types = list(types or config.NINJA_TYPES)
    hour = int(time.time()) // 3600 * 3600
    rows, ok, unchanged, fail = [], 0, 0, 0
    for i, t in enumerate(types, 1):
        try:
            payload = overview(league, t)
        except Exception as e:
            fail += 1
            log(f"{t}: {e}")
        else:
            if payload is None:
                unchanged += 1
            else:
                rows += rows_of(payload, t)
                ok += 1
        if progress:
            progress(i, len(types))
    written = 0
    if rows:
        conn = psycopg2.connect(**config.DB_CONFIG)
        try:
            cur = conn.cursor()
            written = store.upsert_ninja(cur, schema, hour, rows)
            conn.commit()
            cur.close()
        finally:
            conn.close()
    log(f"types ok={ok} unchanged={unchanged} fail={fail}; rows upserted={written} "
        f"@ {time.strftime('%m-%d %H:%M', time.gmtime(hour))} UTC")
    return {"rows": written, "ok": ok, "unchanged": unchanged, "fail": fail, "hour": hour}


def main():
    """CLI path: provision (league + schema), then the fetch."""
    from cx.stages.provision import EnsureSchemaStage   # lazy: the stage package imports lazily too
    prov = EnsureSchemaStage().run({})
    print(f"[ninja] {prov['league']} -> {prov['schema']}")
    return refresh_prices(prov["schema"], prov["league"], log=lambda m: print(f"[ninja] {m}"))


if __name__ == "__main__":
    main()
