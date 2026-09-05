"""cx configuration: reuses DATA's Postgres; cx-specific settings.

DB lives in the shared Postgres (`DATA.config.DB_CONFIG`); cx isolates itself
in per-league schemas `cx_<short_name>`.
"""
import os
from pathlib import Path

from DATA.config import DB_CONFIG  # shared Postgres (cx uses its own schemas)

REALM = "poe2"
# League to track. None (default) -> resolved from poe2scout on every cycle:
# the first IsCurrent entry of /Leagues is the newest softcore league (several
# leagues carry IsCurrent at once -- poe2scout keeps the previous one current
# for a while -- but the newest is listed first), and with HARDCORE the tracked
# league is its HC twin (ShortName + 'hc': forbiddenrites -> forbiddenriteshc,
# schema cx_forbiddenriteshc). Set a ShortName to pin one league by hand; the
# pin wins over the rule. History: pinned 'runeshc' 2026-06; on 2026-09-05 that
# stale pin had left the store three months behind, hence the rule.
LEAGUE_SHORT = None
HARDCORE = True
# Unique-item reference source(s). poe2scout tracks uniques mostly for softcore
# leagues (HC lists are empty) and a new league's list can stay empty for days
# after launch (2026-09-05: 'forbiddenrites' had 0 unique categories, 'runes'
# 449). A unique's identity is league-invariant and its UniqueItemId is the same
# in every league, so the reference is MERGED: the most complete populated list
# first, then the tracked league's softcore twin on top (current prices, mod
# text) -- see source.uniques_sources. None -> that rule; a ShortName pins one
# source by hand.
UNIQUES_LEAGUE_SHORT = None
# Hours of per-pair history the Actualize cycle backfills (stage cx11_backfill).
BACKFILL_HOURS = 48
POE2SCOUT_BASE = "https://api.poe2scout.com"
# poe2scout asks callers to identify themselves. Public code carries only the
# repo URL (identifiable, no personal data); set POE2CX_CONTACT in the environment
# to your own contact (e.g. an email) for your own runs.
USER_AGENT = os.environ.get("POE2CX_CONTACT") or \
    "poe2cx/0.2 (+https://github.com/yaseminkaratas194149-sys/poe2-cx-dashboard)"

# Canonical DDL template (db/schema.sql, with {schema} placeholder)
SCHEMA_SQL = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

# League-invariant item reference (names EN/RU, icons, stats) lives ONCE in its
# own schema, not per league. db/ref_schema.sql is the {schema} template for it.
REF_SCHEMA = "cx_ref"
REF_SCHEMA_SQL = Path(__file__).resolve().parent.parent / "db" / "ref_schema.sql"


def schema_name(short_name: str) -> str:
    """Map a league short name to its Postgres schema, validated as a safe id."""
    s = "cx_" + short_name
    if not s.replace("_", "").isalnum():
        raise ValueError(f"unsafe schema name: {s!r}")
    return s
