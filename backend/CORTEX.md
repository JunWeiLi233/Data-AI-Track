# Snowflake Cortex explanation endpoint

Install the optional Snowflake dependency in the backend's Python environment:

```sh
python -m pip install -r backend/requirements-snowflake.txt
python -m backend.server --port 8000
```

Set credentials as server-side environment variables through your shell or secret
manager. Do not put real secrets in repository files or frontend JavaScript.

| Variable | Purpose |
|---|---|
| SNOWFLAKE_ACCOUNT | Required account identifier |
| SNOWFLAKE_USER | Required user |
| SNOWFLAKE_PASSWORD | Password authentication, subject to the account's authentication/MFA policy |
| SNOWFLAKE_TOKEN | Alternative OAuth access token; takes precedence over password |
| SNOWFLAKE_WAREHOUSE | Optional warehouse; otherwise the user's default must support the queries |
| SNOWFLAKE_ROLE | Optional role with SELECT and Cortex permissions |
| SNOWFLAKE_SCORING_RUN_ID | Optional scoring run filter; required if more than one row matches a GEOID |
| SNOWFLAKE_CORTEX_MODEL | Defaults to the demonstrated `llama3.3-70b` |

No .env file is automatically loaded. The Snowflake connector is lazily imported,
so existing local features still work without it. Connections use bounded login,
network and SQL timeouts and are closed after each request. Passwords and OAuth
tokens are passed to the connector, never SQL or prompts; errors exposed to the
frontend are sanitized. No external LLM API key is required for this endpoint.

```sh
curl http://127.0.0.1:8000/api/tracts/36081157101/explanation
```

Successful responses retain these fields (and include provenance fields described below):

```json
{
  "geoid": "36081157101",
  "priority_score": 0.7706337458610021,
  "ai_explanation": "Census tract ..."
}
```

The numeric example is illustrative of the existing baseline; responses always
use the currently selected Snowflake row. The Python entry point is
`backend.cortex_explanations.get_tract_explanation(geoid)`.

The endpoint queries only
`NYC_TRANSIT_PLANNER.ANALYTICS.BASELINE_TRACT_SCORES` for priority_score,
nearest_station_distance_km, distance_score, population_score and
income_disadvantage_score, from its DATA VARIANT object, with a bound string GEOID and optional run ID.
The actual JSON paths are `DATA:"GEOID"` (uppercase), lowercase metric keys
(e.g. `DATA:"priority_score"`) and `DATA:"scoring_run_id"`. They are not
standalone table columns. JSON key names are case-sensitive. JSON-null priority
is explicitly converted to SQL NULL before casting to FLOAT. Values
must be finite, nonnegative, and component scores within 0–1; a non-null priority
must match the finalized 50/30/20 weighted sum (1e-6 tolerance). Null priority
remains null and is described as unavailable for residential ranking. The query
never substitutes local data, infers a neighborhood name or reconstructs raw
population/income from normalized components.

It calls parameterized `AI_COMPLETE` with the selected model and actual metrics,
requesting `TYPE OBJECT(sentence_order ARRAY(VARCHAR))`, `show_details=FALSE`
and `return_error_details=TRUE`. Errors returned as Snowflake value/error
envelopes are handled separately from SQL exceptions.
To enforce the no-invented-claims requirement, Cortex returns a JSON ordering of
three verified metric sentences; Python renders only those sentences plus the
actual priority and an accessibility-model limitation. Arbitrary model prose,
numbers or extra fields are never displayed. This is a constrained AI-assisted
explanation for the hackathon, not unconstrained planning advice.

| HTTP status | Meaning |
|---|---|
| 200 | Metrics retrieved and explanation generated |
| 200, labeled `Data-based fallback` in explanation | Cortex unavailable, empty/truncated output or unsupported response; explanation still uses actual retrieved metrics |
| 400 | GEOID is not an 11-digit string |
| 404 | No selected Snowflake tract row |
| 409 | Multiple scoring rows; select one using SNOWFLAKE_SCORING_RUN_ID |
| 502 | Invalid/incomplete metrics or a non-null score inconsistent with finalized weights |
| 503 | Missing credentials/connector, connection failure or tract query failure |

