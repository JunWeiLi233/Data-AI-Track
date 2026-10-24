"""Read selected tract metrics and explain them with Snowflake Cortex AI."""
import json
import logging
import math
import os
import re

LOGGER = logging.getLogger(__name__)

TABLE = "NYC_TRANSIT_PLANNER.ANALYTICS.BASELINE_TRACT_SCORES"
METRICS = ("priority_score", "nearest_station_distance_km", "distance_score",
           "population_score", "income_disadvantage_score")


def _redact(message):
    """Keep useful Snowflake error text while removing secrets and URLs."""
    text = str(message)
    sensitive = ("PASSWORD", "PASSWD", "TOKEN", "SECRET", "API_KEY", "PRIVATE_KEY")
    values = [value for name, value in os.environ.items() if value and (
        any(part in name.upper() for part in sensitive) or name in ("SNOWFLAKE_USER", "SNOWFLAKE_ACCOUNT"))]
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
        text = text.replace(json.dumps(value)[1:-1], "[REDACTED]")
    text = re.sub(r"https?://[^\s]+", "[REDACTED_URL]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)\b(password|passwd|token|access_token|refresh_token|client_secret|private_key|api_key|authorization)"
        r"\s*[:=]\s*(\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1=[REDACTED]", text)
    return text.replace("\r", " ").replace("\n", " ")[:2048]


def _log_failure(phase, error, *, cursor=None, geoid=None):
    """Log a redacted message and connector codes, never raw exceptions/tracebacks."""
    state = str(getattr(error, "sqlstate", "") or "")
    message = str(getattr(error, "msg", None) or error)
    lower = message.lower()
    if state.startswith("28") or "authentication" in lower or "incorrect username" in lower:
        category = "authentication"
    elif state == "42501" or any(term in lower for term in ("not authorized", "privilege", "access control")):
        category = "permissions_or_object_access"
    elif "warehouse" in lower:
        category = "warehouse_configuration"
    elif state.startswith("42"):
        category = "sql_query"
    elif phase == "connect":
        category = "connection_or_authentication"
    else:
        category = phase
    query_id = getattr(error, "sfqid", None)
    if not query_id and cursor is not None:
        candidate = getattr(cursor, "sfqid", None)
        query_id = candidate if isinstance(candidate, str) else None
    diagnostic = {"phase": phase, "category": category, "error_type": type(error).__name__,
                  "errno": getattr(error, "errno", None), "sqlstate": state or None,
                  "query_id": query_id, "message": message}
    if geoid is not None:
        diagnostic["geoid"] = geoid
    safe = {key: _redact(value) if value is not None else None for key, value in diagnostic.items()}
    LOGGER.error("Snowflake diagnostic: %s", json.dumps(safe, ensure_ascii=False))


def _tract_query(run_id=None):
    # DATA stores the exported JSON object. JSON keys are case-sensitive:
    # GEOID is uppercase, while metric keys and scoring_run_id are lowercase.
    projections = []
    for metric in METRICS:
        path = f'DATA:"{metric}"'
        if metric == "priority_score":
            # Preserve JSON null as SQL NULL; do not turn unranked tracts into zero.
            path = f"IFF(IS_NULL_VALUE({path}), NULL, {path})"
        projections.append(f"{path}::FLOAT AS {metric}")
    sql = "SELECT " + ", ".join(projections) + " FROM " + TABLE + ' WHERE DATA:"GEOID"::VARCHAR = %s'
    if run_id:
        sql += ' AND DATA:"scoring_run_id"::VARCHAR = %s'
    return sql + " LIMIT 2"


class ExplanationError(Exception):
    """Public, sanitized error suitable for the HTTP endpoint."""
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def _connect():
    account = os.getenv("SNOWFLAKE_ACCOUNT", "").strip()
    user = os.getenv("SNOWFLAKE_USER", "").strip()
    password = os.getenv("SNOWFLAKE_PASSWORD", "")
    token = os.getenv("SNOWFLAKE_TOKEN", "")
    if not account or not user or not (password or token):
        missing = [name for name, value in (("SNOWFLAKE_ACCOUNT", account), ("SNOWFLAKE_USER", user),
                   ("SNOWFLAKE_PASSWORD or SNOWFLAKE_TOKEN", password or token)) if not value]
        LOGGER.warning("Snowflake configuration missing: %s", ", ".join(missing))
        raise ExplanationError(503, "Snowflake credentials are not configured")
    try:
        from snowflake import connector
    except ImportError as error:
        _log_failure("connector_import", error)
        raise ExplanationError(503, "Snowflake connector is not installed") from None
    settings = {"account": account, "user": user, "database": "NYC_TRANSIT_PLANNER",
                "schema": "ANALYTICS", "login_timeout": 10, "network_timeout": 30,
                "session_parameters": {"STATEMENT_TIMEOUT_IN_SECONDS": 30}}
    if token:
        settings.update(authenticator="oauth", token=token)
    else:
        settings["password"] = password
    for variable, key in (("SNOWFLAKE_WAREHOUSE", "warehouse"), ("SNOWFLAKE_ROLE", "role")):
        if os.getenv(variable, "").strip():
            settings[key] = os.environ[variable].strip()
    try:
        return connector.connect(**settings)
    except Exception as error:
        _log_failure("connect", error)
        raise ExplanationError(503, "Snowflake connection is unavailable") from None


