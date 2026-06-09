"""cx configuration: reuses DATA's Postgres; cx-specific settings.

DB lives in the shared Postgres (`DATA.config.DB_CONFIG`); cx isolates itself
in per-league schemas `cx_<short_name>`.
"""
import os
from pathlib import Path

from DATA.config import DB_CONFIG  # shared Postgres (cx uses its own schemas)

REALM = "poe2"
# League to track. None -> the API's default (first IsCurrent, i.e. softcore).
# Set to a ShortName to pin one league: we trade only in HC Runes of Aldur, whose
# ShortName is 'runeshc' (-> schema cx_runeshc). Both 'runes' and 'runeshc' carry
# IsCurrent, so without this pin the resolver would pick softcore 'runes'.
LEAGUE_SHORT = "runeshc"
# poe2scout tracks UNIQUES only for softcore leagues (HC unique categories are
# empty). A unique's name/base/mods/icon are league-invariant, so the unique
# reference is pulled from the softcore counterpart and stored in the active
# schema. Update alongside LEAGUE_SHORT when the league rotates.
UNIQUES_LEAGUE_SHORT = "runes"
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
