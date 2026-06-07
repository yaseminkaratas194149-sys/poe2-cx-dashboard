"""EnsureSchemaStage — provision the current league's schema.

The DAG's first step: resolve the current league, create its schema and
tables from db/schema.sql (idempotent), and UPSERT the league reference row.
Downstream stages read the resolved schema from ctx.
"""
import psycopg2

from DATA.pipeline.stage import Stage
from cx import config, source


class EnsureSchemaStage(Stage):
    name = "cx00_provision"
    label = "Schema"
    deps = []

    def run(self, ctx):
        lg = source.current_league()
        schema = config.schema_name(lg["short_name"])
        self.log(f"{lg['league']} -> {schema}")

        ddl = config.SCHEMA_SQL.read_text(encoding="utf-8").replace("{schema}", schema)
        conn = psycopg2.connect(**config.DB_CONFIG)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute(ddl)  # all statements IF NOT EXISTS / OR REPLACE -> idempotent
            cur.execute(
                f"INSERT INTO {schema}.league (league, short_name, realm, base_currency_api_id) "
                f"VALUES (%s, %s, %s, %s) "
                f"ON CONFLICT (league) DO UPDATE SET "
                f"short_name = EXCLUDED.short_name, realm = EXCLUDED.realm, "
                f"base_currency_api_id = EXCLUDED.base_currency_api_id",
                (lg["league"], lg["short_name"], lg["realm"], lg["base_currency_api_id"]),
            )
            cur.close()
        finally:
            conn.close()

        self.log(f"schema ready: {schema}")
        return {
            "schema": schema,
            "league": lg["league"],
            "short_name": lg["short_name"],
            "count": 1,
        }
