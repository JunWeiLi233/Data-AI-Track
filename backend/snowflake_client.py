"""Connect to the team Snowflake account, upload cleaned tables, and check Cortex.

Run from the repository root after filling in .env (see .env.example):

    python -m backend.snowflake_client            # upload tables + Cortex check
    python -m backend.snowflake_client --check    # connection + Cortex check only
"""
import argparse
import os
import re
from pathlib import Path

import pandas as pd

from backend.data_loader import load_data

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
REQUIRED = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER"]
DEFAULTS = {
    "SNOWFLAKE_ROLE": "TRANSIT_TEAM",
    "SNOWFLAKE_WAREHOUSE": "TRANSIT_WH",
    "SNOWFLAKE_DATABASE": "TRANSIT_GAP",
    "SNOWFLAKE_SCHEMA": "PUBLIC",
}
DEFAULT_MODEL = "mistral-large2"


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
    # Password may be a real password or a programmatic access token.
    if env.get("SNOWFLAKE_PASSWORD"):
        settings["password"] = env["SNOWFLAKE_PASSWORD"]
    if env.get("SNOWFLAKE_AUTHENTICATOR"):
        settings["authenticator"] = env["SNOWFLAKE_AUTHENTICATOR"]
    if "password" not in settings and "authenticator" not in settings:
        raise RuntimeError("Set SNOWFLAKE_PASSWORD, or SNOWFLAKE_AUTHENTICATOR=externalbrowser.")
    return settings


def connect():
    import snowflake.connector
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    return snowflake.connector.connect(**connection_settings())


def to_snowflake_frame(frame):
    """Make a loader DataFrame uploadable: plain-text columns, UPPER_SNAKE names.

    Shapely objects are dropped (tracts keep the original WKT in THE_GEOM) and
    tuples of merged station IDs or names are joined with " | ".
    """
    frame = frame.drop(columns=["geometry"], errors="ignore").copy()
    for column in frame.columns[frame.dtypes == object]:
        frame[column] = frame[column].map(
            lambda v: " | ".join(map(str, v)) if isinstance(v, tuple) else v).astype("string")
    names = [re.sub(r"[^0-9A-Z]+", "_", str(c).upper()).strip("_") for c in frame.columns]
    if len(set(names)) != len(names):
        raise ValueError(f"Column names collide after cleaning: {names}")
    frame.columns = names
    return frame


def upload(conn, data=None):
    """Replace STATIONS, ENTRANCES and TRACTS with freshly loaded data. Returns row counts."""
    from snowflake.connector.pandas_tools import write_pandas

    data = data or load_data()
    counts = {}
    for table, frame in [("STATIONS", data.stations), ("ENTRANCES", data.entrances),
                         ("TRACTS", data.tracts)]:
        ok, _, rows, _ = write_pandas(conn, to_snowflake_frame(frame), table,
                                      auto_create_table=True, overwrite=True,
                                      quote_identifiers=False)
        if not ok:
            raise RuntimeError(f"Upload of {table} failed")
        counts[table] = rows
    return counts


def cortex_check(conn, model=None):
    """Ask a Cortex LLM a one-line question to prove AI access works."""
    model = model or os.environ.get("SNOWFLAKE_CORTEX_MODEL", DEFAULT_MODEL)
    cursor = conn.cursor()
    cursor.execute("SELECT SNOWFLAKE.CORTEX.COMPLETE(%s, %s)",
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
                print(f"Uploaded {rows} rows to {table}")
        print("Cortex says:", cortex_check(conn))


if __name__ == "__main__":
    main()
