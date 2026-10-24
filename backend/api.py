"""JSON payloads for the frontend, kept free of HTTP so they can be unit tested."""
from functools import lru_cache

import pandas as pd
import shapely
from pyproj import Transformer
from shapely.geometry import mapping
from shapely.ops import transform

from .data_loader import DEFAULT_DATA_DIR

SQUARE_FEET_TO_KM2 = (1200 / 3937) ** 2 / 1e6  # US survey feet
GOALS = ("access", "eff")
MAX_STATIONS = 5

# Placeholder candidates until backend/scoring.py produces modeled results.
SAMPLE_CANDIDATES = [
    {"id": "eq", "name": "Eastern Queens", "near": "Near 225th St & Springfield Blvd",
     "latitude": 40.72303, "longitude": -73.74421, "residents": 18500, "minutes_saved": 12, "score": 87},
    {"id": "cq", "name": "Central Queens", "near": "Near Union Tpke & 164th St",
     "latitude": 40.71800, "longitude": -73.80021, "residents": 14200, "minutes_saved": 10, "score": 78},
    {"id": "sb", "name": "Southern Brooklyn", "near": "Near Flatbush Ave & Ave D",
     "latitude": 40.64001, "longitude": -73.95622, "residents": 12800, "minutes_saved": 9, "score": 74},
    {"id": "eb", "name": "Eastern Brooklyn", "near": "Near Livonia Ave & Van Siclen Ave",
     "latitude": 40.66502, "longitude": -73.89023, "residents": 11600, "minutes_saved": 8, "score": 72},
    {"id": "sq", "name": "Southeast Queens", "near": "Near Cross Bay Blvd & 157th Ave",
     "latitude": 40.65803, "longitude": -73.84023, "residents": 10300, "minutes_saved": 7, "score": 69},
]


def recommendations(goal="access", n=MAX_STATIONS):
    if goal not in GOALS:
        raise ValueError(f"goal must be one of {GOALS}")
    n = int(n)
    if not 1 <= n <= MAX_STATIONS:
        raise ValueError(f"n must be between 1 and {MAX_STATIONS}")
    if goal == "access":
        key = lambda c: c["score"]
    else:
        key = lambda c: c["minutes_saved"] * c["residents"]
    ranked = sorted(SAMPLE_CANDIDATES, key=key, reverse=True)[:n]
    stations = [{**candidate, "rank": rank} for rank, candidate in enumerate(ranked, 1)]
    return {"sample": True, "goal": goal, "stations": stations}


@lru_cache(maxsize=1)
def _data(data_dir=DEFAULT_DATA_DIR):
    from .data_loader import load_data

    return load_data(data_dir)


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
