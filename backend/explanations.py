"""One grounded explanation function for a Snowflake or backend analytics row.

No database writes or score recomputation. The external model can arrange only
verified sentences; arbitrary generated claims are never displayed.
"""
import json
import math
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pandas as pd

WEIGHTS = {"distance": 0.5, "population": 0.3, "income": 0.2}


def _number(row, key, *, nullable=False):
    if key not in row:
        raise ValueError(f"Missing analytics field: {key}")
    value = row[key]
    if value is None or pd.isna(value):
        if nullable:
            return None
        raise ValueError(f"{key} is required")
    if isinstance(value, bool):
        raise ValueError(f"{key} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{key} must be finite and nonnegative")
    return number


def _facts(analytics_row):
    raw = dict(analytics_row)  # Also accepts a pandas Series.
    row = {str(key).lower(): value for key, value in raw.items()}
    if len(row) != len(raw):
        raise ValueError("Ambiguous analytics column names")
    geoid = row.get('geoid')
    if not isinstance(geoid, str) or not re.fullmatch(r'[0-9]{11}', geoid):
        raise ValueError("GEOID must be an 11-digit string")
    facts = {"GEOID": geoid}
    for key in ("population", "nearest_station_distance_km", "distance_score", "population_score",
                "income_disadvantage_score", "median_household_income", "priority_score"):
        facts[key] = _number(row, key, nullable=key in ("median_household_income", "priority_score"))
    if not facts['population'].is_integer():
        raise ValueError("population must be an integer")
    facts['population'] = int(facts['population'])
    for key in ("distance_score", "population_score", "income_disadvantage_score", "priority_score"):
        if facts[key] is not None and facts[key] > 1:
            raise ValueError(f"{key} must be between 0 and 1")
    if facts['population'] == 0:
        if facts['priority_score'] is not None:
            raise ValueError("Zero-population tracts must have no residential priority score")
    else:
        expected = (0.5 * facts['distance_score'] + 0.3 * facts['population_score']
                    + 0.2 * facts['income_disadvantage_score'])
        if facts['priority_score'] is None or not math.isclose(facts['priority_score'], expected, abs_tol=1e-6):
            raise ValueError("Priority score does not match the required 50/30/20 baseline")
    income = facts['median_household_income']
    facts['income_score_neutral'] = income is None or income <= 0
    if facts['income_score_neutral'] and not math.isclose(facts['income_disadvantage_score'], 0.5, abs_tol=1e-6):
        raise ValueError("Unavailable/nonpositive income must have the existing neutral component of 0.5")
    # Optional run metadata makes accidental custom-weight rows fail explicitly.
    if 'weights' in row:
        weights = json.loads(row['weights']) if isinstance(row['weights'], str) else row['weights']
        if weights != WEIGHTS:
            raise ValueError("Explanation requires the 50/30/20 scoring run")
    # Do not call a top-coded income an exact estimate.
    flag = row.get('median_household_income_topcoded', False)
    if not isinstance(flag, bool):
        if pd.isna(flag):
            flag = False
        elif flag in (0, 1):
            flag = bool(flag)
        else:
            raise ValueError("Income top-code flag must be boolean")
    facts['median_household_income_topcoded'] = flag
    return facts


def _sentences(facts):
    income = facts['median_household_income']
    income_text = "Median household income is unavailable" if income is None else (
        f"Reported median household income is {'at least ' if facts['median_household_income_topcoded'] else ''}${income:,.0f}")
    if facts['income_score_neutral']:
        income_text += "; the model uses a neutral income component rather than treating missing income as zero"
    return {
        "distance": f"The tract reference point is {facts['nearest_station_distance_km']:.3f} km from its nearest existing station, a straight-line Haversine proxy; its distance score is {facts['distance_score']:.4f} (50% weight).",
        "population": f"Reported population is {facts['population']:,}; its population score is {facts['population_score']:.4f} (30% weight).",
        "income": f"{income_text}; its income disadvantage score is {facts['income_disadvantage_score']:.4f} (20% weight).",
    }


def explain_tract(analytics_row):
    """Return {explanation, source, fallback_reason, facts, weights} for one tract.

    Pass a joined Snowflake row or equivalent loader/scorer row as dict/Series.
    Snowflake uppercase columns and Decimal values are accepted. Values are
    validated, never inferred from GEOID or generated by the model. Invalid data
    raises ValueError; provider/configuration failures use a factual fallback.

    Env: LLM_PROVIDER=openai|openai-compatible, LLM_MODEL, LLM_API_KEY,
    OPENAI_API_KEY (OpenAI fallback), LLM_BASE_URL (custom /v1 endpoint).
    Returns a JSON-compatible dict; credentials/exceptions are never included.
    """
    facts = _facts(analytics_row)
    sentences = _sentences(facts)
    order = list(sentences)
    source, reason = "fallback", "no_api_key"
    provider = os.getenv('LLM_PROVIDER', 'openai').strip().lower()
    key = os.getenv('LLM_API_KEY', '').strip() or (
        os.getenv('OPENAI_API_KEY', '').strip() if provider == 'openai' else '')
    if key:
        model = os.getenv('LLM_MODEL', 'gpt-4o-mini' if provider == 'openai' else '').strip()
        base = os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1' if provider == 'openai' else '').strip()
        parsed = urlparse(base)
        if (provider not in ('openai', 'openai-compatible') or not model or parsed.scheme != 'https'
                or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
            reason = 'invalid_provider_configuration'
        else:
            payload = {"model": model, "response_format": {"type": "json_object"}, "messages": [
                {"role": "system", "content":
                 'Organize an explanation for government infrastructure planners using ONLY supplied facts. '
                 'Return JSON with exactly one key, sentence_order, whose value is a permutation of '
                 '["distance", "population", "income"], most useful explanatory factor first. '
                 'Consider the supplied component scores and 50/30/20 weights. '
                 'Do not write prose or invent neighborhood names, ridership, costs, feasibility or travel times.'},
                {"role": "user", "content": json.dumps({"facts": facts, "weights": WEIGHTS,
                                                          "verified_sentences": sentences}, allow_nan=False)}]}
            request = Request(base.rstrip('/') + '/chat/completions',
                              data=json.dumps(payload).encode('utf-8'), method='POST',
                              headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
            try:
                with urlopen(request, timeout=10) as response:
                    message = json.loads(response.read())['choices'][0]['message']['content']
                result = json.loads(message)
                candidate = result['sentence_order']
                if (set(result) != {'sentence_order'} or not isinstance(candidate, list)
                        or len(candidate) != 3 or not all(isinstance(item, str) for item in candidate)
                        or set(candidate) != set(sentences)):
                    raise ValueError('Invalid sentence order')
                order, source, reason = candidate, 'llm', None
            except (HTTPError, URLError, OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
                # Do not expose response bodies, provider errors or API keys to the UI.
                reason = 'provider_unavailable_or_invalid_response'
    if facts['priority_score'] is None:
        lead = f"Census tract {facts['GEOID']} has no residential priority score or rank because its population is zero."
    else:
        lead = f"Census tract {facts['GEOID']} has an investment priority score of {facts['priority_score']:.4f} on a 0–1 scale; higher scores indicate greater modeled need."
    text = ' '.join([lead] + [sentences[item] for item in order] + [
        'This is a geographic planning screen, not a prediction of ridership, construction costs, feasibility or travel time.'])
    return {'explanation': text, 'source': source, 'fallback_reason': reason, 'facts': facts,
            'weights': dict(WEIGHTS)}
