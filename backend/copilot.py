"""Planning copilot: grounded answers from Snowflake Cortex.

Each question gets two kinds of evidence before the answer is written:

1. Cortex Search over the profiles loaded by snowflake_store (neighborhoods,
   tracts, stations, lines and method notes).
2. Optional read-only SQL: AI_COMPLETE drafts one SELECT against the planning
   tables, which runs as SUBWAY_READER (no secondary roles) after a guard.

AI_COMPLETE then answers from that evidence plus the recommendations currently
shown in the app, and the response lists the sources and SQL it used.
"""
import json
import re
import threading

from .snowflake_store import SEARCH_SERVICE, connect

MODEL = "claude-sonnet-4-5"
SEARCH_LIMIT = 6
MAX_ROWS = 50
MAX_HISTORY = 6

SCHEMA = """Tables in SUBWAY.APP (Snowflake SQL; distances are straight-line km):
TRACTS(GEOID, BOROUGH, NEIGHBORHOOD, POPULATION, MEDIAN_INCOME, INCOME_TOPCODED, AREA_KM2,
  DENSITY_PER_KM2, LATITUDE, LONGITUDE, NEAREST_STATION, NEAREST_STATION_KM, WALK_MINUTES,
  DISTANCE_SCORE, POPULATION_SCORE, INCOME_DISADVANTAGE_SCORE, NEED_SCORE (0-1, higher = more need),
  NEED_RANK (1 = most need), IS_TRANSIT_DESERT (more than 1.2 km from every station), GEOGRAPHY)
STATIONS(STATION_ID, NAME, LATITUDE, LONGITUDE, LINES, ROUTES, BOROUGH, NEIGHBORHOOD, LOCATION)
LINES(LINE, DIVISION, ROUTES, COLOR, STATION_COUNT, LENGTH_KM, STATIONS)
NEIGHBORHOODS(BOROUGH, NEIGHBORHOOD, TRACTS, POPULATION, AVG_NEAREST_STATION_KM, DESERT_POPULATION,
  MEDIAN_OF_TRACT_INCOMES, MAX_NEED_SCORE)
BOROUGH values: Manhattan, Bronx, Brooklyn, Queens, Staten Island. GEOGRAPHY and LOCATION are
GEOGRAPHY columns for ST_ functions (ST_DWITHIN uses meters); never select them directly."""

SQL_PROMPT = """You write Snowflake SQL for a NYC subway planning assistant.
{schema}

Question: {question}

If a single read-only query over these tables would help answer the question (counts, rankings,
filters, aggregates, comparisons), reply with only that query: one SELECT or WITH statement, no
semicolon, at most {max_rows} rows, rounded numbers, readable column aliases. If the question is
about methodology, definitions, or the app's current recommendations, or the tables cannot help,
reply with exactly NONE."""

ANSWER_PROMPT = """You are the planning copilot in the NYC Subway Optimization app, helping city
planners understand where new subway stations would improve access.

Answer the question using only the evidence below. Give specific numbers and place names from the
evidence and keep it under 180 words. Format: plain sentences, "- " bullets and **bold** only (no
headings, tables or links). If the evidence does not answer the question, say what is missing instead
of guessing. When relevant, remind the reader that distances are straight-line and the models measure
geographic access, not ridership or cost.

Definitions: a 10-minute walk is 800 m; walking time assumes 80 m per minute; a transit desert is a
residential tract more than 1.2 km (about 0.75 miles, a 15-minute walk) from every station; the need
score runs from 0 to 1 (higher means more need) and need rank 1 is the most underserved tract; a
station's need score (0 to 100) is the average need score of the tracts it brings closer to a station,
so higher again means more need. Stations are ranked in the order the optimizer added them: each one
had the largest gain for the chosen goal given the stations already placed.

Recommendations currently shown in the app (JSON):
{recommendations}

Retrieved profiles:
{documents}

SQL query result{sql_note}:
{rows}

Conversation so far:
{history}

Question: {question}"""

FORBIDDEN = re.compile(r"\b(insert|update|delete|merge|create|alter|drop|truncate|grant|revoke|call|"
                       r"execute|use|put|get|copy|undrop|comment|describe|show|set|unset)\b", re.I)

_lock = threading.Lock()
_connections = {}


def _cursor(role):
    """Per-role cached connection; the reader never inherits the app role's privileges."""
    con = _connections.get(role)
    if con is None or con.is_closed():
        con = connect(role=role)
        if role == "SUBWAY_READER":
            con.cursor().execute("use secondary roles none")
        _connections[role] = con
    return con.cursor()