Service errors include geoid, null priority_score, null ai_explanation, and a safe
error message. No costs, ridership, feasibility, travel-time or neighborhood claims
are generated. Haversine distances are explicitly a straight-line geographic
proxy. Existing scoring, simulator and export modules are unchanged; the previous
external-provider explanation helper is retained but not called by this route.

The standard-library HTTP server remains intended for local hackathon use. The
endpoint is not a production authentication, quota or deployment solution.

References: [Snowflake connector authentication](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect),
[AI_COMPLETE single-string signature](https://docs.snowflake.com/en/sql-reference/functions/ai_complete-single-string).
The automated tests mock Snowflake, so they do not establish live account access.


## Retest the DATA VARIANT query and diagnose errors

Restart the backend in the same shell that has the Snowflake environment
variables configured, using the Python environment with the connector installed:

```sh
python -m backend.server --port 8000
curl -i http://127.0.0.1:8000/api/tracts/36081157101/explanation
```

The previous `Snowflake tract data is unavailable` error originates in the
tract query/fetch phase, after `_connect()` has returned. It does not indicate a
missing tract (404) or a connection failure (different 503 message). The former
query incorrectly referenced flat columns on a single-DATA-column JSON table.
The corrected query extracts the exported fields from VARIANT and casts them:

```sql
SELECT
  DATA:"GEOID"::VARCHAR AS GEOID,
  IFF(IS_NULL_VALUE(DATA:"priority_score"), NULL, DATA:"priority_score")::FLOAT AS priority_score,
  DATA:"nearest_station_distance_km"::FLOAT AS nearest_station_distance_km,
  DATA:"distance_score"::FLOAT AS distance_score,
  DATA:"population_score"::FLOAT AS population_score,
  DATA:"income_disadvantage_score"::FLOAT AS income_disadvantage_score
FROM NYC_TRANSIT_PLANNER.ANALYTICS.BASELINE_TRACT_SCORES
WHERE DATA:"GEOID"::VARCHAR = '36081157101';
```

Run that read-only query in Snowsight under the **same role and warehouse** as the
backend. To confirm the actual JSON keys/context without printing credentials:

```sql
SELECT CURRENT_ROLE(), CURRENT_WAREHOUSE();
DESCRIBE TABLE NYC_TRANSIT_PLANNER.ANALYTICS.BASELINE_TRACT_SCORES;
SELECT OBJECT_KEYS(DATA)
FROM NYC_TRANSIT_PLANNER.ANALYTICS.BASELINE_TRACT_SCORES LIMIT 1;
```

Use the backend terminal's `backend.cortex_explanations` logger for failures. It
logs a single structured `Snowflake diagnostic` record containing phase,
classification, error class, errno, SQLSTATE, query ID when supplied, and redacted
error text. It never logs connection settings, SQL bind parameters, prompts, raw
exceptions/tracebacks or response bodies. Environment credential values,
credential assignments, bearer tokens and URLs are redacted before logging.
Keep connector/root DEBUG logging off; third-party debug logs are independent of
this module's redaction. Public HTTP errors remain sanitized.

- `connect` + authentication/connection classification: inspect the login method,
  account identifier and authentication policy. Credentials must be visible to
  the running server process, not just configured in another terminal.
- `tract_query` + `sql_query`: inspect exact error and VARIANT path casing.
- `permissions_or_object_access`: check the configured role's object access;
  "does not exist or not authorized" cannot conclusively distinguish absence
  from denied access without inspecting grants/query history.
- `warehouse_configuration`: configure SNOWFLAKE_WAREHOUSE or a usable user default.
- `tract_lookup ... returned no rows`: successful query with no selected GEOID/run;
  verify the key case, tract presence, and SNOWFLAKE_SCORING_RUN_ID filter.
- `metric_validation`: selected fields are missing, malformed, non-finite or do
  not match the finalized baseline. Do not replace these with fabricated values.
- `cortex_sql`, `cortex_service`, or `cortex_response_validation`: see the Cortex diagnostics and verification section below. The HTTP response uses a clearly labeled factual fallback.

The query ID can be looked up in Snowflake Query History to inspect the server's
failure under the same context. Unit tests validate the new JSON-path SQL and
error redaction with mocked Snowflake errors; they do not establish live access
from another process.


## Cortex response diagnostics and verification

The reported error `string indices must be integers, not 'str'` occurred after
JSON decoding, when the old code expected an object but received a string.
The previous call did not enforce a response schema and the parser assumed one
encoding layer. The updated call requests structured output, and the parser
accepts native objects or up to four layers of JSON text encoding. A complete
JSON Markdown fence is accepted, but surrounding prose is not. The required
sentence-order object must still contain exactly three unique allowed IDs; extra
claims, fabricated values or arbitrary generated prose are never displayed.

Current SQL (the real request binds model and prompt as parameters):

```sql
SELECT AI_COMPLETE(
  model => 'llama3.3-70b',
  prompt => 'Return JSON with sentence_order listing distance, population, income once each.',
  model_parameters => {'temperature': 0, 'max_tokens': 256},
  response_format => TYPE OBJECT(sentence_order ARRAY(VARCHAR)),
  show_details => FALSE,
  return_error_details => TRUE
);
```

This intentionally does not log the completion body or prompt. Diagnostics use
only the model, query ID, error class/code, redacted Snowflake error text, response
native type/character count, and a fixed validation reason code.

| Phase | Category/fallback_reason | Meaning |
|---|---|---|
| cortex_sql | cortex_sql_error | SQL compilation/execution or fetch failure |
| cortex_sql or cortex_service | cortex_permissions_error | Privileges/access-control error reported by Snowflake |
| cortex_sql or cortex_service | cortex_model_unavailable | Unknown/disallowed/unavailable model or regional restriction |
| cortex_service | cortex_service_error | SQL succeeded, but AI_COMPLETE returned an explicit error envelope |
| cortex_response_validation | cortex_response_validation | Empty, malformed, excessively encoded, or invalid sentence-order response |

Response-validation logs include codes such as `empty_completion`,
`invalid_json_or_truncated_output`, `unexpected_response_fields`, or
`invalid_sentence_order`. Only fixed codes and shape metadata are logged, not
arbitrary response excerpts. Snowflake model/permission classification is based
on the error Snowflake actually returned; it is not proof that grants changed.

Every successful HTTP response now includes:

- `explanation_source`: `snowflake_cortex` **only after a successful Cortex SQL
  call, fetch, and validated response**; otherwise `fallback`.
- `fallback_reason`: null for an accepted Cortex result; a category above otherwise.
- `cortex_query_id`: the completed Cortex query ID, or the failed query's ID when
  Snowflake provides one. It never reuses the preceding tract SELECT query ID
  after a Cortex SQL failure.
- `cortex_model`: requested model, default `llama3.3-70b`.

These fields explicitly distinguish a successful database query from a successful
AI explanation. The text itself remains Python-rendered verified sentences, whose
order is selected by Cortex. This provenance identifies that constrained AI path;
it does not imply unrestricted model-generated prose.

Restart the backend in its configured environment, then verify:

```sh
curl --fail --silent --show-error \
  http://127.0.0.1:8000/api/tracts/36081157101/explanation | \
  python -c 'import json,sys; r=json.load(sys.stdin); assert r["explanation_source"] == "snowflake_cortex", r.get("fallback_reason"); assert r["fallback_reason"] is None; assert r["cortex_query_id"]; print(json.dumps(r, indent=2))'
```

A zero exit status confirms that the running endpoint returned the accepted
Cortex path rather than fallback. Look up `cortex_query_id` in Snowflake Query
History to corroborate its AI_COMPLETE statement under the backend's role.
Mocks verify parsing/control flow but cannot establish that the live account
executed Cortex successfully. Do not describe mocked success as live verification.

Official references: [structured outputs](https://docs.snowflake.com/en/sql-reference/functions/ai_complete-structured-outputs),
[AI_COMPLETE error envelopes](https://docs.snowflake.com/en/sql-reference/functions/ai_complete).
