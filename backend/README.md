# Backend: data loading and validation

Run from the repository root (Python 3.10+):

```sh
python -m pip install -r backend/requirements.txt
python -m unittest discover -s tests -v
```

```python
from backend.data_loader import load_data

data = load_data()  # or load_data('/path/to/data/raw')
print(data.report)
stations = data.stations
entrances = data.entrances
tracts = data.tracts
```

All outputs are pandas DataFrames except `report`, a dictionary suitable for
Streamlit display. The default data path is relative to this module, independent
of the frontend's working directory. Loaders only read CSVs and never save them.
Individual functions are also public: `load_stations`, `load_entrances`,
`load_tracts`, and `load_demographics`.

## Actual source schemas and cleaning

- **stops.csv**: 1,503 GTFS records, including 501 `location_type=1` parents
  and 1,002 child platforms. Use parent stations, then collapse identical
  numeric latitude/longitude pairs to 496 physical locations. `station_ids`
  and `station_names` retain all collapsed parents. Nearby but distinct
  coordinates are not merged; these are physical location proxies, not a
  definitive inventory of transfer complexes. Invalid parents stay flagged.
- **entrances.csv**: 1,868 records. Use `Entrance Latitude` and
  `Entrance Longitude`, retaining Entry and other metadata. Do not count
  entrances as stations or assume every entrance permits entry. No inferred
  GTFS join is made: this export has names and coordinates, not GTFS IDs.
- **census_tracts.csv**: 2,325 tract boundaries, `the_geom` WKT MULTIPOLYGON
  with longitude first, and string `GEOID`. Validate polygon type, topology
  and coordinate ranges. Preserve invalid rows with missing reference points.
  For valid geometry, derive an interior representative point in EPSG:2263
  and transform back to WGS84. This is a geometric proxy, not a residential
  centroid. The supplied area/length columns are not used for distances.
- **income.csv**: `B19013_001E` is median household income, reported in 2024
  inflation-adjusted dollars according to its descriptive header. Remove the
  `Geography` descriptive row; strip `1400000US` from `GEO_ID`. Preserve the
  128 `-` values as missing. Parse the 13 `250,000+` values as reported lower
  bounds of 250000, with `median_household_income_topcoded=True` and raw text.
  These are not exact estimates. Do not interpret missing income as zero.
- **population.csv**: use only `S0101_C01_001E` for total population, not age,
  sex or percentage columns. Remove the descriptive row and normalize the
  same GEOID prefix. Retain observed zero population. Non-numeric, negative,
  non-finite or fractional counts become missing.

Both ACS files contain 2,327 data rows. Tracts are the left side of validated
one-to-one joins; demographic-only GEOIDs are listed in the report rather than
assigned invented boundaries. Missing and top-coded flags, raw estimate text,
and raw margins of error remain available. Duplicate or malformed GEOIDs raise
ValueError instead of multiplying rows or silently guessing identifiers.

Numeric demographic extremes are retained: outlier treatment belongs in the
subsequent scoring model and should not alter observed source estimates. Source
vintages and tract definitions should be checked before interpreting unmatched
GEOIDs; the loader does not infer a crosswalk. No scoring or simulation is
implemented at this stage. Future distance calculations should use geodesic or
haversine distance in kilometers, never Euclidean distance in latitude/longitude.
