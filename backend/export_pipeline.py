"""Read-only source preparation for Snowflake; no connection or upload code.

Run: python -m backend.export_pipeline --output-dir data/processed
Each immutable run directory contains four JSONL tables, dictionary and QC report.
"""
import argparse
import hashlib
import json
import math
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Geod

from .data_loader import DEFAULT_DATA_DIR, load_data
from .scoring import score_tracts

DEFAULT_OUTPUT_DIR = DEFAULT_DATA_DIR.parent / "processed"
SOURCE_FILES = ("stops.csv", "entrances.csv", "census_tracts.csv", "income.csv", "population.csv")
EXPECTED_COUNTS = {"TRACTS_PROCESSED": 2325, "STATION_LOCATIONS": 496,
                   "BASELINE_TRACT_SCORES": 2325, "SCORING_RUNS": 1}
# Broad NYC plausibility envelope, NOT a municipal boundary test.
NYC_BOUNDS = {"min_latitude": 40.45, "max_latitude": 40.95,
              "min_longitude": -74.30, "max_longitude": -73.65}

# Column definitions are the single source for table ordering and the dictionary.
SCHEMA = {
    "TRACTS_PROCESSED": {
        "dataset_id": ("VARCHAR", False, "SHA256 of the five named source file hashes."),
        "GEOID": ("VARCHAR(11)", False, "11-digit tract identifier; always a string."),
        "borough": ("VARCHAR", False, "Source BoroName."),
        "tract_label": ("VARCHAR", False, "Source CTLabel, preserved as text."),
        "neighborhood": ("VARCHAR", False, "Source NTAName."),
        "latitude": ("FLOAT", False, "WGS84 interior reference point latitude, degrees."),
        "longitude": ("FLOAT", False, "WGS84 interior reference point longitude, degrees."),
        "boundary_wkt": ("VARCHAR", False, "WKT serialized from loader Shapely geometry; EPSG:4326, lon first."),
        "geometry_status": ("VARCHAR", False, "Loader geometry validation status."),
        "coordinates_valid": ("BOOLEAN", False, "Finite coordinates within global latitude/longitude ranges."),
        "nyc_bbox_valid": ("BOOLEAN", False, "Inside documented broad NYC envelope; not a jurisdiction test."),
        "population": ("NUMBER(18,0)", False, "Reported total population, including observed zeros."),
        "population_missing": ("BOOLEAN", False, "Loader population missingness flag."),
        "population_raw": ("VARCHAR", False, "Unaltered Census population estimate text."),
        "population_moe_raw": ("VARCHAR", False, "Source margin of error text; not numerically modeled."),
        "median_household_income": ("NUMBER(18,2)", True, "Reported dollars; top-coded values are lower bounds, not exact estimates."),
        "median_household_income_missing": ("BOOLEAN", False, "True for unavailable reported income; no income imputation."),
        "median_household_income_topcoded": ("BOOLEAN", False, "True for a reported upper top-code such as 250,000+."),
        "median_household_income_raw": ("VARCHAR", False, "Unaltered income estimate text, including Census markers."),
        "median_household_income_moe_raw": ("VARCHAR", False, "Source margin of error text, including Census markers."),
        "residential_population_positive": ("BOOLEAN", False, "Positive population; zero-population rows remain in the table."),
    },
    "STATION_LOCATIONS": {
        "dataset_id": ("VARCHAR", False, "Source dataset fingerprint."),
        "station_location_id": ("VARCHAR", False, "loc_ plus SHA256 of exact numeric coordinates; not a physical station ID."),
        "canonical_name": ("VARCHAR", False, "Lexicographically first distinct source name; not a verified official identity."),
        "aliases": ("VARIANT", False, "Sorted JSON array of all distinct source names, including canonical name."),
        "gtfs_parent_ids": ("VARIANT", False, "Sorted JSON array of parent station IDs retained by the loader."),
        "latitude": ("FLOAT", False, "Location latitude in WGS84 degrees."),
        "longitude": ("FLOAT", False, "Location longitude in WGS84 degrees."),
        "coordinates_valid": ("BOOLEAN", False, "Finite coordinates within global ranges."),
        "nyc_bbox_valid": ("BOOLEAN", False, "Broad NYC plausibility envelope check."),
        "identity_ambiguous": ("BOOLEAN", False, "Multiple GTFS parent IDs share these coordinates; requires review."),
        "multiple_source_names": ("BOOLEAN", False, "More than one distinct name shares this coordinate location."),
        "physical_identity_verified": ("BOOLEAN", False, "Always false: coordinate deduplication does not establish physical identity."),
        "identity_review_reason": ("VARCHAR", True, "Explanation for flagged identity ambiguity; null otherwise."),
    },
    "BASELINE_TRACT_SCORES": {
        "scoring_run_id": ("VARCHAR", False, "Foreign key to SCORING_RUNS."),
        "dataset_id": ("VARCHAR", False, "Source dataset fingerprint, shared with processed tracts/stations."),
        "GEOID": ("VARCHAR(11)", False, "Foreign key to TRACTS_PROCESSED within the dataset."),
        "nearest_station_distance_km": ("FLOAT", False, "Straight-line Haversine distance to supplied station inventory, including SIR."),
        "distance_score": ("FLOAT", False, "0-1 normalized distance need component."),
        "population_score": ("FLOAT", False, "0-1 normalized population component."),
        "income_disadvantage_score": ("FLOAT", False, "0-1 normalized reciprocal-income component; neutral fallback is 0.5."),
        "income_score_neutral": ("BOOLEAN", False, "Missing/nonpositive income uses neutral component; reported income is not imputed."),
        "score_eligible": ("BOOLEAN", False, "Existing scorer residential ranking eligibility."),
        "priority_score": ("FLOAT", True, "Configured weighted sum; null for ineligible tracts."),
        "rank": ("NUMBER(18,0)", True, "Descending residential rank; minimum rank for ties, null if ineligible."),
    },
    "SCORING_RUNS": {
        "scoring_run_id": ("VARCHAR", False, "Unique export/scoring run identifier; may be supplied for traceability."),
        "dataset_id": ("VARCHAR", False, "SHA256 source dataset fingerprint."),
        "created_at_utc": ("TIMESTAMP_TZ", False, "UTC ISO-8601 run creation timestamp."),
        "weights": ("VARIANT", False, "Effective distance/population/income weights from scorer metadata."),
        "normalization_bounds": ("VARIANT", False, "All baseline lower/upper factor bounds, reusable by future scenarios."),
        "clip_quantiles": ("VARIANT", False, "Baseline lower/upper clipping quantiles."),
        "scoring_metadata": ("VARIANT", False, "Complete scorer DataFrame attributes, including policies and distance method."),
        "source_provenance": ("VARIANT", False, "Named input hashes/byte sizes, loader report, code hashes; source vintages unconfirmed."),
        "methodology": ("VARIANT", False, "Formula, interpretation, geographic scope, policies and limitations."),
    },
}


