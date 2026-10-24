# NYC Subway Expansion Planner

This is a tool we built to look at where New York City could add subway stations. It scores every census tract by how far it is from a station, how many people live there and their household income. Then it suggests new station sites along the existing lines and shows how many residents each one would bring within a 10 minute walk.

The app has a map, a few controls for the goal, number of stations, spacing and budget, and a chat panel. The chat panel answers planning questions using data stored in Snowflake and the Cortex AI model.

## What it does

- Loads the MTA station and entrance exports, NYC census tract boundaries and ACS population and income data, and checks them for problems.
- Scores each residential tract from 0 to 100 so the most underserved places come first.
- Rebuilds the existing subway lines from the station data so new stations can be placed on them.
- Picks new station sites one at a time, each time choosing the site that helps the most people.
- Simulates the network with the new stations to measure the change in walking distance.
- Groups tracts into neighborhood types with k-means.
- Checks the scores against a separate outcome, the share of workers with 60+ minute commutes.
- Answers questions in a chat panel through Snowflake Cortex, using only fixed SQL templates written in the backend.

## Running it

I used Python 3.10+ and Node 20+. Run these from the repository root.

```sh
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..
.venv/bin/python -m backend.server --port 8000
```

Then open http://127.0.0.1:8000.

For live reload while editing the frontend, keep the server running and start Vite in a second terminal.

```sh
cd frontend && npm run dev
```

That runs on http://localhost:5173 and sends `/api` requests to port 8000.

The map and station suggestions work without Snowflake. The chat panel needs a Snowflake login. Copy `.env.example` to `.env` and fill in your account details. The `.env` file is ignored by git.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The Snowflake tests are mocked by default. Set `SNOWFLAKE_LIVE_TESTS=1` to run them against a real account.

## Layout

| Path | What is in it |
|---|---|
| `backend/` | Data loading, scoring, line rebuilding, station placement, simulation, clustering, the Snowflake copilot and the HTTP server |
| `frontend/` | React, Tailwind and Leaflet app built with Vite |
| `data/raw/` | Source CSVs (MTA, NYC Open Data, Census ACS, LEHD LODES) |
| `data/gold/` | Commute based labels used to check the scores |
| `data/processed/` | Exported tables that were uploaded to Snowflake |
| `snowflake/` | SQL setup scripts |
| `tests/` | Unit tests |

## More detail

- [backend/README.md](backend/README.md) covers the data cleaning, scoring formula, station placement, clustering, gold labels, simulator and the chat copilot.
- [backend/EXPORTS.md](backend/EXPORTS.md) covers how the processed tables are prepared for Snowflake.
- [backend/CORTEX.md](backend/CORTEX.md) covers the tract explanation endpoint.
- [backend/EXPLANATIONS.md](backend/EXPLANATIONS.md) covers the tract explanation helper.

## Limits

Distances are straight lines, so rivers, street layout and entrances are ignored. Walking speed is assumed to be 80 m per minute. The suggestions are a screening aid. They do not forecast ridership, travel time or construction cost, and real decisions would need engineering review and community input. The dollar ranges shown in the app are rough, based on published per km costs of past NYC projects.

## Data sources

- MTA subway stations (GTFS) and station entrances
- NYC Open Data census tract boundaries
- U.S. Census Bureau ACS 5-year tables for population, income, commute time and vehicles
- LEHD LODES 2023 home to work flows
- OpenStreetMap map tiles
