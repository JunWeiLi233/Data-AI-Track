# NYC Subway Optimization

A planning toolkit for finding where New York City's subway falls short and
where new stations would help the most residents. It has four parts:

- **Web app:** a map where you choose a goal and constraints, place up to 100
  new stations and compare their impact and cost.
- **Analysis:** tract need scoring, neighborhood clustering, commute-based gold
  labels and supervised models that test which signals predict underservice.
- **Simulation:** a greedy station optimizer and an expansion simulator that
  re-scores the city with proposed stations added.
- **Snowflake:** a validated export pipeline, table loading, and an AI planning
  copilot grounded in the Snowflake tables.

All results are **modeled**: straight-line geography and planning proxies, not
ridership, travel-time, feasibility or construction-cost forecasts.

## Contents

- [Quick start](#quick-start)
- [The web app](#the-web-app)
- [Analysis](#analysis)
- [Simulation and station placement](#simulation-and-station-placement)
- [Snowflake](#snowflake)
- [API](#api)
- [How the numbers are made](#how-the-numbers-are-made)
- [Project layout](#project-layout)
- [Data sources](#data-sources)
- [Further documentation](#further-documentation)

## Quick start

Requirements: Python 3.10+ and Node 20+. Run everything from the repository root.

```sh
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
npm --prefix frontend install
```

Start the API. It loads the data on startup, which can take about a minute:

```sh
.venv/bin/python -m backend.server --port 8000
```

Then either start the dev UI with live reload at http://localhost:5173, which
proxies `/api` to port 8000:

```sh
npm --prefix frontend run dev
```

or build it once and let the Python server serve it at http://127.0.0.1:8000:

```sh
npm --prefix frontend run build
```

Run the tests:

```sh
.venv/bin/python -m unittest discover -s tests
```

Tests that need a live Snowflake connection are skipped unless
`SNOWFLAKE_LIVE_TESTS=1` is set. Everything except the copilot and Cortex
explanations runs locally from `data/raw`.

## The web app

React + Tailwind (Vite) with a Leaflet map, served by `backend/server.py`.

- **Goal:** *Accessibility* brings the most residents within a 10-minute walk
  (about 800 m); *Efficiency* cuts the average walk to the nearest station.
- **Stations and constraints:** 1 to 100 new stations, either on existing track
  only or allowed off the network, with optional minimum spacing and a
  Low / Medium / High budget for new track. The controls sit in a sidebar
  (a drawer on small screens) and results update automatically.
- **Map:** population density, existing lines and stations, 10-minute walk
  areas and transit deserts (more than a 15-minute walk from any station). Hover
  a numbered marker for a summary; click it for full details, including relative
  cost and an illustrative dollar range.
- **Impact:** coverage, average walk before and after, new track needed and
  the cost range for the whole scenario.
- **Copilot:** the snowflake button opens a chat answered from the Snowflake
  planning tables (needs Snowflake access; see [Snowflake](#snowflake)).

## Analysis

| Module | What it does |
| --- | --- |
| `backend/data_loader.py` | Read-only loaders and validation for the station, entrance and Census exports. Nothing is imputed or edited. |
| `backend/scoring.py` | Explainable need score per residential tract: distance to a station 50%, population 30%, lower income 20%. |
| `backend/network.py` | Rebuilds the existing lines from the station and entrance exports (minimum spanning tree per named line), since the data has no track shapes. |
| `backend/clustering.py` | k-means neighborhood types from transit access, density and income, described in words. |
| `backend/gold_labels.py` | Underservice labels from an outcome no method sees: the share of workers commuting 60+ minutes (ACS B08303), top quarter of NYC tracts. |
| `backend/travel_time.py` | Rough subway trip from each tract to Midtown or Lower Manhattan over the reconstructed network. |
| `backend/job_destinations.py` | Where each tract's residents work (LEHD LODES 2023) and how far those jobs are by subway. |
| `backend/supervised.py` | Logistic regression and gradient boosting scored against the gold labels, next to the rule-based methods, with folds grouped by community district. |

Command-line entry points:

```sh
python -m backend.gold_labels               # rebuild data/gold/gold_labels.csv
python -m backend.gold_labels --download    # refresh the two ACS source CSVs
python -m backend.supervised                # compare every method to the labels
python -m backend.clustering                # neighborhood types (--clusters N)
python -m backend.job_destinations          # summarize home-to-work flows (--download rebuilds from LODES)
```

**Findings so far** (ROC AUC against the gold labels; random is 0.5):
logistic regression with location features scores 0.77, travel time to
Manhattan alone 0.70, distance to a station alone 0.61, and the rule-based
priority score 0.55. How far residents' jobs are by subway predicts long
commutes best; the priority score and k-means groups agree least with the
labels. Full table and caveats are in [`backend/README.md`](backend/README.md#results).

## Simulation and station placement

- **`backend/optimizer.py`** places stations greedily. Candidate sites are every
  200 m along the existing lines (infill) and, when allowed, at tract centers off
  the network (new track). Each step adds the site with the largest marginal gain
  for the chosen goal, subject to spacing and the budget's new-track limit. It
  stops early when no remaining site helps, so a request for 100 stations can
  return fewer.
- **`backend/simulator.py`** re-scores the city with proposed stations added,
  using the same baseline scoring bounds, and reports residents who gain access
  and how far walks shrink. The app's impact figures come from it.

## Snowflake

| Piece | What it does |
| --- | --- |
| `backend/export_pipeline.py` | Prepares validated JSONL tables, a data dictionary and a QC report in `data/processed/<run>/`. No connection or upload. |
| `backend/snowflake_client.py` | Loads those tables into Snowflake and checks Cortex. Running it without `--check` replaces uploaded tables. |
| `backend/copilot.py`, `backend/copilot_data.py` | The planning copilot behind `POST /api/chat`: bounded, read-only SELECT templates over the analytics tables, answered with Snowflake `AI_COMPLETE`. No model-written SQL. |
| `backend/cortex_explanations.py`, `backend/explanations.py` | Grounded plain-language explanations of a tract's metrics. |
| `snowflake/setup.sql` | One-time account setup (role, warehouse, database). Run as ACCOUNTADMIN. |
| `backend/snowflake_store.py`, `snowflake/copilot_setup.sql` | Legacy provisioning for an earlier copilot design; the current copilot does not use them. |

To enable the copilot, copy `.env.example` to `.env` and fill in your own
Snowflake login, database, schema, role and warehouse. `.env` is git-ignored;
never commit credentials. Restart the server after changing it. Setup and
required grants are in [`backend/README.md`](backend/README.md#snowflake-table-based-planning-copilot).

## API

Served by `python -m backend.server`; payloads are built in `backend/api.py`.

| Endpoint | Returns |
| --- | --- |
| `GET /api/summary` | Headline counts from the loaded data |
| `GET /api/tracts` | Tract GeoJSON with residents per km² and class breaks |
| `GET /api/stations` | The 496 physical station locations |
| `GET /api/lines` | Reconstructed existing lines |
| `GET /api/deserts` | Outline of residential tracts more than 1.2 km from any station |
| `GET /api/clusters` | Neighborhood types from `clustering.py` |
| `GET /api/recommendations?goal=access\|eff&n=1..100&along=1\|0&spacing=km&budget=Low\|Medium\|High` | Placed stations and network-wide impact |
| `GET /api/tracts/<geoid>/explanation` | Cortex explanation of one tract (Snowflake) |
| `POST /api/chat` | Copilot answer with sources and the SQL it ran (Snowflake) |

## How the numbers are made

- **Need score (0–100):** distance to a station 50%, population 30%, lower
  income 20%, relative to the set of NYC tracts.
- **Walks:** straight-line distance from tract centers at 80 m per minute; a
  10-minute walk is 800 m and a transit desert is beyond 1.2 km.
- **Relative cost:** the km of new track a station needs. Infill stops need
  none; the budget tiers allow up to 0, 1.5 and 3 km.
- **Illustrative dollar cost:** new-track km × $1.7B–$2.7B per km, the range of
  published NYC figures from the Second Avenue Subway:
  - [FTA project profile, Phase 2 (Feb 2024)](https://www.transit.dot.gov/sites/fta.dot.gov/files/2024-03/NY-New-York-Second-Avenue-Subway-Phase-2-Profile-AR25.pdf):
    $7,699.03M for 1.76 miles (2.83 km), about $2.72B per km.
  - [Transit Costs Project, Phase 2](https://transitcosts.com/?p=7410):
    $2,423.1M per km (2024 USD).
  - [Transit Costs Project, Phase 1](https://transitcosts.com/newyork-second-avenue-phase-1/):
    $1,704.1M per km (2012 USD).

  These are whole tunneled projects including stations, in different price
  years, so the range is an order of magnitude, not an estimate for a site.
  Infill stops get no dollar figure because the sources give none.

## Project layout

| Path | Contents |
| --- | --- |
| `backend/` | Python: data loading, scoring, network, clustering, labels, models, optimizer, simulator, Snowflake and the HTTP server |
| `frontend/` | React + Tailwind UI (Vite); components in `src/components`, cost sources in `src/lib/cost.js` |
| `data/raw/` | Source data (see below) |
| `data/gold/` | Commute-based gold labels |
| `data/processed/` | Validated export runs for Snowflake |
| `snowflake/` | SQL setup scripts |
| `tests/` | Backend unit tests |

## Data sources

| File | Source |
| --- | --- |
| `stops.csv` | MTA subway GTFS stops (collapsed to 496 physical station locations) |
| `entrances.csv` | MTA subway station entrances, with division and line names |
| `census_tracts.csv` | Census tract boundaries (NYC Open Data) |
| `population.csv`, `income.csv` | U.S. Census Bureau ACS tract estimates (income is table B19013) |
| `commute_time.csv`, `vehicles.csv` | ACS 2024 5-year tables B08303 and B25044 |
| `lodes_flows.csv.gz` | LEHD LODES 2023 home-to-work flows, aggregated to tracts |

Basemap © OpenStreetMap contributors. OSM's volunteer tile servers are for
development only; switch `TILE_URL` in `frontend/src/components/MapPanel.jsx`
before heavy use.

## Further documentation

- [`backend/README.md`](backend/README.md): data cleaning, scoring, network,
  optimizer, clustering, labels, models, simulator and copilot in detail.
- [`backend/EXPORTS.md`](backend/EXPORTS.md): the Snowflake export pipeline.
- [`backend/CORTEX.md`](backend/CORTEX.md): the Cortex explanation endpoint.
- [`backend/EXPLANATIONS.md`](backend/EXPLANATIONS.md): grounded tract explanations.
