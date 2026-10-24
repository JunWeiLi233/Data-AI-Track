# NYC Subway Optimization

A planning tool that suggests where new NYC subway stations would help the most
residents, and shows the trade-offs on a map. It scores census tracts by need,
places stations greedily on candidate sites, and checks the result with an
expansion simulator. A Snowflake-backed copilot answers questions about the
planning data.

All results are **modeled**: straight-line walking distances and planning
proxies, not ridership, travel-time or construction-cost forecasts.

## What you can do

- **Choose a goal.** *Accessibility* brings the most residents within a 10-minute
  walk (about 800 m); *Efficiency* cuts the average walk to the nearest station.
- **Add 1 to 100 stations**, limited to existing track or allowed off the
  network, with optional minimum spacing and a Low / Medium / High budget for new
  track.
- **Read the map.** It shows population density, existing lines and stations, walk
  areas and transit deserts (more than a 15-minute walk from any station). Hover a
  numbered marker for a summary; click it for full details.
- **Compare impact.** See coverage, average walk before and after, new track
  needed and an illustrative cost range.
- **Ask the copilot.** Click the snowflake button for questions answered from the
  Snowflake planning tables.

## Quick start

Requirements: Python 3.10+ and Node 20+.

```sh
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
npm --prefix frontend install
```

Run the API (it loads the data on startup, which can take about a minute):

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

The copilot needs Snowflake access; everything else runs locally from
`data/raw`. To enable it, copy `.env.example` to `.env` and fill in your own
Snowflake login (see [Snowflake copilot](backend/README.md#snowflake-table-based-planning-copilot)).
`.env` is git-ignored; never commit credentials.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests
```

Tests that need a live Snowflake connection are skipped unless
`SNOWFLAKE_LIVE_TESTS=1` is set.

## Project layout

| Path | Contents |
| --- | --- |
| `backend/` | Data loading, tract scoring, line network, optimizer, simulator, clustering, copilot and the HTTP server (`server.py`, `api.py`) |
| `frontend/` | React + Tailwind UI built with Vite; Leaflet map on OpenStreetMap tiles |
| `data/raw/` | Source data: GTFS stops, station entrances, census tracts, population, income, commute and vehicle tables, LODES commute flows |
| `data/gold/`, `data/processed/` | Commute-based gold labels and validated exports for Snowflake |
| `snowflake/` | SQL setup scripts |
| `tests/` | Unit tests for the backend |

[`backend/README.md`](backend/README.md) documents the data cleaning, scoring,
optimizer, simulator, API endpoints and copilot in detail.

## How the numbers are made

- **Need score (0–100):** weights distance to a station 50%, population 30% and
  lower income 20% (`backend/scoring.py`).
- **Station placement:** greedy, on candidate sites every 200 m along existing
  lines or at tract centers off the network (`backend/optimizer.py`).
- **Walks:** straight-line distance from tract centers at 80 m per minute.
- **Relative cost:** the km of new track a station needs. Infill stops on
  existing track need none; budget tiers allow up to 0, 1.5 and 3 km.
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

## Data sources

Subway stations from MTA GTFS and the station entrance export; census tract
boundaries from NYC Open Data; population, income, commute and vehicle data from
the U.S. Census Bureau ACS; job flows from LEHD LODES 2023. Basemap © OpenStreetMap
contributors. OSM's volunteer tile servers are for development only; switch
`TILE_URL` in `frontend/src/components/MapPanel.jsx` before heavy use.
