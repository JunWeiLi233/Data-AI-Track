"""JSON payloads for the frontend, kept free of HTTP so they can be unit tested."""
from functools import lru_cache

import pandas as pd
import shapely
from pyproj import Transformer
from shapely.geometry import mapping
from shapely.ops import transform

from . import clustering, network, optimizer, planning_model, supervised

SQUARE_FEET_TO_KM2 = (1200 / 3937) ** 2 / 1e6  # US survey feet
# Residential tracts beyond a 15-minute straight-line walk of any station.
DESERT_KM = 1.2
BOOLEAN = {"1": True, "true": True, "0": False, "false": False}
# Plain-language reasons for planning_model.main_reason; each says why the
# feature raises a tract's chance of being underserved.
PLANNING_REASONS = {
    "job_share_over_45": "Many residents' jobs are 45+ minutes away by subway",
    "log_station_distance": "Close to a station, so residents ride to distant jobs rather than drive",
    "log_density": "Dense neighborhood, with fewer car commuters",
}


def recommendations(goal="access", n=5, along="1", spacing=None, budget=None):
    """Optimizer output for query-string parameters (all values may be strings)."""
    if str(along).lower() not in BOOLEAN:
        raise ValueError("along must be 1/0 or true/false")
    try:
        spacing = None if spacing in (None, "") else float(spacing)
    except ValueError as exc:
        raise ValueError("spacing must be a number of kilometers") from exc
    return optimizer.optimize(goal, n, along_lines=BOOLEAN[str(along).lower()],
                              min_spacing_km=spacing, budget=budget or None)


def _data():
    return optimizer.context().data


@lru_cache(maxsize=1)
def existing_lines():
    ctx = optimizer.context()
    return network.lines_geojson(ctx.stations, ctx.lines)


@lru_cache(maxsize=1)
def transit_deserts():
    """Merged outline of residential tracts beyond DESERT_KM of every station."""
    baseline = optimizer.context().baseline
    far = baseline[(baseline.population > 0) & (baseline.nearest_station_distance_km > DESERT_KM)
                   & (baseline.geometry_status == "valid")]
    outline = shapely.union_all(list(far.geometry)).simplify(0.0002, preserve_topology=True)
    return {"type": "FeatureCollection", "threshold_km": DESERT_KM,
            "population": int(far.population.sum()), "tracts": len(far),
            "features": [{"type": "Feature", "properties": {},
                          "geometry": mapping(_rounded(outline))}]}


@lru_cache(maxsize=1)
def data_summary():
    """Headline counts from the cleaned source data."""
    data = _data()
    population = data.tracts.population.dropna()
    return {
        "physical_station_locations": data.report["physical_station_locations"],
        "entrances": data.report["entrance_rows"],
        "tracts": data.report["tract_rows"],
        "population": int(population.sum()),
        "tracts_missing_population": data.report["population_missing_on_tracts"],
    }


@lru_cache(maxsize=1)
def tract_clusters():
    """Neighborhood types from k-means; tracts maps GEOID to cluster for joining with /api/tracts."""
    clustered = clustering.cluster_tracts(optimizer.context().baseline)
    info = clustered.attrs["clustering"]
    summary = clustering.cluster_summary(clustered)
    eligible = clustered[clustered.cluster.notna()]
    return {
        "n_clusters": info["n_clusters"],
        "silhouette": round(info["silhouette"], 3),
        "excluded_tracts": info["excluded_tracts"],
        "clusters": [{
            "cluster": int(row.cluster), "name": row.cluster_name, "tracts": int(row.tracts),
            "population": int(row.population),
            "median_station_distance_km": round(float(row.median_station_distance_km), 3),
            "median_density_km2": round(float(row.median_density_km2)),
            "median_household_income": round(float(row.median_household_income)),
            "mean_priority_score": round(float(row.mean_priority_score), 3),
        } for row in summary.itertuples()],
        "tracts": dict(zip(eligible.GEOID, eligible.cluster.astype(int).tolist())),
    }


