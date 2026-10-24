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

## Frontend

The UI is a React + Tailwind CSS app built with Vite (Node 20+):

```sh
cd frontend && npm install && npm run build && cd ..
python -m backend.server  # then open http://127.0.0.1:8000
```

For live reload, keep the server running and use `cd frontend && npm run dev`
(http://localhost:5173, which proxies `/api` to port 8000). Components live in
`frontend/src/components`; theme colors are Tailwind tokens in `src/index.css`.

`backend/server.py` (standard library only) serves `frontend/dist` and JSON
endpoints built in `backend/api.py`; data is loaded once per process:

- `/api/summary`: headline counts from `load_data`.
- `/api/tracts`: tract GeoJSON with residents per km² (area measured in
  EPSG:2263; polygons simplified for display only) and quartile class breaks.
- `/api/stations`: the 496 physical station locations.
- `/api/lines`: existing lines from `backend/network.py` (below).
- `/api/deserts`: merged outline of residential tracts more than 1.2 km
  (a 15-minute walk) from every station in the baseline scoring.
- `/api/recommendations?goal=access|eff&n=1..100&along=1|0&spacing=km&budget=Low|Medium|High`:
  `backend/optimizer.py` output (below).

The map is Leaflet 1.9.4 (npm) on OpenStreetMap tiles. OSM's volunteer
tile servers suit development only; switch `TILE_URL` in `frontend/src/components/MapPanel.jsx` to a
tile provider or self-hosted tiles before heavy use.

### Existing line network (`backend/network.py`)

The exports contain no track shapes. Each entrance-export station (one row per
Division + Line + station coordinates) is matched to its GTFS parent location
(median 0 m, maximum 0.46 km). Parents missing from the entrance export join the
line of a same-prefix stop within 1.5 km, or a stop in the same complex within
0.3 km; the Staten Island Railway and Second Avenue Subway, absent from the
entrance export, form their own lines. Each line is the minimum spanning tree of
its stations by haversine distance (links over 6 km are refused), which yields
38 connected lines whose consecutive stations are linked. Routes are those at
40% or more of a line's stations, since entrance rows also list transfer routes;
the leading route sets the MTA color. Edges are straight station-to-station
segments, not track alignments.

### Station placement (`backend/optimizer.py`)

Candidate sites lie every 200 m along line edges, on land (inside a tract
polygon, so not on river crossings); when `along=0`, tract reference points
off the network are added, with `offline_km` measuring new track to the
nearest line. Budgets cap `offline_km` (Low 0, Medium 1.5, High 3 km); `along=1`
forces 0. Sites must be `spacing` km (default 0.5) from existing and chosen
stations. Greedy steps add the site with the largest marginal gain:

- `access`: residents newly within 0.8 km, weighted by baseline
  `priority_score` from `score_tracts` (distance, population, income).
- `eff`: population-weighted reduction in nearest-station distance.

Per-station `residents` are marginal (newly within 0.8 km when that station was
added) and sum to `impact.residents_added`; `minutes_saved` is the
population-weighted straight-line distance saved at 80 m per minute; `score` is
the benefiting residents' mean baseline priority (0 to 100). `impact` is
recomputed with `simulate_expansion` over all evaluable residential tracts.
These are accessibility proxies, not ridership, travel-time or cost forecasts;
fewer than `n` stations are returned when the constraints leave no site with a
positive gain.

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


## Neighborhood types (`backend/clustering.py`)

I added a machine learning model that sorts tracts into neighborhood types. We
have no labels that say which tracts are underserved, so I did not train a model
to predict that. It would only learn to copy the scoring formula above. Instead I
used k-means clustering, which groups tracts that look alike without needing any
labels.

Each tract is described by three numbers. They are the distance to the nearest
station from `score_tracts`, residents per km² with area measured in EPSG:2263
like the frontend, and median household income. I take the log of each, since
all three are skewed, and then standardize them so no single number dominates.

```python
from backend.clustering import cluster_tracts, cluster_summary

clustered = cluster_tracts()             # 4 groups by default
clustered = cluster_tracts(scored, 6)    # reuse an existing score_tracts result
print(cluster_summary(clustered))        # one row per group
```

```sh
python -m backend.clustering --clusters 4
```

The server also returns the groups at `/api/clusters`. The `tracts` field maps
each GEOID to its group number so the map can join it with `/api/tracts`.

Group 0 is always the one closest to the subway, and each group gets a plain name
such as "Dense, lower income". The 126 tracts with no residents, no income
estimate or bad geometry get no group, and nothing is filled in for them. The
result does not change if the input rows are shuffled.

With the current data the four groups look like this.

| Group | Name | Tracts | Residents | Median distance | Mean priority |
|---:|---|---:|---:|---:|---:|
| 0 | Near the subway, dense, higher income | 603 | 2.68M | 0.31 km | 0.18 |
| 1 | Dense, lower income | 662 | 2.94M | 0.37 km | 0.25 |
| 2 | Spread out | 621 | 1.80M | 0.83 km | 0.19 |
| 3 | Very far from the subway, spread out | 313 | 1.02M | 3.34 km | 0.41 |

The silhouette score is about 0.31, which means the groups are real but they
overlap. Three to six groups all score about the same, so four is a readability
choice and not a discovery. The names describe the group as a whole, and a single
tract can differ from its group's name. Distances are straight lines, and the
model knows nothing about buses, travel time or cost.


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

## Snowflake

I set up one shared Snowflake account for the team. Everyone uses the
`TRANSIT_TEAM` role and the `TRANSIT_WH` warehouse, and the tables live in
`TRANSIT_GAP.PUBLIC`. They are the four validated tables from
`export_pipeline.prepare_exports()`, which are `TRACTS_PROCESSED`,
`STATION_LOCATIONS`, `BASELINE_TRACT_SCORES` and `SCORING_RUNS` (see
`EXPORTS.md`). Entrances are not loaded, for the same reasons they are not exported.

The account owner runs `snowflake/setup.sql` once in a Snowsight worksheet as
ACCOUNTADMIN. The bottom of that file has a template for adding each teammate.

Each teammate then copies `.env.example` to `.env` and fills in their own login.
The `.env` file stays on your machine and git ignores it, so please never commit
a password.

```sh
python -m backend.snowflake_client --check   # test the login and Cortex
python -m backend.snowflake_client           # load the four tables, then test Cortex
```

Loading replaces the tables each time, so it is safe to run again after the data
or scoring changes. Each table is created with the Snowflake types and NOT NULL
rules declared in `export_pipeline.SCHEMA`, so column names are the same as the
export in upper case. Rows go through a temporary staging table first. Lists and
nested values such as `ALIASES` and `WEIGHTS` are parsed into VARIANT, and
missing values like the 126 unavailable incomes arrive as SQL NULL rather than
zero. After each table loads, the script compares the Snowflake row count with
the export and stops if they differ.

Tract boundaries stay as WKT text in `BOUNDARY_WKT`. The script prints how many
of them Snowflake cannot read with `TRY_TO_GEOGRAPHY`, which is the check
`EXPORTS.md` asks for before anyone converts them.

If Cortex says the model is not available, set `SNOWFLAKE_CORTEX_MODEL` in `.env`
to another model or turn on cross-region inference near the end of `setup.sql`.

## Snowflake planning copilot

The Copilot panel answers planners' questions with Snowflake Cortex, grounded in
the same data and models the app serves.

**One-time setup** (Snowflake account with `AI_COMPLETE`; new accounts cannot use
the legacy `SNOWFLAKE.CORTEX.COMPLETE`):

1. Generate a key pair for the service user (the private key stays local):
   `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out ~/.snowflake/keys/subway_app_rsa_key.p8`
2. Run `snowflake/copilot_setup.sql` as ACCOUNTADMIN with the public key filled in. It
   creates `SUBWAY_WH`, `SUBWAY.APP`, the `SUBWAY_APP` role (loads tables, owns
   the search service), the read-only `SUBWAY_READER` role (with
   `SNOWFLAKE.CORTEX_USER`) and the key-pair service user `SUBWAY_APP_SVC`.
3. Add a `[subway_app]` profile to `~/.snowflake/connections.toml` (account,
   `user = "SUBWAY_APP_SVC"`, `authenticator = "SNOWFLAKE_JWT"`,
   `private_key_file`, role, warehouse, database `SUBWAY`, schema `APP`), or
   point `SNOWFLAKE_CONNECTION_NAME` at another profile.
4. `python -m backend.snowflake_store` loads `TRACTS` (with a `GEOGRAPHY`
   column), `STATIONS` (point `LOCATION`), `LINES`, the `NEIGHBORHOODS` view and
   about 3,100 text `PROFILES` (neighborhoods, tracts, stations, lines, method
   notes), and creates the Cortex Search service `SUBWAY.APP.PLANNING_SEARCH`.
   Rerun it whenever the data or models change.

**Per question** (`POST /api/chat`, `backend/copilot.py`):

1. Cortex Search retrieves the six most relevant profiles.
2. `AI_COMPLETE` (`claude-sonnet-4-5`) drafts at most one SELECT over the
   planning tables, or declines. A guard allows a single SELECT/WITH without
   DML, DDL or session commands, and it runs as `SUBWAY_READER` with secondary
   roles off, a 20-second timeout and 50 rows.
3. `AI_COMPLETE` answers from the profiles, the query result and the
   recommendations currently shown in the app (sent with self-describing field
   names), and the response lists its sources and SQL.

Answers take about 10 to 15 seconds (two model calls). Unit tests cover the SQL
guard and parsing; `SNOWFLAKE_LIVE_TESTS=1 python -m unittest tests.test_copilot`
also calls Snowflake.
