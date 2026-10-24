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
GEOIDs; the loader does not infer a crosswalk. Scoring and geographic expansion simulation are documented below.


## Government infrastructure planning: underserved tract scoring

This module supports city governments identifying underserved communities and
prioritizing residential infrastructure investments using demographic and
geographic evidence. It supplies transparent components for future expansion
scenario comparisons. It is a deterministic planning index, not a learned AI
model or an individual commuter routing service.

```python
from backend.data_loader import load_data
from backend.scoring import score_tracts

data = load_data()
ranked = score_tracts(data)  # default weights: 0.5, 0.3, 0.2
custom = score_tracts(data, weights={"distance": 50, "population": 30, "income": 20})
# For an untrimmed min-max demonstration:
untrimmed = score_tracts(data, clip_quantiles=(0, 1))
```

`score_tracts()` also loads the default datasets when no data argument is given.
Pass the loader result to reuse frontend-cached data. Neither the input frames nor
raw files are changed. All loader tract columns (including GEOID, coordinates,
population, income, and quality flags) are retained. Added columns are
`nearest_station_distance_km`, `distance_score`, `population_score`,
`income_disadvantage_score`, `income_score_neutral`, `score_eligible`,
`priority_score`, and nullable integer `rank`.

1. Compute the minimum haversine great-circle distance from each loader-derived
   tract interior point to the loader's deduplicated physical station locations,
   including Staten Island Railway. This is a straight-line accessibility proxy.
   Calculations convert degrees to radians and use mean Earth radius 6371.0088 km.
   Invalid stations are excluded; no usable stations raises ValueError. Invalid
   tract coordinates/geometries produce a missing distance.
2. Use distance, total population, and **1 / positive median household income**
   as factors. Larger values mean greater need. Negative, non-finite or fractional
   population is unusable; observed zero population is retained for geographic
   analysis and normalization but excluded from residential rankings. Nonpositive income
   is unusable for the reciprocal, while its original value remains in output.
3. Separately clip each factor to its observed 1st/99th percentiles and min-max
   scale: `(clipped_value - lower) / (upper - lower)`. This limits extreme values'
   influence without altering reported demographics or distance. Bounds use all
   usable observations for each factor, regardless of ranking eligibility. Set
   `clip_quantiles=(0, 1)` to disable clipping. Constant components score 0.5;
   a component with no usable values stays missing.
4. Give missing/unusable income a **neutral component score of 0.5** and set
   `income_score_neutral=True`. This is a transparent scoring convention, not an
   imputed income estimate: it neither equates missing income with poverty nor
   drops the income weight. It can still affect rankings and should be shown to
   demo users. Top-coded income uses the loader's reported lower bound; its source
   flag remains visible. Missing active distance/population leaves score and rank
   missing. Zero-weight components do not block ranking, but zero-population
   tracts are always ineligible regardless of weights: `score_eligible=False`,
   with missing `priority_score` and `rank`. Their rows, geographic coordinates,
   distances, demographics and component scores remain in the returned dataset.
   This exclusion does not change normalization bounds for positive-population tracts.
5. Compute `0.5 * distance_score + 0.3 * population_score +
   0.2 * income_disadvantage_score`. A custom mapping must contain all three keys
   `distance`, `population`, `income`, with finite nonnegative values and a positive
   total. Weights are normalized to sum to one, so percentages work as well.
6. Sort highest priority first. Exact ties share minimum rank (1, 1, 3), with GEOID
   determining display order. Incomplete and zero-population tracts appear last
   with missing rank.

`ranked.attrs["scoring"]` records effective weights, clipping quantiles and factor
bounds, distance method and neutral-score policy. `nearest_station_distances`
is also public for distance-only use with the loader's tract/station DataFrames.

### Interpretation and limitations

This is a relative, explainable hackathon prioritization index, not a validated
investment recommendation. Use fixed baseline bounds for comparisons, as described below. Separately
normalized datasets do not yield comparable absolute need measures.

