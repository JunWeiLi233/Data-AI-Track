"""Read-only loaders for the supplied NYC subway and Census CSV exports."""
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from shapely import wkt
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import transform
from pyproj import Transformer

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"


@dataclass
class SubwayData:
    stations: pd.DataFrame
    entrances: pd.DataFrame
    tracts: pd.DataFrame
    report: dict


def _read(path, required):
    frame = pd.read_csv(path, dtype="string", keep_default_na=False)
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"{Path(path).name}: missing columns {sorted(missing)}")
    return frame


def _coordinates(frame, lat, lon):
    frame = frame.copy()
    frame["latitude"] = pd.to_numeric(frame[lat], errors="coerce")
    frame["longitude"] = pd.to_numeric(frame[lon], errors="coerce")
    frame["coordinates_valid"] = (
        frame.latitude.between(-90, 90) & frame.longitude.between(-180, 180)
    ).fillna(False)
    # Preserve invalid source text but never expose invalid coordinates for distance use.
    frame.loc[~frame.coordinates_valid, ["latitude", "longitude"]] = float("nan")
    return frame


def _unique_geoid(frame, source):
    if not frame.GEOID.str.fullmatch(r"\d{11}").all():
        raise ValueError(f"{source}: expected 11-digit tract GEOIDs")
    if frame.GEOID.duplicated().any():
        raise ValueError(f"{source}: duplicate tract GEOIDs would multiply joined rows")


def load_stations(path=DEFAULT_DATA_DIR / "stops.csv"):
    """Keep GTFS parents, then deduplicate exact coordinates (no fuzzy merging).

    Invalid parent coordinates remain flagged; child platform rows are never
    counted as additional stations. Original parent IDs remain in station_ids.
    """
    frame = _read(path, ["stop_id", "stop_name", "stop_lat", "stop_lon",
                         "location_type", "parent_station"])
    if frame.stop_id.duplicated().any():
        raise ValueError("stops.csv: duplicate stop_id")
    parents = _coordinates(frame[frame.location_type == "1"], "stop_lat", "stop_lon")
    if parents.empty:
        raise ValueError("stops.csv: no parent stations (location_type=1)")
    valid = parents[parents.coordinates_valid].copy()
    grouped = valid.groupby(["latitude", "longitude"], sort=False)
    result = grouped.first().reset_index()
    result["station_ids"] = grouped.stop_id.agg(tuple).to_list()
    result["station_names"] = grouped.stop_name.agg(lambda x: tuple(dict.fromkeys(x))).to_list()
    invalid = parents[~parents.coordinates_valid].copy()
    invalid["station_ids"] = invalid.stop_id.map(lambda x: (x,))
    invalid["station_names"] = invalid.stop_name.map(lambda x: (x,))
    result = pd.concat([result, invalid], ignore_index=True)
    result.attrs["source_rows"] = len(frame)
    result.attrs["parent_rows"] = len(parents)
    result.attrs["orphan_platform_rows"] = int(((frame.location_type == "0") &
        ~frame.parent_station.isin(parents.stop_id)).sum())
    return result


def load_entrances(path=DEFAULT_DATA_DIR / "entrances.csv"):
    """Retain entrance records separately; entrances are not extra stations."""
    frame = _read(path, ["Station Name", "Entrance Latitude", "Entrance Longitude", "Entry"])
    return _coordinates(frame, "Entrance Latitude", "Entrance Longitude")


def load_tracts(path=DEFAULT_DATA_DIR / "census_tracts.csv"):
    """Parse lon/lat WKT; derive interior points in NYC projected CRS EPSG:2263.

    Invalid geometries are flagged, not repaired or replaced. Representative
    points are geometric proxies, not population-weighted residential locations.
    """
    frame = _read(path, ["GEOID", "the_geom"])
    _unique_geoid(frame, "census_tracts.csv")
    forward = Transformer.from_crs("EPSG:4326", "EPSG:2263", always_xy=True).transform
    reverse = Transformer.from_crs("EPSG:2263", "EPSG:4326", always_xy=True).transform
    geometries, statuses, latitudes, longitudes = [], [], [], []
    for value in frame.the_geom:
        geometry, status, lat, lon = None, "invalid_wkt", float("nan"), float("nan")
        try:
            geometry = wkt.loads(value)
            status = "invalid_geometry"
            if isinstance(geometry, (Polygon, MultiPolygon)) and not geometry.is_empty and geometry.is_valid:
                xmin, ymin, xmax, ymax = geometry.bounds
                if -180 <= xmin <= xmax <= 180 and -90 <= ymin <= ymax <= 90:
                    point = transform(reverse, transform(forward, geometry).representative_point())
                    lon, lat, status = point.x, point.y, "valid"
        except Exception:
            # Malformed rows are retained and explicitly excluded from spatial use.
            pass
        geometries.append(geometry)
        statuses.append(status)
        latitudes.append(lat)
        longitudes.append(lon)
    frame["geometry"] = geometries
    frame["geometry_status"] = statuses
    frame["latitude"], frame["longitude"] = latitudes, longitudes
    return frame


