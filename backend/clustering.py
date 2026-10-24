"""Unsupervised neighborhood types: k-means on transit access, density and income.

There are no ground-truth "underserved" labels, so this does not predict one.
It groups residential tracts that look alike on three features, then describes
each group in words and reports how Emily's priority score falls across them.

Features (log-transformed, then standardized so each counts equally):
- nearest_station_distance_km: straight-line haversine km, from score_tracts;
- population_density_km2: residents per km², area measured in EPSG:2263;
- median_household_income: ACS estimate (top-coded values kept at 250,000).

Tracts with zero or missing population, missing or nonpositive income, or
invalid geometry are excluded and get no cluster; nothing is imputed.

Run from the repository root: python -m backend.clustering [--clusters 4]
"""
import argparse

import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from .data_loader import load_data
from .scoring import score_tracts

FEATURES = ("nearest_station_distance_km", "population_density_km2", "median_household_income")
SQUARE_FEET_TO_KM2 = (1200 / 3937) ** 2 / 1e6  # US survey feet
DEFAULT_CLUSTERS = 4
# Standardized centroid thresholds for the words used in cluster names.
NOTABLE, STRONG = 0.5, 1.5


def tract_features(scored):
    """Add area, density and the eligibility flag to a score_tracts DataFrame."""
    required = {"GEOID", "geometry", "geometry_status", "population",
                "median_household_income", "nearest_station_distance_km"}
    if not required.issubset(scored.columns):
        raise ValueError(f"scored tracts missing columns: {sorted(required - set(scored.columns))}")
    result = scored.copy()
    valid = result.geometry_status.eq("valid").fillna(False).to_numpy(dtype=bool)
    to_feet = Transformer.from_crs("EPSG:4326", "EPSG:2263", always_xy=True).transform
    area = np.full(len(result), np.nan)
    area[valid] = shapely.area(shapely.transform(
        np.asarray(result.geometry[valid], dtype=object), to_feet, interleaved=False)) * SQUARE_FEET_TO_KM2
    result["area_km2"] = area
    population = pd.to_numeric(result.population, errors="coerce").astype("float64")
    income = pd.to_numeric(result.median_household_income, errors="coerce").astype("float64")
    result["population_density_km2"] = population / result.area_km2.where(result.area_km2 > 0)
    result["cluster_eligible"] = (valid & population.gt(0) & income.gt(0)
                                  & result.nearest_station_distance_km.notna()
                                  & result.population_density_km2.gt(0)).to_numpy(dtype=bool)
    return result


def _matrix(frame):
    return np.column_stack([np.log1p(frame.nearest_station_distance_km.astype("float64")),
                            np.log(frame.population_density_km2.astype("float64")),
                            np.log(frame.median_household_income.astype("float64"))])


def _describe(center):
    """Words for a standardized centroid (distance, density, income)."""
    distance, density, income = center
    words = []
    if distance > STRONG:
        words.append("very far from the subway")
    elif distance > NOTABLE:
        words.append("far from the subway")
    elif distance < -NOTABLE:
        words.append("near the subway")
    if density > NOTABLE:
        words.append("dense")
    elif density < -STRONG:
        words.append("very spread out")
    elif density < -NOTABLE:
        words.append("spread out")
    if income < -NOTABLE:
        words.append("lower income")
    elif income > NOTABLE:
        words.append("higher income")
    return ", ".join(words).capitalize() or "Typical"


def cluster_tracts(scored=None, n_clusters=DEFAULT_CLUSTERS, *, random_state=0):
    """Return scored tracts with cluster (Int64, 0 = closest to the subway) and cluster_name.

    scored defaults to score_tracts(load_data()). Ineligible tracts keep a
    missing cluster. attrs["clustering"] records the fit for display.
    """
    if not isinstance(n_clusters, int) or isinstance(n_clusters, bool) or n_clusters < 2:
        raise ValueError("n_clusters must be an integer of at least 2")
    if scored is None:
        scored = score_tracts(load_data())
    result = tract_features(scored)
    # Sort by GEOID so the fit does not depend on the caller's row order.
    eligible = result[result.cluster_eligible].sort_values("GEOID")
    if len(eligible) <= n_clusters:
        raise ValueError(f"need more than {n_clusters} eligible tracts, found {len(eligible)}")
    scaler = StandardScaler()
    z = scaler.fit_transform(_matrix(eligible))
    model = KMeans(n_clusters=n_clusters, n_init=10, random_state=random_state).fit(z)
    # Relabel so cluster numbers are stable and readable: ordered by station distance.
    order = np.argsort(model.cluster_centers_[:, 0], kind="stable")
    relabel = np.empty(n_clusters, dtype=int)
    relabel[order] = np.arange(n_clusters)
    centers = model.cluster_centers_[order]
    names = [_describe(center) for center in centers]
    for i, name in enumerate(names):
        if names.count(name) > 1:
            names[i] = f"{name} (group {i + 1})"
    labels = pd.Series(pd.NA, index=result.index, dtype="Int64")
    labels[eligible.index] = relabel[model.labels_]
    result["cluster"] = labels
    result["cluster_name"] = labels.map(dict(enumerate(names))).astype("string")
    result.attrs["clustering"] = {
        "method": "kmeans", "n_clusters": n_clusters, "random_state": random_state,
        "features": list(FEATURES), "transform": "log1p(distance), log(density), log(income); standardized",
        "silhouette": float(silhouette_score(z, model.labels_)),
        "eligible_tracts": int(len(eligible)), "excluded_tracts": int(len(result) - len(eligible)),
        "names": names,
        # Centroids back in original units (geometric-mean style, from log space).
        "centers": [{"nearest_station_distance_km": float(np.expm1(d)),
                     "population_density_km2": float(np.exp(p)),
                     "median_household_income": float(np.exp(i))}
                    for d, p, i in scaler.inverse_transform(centers)],
    }
    return result


def cluster_summary(clustered):
    """One row per cluster: size, residents, medians and mean priority score."""
    eligible = clustered[clustered.cluster.notna()].copy()
    eligible["population"] = pd.to_numeric(eligible.population, errors="coerce").astype("float64")
    aggregations = {
        "cluster_name": ("cluster_name", "first"),
        "tracts": ("GEOID", "size"),
        "population": ("population", "sum"),
        "median_station_distance_km": ("nearest_station_distance_km", "median"),
        "median_density_km2": ("population_density_km2", "median"),
        "median_household_income": ("median_household_income", "median"),
    }
    if "priority_score" in eligible:
        aggregations["mean_priority_score"] = ("priority_score", "mean")
    summary = eligible.groupby("cluster").agg(**aggregations).reset_index()
    summary["population"] = summary.population.astype("int64")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clusters", type=int, default=DEFAULT_CLUSTERS)
    args = parser.parse_args()
    clustered = cluster_tracts(n_clusters=args.clusters)
    info = clustered.attrs["clustering"]
    print(f"{info['n_clusters']} clusters over {info['eligible_tracts']} tracts "
          f"({info['excluded_tracts']} excluded), silhouette {info['silhouette']:.3f}")
    with pd.option_context("display.width", 200, "display.max_columns", None):
        print(cluster_summary(clustered).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