Tract interior points are geometric proxies and can poorly represent where people
live, especially in large or irregular tracts. Straight-line distance ignores
walking routes, water barriers, entrance access, service frequency, accessibility,
capacity and transfers. Population is total population, not density or predicted
ridership; zero-population tracts are excluded from residential ranking but
retained for geographic analysis. Income is
an uncertain household estimate, not individual wealth. Margins of error are
retained but not modeled, and top-coded high incomes are lower bounds. Reciprocal
income emphasizes low-income differences; clipping reduces, but does not remove,
that modeling choice. Missing-income neutrality can overstate or understate need.
No station catchment population model or frontend is implemented here.


### Fixed baseline normalization for future expansion scenarios

Fit the current network once on the full tract dataset. Reuse all three factor
bounds for every scenario; do not refit percentiles after adding stations or
filtering the display. This prevents a changed reference scale from obscuring
improvements or making unchanged tracts appear more underserved.

```python
baseline = score_tracts(data)
bounds = baseline.attrs["scoring"]["normalization_bounds"]
# scenario_data is a SubwayData with a proposed station inventory;
# demographics and tract reference points stay fixed. The simulator below
# performs this fixed-baseline comparison automatically.
# scenario = score_tracts(scenario_data, normalization_bounds=bounds)
# Compare priority_score and nearest_station_distance_km by GEOID.
```

`normalization_bounds` accepts the exact distance/population/income lower/upper
mapping stored in scoring metadata. It is JSON-serializable for saving alongside
a baseline. Supplied bounds take precedence over `clip_quantiles`; metadata labels
the reference as `baseline` instead of `fitted`. Without this argument, existing
behavior is unchanged. Keep the bounds, effective weights, source data vintages,
tract definitions and missing-value policies fixed for a scenario comparison.
For policy weight sensitivity analysis, change weights explicitly while reusing
the same bounds and report those weights beside the components.

Values outside baseline bounds saturate at 0 or 1. Constant baseline components
retain the existing neutral 0.5 rule; factors with no baseline observations stay
unscalable (income still uses its existing neutral fallback). Such components
cannot express scenario improvements reliably; use raw distance changes as well.
Ranks remain relative to the evaluated eligible set and are not themselves a
measure of benefit. Adding a station should reduce or preserve distance and,
under fixed weights and bounds, reduce or preserve priority scores; a lower score
then indicates reduced modeled need, not a negative outcome.

Retain and display raw factors, normalized components, weights, missing-income
flags and normalization bounds so planners can explain why a tract ranks highly.
The index does not estimate construction costs, engineering feasibility,
benefit-cost ratios, ridership, displacement, environmental impacts or causal
community benefits. Government investment decisions need those analyses, broader
equity evidence and community input. The project pitch's AI capabilities should
be distinguished from this transparent rule-based scoring module.


## Subway expansion simulator

`backend.simulator` supports city planners comparing **station additions** to the
same existing network, including Staten Island Railway. It reuses `score_tracts`
for all distances, components and rankings. The baseline is scored once per
comparison; all proposals use exactly its fitted normalization bounds, weights,
tracts and demographics. The underlying data and input DataFrames are not modified.
There are no writes, commits, network calls or frontend/Snowflake integrations.

```python
from backend.data_loader import load_data
from backend.simulator import simulate_expansion, compare_scenarios

data = load_data()
one = simulate_expansion(
    {"latitude": 40.75, "longitude": -73.72, "name": "Hypothetical A"},
    data,
    min_distance_improvement_km=0.1,  # 100 meters
)
tract_map = one.tracts
station_map = one.proposed_stations
metrics = one.summary

comparison = compare_scenarios(
    {
        "No change": [],
        "Option A": [{"latitude": 40.75, "longitude": -73.72}],
        "Option B": [
            {"latitude": 40.75, "longitude": -73.72},
            {"latitude": 40.76, "longitude": -73.75},
        ],
    },
    data,
    weights={"distance": 0.5, "population": 0.3, "income": 0.2},
    min_distance_improvement_km=0.1,
)
dashboard_table = comparison.summary
baseline_map = comparison.baseline
option_a_map = comparison.scenarios["Option A"].tracts
```

