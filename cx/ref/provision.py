"""Provision the cx_ref item-reference schema (league-invariant, one copy).

Reads db/ref_schema.sql, substitutes {schema} with config.REF_SCHEMA, runs it.
All statements are IF NOT EXISTS / OR REPLACE -> idempotent. Standalone (not in
the per-league DAG): item reference does not vary by league.
"""
import psycopg2

from cx import config


def ensure_ref_schema() -> str:
    schema = config.REF_SCHEMA
    ddl = config.REF_SCHEMA_SQL.read_text(encoding="utf-8").replace("{schema}", schema)
    conn = psycopg2.connect(**config.DB_CONFIG)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(ddl)  # idempotent
        cur.close()
    finally:
        conn.close()
    return schema
