"""Government planning scenarios using straight-line geographic accessibility.

Station additions, not construction feasibility, ridership or travel-time forecasts.
All scenarios reuse the same baseline scoring bounds and effective weights.
"""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Dict

import numpy as np
import pandas as pd

from .data_loader import SubwayData, load_data
from .scoring import score_tracts

SCORE_COLUMNS = ["nearest_station_distance_km", "distance_score", "population_score",
                 "income_disadvantage_score", "priority_score", "rank", "score_eligible"]


@dataclass
class ScenarioResult:
    name: str
    proposed_stations: pd.DataFrame
    tracts: pd.DataFrame
    summary: dict
    scoring_metadata: dict


@dataclass
class ScenarioComparison:
    baseline: pd.DataFrame
    scenarios: Dict[str, ScenarioResult]
    summary: pd.DataFrame
    metadata: dict


def _proposals(stations):
    """Validate explicit latitude/longitude records; preserve optional map labels."""
    if isinstance(stations, pd.DataFrame):
        frame = stations.copy(deep=True)
    elif isinstance(stations, Mapping):
        frame = pd.DataFrame([stations])
    else:
        try:
            records = list(stations)
        except TypeError as exc:
            raise ValueError("proposed stations must be latitude/longitude records or a DataFrame") from exc
        if any(not isinstance(row, Mapping) for row in records):
            raise ValueError("each proposed station must contain latitude and longitude keys")
        frame = pd.DataFrame(records) if records else pd.DataFrame(columns=["latitude", "longitude"])
    if not {"latitude", "longitude"}.issubset(frame.columns) or not frame.columns.is_unique:
        raise ValueError("proposed stations require unique latitude and longitude columns")
    for column, limit in (("latitude", 90), ("longitude", 180)):
        # Accept numeric text from frontend forms, but never interpret booleans as coordinates.
        if frame[column].map(lambda value: isinstance(value, (bool, np.bool_))).any():
            raise ValueError(f"{column} must be a finite numeric coordinate")
        values = pd.to_numeric(frame[column], errors="coerce").astype("float64")
        if not (np.isfinite(values) & values.between(-limit, limit)).all():
            raise ValueError(f"{column} must be finite and between {-limit} and {limit}")
        frame[column] = values
    return frame.drop_duplicates(["latitude", "longitude"]).reset_index(drop=True)


def _threshold(value):
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("min_distance_improvement_km must be finite and nonnegative") from exc
    if not np.isfinite(threshold) or threshold < 0:
        raise ValueError("min_distance_improvement_km must be finite and nonnegative")
    return threshold


def _compare_tracts(baseline, proposed, threshold):
    before = baseline.rename(columns={key: "baseline_" + key for key in SCORE_COLUMNS})
    after = proposed[["GEOID"] + SCORE_COLUMNS].rename(
        columns={key: "proposed_" + key for key in SCORE_COLUMNS})
    # Align by GEOID: the proposed ranking can change tract row order.
    tracts = before.merge(after, on="GEOID", how="left", validate="one_to_one", sort=False)
    old = tracts.baseline_nearest_station_distance_km
    new = tracts.proposed_nearest_station_distance_km
    reduction = old - new
    if (reduction.dropna() < 0).any():
        raise RuntimeError("Station additions unexpectedly increased nearest-station distance")
    tracts["distance_improvement_km"] = reduction
    tracts["priority_score_reduction"] = tracts.baseline_priority_score - tracts.proposed_priority_score
    pop = pd.to_numeric(tracts.population, errors="coerce").astype("float64")
    known_population = np.isfinite(pop) & pop.ge(0) & pop.mod(1).eq(0)
    residential = known_population & pop.gt(0)
    eligible = residential & old.notna() & new.notna()
    # Strictly positive reduction is required even when the threshold is zero.
    benefited = eligible & reduction.gt(0) & reduction.ge(threshold)
    tracts["residential_benefit_eligible"] = eligible
    tracts["benefited"] = benefited
    evaluated_population = int(pop[eligible].sum())
    weighted = float((pop[eligible] * reduction[eligible]).sum() / evaluated_population) if evaluated_population else None
    summary = {
        "min_distance_improvement_km": threshold,
        "total_tracts": len(tracts),
        "residential_tracts_evaluated": int(eligible.sum()),
        "residential_tracts_benefiting": int(benefited.sum()),
        "population_evaluated": evaluated_population,
        "population_potentially_benefiting": int(pop[benefited].sum()),
        "population_weighted_average_distance_reduction_km": weighted,
        "zero_population_tracts": int((known_population & pop.eq(0)).sum()),
        "tracts_with_unknown_or_invalid_population": int((~known_population).sum()),
        "residential_tracts_without_valid_distances": int((residential & ~eligible).sum()),
    }
    return tracts, summary


