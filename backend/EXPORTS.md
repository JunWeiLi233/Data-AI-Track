# Processed data exports for Snowflake

This pipeline prepares files only. It makes no Snowflake connection and never
uploads, commits, pushes, repairs or edits source datasets. It calls the existing
`load_data` and `score_tracts` functions without changing their methodology.

From the repository root, with backend dependencies installed:

```sh
python -m backend.export_pipeline
python -m unittest discover -s tests -v
```

```python
from backend.export_pipeline import prepare_exports, export_processed_data

bundle = prepare_exports()  # Four validated DataFrames; no writes.
run_directory = export_processed_data()  # data/processed/<scoring_run_id>/
# Optional: scoring_run_id="demo-baseline-v1", weights={...}, clip_quantiles=(0.01, 0.99)
```

The CLI accepts `--data-dir`, `--output-dir`, and `--run-id`. Counts default to
2,325 tracts, 496 coordinate locations, 2,325 score rows and one run row. A changed
source release with different counts intentionally fails validation. For another
validated dataset, the Python interface accepts a complete `expected_counts`
mapping. Do not reduce expected counts merely to hide dropped data.

Every run creates a new directory; existing run IDs are rejected. Tables are
validated before writing and published together from a temporary directory.
Nothing is written under the raw directory. Unique run IDs prevent overwriting a
prior baseline. Do not run simultaneous exports using the same manually supplied
run ID. Default UUIDs are appropriate for independent runs.

| File | Rows for current sources | Grain |
|---|---:|---|
| TRACTS_PROCESSED.jsonl | 2,325 | dataset_id + GEOID |
| STATION_LOCATIONS.jsonl | 496 | dataset_id + station_location_id |
| BASELINE_TRACT_SCORES.jsonl | 2,325 | scoring_run_id + GEOID |
| SCORING_RUNS.jsonl | 1 | scoring_run_id |
| data_dictionary.json | metadata | Machine-readable column types, nullability and descriptions |
| data_dictionary.md | metadata | Human-readable per-column dictionary |
| validation_report.json | metadata | Counts, checks, remaining issues and exported file SHA256s |

JSON Lines is UTF-8, one object per line. GEOIDs are JSON strings; aliases and
GTFS parent IDs are sorted distinct JSON string arrays; reported missing income
and ineligible scores/ranks are JSON `null`. NaN/Infinity are never serialized.
A future Snowflake load must explicitly convert JSON nulls to SQL NULL when
projecting scalar columns. The files are preparation artifacts, not upload SQL.
The generated dictionary records intended Snowflake VARCHAR, NUMBER, FLOAT,
BOOLEAN, VARIANT and TIMESTAMP_TZ types. Boundary WKT is VARCHAR staging text:
validate its conversion to GEOGRAPHY during a future integration rather than
assuming Shapely and Snowflake accept exactly the same geometries.

## Identity and lineage

`dataset_id` fingerprints the five named source files (SHA256 plus byte counts).
It changes when source bytes change. `station_location_id` fingerprints exact
numeric coordinates using canonical float representations; it is stable across
source record reordering and does not claim to identify a physical station.
GEOID is the stable tract identifier. Every baseline score references its run
and dataset; every tract/coordinate location carries its dataset identifier.

The run record exports effective weights, quantile settings, the complete scorer
metadata and all three normalization bounds formerly held in DataFrame attrs.
Future scenarios must reuse these bounds and weights. Provenance includes raw
file hashes, loader diagnostics and hashes of the loader, scorer and export
module. Data source years are explicitly unconfirmed, rather than invented.
Income raw values and margin-of-error markers remain available for interpretation.
Source snapshots must remain available if independent reproduction is needed;
file hashes alone do not contain source data.

## Validation and remaining source issues

Hard validation checks exact table counts/columns, required values, unique keys,
11-digit string GEOIDs, all tract foreign keys, dataset/run relationships,
coordinate ranges, NYC bounding-box plausibility, nonnegative demographics and
distances, finite numbers, component ranges, nullable score/rank eligibility,
consistent flags, rank ordering, sorted station arrays, parent-ID uniqueness and
strict JSON serialization. Source fingerprints are checked again after loading
and scoring. Invalid exports fail instead of dropping rows or inventing values.

Current expected source issues are reported, preserved, and do not prevent the
four-table export:

- 126 unavailable tract income estimates; 13 top-coded lower bounds.
- 82 zero-population tracts remain with null residential priority/rank.
- Five coordinate locations contain multiple GTFS parent IDs and are flagged
  `identity_ambiguous=True`; all 496 locations have
  `physical_identity_verified=False`. Two locations also have differing names,
  including the suspicious H01/H02 Aqueduct pair. Review before treating them as
  verified physical stations. The export does not change scoring deduplication.
- Two demographic GEOIDs lack supplied boundaries: 36047990100 and 36081990100.
  They are listed in run provenance/QC, not added to tract geography or scores.
  This preserves all boundary-file tracts without claiming to prove completeness
  against an independent official citywide list.
- Entrances are not one of the four exported tables. One source entrance at
  34th St has positive longitude 73.993327; 11 coordinate-sharing groups contain
  separate entrance records. Their issues are recorded without source correction.

## Geographic validation for future entrance exports

`validate_entrance_geography(loader_entrances)` returns a copy with additional
quality fields, retaining every original column/value and row. Global coordinate
ranges alone miss wrong-hemisphere errors. The helper adds:

- `export_coordinates_valid`: finite global latitude/longitude ranges.
- `nyc_bbox_valid`: latitude 40.45–40.95 and longitude -74.30–-73.65, inclusive.
- `station_reference_distance_km`: WGS84 ellipsoid distance from an entrance to
  its own source-reported station reference, for validation only.
- `station_reference_consistent`: separation at most 1 km, configurable using
  `max_station_separation_km`; missing/invalid references fail this check.
- `geography_review_required`: any of the above checks fails.

This broad NYC envelope is a plausibility screen, not a jurisdiction, land-access
or city-boundary test; nearby non-NYC points may pass. Separation thresholds are
review heuristics, not evidence of error. A future entrance export should retain
source values and flags, with a trusted-map view excluding unreviewed suspect
records. Never automatically flip a longitude or merge conflicting entry records.
No geographic quality screen changes existing scoring or simulator distances.

The underlying accessibility index remains a transparent government planning
screen: straight-line Haversine distance, reported population, reciprocal income,
configurable default 50/30/20 weights. It does not predict feasibility, travel
time, ridership, service quality, engineering cost or causal investment benefits.