def load_demographics(path, estimate_column, output_column):
    """Read an ACS export, retaining reported bounds, missingness and MOE text."""
    moe = estimate_column[:-1] + "M"
    frame = _read(path, ["GEO_ID", "NAME", estimate_column, moe])
    description_rows = frame.GEO_ID == "Geography"
    frame = frame[~description_rows].copy()
    frame["GEOID"] = frame.GEO_ID.str.replace(r"^1400000US", "", regex=True)
    _unique_geoid(frame, Path(path).name)
    raw = frame[estimate_column].str.strip()
    topcoded = raw.str.fullmatch(r"[\d,]+\+")
    numeric = pd.to_numeric(raw.str.replace(",", "", regex=False).str.removesuffix("+"), errors="coerce")
    numeric = numeric.where(numeric.ge(0) & numeric.lt(float("inf")))
    if output_column == "population":
        numeric = numeric.where(numeric.mod(1).eq(0) & ~topcoded)
    result = frame[["GEOID", "NAME"]].rename(columns={"NAME": output_column + "_area_name"})
    result[output_column] = numeric
    result[output_column + "_raw"] = raw
    result[output_column + "_topcoded"] = topcoded
    result[output_column + "_missing"] = numeric.isna()
    result[output_column + "_moe_raw"] = frame[moe]
    result.attrs["description_rows_removed"] = int(description_rows.sum())
    return result


def load_data(data_dir=DEFAULT_DATA_DIR):
    """Return stations, entrances, left-joined tracts, and a JSON-friendly report.

    No files are written, and no missing demographic values are imputed.
    """
    root = Path(data_dir)
    stations = load_stations(root / "stops.csv")
    entrances = load_entrances(root / "entrances.csv")
    tracts = load_tracts(root / "census_tracts.csv")
    income = load_demographics(root / "income.csv", "B19013_001E", "median_household_income")
    population = load_demographics(root / "population.csv", "S0101_C01_001E", "population")
    report = {
        "station_source_rows": stations.attrs["source_rows"],
        "station_parent_rows": stations.attrs["parent_rows"],
        "physical_station_locations": int(stations.coordinates_valid.sum()),
        "invalid_station_coordinates": int((~stations.coordinates_valid).sum()),
        "orphan_platform_rows": stations.attrs["orphan_platform_rows"],
        "entrance_rows": len(entrances),
        "invalid_entrance_coordinates": int((~entrances.coordinates_valid).sum()),
        "tract_rows": len(tracts),
        "invalid_tract_geometries": int((tracts.geometry_status != "valid").sum()),
    }
    for name, table, column in [("income", income, "median_household_income"),
                                 ("population", population, "population")]:
        report[name + "_rows"] = len(table)
        report[name + "_description_rows_removed"] = table.attrs["description_rows_removed"]
        report[name + "_geoids_without_boundary"] = sorted(set(table.GEOID) - set(tracts.GEOID))
        report["tracts_without_" + name + "_row"] = sorted(set(tracts.GEOID) - set(table.GEOID))
        tracts = tracts.merge(table, on="GEOID", how="left", validate="one_to_one")
        tracts[column + "_missing"] = tracts[column].isna()
        report[name + "_missing_on_tracts"] = int(tracts[column].isna().sum())
        report[name + "_topcoded_on_tracts"] = int(tracts[column + "_topcoded"].fillna(False).sum())
    return SubwayData(stations, entrances, tracts, report)