@lru_cache(maxsize=1)
def existing_stations():
    stations = _data().stations
    stations = stations[stations.coordinates_valid]
    return {"stations": [
        {"name": " / ".join(names), "latitude": float(lat), "longitude": float(lon)}
        for names, lat, lon in zip(stations.station_names, stations.latitude, stations.longitude)
    ]}


def _rounded(geometry, digits=5):
    return shapely.set_precision(geometry, 10 ** -digits)


@lru_cache(maxsize=1)
def tract_density():
    """Tract polygons as GeoJSON with residents per km² and quartile class breaks.

    Area is measured in EPSG:2263 (US feet); geometry is simplified only for display.
    """
    to_feet = Transformer.from_crs("EPSG:4326", "EPSG:2263", always_xy=True).transform
    tracts = _data().tracts
    features, densities = [], []
    for geoid, geometry, status, population in zip(
            tracts.GEOID, tracts.geometry, tracts.geometry_status, tracts.population):
        if status != "valid":
            continue
        area_km2 = transform(to_feet, geometry).area * SQUARE_FEET_TO_KM2
        density = None
        if pd.notna(population) and area_km2 > 0:
            density = round(float(population) / area_km2)
            if density > 0:  # parks and airports would skew the class breaks
                densities.append(density)
        shape = _rounded(geometry.simplify(0.00005, preserve_topology=True))
        features.append({
            "type": "Feature",
            "properties": {
                "geoid": geoid,
                "population": None if pd.isna(population) else int(population),
                "density": density,
            },
            "geometry": mapping(shape),
        })
    breaks = [round(float(q)) for q in pd.Series(densities).quantile([0.25, 0.5, 0.75])]
    return {"type": "FeatureCollection", "breaks": breaks, "features": features}


def _round(value, digits=3):
    return None if pd.isna(value) else round(float(value), digits)


@lru_cache(maxsize=1)
def planning_predictions():
    """Planning model fit on labeled tracts, with a prediction for every tract that has features.

    tracts maps GEOID to probability, its 90% interval, the main reason key (None
    when no feature raises the score above an average tract) and, where labeled,
    the observed share of 60+ minute commutes and the gold label.
    """
    frame = supervised.feature_frame(_data())
    labeled = supervised.training_frame(frame=frame)
    checks = planning_model.validate(labeled)
    model, coefficients, boot = planning_model.train_planning_model(labeled)
    usable = frame[supervised.has_features(frame)]
    predictions = planning_model.predict(model, boot, usable)
    own = predictions[[f"contribution_{f}" for f in planning_model.FEATURES[:planning_model.TRACT_FEATURES]]]
    raises = own.max(axis=1) > 0
    tracts = {}
    for geoid, row, reason, share, label in zip(
            predictions.GEOID, predictions.itertuples(), predictions.main_reason.where(raises),
            usable.long_commute_share, usable.underserved):
        tracts[geoid] = {
            "probability": _round(row.probability), "low": _round(row.low_90), "high": _round(row.high_90),
            "reason": None if pd.isna(reason) else reason,
            "observed_long_commute_share": _round(share), "underserved": None if pd.isna(label) else int(label),
        }
    by_borough = checks["by_borough"]
    return {
        "model": {
            "features": list(planning_model.FEATURES),
            "labeled_tracts": len(labeled),
            "roc_auc": _round(checks["roc_auc"]), "roc_auc_confident": _round(checks["roc_auc_confident"]),
            "brier": _round(checks["brier"], 4), "brier_prevalence_only": _round(checks["brier_prevalence_only"], 4),
            "coefficients": [{"feature": name, "coefficient": _round(row.coefficient),
                              "low_95": _round(row.low_95), "high_95": _round(row.high_95)}
                             for name, row in coefficients.iterrows()],
            "boroughs": [{"borough": name, "predicted": _round(row.predicted), "observed": _round(row.observed),
                          "roc_auc_within": _round(row.roc_auc_within)} for name, row in by_borough.iterrows()],
        },
        "reasons": PLANNING_REASONS,
        "tracts": tracts,
    }