@dataclass
class ExportBundle:
    tables: dict
    validation: dict


def _json_value(value):
    """Convert pandas/NumPy scalars recursively, retaining real JSON nulls."""
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or value is pd.NA:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _json(value):
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, allow_nan=False)


def _fingerprints(root):
    return [{"file": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
             "bytes": (root / name).stat().st_size} for name in SOURCE_FILES]


def _geography_flags(frame):
    lat = pd.to_numeric(frame.latitude, errors="coerce").astype("float64")
    lon = pd.to_numeric(frame.longitude, errors="coerce").astype("float64")
    valid = np.isfinite(lat) & np.isfinite(lon) & lat.between(-90, 90) & lon.between(-180, 180)
    nyc = valid & lat.between(NYC_BOUNDS["min_latitude"], NYC_BOUNDS["max_latitude"]) & lon.between(
        NYC_BOUNDS["min_longitude"], NYC_BOUNDS["max_longitude"])
    return valid, nyc


def validate_entrance_geography(entrances, *, max_station_separation_km=1.0):
    """Return a copy with NYC/location flags; never change original source values.

    Takes loader entrances. Broad bbox is a plausibility screen, not proof of
    jurisdiction or land access. Also flag distance from each entrance to its
    own source-reported station reference (WGS84 ellipsoid, validation only).
    These diagnostics do not alter scoring or station inventory.
    """
    limit = float(max_station_separation_km)
    if not np.isfinite(limit) or limit < 0:
        raise ValueError("max_station_separation_km must be finite and nonnegative")
    result = entrances.copy(deep=True)
    global_valid, nyc = _geography_flags(result)
    result["export_coordinates_valid"] = global_valid
    result["nyc_bbox_valid"] = nyc
    reference = pd.DataFrame({"latitude": result["Station Latitude"], "longitude": result["Station Longitude"]})
    reference_valid, _ = _geography_flags(reference)
    separation = pd.Series(np.nan, index=result.index)
    usable = global_valid & reference_valid
    if usable.any():
        _, _, meters = Geod(ellps="WGS84").inv(
            pd.to_numeric(reference.longitude[usable]).astype(float).tolist(),
            pd.to_numeric(reference.latitude[usable]).astype(float).tolist(),
            pd.to_numeric(result.longitude[usable]).astype(float).tolist(),
            pd.to_numeric(result.latitude[usable]).astype(float).tolist())
        separation.loc[usable] = np.asarray(meters, dtype=float) / 1000
    result["station_reference_distance_km"] = separation
    result["station_reference_consistent"] = separation.le(limit)
    result["geography_review_required"] = ~global_valid | ~nyc | ~result.station_reference_consistent
    return result


