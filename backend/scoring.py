"""Explainable residential infrastructure priority scoring for city planning.

No source values are imputed or mutated. Distances are straight-line haversine
kilometers; scores are relative to the supplied set of tracts.
"""
from collections.abc import Mapping

import numpy as np
import pandas as pd

from .data_loader import SubwayData, load_data

DEFAULT_WEIGHTS = {"distance": 0.5, "population": 0.3, "income": 0.2}
EARTH_RADIUS_KM = 6371.0088


def _numeric(values):
    return pd.to_numeric(values, errors="coerce").astype("float64").replace(
        [np.inf, -np.inf], np.nan
    )


def _coordinate_arrays(frame):
    required = {"latitude", "longitude"}
    if not required.issubset(frame.columns):
        raise ValueError("Coordinates require latitude and longitude columns")
    lat, lon = _numeric(frame.latitude), _numeric(frame.longitude)
    valid = lat.between(-90, 90) & lon.between(-180, 180)
    return lat.to_numpy(), lon.to_numpy(), valid.to_numpy()


def nearest_station_distances(tracts, stations):
    """Return nearest haversine distances (km), preserving tract row order/index.

    Takes loader DataFrames. Invalid station coordinates are excluded; invalid
    tract coordinates produce NaN. Raises if no usable stations exist. Processes
    tracts in batches to avoid allocating the entire tract-by-station matrix.
    """
    lat, lon, valid = _coordinate_arrays(tracts)
    station_lat, station_lon, station_valid = _coordinate_arrays(stations)
    if "coordinates_valid" in stations:
        station_valid &= stations.coordinates_valid.fillna(False).to_numpy(dtype=bool)
    if "geometry_status" in tracts:
        valid &= tracts.geometry_status.eq("valid").fillna(False).to_numpy(dtype=bool)
    if not station_valid.any():
        raise ValueError("At least one station with valid coordinates is required")
    slat = np.radians(station_lat[station_valid])[None, :]
    slon = np.radians(station_lon[station_valid])[None, :]
    result = np.full(len(tracts), np.nan)
    indices = np.flatnonzero(valid)
    for start in range(0, len(indices), 256):
        positions = indices[start:start + 256]
        tlat = np.radians(lat[positions])[:, None]
        tlon = np.radians(lon[positions])[:, None]
        a = (np.sin((slat - tlat) / 2) ** 2
             + np.cos(tlat) * np.cos(slat) * np.sin((slon - tlon) / 2) ** 2)
        distances = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        result[positions] = distances.min(axis=1)
    return pd.Series(result, index=tracts.index, name="nearest_station_distance_km")


def _validate_bounds(bounds):
    """Copy a previously fitted normalization reference; never refit silently."""
    if not isinstance(bounds, Mapping) or set(bounds) != set(DEFAULT_WEIGHTS):
        raise ValueError("normalization_bounds must contain distance, population, income")
    result = {}
    for key in DEFAULT_WEIGHTS:
        item = bounds[key]
        if not isinstance(item, Mapping) or set(item) != {"lower", "upper"}:
            raise ValueError(f"{key} bounds must contain lower and upper")
        lower, upper = item["lower"], item["upper"]
        if lower is None and upper is None:
            result[key] = {"lower": None, "upper": None}
            continue
        try:
            lower, upper = float(lower), float(upper)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} bounds must be finite ordered numbers or both None") from exc
        if not np.isfinite(lower) or not np.isfinite(upper) or not 0 <= lower <= upper:
            raise ValueError(f"{key} bounds must be finite and 0 <= lower <= upper")
        result[key] = {"lower": lower, "upper": upper}
    return result


def _normalize(values, quantiles, bounds=None):
    """Apply fixed bounds or fit winsorized min-max scaling; preserve NaN."""
    if bounds is None:
        valid = values.dropna()
        if valid.empty:
            bounds = {"lower": None, "upper": None}
        else:
            bounds = dict(zip(("lower", "upper"),
                              (float(valid.quantile(q)) for q in quantiles)))
    lower, upper = bounds["lower"], bounds["upper"]
    if lower is None:
        scores = values * np.nan
    elif upper == lower:
        scores = values.where(values.isna(), 0.5)
    else:
        scores = (values.clip(lower, upper) - lower) / (upper - lower)
    return scores, dict(bounds)


