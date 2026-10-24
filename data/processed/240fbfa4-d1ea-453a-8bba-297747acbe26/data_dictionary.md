# Processed export data dictionary

UTF-8 JSON Lines; one JSON object per row; explicit JSON nulls; never NaN/Infinity

Future Snowflake scalar projection must map JSON null to SQL NULL explicitly; do not replace null with zero.

boundary_wkt is VARCHAR staging text; validate before optional GEOGRAPHY conversion. Coordinates are longitude first in WKT.

## TRACTS_PROCESSED

Key: dataset_id, GEOID

| Column | Intended Snowflake type | Nullable | Meaning |
|---|---|---|---|
| dataset_id | VARCHAR | False | SHA256 of the five named source file hashes. |
| GEOID | VARCHAR(11) | False | 11-digit tract identifier; always a string. |
| borough | VARCHAR | False | Source BoroName. |
| tract_label | VARCHAR | False | Source CTLabel, preserved as text. |
| neighborhood | VARCHAR | False | Source NTAName. |
| latitude | FLOAT | False | WGS84 interior reference point latitude, degrees. |
| longitude | FLOAT | False | WGS84 interior reference point longitude, degrees. |
| boundary_wkt | VARCHAR | False | WKT serialized from loader Shapely geometry; EPSG:4326, lon first. |
| geometry_status | VARCHAR | False | Loader geometry validation status. |
| coordinates_valid | BOOLEAN | False | Finite coordinates within global latitude/longitude ranges. |
| nyc_bbox_valid | BOOLEAN | False | Inside documented broad NYC envelope; not a jurisdiction test. |
| population | NUMBER(18,0) | False | Reported total population, including observed zeros. |
| population_missing | BOOLEAN | False | Loader population missingness flag. |
| population_raw | VARCHAR | False | Unaltered Census population estimate text. |
| population_moe_raw | VARCHAR | False | Source margin of error text; not numerically modeled. |
| median_household_income | NUMBER(18,2) | True | Reported dollars; top-coded values are lower bounds, not exact estimates. |
| median_household_income_missing | BOOLEAN | False | True for unavailable reported income; no income imputation. |
| median_household_income_topcoded | BOOLEAN | False | True for a reported upper top-code such as 250,000+. |
| median_household_income_raw | VARCHAR | False | Unaltered income estimate text, including Census markers. |
| median_household_income_moe_raw | VARCHAR | False | Source margin of error text, including Census markers. |
| residential_population_positive | BOOLEAN | False | Positive population; zero-population rows remain in the table. |

## STATION_LOCATIONS

Key: dataset_id, station_location_id

| Column | Intended Snowflake type | Nullable | Meaning |
|---|---|---|---|
| dataset_id | VARCHAR | False | Source dataset fingerprint. |
| station_location_id | VARCHAR | False | loc_ plus SHA256 of exact numeric coordinates; not a physical station ID. |
| canonical_name | VARCHAR | False | Lexicographically first distinct source name; not a verified official identity. |
| aliases | VARIANT | False | Sorted JSON array of all distinct source names, including canonical name. |
| gtfs_parent_ids | VARIANT | False | Sorted JSON array of parent station IDs retained by the loader. |
| latitude | FLOAT | False | Location latitude in WGS84 degrees. |
| longitude | FLOAT | False | Location longitude in WGS84 degrees. |
| coordinates_valid | BOOLEAN | False | Finite coordinates within global ranges. |
| nyc_bbox_valid | BOOLEAN | False | Broad NYC plausibility envelope check. |
| identity_ambiguous | BOOLEAN | False | Multiple GTFS parent IDs share these coordinates; requires review. |
| multiple_source_names | BOOLEAN | False | More than one distinct name shares this coordinate location. |
| physical_identity_verified | BOOLEAN | False | Always false: coordinate deduplication does not establish physical identity. |
| identity_review_reason | VARCHAR | True | Explanation for flagged identity ambiguity; null otherwise. |

## BASELINE_TRACT_SCORES

Key: scoring_run_id, GEOID

| Column | Intended Snowflake type | Nullable | Meaning |
|---|---|---|---|
| scoring_run_id | VARCHAR | False | Foreign key to SCORING_RUNS. |
| dataset_id | VARCHAR | False | Source dataset fingerprint, shared with processed tracts/stations. |
| GEOID | VARCHAR(11) | False | Foreign key to TRACTS_PROCESSED within the dataset. |
| nearest_station_distance_km | FLOAT | False | Straight-line Haversine distance to supplied station inventory, including SIR. |
| distance_score | FLOAT | False | 0-1 normalized distance need component. |
| population_score | FLOAT | False | 0-1 normalized population component. |
| income_disadvantage_score | FLOAT | False | 0-1 normalized reciprocal-income component; neutral fallback is 0.5. |
| income_score_neutral | BOOLEAN | False | Missing/nonpositive income uses neutral component; reported income is not imputed. |
| score_eligible | BOOLEAN | False | Existing scorer residential ranking eligibility. |
| priority_score | FLOAT | True | Configured weighted sum; null for ineligible tracts. |
| rank | NUMBER(18,0) | True | Descending residential rank; minimum rank for ties, null if ineligible. |

## SCORING_RUNS

Key: scoring_run_id

| Column | Intended Snowflake type | Nullable | Meaning |
|---|---|---|---|
| scoring_run_id | VARCHAR | False | Unique export/scoring run identifier; may be supplied for traceability. |
| dataset_id | VARCHAR | False | SHA256 source dataset fingerprint. |
| created_at_utc | TIMESTAMP_TZ | False | UTC ISO-8601 run creation timestamp. |
| weights | VARIANT | False | Effective distance/population/income weights from scorer metadata. |
| normalization_bounds | VARIANT | False | All baseline lower/upper factor bounds, reusable by future scenarios. |
| clip_quantiles | VARIANT | False | Baseline lower/upper clipping quantiles. |
| scoring_metadata | VARIANT | False | Complete scorer DataFrame attributes, including policies and distance method. |
| source_provenance | VARIANT | False | Named input hashes/byte sizes, loader report, code hashes; source vintages unconfirmed. |
| methodology | VARIANT | False | Formula, interpretation, geographic scope, policies and limitations. |