def _frame(table, records):
    frame = pd.DataFrame(records, columns=list(SCHEMA[table]))
    for column, (kind, _, _) in SCHEMA[table].items():
        if kind.startswith("VARCHAR") or kind == "TIMESTAMP_TZ":
            frame[column] = frame[column].astype("string")
        elif kind == "BOOLEAN":
            frame[column] = frame[column].astype("boolean")
        elif kind == "NUMBER(18,0)":
            frame[column] = frame[column].astype("Int64")
        elif kind == "FLOAT" or kind.startswith("NUMBER"):
            frame[column] = frame[column].astype("Float64")
    return frame


def _location_id(latitude, longitude):
    # float.hex is exact, stable and independent of input row ordering/formatting.
    parts = [float(value).hex() if value != 0 else float(0).hex() for value in (latitude, longitude)]
    return "loc_" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def validate_export_tables(tables, *, expected_counts=None):
    """Fail closed on invalid projected tables, FK breaks, or unexpected nulls."""
    counts = EXPECTED_COUNTS if expected_counts is None else expected_counts
    if set(tables) != set(SCHEMA) or set(counts) != set(SCHEMA):
        raise ValueError("Expected exactly the four defined export tables and row counts")
    def require(condition, message):
        if not condition:
            raise ValueError(message)
    for table, schema in SCHEMA.items():
        frame = tables[table]
        require(list(frame.columns) == list(schema), f"{table}: unexpected columns")
        require(len(frame) == counts[table], f"{table}: expected {counts[table]} rows, got {len(frame)}")
        for column, (kind, nullable, _) in schema.items():
            if not nullable:
                require(not frame[column].isna().any(), f"{table}.{column}: unexpected null")
            if kind == "FLOAT" or kind.startswith("NUMBER"):
                values = pd.to_numeric(frame[column], errors="raise").dropna().to_numpy(dtype=float)
                require(np.isfinite(values).all(), f"{table}.{column}: non-finite value")
        # Ensure all rows can actually be serialized with strict JSON (no NaN/Infinity).
        for record in frame.to_dict(orient="records"):
            _json(record)
    tracts, stations, scores, runs = (tables[name] for name in SCHEMA)
    for frame in (tracts, scores):
        require(frame.GEOID.map(lambda value: isinstance(value, str) and bool(re.fullmatch(r"[0-9]{11}", value))).all(), "Malformed string GEOID")
        require(not frame.GEOID.duplicated().any(), "Duplicate GEOID")
    require(not stations.station_location_id.duplicated().any(), "Duplicate station_location_id")
    require(not stations.duplicated(["latitude", "longitude"]).any(), "Duplicate station coordinates")
    require(not runs.scoring_run_id.duplicated().any() and len(runs) == 1, "Expected exactly one scoring run")
    dataset = runs.iloc[0].dataset_id
    require(all(frame.dataset_id.eq(dataset).all() for frame in tables.values()), "Dataset foreign key mismatch")
    require(scores.scoring_run_id.isin(runs.scoring_run_id).all(), "Scoring run foreign key mismatch")
    require(set(scores.GEOID) == set(tracts.GEOID), "Tract foreign key/retention mismatch")
    for frame in (tracts, stations):
        global_valid, nyc = _geography_flags(frame)
        require(global_valid.all() and nyc.all(), "Coordinates invalid or outside NYC plausibility envelope")
        require(frame.coordinates_valid.all() and frame.nyc_bbox_valid.all(), "Inconsistent geographic flags")
    require(tracts.geometry_status.eq("valid").all(), "Invalid tract geometry")
    require(tracts.population.ge(0).all(), "Negative population")
    require(tracts.population_missing.eq(tracts.population.isna()).all(), "Population missingness mismatch")
    require(tracts.median_household_income_missing.eq(tracts.median_household_income.isna()).all(), "Income missingness mismatch")
    require(tracts.median_household_income.dropna().ge(0).all(), "Negative income")
    for column in ("distance_score", "population_score", "income_disadvantage_score", "priority_score"):
        require(scores[column].dropna().between(0, 1).all(), f"{column}: outside 0-1")
    require(scores.nearest_station_distance_km.ge(0).all(), "Negative distance")
    require(scores.priority_score.isna().eq(~scores.score_eligible).all(), "Priority eligibility/null mismatch")
    require(scores['rank'].isna().eq(~scores.score_eligible).all(), "Rank eligibility/null mismatch")
    zero_geoids = tracts.loc[tracts.population.eq(0), "GEOID"]
    require(scores.loc[scores.GEOID.isin(zero_geoids), 'rank'].isna().all(), "Zero-population tract ranked")
    require(scores['rank'].equals(scores.priority_score.rank(ascending=False, method="min").astype("Int64")), "Unexpected residential ranks")
    for column in ("aliases", "gtfs_parent_ids"):
        require(stations[column].map(lambda value: isinstance(value, list) and bool(value)
            and all(isinstance(item, str) and bool(item) for item in value)
            and value == sorted(set(value))).all(), f"{column}: expected sorted unique nonempty string arrays")
    parent_ids = stations.gtfs_parent_ids.explode()
    require(not parent_ids.duplicated().any(), "GTFS parent ID mapped to multiple locations")
    require(stations.identity_ambiguous.eq(stations.gtfs_parent_ids.map(len).gt(1)).all(), "Station ambiguity flag mismatch")
    require(stations.multiple_source_names.eq(stations.aliases.map(len).gt(1)).all(), "Station alias flag mismatch")
    require(not stations.physical_identity_verified.any(), "Unverified station identity asserted")
    require(tracts.residential_population_positive.eq(tracts.population.gt(0)).all(), "Residential population flag mismatch")
    return {"passed": True, "row_counts": {name: len(frame) for name, frame in tables.items()},
            "checks": ["counts", "column_contracts", "required_values", "strict_json", "finite_numbers",
                       "string_geoids", "uniqueness", "foreign_keys", "coordinate_ranges_and_nyc_bbox",
                       "missingness_flags", "score_ranges", "residential_rank_eligibility"]}


