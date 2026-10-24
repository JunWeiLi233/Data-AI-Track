"""JSON payloads for the frontend, kept free of HTTP so they can be unit tested."""
from functools import lru_cache

import pandas as pd
import shapely
from pyproj import Transformer
from shapely.geometry import mapping
from shapely.ops import transform

from . import network, optimizer

SQUARE_FEET_TO_KM2 = (1200 / 3937) ** 2 / 1e6  # US survey feet
# Residential tracts beyond a 15-minute straight-line walk of any station.
DESERT_KM = 1.2
BOOLEAN = {"1": True, "true": True, "0": False, "false": False}


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