def _complete(prompt, max_tokens=1024):
    cur = _cursor("SUBWAY_APP")
    raw = cur.execute("select ai_complete(%s, %s, parse_json(%s))",
                      (MODEL, prompt, json.dumps({"temperature": 0, "max_tokens": max_tokens}))).fetchone()[0]
    return parse_completion(raw)


def parse_completion(raw):
    """AI_COMPLETE returns a JSON string literal when called with model parameters."""
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            raw = value if isinstance(value, str) else value.get("choices", [{}])[0].get("messages", raw)
        except (ValueError, AttributeError):
            pass
    return str(raw).strip()


def clean_sql(text):
    """Return a single guarded SELECT, or None when the model declined or the SQL is unsafe."""
    text = re.sub(r"^```(?:sql)?|```$", "", text.strip(), flags=re.I | re.M).strip().rstrip(";").strip()
    if not text or text.upper() == "NONE" or ";" in text:
        return None
    if not re.match(r"^(select|with)\b", text, re.I) or FORBIDDEN.search(text):
        return None
    return text


def search(question):
    cur = _cursor("SUBWAY_APP")
    payload = json.dumps({"query": question, "columns": ["DOC_ID", "KIND", "TITLE", "BODY"], "limit": SEARCH_LIMIT})
    raw = cur.execute("select snowflake.cortex.search_preview(%s, %s)", (SEARCH_SERVICE, payload)).fetchone()[0]
    return json.loads(raw)["results"]


def run_sql(sql):
    cur = _cursor("SUBWAY_READER")
    cur.execute("alter session set statement_timeout_in_seconds = 20")
    cur.execute(f"select * from ({sql}) limit {MAX_ROWS}")
    columns = [c[0] for c in cur.description]
    rows = []
    for record in cur.fetchall():
        row = {}
        for column, value in zip(columns, record):
            if isinstance(value, str) and len(value) > 300:
                value = value[:300] + "…"
            row[column] = value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
        rows.append(row)
    return columns, rows


def ask(question, recommendations=None, history=None):
    question = (question or "").strip()
    if not question:
        raise ValueError("question is required")
    if len(question) > 1000:
        raise ValueError("question must be at most 1000 characters")
    history = [h for h in (history or []) if isinstance(h, dict)][-MAX_HISTORY:]
    with _lock:
        documents = search(question)
        sql = clean_sql(_complete(SQL_PROMPT.format(schema=SCHEMA, question=question, max_rows=MAX_ROWS), 600))
        columns, rows, sql_error = [], [], None
        if sql:
            try:
                columns, rows = run_sql(sql)
            except Exception as error:  # surface the failure to the model and the UI
                sql_error = str(error).splitlines()[0][:200]
        answer = _complete(ANSWER_PROMPT.format(
            recommendations=json.dumps(_compact(recommendations), default=str)[:6000],
            documents="\n".join(f"- [{d['TITLE']}] {d['BODY']}" for d in documents) or "none",
            sql_note=f" (query: {sql})" if sql else "",
            rows=json.dumps(rows, default=str)[:6000] if sql and not sql_error else (
                f"query failed: {sql_error}" if sql_error else "no query was needed"),
            history="\n".join(f"{h.get('role', 'user')}: {str(h.get('text', ''))[:500]}" for h in history) or "none",
            question=question))
    return {"answer": answer, "model": MODEL,
            "sources": [{"id": d["DOC_ID"], "kind": d["KIND"], "title": d["TITLE"]} for d in documents],
            "sql": sql, "sql_error": sql_error, "columns": columns, "rows": rows[:10]}


def _compact(recommendations):
    """Keep the fields the model needs from the app's /api/recommendations payload."""
    if not isinstance(recommendations, dict):
        return None
    # Self-describing keys, so the model cannot misread what each number measures.
    keys = {"rank": "rank", "name": "neighborhood", "borough": "borough", "near": "location",
            "on_existing_line": "on_existing_track", "offline_km": "new_track_km",
            "residents": "residents_newly_within_10_min_walk", "residents_benefiting": "residents_with_shorter_walk",
            "minutes_saved": "avg_walk_minutes_saved_for_those_residents",
            "score": "need_score_0_to_100_higher_means_more_need"}
    return {"goal": {"access": "accessibility", "eff": "efficiency"}.get(recommendations.get("goal"),
                                                                       recommendations.get("goal")),
            "constraints": recommendations.get("constraints"),
            "impact": recommendations.get("impact"),
            "stations": [{label: s.get(key) for key, label in keys.items()}
                         for s in recommendations.get("stations", []) if isinstance(s, dict)]}