def prepare_exports(data_dir=DEFAULT_DATA_DIR, *, scoring_run_id=None, weights=None,
                    clip_quantiles=(0.01, 0.99), expected_counts=None):
    """Build and validate four tables without writing; reuse loader and scorer."""
    root = Path(data_dir).resolve()
    run_id = str(uuid.uuid4()) if scoring_run_id is None else scoring_run_id
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
        raise ValueError("scoring_run_id must be a safe 1-80 character identifier")
    provenance = _fingerprints(root)
    dataset_id = hashlib.sha256(_json(provenance).encode()).hexdigest()
    data = load_data(root)
    baseline = score_tracts(data, weights, clip_quantiles=clip_quantiles)
    valid, nyc = _geography_flags(data.tracts)
    tracts = data.tracts
    columns = {"dataset_id": dataset_id, "GEOID": tracts.GEOID,
               "borough": tracts.BoroName, "tract_label": tracts.CTLabel, "neighborhood": tracts.NTAName,
               "latitude": tracts.latitude, "longitude": tracts.longitude,
               "boundary_wkt": tracts.geometry.map(lambda value: value.wkt if value is not None else None),
               "geometry_status": tracts.geometry_status, "coordinates_valid": valid, "nyc_bbox_valid": nyc,
               "residential_population_positive": tracts.population.gt(0)}
    for column in SCHEMA["TRACTS_PROCESSED"]:
        if column not in columns:
            columns[column] = tracts[column]
    tables = {"TRACTS_PROCESSED": _frame("TRACTS_PROCESSED", pd.DataFrame(columns).to_dict("records"))}
    records = []
    valid, nyc = _geography_flags(data.stations)
    for index, station in data.stations.iterrows():
        ids = sorted(set(station.station_ids))
        aliases = sorted(set(station.station_names))
        ambiguous = len(ids) > 1
        records.append({"dataset_id": dataset_id,
            "station_location_id": _location_id(station.latitude, station.longitude),
            "canonical_name": aliases[0], "aliases": aliases, "gtfs_parent_ids": ids,
            "latitude": station.latitude, "longitude": station.longitude,
            "coordinates_valid": valid.loc[index], "nyc_bbox_valid": nyc.loc[index],
            "identity_ambiguous": ambiguous, "multiple_source_names": len(aliases) > 1,
            "physical_identity_verified": False,
            "identity_review_reason": "multiple_gtfs_parent_ids_share_coordinates" if ambiguous else None})
    tables["STATION_LOCATIONS"] = _frame("STATION_LOCATIONS", records)
    scores = baseline[[column for column in SCHEMA["BASELINE_TRACT_SCORES"] if column not in ("scoring_run_id", "dataset_id")]].copy()
    scores["scoring_run_id"], scores["dataset_id"] = run_id, dataset_id
    tables["BASELINE_TRACT_SCORES"] = _frame("BASELINE_TRACT_SCORES", scores.to_dict("records"))
    code_hashes = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                   for name in ("data_loader.py", "scoring.py", "export_pipeline.py")}
    metadata = baseline.attrs["scoring"]
    tables["SCORING_RUNS"] = _frame("SCORING_RUNS", [{
        "scoring_run_id": run_id, "dataset_id": dataset_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "weights": metadata["weights"], "normalization_bounds": metadata["normalization_bounds"],
        "clip_quantiles": metadata["clip_quantiles"], "scoring_metadata": metadata,
        "source_provenance": {"files": provenance, "code_sha256": code_hashes,
                              "loader_report": data.report, "source_vintages_confirmed": False},
        "methodology": {
            "formula": "distance_weight * distance_score + population_weight * population_score + income_weight * income_disadvantage_score",
            "default_weights": {"distance": 0.5, "population": 0.3, "income": 0.2},
            "normalization": "quantile-clipped min-max; income factor is reciprocal positive reported income",
            "missing_income": "neutral component 0.5, never impute reported income",
            "zero_population": "retain geography and components; null priority and rank",
            "distance_proxy": "straight-line Haversine km to supplied inventory including Staten Island Railway",
            "baseline_comparison": "reuse normalization_bounds and weights for scenario scores",
            "nyc_bbox": NYC_BOUNDS,
            "limitations": ["geometric tract reference points, not population centroids", "whole-tract demographic estimates",
                            "no walking routes, travel times, service frequency or ridership prediction",
                            "not construction feasibility or investment benefit-cost analysis", "source vintages not independently confirmed"],
        }}])
    # Stable output ordering; station IDs remain stable under source reordering.
    tables["TRACTS_PROCESSED"] = tables["TRACTS_PROCESSED"].sort_values("GEOID").reset_index(drop=True)
    tables["STATION_LOCATIONS"] = tables["STATION_LOCATIONS"].sort_values("station_location_id").reset_index(drop=True)
    tables["BASELINE_TRACT_SCORES"] = tables["BASELINE_TRACT_SCORES"].sort_values("GEOID").reset_index(drop=True)
    validation = validate_export_tables(tables, expected_counts=expected_counts)
    entrances = validate_entrance_geography(data.entrances)
    suspicious = entrances.loc[entrances.geography_review_required]
    validation["quality_issues"] = {
        "missing_income": int(tracts.median_household_income.isna().sum()),
        "topcoded_income": int(tracts.median_household_income_topcoded.sum()),
        "zero_population_unranked": int(tracts.population.eq(0).sum()),
        "ambiguous_station_locations": int(tables["STATION_LOCATIONS"].identity_ambiguous.sum()),
        "unmatched_demographic_geoids": sorted(set(data.report["income_geoids_without_boundary"]) | set(data.report["population_geoids_without_boundary"])),
        "entrances_review_required_not_exported": len(suspicious),
        "entrance_reviews": suspicious[["Station Name", "Entrance Latitude", "Entrance Longitude", "nyc_bbox_valid", "station_reference_distance_km"]].to_dict("records"),
        "entrance_duplicate_coordinate_groups": int(entrances.groupby(["latitude", "longitude"]).size().gt(1).sum()),
    }
    if provenance != _fingerprints(root):
        raise ValueError("Source files changed during export preparation")
    validation["raw_source_hashes_unchanged"] = True
    return ExportBundle(tables, validation)