def _weights(weights):
    if weights is None:
        weights = DEFAULT_WEIGHTS
    if not isinstance(weights, Mapping) or set(weights) != set(DEFAULT_WEIGHTS):
        raise ValueError("weights must contain exactly distance, population, income")
    try:
        result = {key: float(weights[key]) for key in DEFAULT_WEIGHTS}
    except (TypeError, ValueError) as exc:
        raise ValueError("weights must be finite nonnegative numbers") from exc
    total = sum(result.values())
    if not all(np.isfinite(v) and v >= 0 for v in result.values()) or not np.isfinite(total) or total <= 0:
        raise ValueError("weights must be finite, nonnegative, and sum to a positive number")
    return {key: value / total for key, value in result.items()}


def score_tracts(data=None, weights=None, *, clip_quantiles=(0.01, 0.99),
                 normalization_bounds=None):
    """Return ranked tract DataFrame from SubwayData (or load_data() by default).

    weights: complete mapping of distance/population/income weights. Nonnegative
    values are normalized to sum to one (percentages and fractions both work).
    clip_quantiles: lower/upper quantiles used separately for distance, population
    and reciprocal positive income; (0, 1) disables outlier clipping.
    normalization_bounds: reuse baseline.attrs["scoring"]["normalization_bounds"]
    for comparable scenario scores. If supplied, these fixed bounds take
    precedence over quantiles; no factors are refitted to scenario observations.

    Missing/nonpositive income gets a neutral component score of 0.5, explicitly
    flagged by income_score_neutral; the original estimate remains unchanged.
    Missing active distance/population leaves priority/rank missing. A zero
    weight disables that component's completeness requirement. Ties share the
    minimum rank; output order breaks ties by GEOID for reproducibility.
    Zero-population tracts retain distances/components but have no residential
    priority score or rank, regardless of weights; normalization is unchanged.
    """
    effective_weights = _weights(weights)
    reference = None if normalization_bounds is None else _validate_bounds(normalization_bounds)
    try:
        lower, upper = clip_quantiles
        lower, upper = float(lower), float(upper)
    except (TypeError, ValueError) as exc:
        raise ValueError("clip_quantiles must contain two numbers, 0 <= lower < upper <= 1") from exc
    if not 0 <= lower < upper <= 1:
        raise ValueError("clip_quantiles must satisfy 0 <= lower < upper <= 1")
    if data is None:
        data = load_data()
    if not isinstance(data, SubwayData):
        raise TypeError("data must be a SubwayData returned by load_data()")
    required = {"GEOID", "latitude", "longitude", "population", "median_household_income"}
    if not required.issubset(data.tracts.columns):
        raise ValueError(f"tracts missing columns: {sorted(required - set(data.tracts.columns))}")
    result = data.tracts.copy().reset_index(drop=True)
    if not result.GEOID.map(lambda x: isinstance(x, str) and len(x) == 11 and x.isascii() and x.isdigit()).all():
        raise ValueError("GEOID must contain 11-digit strings")
    if result.GEOID.duplicated().any():
        raise ValueError("GEOID must be unique")
    result["nearest_station_distance_km"] = nearest_station_distances(result, data.stations)
    population = _numeric(result.population)
    population = population.where(population.ge(0) & population.mod(1).eq(0))
    income = _numeric(result.median_household_income)
    # Reciprocal income is defined only for strictly positive reported estimates.
    income = income.where(income.gt(0))
    factors = {"distance": result.nearest_station_distance_km,
               "population": population, "income": 1.0 / income}
    columns = {"distance": "distance_score", "population": "population_score",
               "income": "income_disadvantage_score"}
    bounds = {}
    for key, values in factors.items():
        result[columns[key]], bounds[key] = _normalize(
            values, (lower, upper), None if reference is None else reference[key])
    result["income_score_neutral"] = income.isna()
    result["income_disadvantage_score"] = result.income_disadvantage_score.fillna(0.5)
    priority = pd.Series(0.0, index=result.index)
    # Keep zero-population rows in normalization and geographic output, but
    # exclude them from residential investment priority regardless of weights.
    eligible = ~population.eq(0)
    for key, weight in effective_weights.items():
        if weight > 0:
            component = result[columns[key]]
            priority += weight * component
            eligible &= component.notna()
    result["score_eligible"] = eligible
    result["priority_score"] = priority.where(eligible)
    result["rank"] = result.priority_score.rank(ascending=False, method="min").astype("Int64")
    result = result.sort_values(["priority_score", "GEOID"], ascending=[False, True],
                                na_position="last").reset_index(drop=True)
    result.attrs["scoring"] = {"weights": effective_weights,
        "clip_quantiles": (lower, upper) if reference is None else None,
        "normalization_source": "fitted" if reference is None else "baseline",
        "normalization_bounds": bounds,
        "distance_method": "haversine_km", "earth_radius_km": EARTH_RADIUS_KM,
        "missing_income_score": 0.5, "constant_component_score": 0.5,
        "income_factor": "reciprocal_positive_income"}
    return result