def _metric_values(row):
    try:
        values = {key: None if value is None else float(value) for key, value in zip(METRICS, row)}
        if len(row) != len(METRICS):
            raise ValueError()
        for key, value in values.items():
            if value is None:
                if key != "priority_score":
                    raise ValueError()
            elif not math.isfinite(value) or value < 0 or (key != "nearest_station_distance_km" and value > 1):
                raise ValueError()
        expected = 0.5 * values["distance_score"] + 0.3 * values["population_score"] + 0.2 * values["income_disadvantage_score"]
        if values["priority_score"] is not None and not math.isclose(values["priority_score"], expected, abs_tol=1e-6):
            raise ValueError()
        return values
    except (TypeError, ValueError, OverflowError):
        raise ExplanationError(502, "Snowflake tract metrics are invalid or do not match the 50/30/20 baseline") from None


def _close(resource):
    if resource is not None:
        try:
            resource.close()
        except Exception:
            pass


def get_tract_explanation(geoid):
    """Return geoid, priority_score, ai_explanation, using no local data substitute.

    Missing tract: 404; duplicate scoring runs: 409; invalid metrics: 502;
    missing credentials/connection/query failure: 503. Cortex failure returns
    a labeled deterministic explanation based only on retrieved Snowflake facts.
    """
    if not isinstance(geoid, str) or not re.fullmatch(r"[0-9]{11}", geoid):
        raise ValueError("GEOID must be an 11-digit string")
    connection = cursor = None
    try:
        connection = _connect()
        try:
            cursor = connection.cursor()
            parameters = [geoid]
            run_id = os.getenv("SNOWFLAKE_SCORING_RUN_ID", "").strip()
            if run_id:
                parameters.append(run_id)
            cursor.execute(_tract_query(run_id), tuple(parameters), timeout=30)
            rows = cursor.fetchmany(2)
        except Exception as error:
            _log_failure("tract_query", error, cursor=cursor, geoid=geoid)
            raise ExplanationError(503, "Snowflake tract data is unavailable") from None
        if not rows:
            LOGGER.warning("Snowflake tract_lookup: geoid=%s returned no rows; check JSON key case and scoring run filter", geoid)
            raise ExplanationError(404, "Census tract not found")
        if len(rows) != 1:
            raise ExplanationError(409, "Multiple scoring rows found; configure SNOWFLAKE_SCORING_RUN_ID")
        try:
            facts = _metric_values(rows[0])
        except ExplanationError as error:
            _log_failure("metric_validation", error, cursor=cursor, geoid=geoid)
            raise
        score = facts["priority_score"]
        lead = (f"Census tract {geoid} has investment priority {score:.4f} on a 0–1 scale; higher means greater modeled need."
                if score is not None else f"Census tract {geoid} has no residential priority score in this scoring run.")
        sentences = {
            "distance": f"Nearest-station distance is {facts['nearest_station_distance_km']:.3f} km using a straight-line Haversine proxy; the distance score is {facts['distance_score']:.4f} with 50% weight.",
            "population": f"The population score is {facts['population_score']:.4f} with 30% weight.",
            "income": f"The income disadvantage score is {facts['income_disadvantage_score']:.4f} with 20% weight; this component alone does not establish a household income estimate.",
        }
        prompt = (
            "Arrange a concise explanation for government infrastructure planners using only these actual Snowflake metrics. "
            "Return only JSON with exactly one key, sentence_order, containing each of distance, population, income once, "
            "most useful explanatory factor first. Do not produce prose or add facts. "
            "Weights are 50% distance, 30% population, 20% income disadvantage. "
            "Never invent neighborhood names, construction costs, ridership, travel times or feasibility claims.\n"
            + json.dumps({"geoid": geoid, "metrics": facts, "verified_sentences": sentences}, allow_nan=False))
        order, fallback = list(sentences), False
        try:
            model = os.getenv("SNOWFLAKE_CORTEX_MODEL", "llama3.3-70b").strip() or "llama3.3-70b"
            cursor.execute("SELECT AI_COMPLETE(%s, %s, {'temperature': 0, 'max_tokens': 128})",
                           (model, prompt), timeout=30)
            completion = cursor.fetchone()
            result = json.loads(completion[0])
            candidate = result["sentence_order"]
            if (set(result) != {"sentence_order"} or not isinstance(candidate, list) or len(candidate) != 3
                    or not all(isinstance(item, str) for item in candidate) or set(candidate) != set(sentences)):
                raise ValueError()
            order = candidate
        except Exception as error:
            _log_failure("cortex", error, cursor=cursor, geoid=geoid)
            # Never render arbitrary model text or expose Snowflake/provider error details.
            fallback = True
        text = " ".join([lead] + [sentences[key] for key in order] + [
            "This is a geographic planning screen, not a feasibility, cost, ridership or travel-time prediction."])
        if fallback:
            text = "Data-based fallback (Cortex unavailable or response rejected). " + text
        return {"geoid": geoid, "priority_score": score, "ai_explanation": text}
    finally:
        _close(cursor)
        _close(connection)