def _dictionary():
    return {"format": "UTF-8 JSON Lines; one JSON object per row; explicit JSON nulls; never NaN/Infinity",
            "null_loading_note": "Future Snowflake scalar projection must map JSON null to SQL NULL explicitly; do not replace null with zero.",
            "boundary_loading_note": "boundary_wkt is VARCHAR staging text; validate before optional GEOGRAPHY conversion. Coordinates are longitude first in WKT.",
            "keys": {"TRACTS_PROCESSED": ["dataset_id", "GEOID"],
                     "STATION_LOCATIONS": ["dataset_id", "station_location_id"],
                     "BASELINE_TRACT_SCORES": ["scoring_run_id", "GEOID"],
                     "SCORING_RUNS": ["scoring_run_id"]},
            "tables": {table: [{"column": column, "snowflake_type": kind, "nullable": nullable, "description": description}
                               for column, (kind, nullable, description) in columns.items()] for table, columns in SCHEMA.items()}}


def export_processed_data(output_dir=DEFAULT_OUTPUT_DIR, data_dir=DEFAULT_DATA_DIR, **options):
    """Write a new complete run directory atomically; never overwrite prior runs.

    Returns the run directory Path. Options are forwarded to prepare_exports.
    """
    output = Path(output_dir).resolve()
    raw = Path(data_dir).resolve()
    if output == raw or raw in output.parents:
        raise ValueError("Output directory must not be inside the raw data directory")
    bundle = prepare_exports(raw, **options)
    run_id = bundle.tables["SCORING_RUNS"].iloc[0].scoring_run_id
    target = output / run_id
    if target.exists():
        raise FileExistsError(f"Export run already exists: {target}")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".export-", dir=output) as scratch:
        staging = Path(scratch) / "run"
        staging.mkdir()
        for table, frame in bundle.tables.items():
            with (staging / f"{table}.jsonl").open("w", encoding="utf-8") as stream:
                for record in frame.to_dict("records"):
                    stream.write(_json(record) + "\n")
        dictionary = _dictionary()
        (staging / "data_dictionary.json").write_text(_json(dictionary) + "\n", encoding="utf-8")
        lines = ["# Processed export data dictionary", "", dictionary["format"], "", dictionary["null_loading_note"], "", dictionary["boundary_loading_note"]]
        for table, columns in dictionary["tables"].items():
            lines.extend(["", f"## {table}", "", "Key: " + ", ".join(dictionary["keys"][table]), "",
                          "| Column | Intended Snowflake type | Nullable | Meaning |", "|---|---|---|---|"])
            lines.extend(f"| {c['column']} | {c['snowflake_type']} | {c['nullable']} | {c['description']} |" for c in columns)
        (staging / "data_dictionary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = {"scoring_run_id": run_id, **bundle.validation,
                  "file_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in staging.iterdir()}}
        (staging / "validation_report.json").write_text(_json(report) + "\n", encoding="utf-8")
        staging.rename(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    print(export_processed_data(args.output_dir, args.data_dir, scoring_run_id=args.run_id))


if __name__ == "__main__":
    main()
