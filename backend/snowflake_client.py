"""Connect to the team Snowflake account, load the validated export tables, and check Cortex.

Tables come from export_pipeline.prepare_exports() and are created with the
Snowflake types declared in export_pipeline.SCHEMA. Run from the repository root
after filling in .env (see .env.example):

    python -m backend.snowflake_client            # load tables + Cortex check
    python -m backend.snowflake_client --check    # connection + Cortex check only
"""
import argparse
import os
from pathlib import Path

from .export_pipeline import SCHEMA, _json, prepare_exports

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
REQUIRED = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER"]
DEFAULTS = {
    "SNOWFLAKE_DATABASE": "NYC_TRANSIT_PLANNER",
    "SNOWFLAKE_SCHEMA": "ANALYTICS",
}
DEFAULT_MODEL = "llama3.3-70b"


def connection_settings(env=os.environ):
    """Build connector kwargs from environment variables; never reads secrets from code."""
    missing = [name for name in REQUIRED if not env.get(name)]
    if missing:
        raise RuntimeError(f"Missing {', '.join(missing)}. Copy .env.example to .env and fill it in.")
    settings = {
        "account": env["SNOWFLAKE_ACCOUNT"],
        "user": env["SNOWFLAKE_USER"],
        **{name.removeprefix("SNOWFLAKE_").lower(): env.get(name) or value
           for name, value in DEFAULTS.items()},
    }
    # Honor an existing role/warehouse, or the user's Snowflake defaults. Never
    # substitute a role or warehouse from a teammate's separate account.
    for name in ("SNOWFLAKE_ROLE", "SNOWFLAKE_WAREHOUSE"):
        if env.get(name):
            settings[name.removeprefix("SNOWFLAKE_").lower()] = env[name]
    # Password may be a real password or a programmatic access token.
    if env.get("SNOWFLAKE_PASSWORD"):
        settings["password"] = env["SNOWFLAKE_PASSWORD"]
    if env.get("SNOWFLAKE_AUTHENTICATOR"):
        settings["authenticator"] = env["SNOWFLAKE_AUTHENTICATOR"]
    if "password" not in settings and "authenticator" not in settings:
        raise RuntimeError("Set SNOWFLAKE_PASSWORD, or SNOWFLAKE_AUTHENTICATOR=externalbrowser.")
    return settings


def connect(**overrides):
    import snowflake.connector
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    settings = connection_settings()
    settings.update(overrides)
    return snowflake.connector.connect(**settings)


def _ident(name):
    # Quoted upper case matches how Snowflake stores unquoted names, and keeps RANK safe.
    return '"' + name.upper() + '"'


def create_table_sql(table):
    """CREATE OR REPLACE with the export's declared types and NOT NULL contracts."""
    columns = ",\n  ".join(f"{_ident(column)} {kind}{'' if nullable else ' NOT NULL'}"
                           for column, (kind, nullable, _) in SCHEMA[table].items())
    return f"CREATE OR REPLACE TABLE {_ident(table)} (\n  {columns}\n)"


def staging_frame(table, frame):
    """Upper-case columns; VARIANT values become strict JSON text for PARSE_JSON.

    Missing scalars stay pandas NA, which Parquet carries as SQL NULL (never zero).
    """
    frame = frame.copy()
    for column, (kind, _, _) in SCHEMA[table].items():
        if kind == "VARIANT":
            frame[column] = frame[column].map(_json).astype("string")
    frame.columns = [column.upper() for column in frame.columns]
    return frame


def insert_sql(table, stage):
    """Copy staged rows into the typed table, parsing VARIANT text and casting scalars."""
    select = ", ".join(f"PARSE_JSON({_ident(column)})" if kind == "VARIANT" else f"{_ident(column)}::{kind}"
                       for column, (kind, _, _) in SCHEMA[table].items())
    return f"INSERT INTO {_ident(table)} SELECT {select} FROM {_ident(stage)}"


def upload(conn, bundle=None):
    """Replace the four export tables in the current schema. Returns loaded row counts.

    Each table is staged in a temporary table, inserted with explicit types, and
    its Snowflake row count is checked against the validated export.
    """
    from snowflake.connector.pandas_tools import write_pandas

    bundle = bundle or prepare_exports()
    cursor = conn.cursor()
    counts = {}
    for table, frame in bundle.tables.items():
        stage = f"{table}_STAGE"
        cursor.execute(create_table_sql(table))
        result = write_pandas(conn, staging_frame(table, frame), stage, auto_create_table=True,
                              overwrite=True, table_type="temporary")
        if not result.success:
            raise RuntimeError(f"Staging {table} failed")
        cursor.execute(insert_sql(table, stage))
        cursor.execute(f"DROP TABLE IF EXISTS {_ident(stage)}")
        loaded = cursor.execute(f"SELECT COUNT(*) FROM {_ident(table)}").fetchone()[0]
        if loaded != len(frame):
            raise RuntimeError(f"{table}: loaded {loaded} rows, expected {len(frame)}")
        counts[table] = loaded
    return counts


def unparsed_boundaries(conn):
    """Count tract WKT that Snowflake cannot read as GEOGRAPHY (see EXPORTS.md)."""
    return conn.cursor().execute(
        'SELECT COUNT(*) FROM "TRACTS_PROCESSED" WHERE TRY_TO_GEOGRAPHY("BOUNDARY_WKT") IS NULL'
    ).fetchone()[0]


def cortex_check(conn, model=None):
    """Ask a Cortex LLM a one-line question to prove AI access works."""
    model = model or os.environ.get("SNOWFLAKE_CORTEX_MODEL", DEFAULT_MODEL)
    cursor = conn.cursor()
    cursor.execute("SELECT AI_COMPLETE(%s, %s)",
                   (model, "In one sentence, what is a census tract?"))
    return cursor.fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="skip the upload")
    args = parser.parse_args()
    with connect() as conn:
        row = conn.cursor().execute(
            "SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE()").fetchone()
        print("Connected as user={}, role={}, warehouse={}, database={}".format(*row))
        if not args.check:
            for table, rows in upload(conn).items():
                print(f"Loaded {rows} rows into {table}")
            print("Tract boundaries Snowflake could not parse as GEOGRAPHY:", unparsed_boundaries(conn))
        print("Cortex says:", cortex_check(conn))


if __name__ == "__main__":
    main()
