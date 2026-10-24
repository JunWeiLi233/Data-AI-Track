"""Greedy station placement on the existing network, validated by the simulator.

Candidate sites are spaced along the reconstructed existing lines (infill
stations) and, when allowed, at tract reference points off the network (which
would need new track). Each step adds the site with the largest marginal gain:

- access: residents newly within a 10-minute walk (WALK_KM) of a station,
  weighted by the baseline scoring priority (distance, population, income);
- eff: population-weighted reduction in straight-line distance to a station.

Distances are straight-line haversine kilometers, walking minutes assume
WALK_KM_PER_MIN, and budgets cap the distance from existing track. These are
planning proxies, not ridership, travel-time or construction-cost models.
"""
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
import shapely

from .data_loader import load_data
from .network import build_lines, haversine_km, line_points
from .scoring import score_tracts
from .simulator import simulate_expansion

GOALS = ("access", "eff")
MAX_STATIONS = 100
WALK_KM = 0.8
WALK_KM_PER_MIN = 0.08
DEFAULT_SPACING_KM = 0.5
# Maximum straight-line distance from existing track for off-network sites.
BUDGET_OFFLINE_KM = {"Low": 0.0, "Medium": 1.5, "High": 3.0}
MIN_IMPROVEMENT_KM = 0.1


@dataclass
class Context:
    data: object
    stations: pd.DataFrame
    lines: list
    baseline: pd.DataFrame
    candidates: pd.DataFrame
    distances: np.ndarray        # candidate x tract, km
    tract_population: np.ndarray
    tract_priority: np.ndarray
    tract_distance: np.ndarray   # baseline nearest-station distance, km


@lru_cache(maxsize=1)
def context():
    data = load_data()
    stations, lines = build_lines(data.stations, data.entrances)
    baseline = score_tracts(data)
    tracts = baseline[baseline.score_eligible & baseline.nearest_station_distance_km.notna()].reset_index(drop=True)
    tlat = tracts.latitude.to_numpy("float64")
    tlon = tracts.longitude.to_numpy("float64")

    on_line = line_points(stations, lines)
    # Drop sites on river and bay crossings: a station needs land (a tract polygon) under it.
    land = shapely.STRtree([g for g, ok in zip(data.tracts.geometry, data.tracts.geometry_status) if ok == "valid"])
    points = shapely.points(on_line.longitude.to_numpy(), on_line.latitude.to_numpy())
    on_land = np.zeros(len(on_line), dtype=bool)
    on_land[land.query(points, predicate="within")[0]] = True
    on_line = on_line[on_land].reset_index(drop=True)
    on_line["offline_km"] = 0.0
    off_line = pd.DataFrame({"latitude": tlat, "longitude": tlon, "line": None,
                             "routes": None, "between": None})
    near_track = haversine_km(tlat[:, None], tlon[:, None],
                              on_line.latitude.to_numpy()[None, :], on_line.longitude.to_numpy()[None, :])
    off_line["offline_km"] = near_track.min(axis=1)
    off_line["line"] = on_line.line.to_numpy()[near_track.argmin(axis=1)]
    candidates = pd.concat([on_line, off_line], ignore_index=True)

    clat = candidates.latitude.to_numpy("float64")
    clon = candidates.longitude.to_numpy("float64")
    distances = haversine_km(clat[:, None], clon[:, None], tlat[None, :], tlon[None, :]).astype("float32")
    slat = stations.latitude.to_numpy("float64")
    slon = stations.longitude.to_numpy("float64")
    to_station = haversine_km(clat[:, None], clon[:, None], slat[None, :], slon[None, :])
    candidates["nearest_station_km"] = to_station.min(axis=1)
    candidates["nearest_station"] = stations.stop_name.to_numpy()[to_station.argmin(axis=1)]
    nearest_tract = distances.argmin(axis=1)
    candidates["neighborhood"] = tracts.NTAName.to_numpy()[nearest_tract]
    candidates["borough"] = tracts.BoroName.to_numpy()[nearest_tract]
    return Context(data, stations, lines, baseline, candidates, distances,
                   tracts.population.to_numpy("float64"), tracts.priority_score.to_numpy("float64"),
                   tracts.nearest_station_distance_km.to_numpy("float64"))


def _gains(goal, ctx, current):
    if goal == "access":
        newly = (ctx.distances <= WALK_KM) & (current > WALK_KM)[None, :]
        return newly @ (ctx.tract_population * ctx.tract_priority)
    reduction = np.clip(current[None, :] - ctx.distances, 0, None)
    return reduction @ ctx.tract_population


