# External AI tract explanations

```python
from backend.explanations import explain_tract

result = explain_tract(analytics_row)
# Streamlit: st.write(result["explanation"])
# Display result["source"] to distinguish "llm" from "fallback".
```

`analytics_row` is a dict or pandas Series containing actual joined Snowflake
metrics, or the equivalent row from the existing loader/scoring output. The
function makes no database writes, local reloads or scoring/simulation changes.
Snowflake stays the analytics database. The repository has no Snowflake connector
configuration, so database authentication/query execution remains with the
existing caller, not a second credential path in the explanation module.

For the uploaded table schema, fetch a **single selected scoring run and GEOID**:
join BASELINE_TRACT_SCORES to TRACTS_PROCESSED on dataset_id and GEOID, and join
SCORING_RUNS on scoring_run_id and dataset_id. Return these fields:

- GEOID (string), population, median_household_income,
  median_household_income_topcoded.
- nearest_station_distance_km, distance_score, population_score,
  income_disadvantage_score, priority_score.
- Optionally weights from SCORING_RUNS for explicit run validation.

Use parameterized queries in the existing Snowflake query layer. Do not join
solely by GEOID across multiple datasets/runs. Uppercase Snowflake column names
and Decimal values are accepted. With local backend data, a row from
`score_tracts(load_data()).iloc[0]` already supplies the required fields.

This minimal feature explains the finalized 50% distance, 30% population and 20%
income disadvantage baseline. It validates that the supplied priority score
matches those weights (tolerance 1e-6) and rejects mismatched or incomplete data
instead of inventing metrics. Custom scoring runs need a future explicit API
extension; their explanation must not be mislabeled as the finalized baseline.

## Configuration

Read credentials from server-side environment variables only. No .env file is
loaded automatically and no key is hardcoded. Configure variables in your runtime
or secret manager, never in frontend JavaScript or committed files.

| Variable | Purpose |
|---|---|
| LLM_PROVIDER | `openai` (default) or `openai-compatible` |
| LLM_API_KEY | External provider secret; optional for fallback mode |
| OPENAI_API_KEY | Used for OpenAI only when LLM_API_KEY is absent |
| LLM_MODEL | Default `gpt-4o-mini` for OpenAI; required for custom providers |
| LLM_BASE_URL | Default `https://api.openai.com/v1`; required for custom providers; HTTPS `/v1`-style base |

Custom providers must support the OpenAI-compatible Chat Completions endpoint
and JSON-object response mode. No new Python dependency is required. The request
has a 10-second timeout, no retries and no tools or web access.

The call returns `explanation`, `source`, `fallback_reason`, `facts` and `weights`.
Missing keys skip the network entirely. HTTP errors, timeouts, malformed outputs
or unsupported provider configuration return the same factual local explanation,
labeled `source="fallback"`. Errors never expose credentials or provider error
bodies to the frontend. Invalid analytics inputs raise ValueError separately.

## Grounding and limits

To enforce the requirement never to invent metrics or neighborhood names, the
LLM **orders verified explanation sentences** using actual component scores and
weights. The model returns only a permutation of distance/population/income IDs;
no model-generated prose, numbers or names are displayed. Unknown IDs, duplicate
IDs or extra generated claims trigger the fallback. This is deliberately a
constrained AI-assisted explanation, not open-ended planning advice.

All visible sentences use actual supplied values. Missing income stays missing,
neutral income handling is described, top-coded income is labeled as a lower
bound, and zero-population tracts are explicitly unranked. Values are rounded
only for display. Every explanation labels distance as a straight-line Haversine
proxy and disclaims ridership, construction-cost, feasibility and travel-time
prediction. It includes no neighborhood name. Only the selected tract's required
numeric facts, GEOID and verified sentences are sent to the configured provider;
other source columns and database credentials are never sent.

Request format reference: [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
Tests mock the provider; a live authenticated API request requires a runtime key
and has not been claimed by offline verification.