These are illustrative coordinates, not recommended locations. Proposals accept
one station mapping, a sequence of mappings, or a DataFrame with explicit
`latitude`/`longitude` columns. Coordinates must be finite and within global
latitude/longitude bounds. Invalid proposals raise ValueError rather than being
silently dropped. Numeric text is accepted; booleans are rejected. Optional
labels remain in the station map output. Exact duplicate coordinates within a
proposal collapse to one row. A proposal matching an existing station adds no
accessibility improvement. `proposed_station_locations` counts unique submitted
locations, including any already present. An empty list is a valid no-change
reference. Scenario names must be nonempty strings.

Scenarios are **independent alternatives**, not cumulative. Each keeps all
existing stations and adds only its own proposals, so nearest-station distances
cannot worsen. Tract comparisons join by string GEOID rather than rank or row
order. Bounds are fitted on the supplied baseline dataset; use the same full
loader output for comparisons and filter the returned map afterward.
`clip_quantiles` and `weights` remain configurable through the scoring interface.
Missing-income handling and zero-population ranking exclusion are unchanged.

### Benefit definitions and denominators

- A residential tract is assessable when it has a known positive integer
  population and valid baseline and proposed distances. Missing income does not
  prevent geographic benefit assessment.
- `distance_improvement_km = baseline_distance - proposed_distance`. A tract is
  `benefited` when assessable, the improvement is **strictly positive**, and it is
  **at least** `min_distance_improvement_km`. The default is 0.1 km. Thresholds must
  be finite and nonnegative. With zero threshold, unchanged tracts still do not
  count. Thresholds affect benefit counts, not distances or scoring.
- `population_potentially_benefiting` sums the entire population estimate of each
  qualifying tract **once per scenario**, even if multiple proposed stations
  improve it. Counts across alternative scenarios must not be added together.
  This is a tract-level potential benefit estimate, not observed or forecast
  ridership or a count of residents within a station catchment.
- `population_weighted_average_distance_reduction_km` is
  `sum(population * distance_improvement_km) / sum(population)` over **all
  assessable residential tracts**, including unchanged tracts and improvements
  below the threshold. `population_evaluated` exposes that denominator. The mean
  is `None` when no residential population can be assessed (a summary DataFrame
  may display this as NaN). It is not the average among beneficiaries alone.
- Zero-population and unknown/invalid-population tracts remain in the tract/map
  tables but contribute neither residential benefit counts nor weighted means.
  Missing distances remain missing, not zero. Summary diagnostics report unknown
  population and positive-population tracts without valid distances.

### Structured result fields

`simulate_expansion` returns a `ScenarioResult` with `name`, `proposed_stations`,
`tracts`, `summary`, and `scoring_metadata`. The tract DataFrame retains baseline
geography, coordinates, demographics and missing-income flags. Scoring outputs
have `baseline_` and `proposed_` prefixes: `nearest_station_distance_km`,
`distance_score`, `population_score`, `income_disadvantage_score`,
`priority_score`, `rank`, `score_eligible`. Additional fields are
`distance_improvement_km`, `priority_score_reduction`,
`residential_benefit_eligible` and `benefited`. Lower proposed priority reflects
reduced modeled need. Normalization saturation can mask distance improvements
in scores; inspect raw distances alongside scores. Rank changes are not benefits.

`compare_scenarios` returns a `ScenarioComparison`: the full `baseline` scoring
DataFrame, named `scenarios` mapping to ScenarioResults, a side-by-side `summary`
DataFrame (one row per option), and `metadata` with the baseline reference and
benefit definitions. For maps, use retained latitude/longitude or WKT/geometry;
for dashboard charts, use the summary table. These are Python dataclasses and
DataFrames, not JSON HTTP responses; serialize only the required columns in a
future API layer. Baseline/scenario scoring metadata makes component scales and
weights inspectable.

### Simulation limitations

This is a **geographic accessibility simulation using straight-line Haversine
distance**, not a construction feasibility, ridership or travel-time prediction
model. Coordinates are range-validated, not checked for land, jurisdiction,
engineering suitability, network connections or service availability. A proposed
station is assumed to operate with no changes to existing service. No tracks,
service plans, entrances, costs, capacity, terrain or street routes are modeled.
A closer point across water may not offer a practical journey. Tract interior
points and whole-tract population counts can overstate or understate resident
benefits and must be labeled as proxies in the dashboard. Results support
screening and comparison for government planning; they do not establish causal
benefits or replace engineering review, equity analysis and community input.