def compare_scenarios(scenarios, data=None, *, weights=None,
                      min_distance_improvement_km=0.1, clip_quantiles=(0.01, 0.99)):
    """Compare independent additions to one baseline (not cumulative scenarios).

    scenarios: nonempty mapping of unique scenario names to proposed station
    DataFrames or records with latitude/longitude keys. Empty station lists are
    permitted as a no-change reference. Exact duplicate proposals are collapsed.
    data: SubwayData from load_data(), or None to load the default datasets.
    weights/clip_quantiles: forwarded to the existing scoring model. Fit once on
    baseline, then reuse its fixed normalization bounds and effective weights.

    Benefit: positive integer population, valid old/new distances, and a strictly
    positive reduction >= min_distance_improvement_km (default 0.1 km). Entire
    qualifying tract populations are counted once per scenario, not once per
    station; these are potentially benefiting residents, not predicted users.
    Weighted mean reduction uses all evaluable residential population, including
    non-beneficiaries; thresholds affect counts only. No evaluable population
    yields None for that mean. Zero/unknown population rows remain on the map.
    """
    if not isinstance(scenarios, Mapping) or not scenarios:
        raise ValueError("scenarios must be a nonempty mapping of names to proposed stations")
    if any(not isinstance(name, str) or not name.strip() for name in scenarios):
        raise ValueError("scenario names must be nonempty strings")
    threshold = _threshold(min_distance_improvement_km)
    # Validate every proposal before loading/scoring so invalid scenarios fail together.
    proposals = {name: _proposals(stations) for name, stations in scenarios.items()}
    if data is None:
        data = load_data()
    baseline = score_tracts(data, weights, clip_quantiles=clip_quantiles)
    scoring = baseline.attrs["scoring"]
    results = {}
    for name, proposal in proposals.items():
        # Preserve the existing station inventory, including Staten Island Railway.
        # Only scoring coordinates are needed for new rows; override input flags.
        additions = proposal[["latitude", "longitude"]].copy()
        additions["coordinates_valid"] = True
        stations = pd.concat([data.stations, additions], ignore_index=True)
        proposed_data = SubwayData(stations, data.entrances, data.tracts, data.report)
        proposed = score_tracts(proposed_data, weights,
                                normalization_bounds=scoring["normalization_bounds"])
        tracts, summary = _compare_tracts(baseline, proposed, threshold)
        summary = {"scenario": name, "proposed_station_locations": len(proposal), **summary}
        results[name] = ScenarioResult(name, proposal.copy(deep=True), tracts, summary,
                                       deepcopy(proposed.attrs["scoring"]))
    return ScenarioComparison(
        baseline=baseline,
        scenarios=results,
        summary=pd.DataFrame([result.summary for result in results.values()]),
        metadata={"baseline_scoring": deepcopy(scoring),
                  "distance_proxy": "straight-line haversine kilometers",
                  "min_distance_improvement_km": threshold,
                  "weighted_mean_denominator": "all evaluable positive-population tracts",
                  "benefit_definition": "positive reduction >= threshold; full qualifying tract population counted once",
                  "scenarios_are_independent": True},
    )


def simulate_expansion(proposed_stations, data=None, *, scenario_name="proposal",
                       weights=None, min_distance_improvement_km=0.1,
                       clip_quantiles=(0.01, 0.99)):
    """Evaluate one proposal; see compare_scenarios for result/benefit definitions."""
    return compare_scenarios(
        {scenario_name: proposed_stations}, data, weights=weights,
        min_distance_improvement_km=min_distance_improvement_km,
        clip_quantiles=clip_quantiles,
    ).scenarios[scenario_name]