def optimize(goal="access", n=5, *, along_lines=True, min_spacing_km=None, budget=None):
    """Return ranked station sites with per-station and network-wide impact."""
    if goal not in GOALS:
        raise ValueError(f"goal must be one of {GOALS}")
    n = int(n)
    if not 1 <= n <= MAX_STATIONS:
        raise ValueError(f"n must be between 1 and {MAX_STATIONS}")
    spacing = DEFAULT_SPACING_KM if min_spacing_km is None else float(min_spacing_km)
    if not np.isfinite(spacing) or spacing < 0:
        raise ValueError("min_spacing_km must be finite and nonnegative")
    if budget is not None and budget not in BUDGET_OFFLINE_KM:
        raise ValueError(f"budget must be one of {list(BUDGET_OFFLINE_KM)}")

    ctx = context()
    cand = ctx.candidates
    max_offline = 0.0 if along_lines else BUDGET_OFFLINE_KM.get(budget, np.inf)
    allowed = (cand.offline_km.to_numpy() <= max_offline) & (cand.nearest_station_km.to_numpy() >= spacing)
    current = ctx.tract_distance.copy()
    clat = cand.latitude.to_numpy("float64")
    clon = cand.longitude.to_numpy("float64")
    picks = []
    for _ in range(n):
        gains = np.where(allowed, _gains(goal, ctx, current), -np.inf)
        best = int(np.argmax(gains))
        if not np.isfinite(gains[best]) or gains[best] <= 0:
            break
        d = ctx.distances[best]
        improved = (current - d) > 0
        newly = (d <= WALK_KM) & (current > WALK_KM)
        pop = ctx.tract_population
        benefit_pop = pop[improved].sum()
        picks.append({
            "candidate": best,
            "residents": int(pop[newly].sum()),
            "residents_benefiting": int(benefit_pop),
            "minutes_saved": round(float((pop[improved] * (current - d)[improved]).sum() / benefit_pop)
                                   / WALK_KM_PER_MIN, 1) if benefit_pop else 0.0,
            "score": round(100 * float((pop[improved] * ctx.tract_priority[improved]).sum() / benefit_pop))
                     if benefit_pop else 0,
        })
        current = np.minimum(current, d)
        allowed &= haversine_km(clat[best], clon[best], clat, clon) >= spacing

    stations = []
    for rank, pick in enumerate(picks, 1):
        site = cand.iloc[pick["candidate"]]
        between = site["between"] if isinstance(site["between"], tuple) else None
        if between:
            near = f"{site.line} line between {between[0]} and {between[1]}"
        else:
            near = f"New track {site.offline_km:.1f} km from the {site.line} line, near {site.nearest_station}"
        stations.append({
            "rank": rank, "name": site.neighborhood, "borough": site.borough, "near": near,
            "latitude": round(float(site.latitude), 6), "longitude": round(float(site.longitude), 6),
            "on_existing_line": between is not None, "line": site.line,
            "routes": list(site.routes) if isinstance(site.routes, list) else [],
            "offline_km": round(float(site.offline_km), 2),
            **{k: v for k, v in pick.items() if k != "candidate"},
        })
    return {"goal": goal, "stations": stations, "impact": _impact(ctx, stations)}


def _impact(ctx, stations):
    """Network-wide effect, recomputed independently with the scenario simulator."""
    result = simulate_expansion([{"latitude": s["latitude"], "longitude": s["longitude"]} for s in stations],
                                ctx.data, min_distance_improvement_km=MIN_IMPROVEMENT_KM)
    tracts = result.tracts[result.tracts.residential_benefit_eligible]
    pop = pd.to_numeric(tracts.population).astype("float64")
    before, after = tracts.baseline_nearest_station_distance_km, tracts.proposed_nearest_station_distance_km
    total = float(pop.sum())
    covered_before = float(pop[before <= WALK_KM].sum())
    covered_after = float(pop[after <= WALK_KM].sum())
    walk = lambda d: float((pop * d).sum() / total / WALK_KM_PER_MIN)
    boroughs = list(dict.fromkeys(s["borough"] for s in stations))
    return {
        "population_evaluated": int(total),
        "covered_before": int(covered_before),
        "covered_after": int(covered_after),
        "coverage_after_pct": round(100 * covered_after / total, 1),
        "residents_added": int(covered_after - covered_before),
        "walk_minutes_before": round(walk(before), 1),
        "walk_minutes_after": round(walk(after), 1),
        "population_potentially_benefiting": result.summary["population_potentially_benefiting"],
        "boroughs": boroughs,
        "on_existing_lines": sum(s["on_existing_line"] for s in stations),
    }
