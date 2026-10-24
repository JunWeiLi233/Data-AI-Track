"""Snowflake copy of the planning data, profiles for retrieval, and a Cortex Search service.

Run once after `snowflake/copilot_setup.sql` (and again whenever the data or models change):

    python -m backend.snowflake_store

Loads authentication from the repository-root .env through snowflake_client.
The Copilot uses SUBWAY.APP and SUBWAY_WH, independently of the export database
settings. The login must be able to use SUBWAY_APP and SUBWAY_READER; no named
connector profile is required.
"""
import numpy as np
import pandas as pd
import shapely
from shapely.ops import transform

from . import optimizer
from .api import DESERT_KM, SQUARE_FEET_TO_KM2
from .network import haversine_km

SEARCH_SERVICE = "SUBWAY.APP.PLANNING_SEARCH"

METHOD_NOTES = [
    ("Need score", "The need score (priority_score, 0 to 1) ranks census tracts for subway investment. "
     "It is a weighted sum of three min-max scaled components clipped at the 1st and 99th percentiles: "
     "distance to the nearest station (weight 0.5), population (0.3) and income disadvantage, the "
     "reciprocal of median household income (0.2). Missing income gets a neutral 0.5. Tracts with zero "
     "population get no score."),
    ("Station placement", "New stations are placed greedily. Candidate sites lie every 200 m along the "
     "existing lines on land, plus tract centers off the network when allowed. Each step adds the site "
     "with the largest gain. The accessibility goal counts residents newly within an 800 m (10-minute) "
     "walk, weighted by need score; the efficiency goal uses the population-weighted reduction in "
     "distance to the nearest station. Budgets cap new track at 0 km (Low), 1.5 km (Medium) or 3 km "
     "(High); minimum spacing applies to existing and new stations."),
    ("Existing lines", "Existing lines are rebuilt from the GTFS stations and the named lines in the MTA "
     "entrance data: each line is the minimum spanning tree of its stations by straight-line distance, "
     "giving 38 lines drawn station to station rather than along the real track."),
    ("Transit deserts", f"A transit desert is a residential tract more than {DESERT_KM} km (about a "
     "15-minute walk) in straight-line distance from every subway or Staten Island Railway station."),
    ("Limitations", "All distances are straight-line haversine kilometers and walking minutes assume 80 m "
     "per minute. The models measure geographic access only; they do not forecast ridership, travel "
     "time, construction cost or engineering feasibility. Population and income are ACS estimates."),
]


def connect(**overrides):
    """Reuse local login settings while keeping the Copilot planning context."""
    import snowflake.connector
    from .snowflake_client import connection_settings, ENV_FILE
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)

    settings = connection_settings()
    # snowflake_client defaults target TRANSIT_GAP.PUBLIC (export tables), while
    # Copilot SQL/Search and the setup script explicitly target SUBWAY.APP.
    settings.update(database="SUBWAY", schema="APP", warehouse="SUBWAY_WH", role="SUBWAY_APP")
    settings.update(overrides)  # Preserve the isolated SUBWAY_READER connection.

    return snowflake.connector.connect(
        **settings,
        enable_server_session_keep_alive_auto_detection=False,
    )


def _fmt(value, digits=0):
    return "unknown" if pd.isna(value) else f"{value:,.{digits}f}"


def build_tables():
    """Return the DataFrames to load, derived from the same models the app serves."""
    ctx = optimizer.context()
    baseline = ctx.baseline
    stations = ctx.stations

    valid = baseline.geometry_status == "valid"
    tracts = baseline[valid].copy()
    slat = stations.latitude.to_numpy("float64")
    slon = stations.longitude.to_numpy("float64")
    distance = haversine_km(tracts.latitude.to_numpy("float64")[:, None],
                            tracts.longitude.to_numpy("float64")[:, None], slat[None, :], slon[None, :])
    from pyproj import Transformer
    to_feet = Transformer.from_crs("EPSG:4326", "EPSG:2263", always_xy=True).transform
    area = np.array([transform(to_feet, g).area * SQUARE_FEET_TO_KM2 for g in tracts.geometry])
    population = pd.to_numeric(tracts.population).astype("float64")
    tract_table = pd.DataFrame({
        "GEOID": tracts.GEOID.to_numpy(),
        "BOROUGH": tracts.BoroName.to_numpy(),
        "NEIGHBORHOOD": tracts.NTAName.to_numpy(),
        "POPULATION": population.to_numpy(),
        "MEDIAN_INCOME": pd.to_numeric(tracts.median_household_income).astype("float64").to_numpy(),
        "INCOME_TOPCODED": tracts.median_household_income_topcoded.fillna(False).astype(bool).to_numpy(),
        "AREA_KM2": area.round(4),
        "DENSITY_PER_KM2": np.where(area > 0, population / area, np.nan).round(0),
        "LATITUDE": tracts.latitude.to_numpy("float64"),
        "LONGITUDE": tracts.longitude.to_numpy("float64"),
        "NEAREST_STATION": stations.stop_name.to_numpy()[distance.argmin(axis=1)],
        "NEAREST_STATION_KM": tracts.nearest_station_distance_km.round(3).to_numpy(),
        "WALK_MINUTES": (tracts.nearest_station_distance_km / optimizer.WALK_KM_PER_MIN).round(1).to_numpy(),
        "DISTANCE_SCORE": tracts.distance_score.round(4).to_numpy(),
        "POPULATION_SCORE": tracts.population_score.round(4).to_numpy(),
        "INCOME_DISADVANTAGE_SCORE": tracts.income_disadvantage_score.round(4).to_numpy(),
        "NEED_SCORE": tracts.priority_score.round(4).to_numpy(),
        "NEED_RANK": tracts["rank"].astype("float64").to_numpy(),
        "IS_TRANSIT_DESERT": ((population > 0) & (tracts.nearest_station_distance_km > DESERT_KM)).to_numpy(),
        "GEOMETRY_WKT": [shapely.set_precision(g.simplify(0.00005, preserve_topology=True), 1e-5).wkt
                         for g in tracts.geometry],
    })

    line_names, line_routes = {}, {}
    for line in ctx.lines:
        for position in line.stations:
            line_names.setdefault(position, []).append(line.name)
            line_routes.setdefault(position, set()).update(line.routes)
    nearest_tract = haversine_km(slat[:, None], slon[:, None], tract_table.LATITUDE.to_numpy()[None, :],
                                 tract_table.LONGITUDE.to_numpy()[None, :]).argmin(axis=1)
    station_table = pd.DataFrame({
        "STATION_ID": stations.station_ids.map(min).to_numpy(),
        "NAME": stations.stop_name.to_numpy(),
        "LATITUDE": slat, "LONGITUDE": slon,
        "LINES": [", ".join(sorted(line_names.get(i, []))) for i in range(len(stations))],
        "ROUTES": [", ".join(sorted(line_routes.get(i, set()))) for i in range(len(stations))],
        "BOROUGH": tract_table.BOROUGH.to_numpy()[nearest_tract],
        "NEIGHBORHOOD": tract_table.NEIGHBORHOOD.to_numpy()[nearest_tract],
    })

    line_table = pd.DataFrame([{
        "LINE": line.name, "DIVISION": line.division, "ROUTES": ", ".join(line.routes),
        "COLOR": line.color, "STATION_COUNT": len(line.stations),
        "LENGTH_KM": round(float(sum(haversine_km(slat[i], slon[i], slat[j], slon[j]) for i, j in line.edges)), 2),
        "STATIONS": ", ".join(stations.stop_name.iloc[p] for p in line.stations),
    } for line in ctx.lines])
    return tract_table, station_table, line_table


def build_profiles(tracts, stations, lines):
    """Short factual documents for Cortex Search (one per neighborhood, tract, station, line, method)."""
    docs = []
    for title, body in METHOD_NOTES:
        docs.append(("method:" + title.lower().replace(" ", "_"), "method", None, title, body))
    for (borough, name), group in tracts.groupby(["BOROUGH", "NEIGHBORHOOD"]):
        pop = group.POPULATION.fillna(0)
        total = pop.sum()
        weighted = (pop * group.NEAREST_STATION_KM).sum() / total if total else np.nan
        desert = pop[group.IS_TRANSIT_DESERT].sum()
        nearest = group.loc[group.NEAREST_STATION_KM.idxmin()]
        docs.append((f"neighborhood:{borough}:{name}", "neighborhood", borough, f"{name}, {borough}",
                     f"{name} is a neighborhood in {borough} with {len(group)} census tracts and about "
                     f"{_fmt(total)} residents. The population-weighted straight-line distance to the nearest "
                     f"station is {_fmt(weighted, 2)} km (about {_fmt(weighted / optimizer.WALK_KM_PER_MIN, 0)} "
                     f"minutes walking). {_fmt(desert)} residents live in transit-desert tracts. Median household "
                     f"income across its tracts ranges from ${_fmt(group.MEDIAN_INCOME.min())} to "
                     f"${_fmt(group.MEDIAN_INCOME.max())}. The highest tract need score is "
                     f"{_fmt(group.NEED_SCORE.max(), 2)}. The closest station is {nearest.NEAREST_STATION}."))
    for row in tracts.itertuples():
        docs.append((f"tract:{row.GEOID}", "tract", row.BOROUGH, f"Tract {row.GEOID} ({row.NEIGHBORHOOD})",
                     f"Census tract {row.GEOID} in {row.NEIGHBORHOOD}, {row.BOROUGH}: population "
                     f"{_fmt(row.POPULATION)}, median household income ${_fmt(row.MEDIAN_INCOME)}"
                     f"{' or more (top-coded)' if row.INCOME_TOPCODED else ''}, density "
                     f"{_fmt(row.DENSITY_PER_KM2)} residents per km². Nearest station {row.NEAREST_STATION} at "
                     f"{_fmt(row.NEAREST_STATION_KM, 2)} km ({_fmt(row.WALK_MINUTES, 0)} minutes walking). Need score "
                     f"{_fmt(row.NEED_SCORE, 2)}, rank {_fmt(row.NEED_RANK)} of the city's scored tracts. "
                     f"{'It is a transit desert.' if row.IS_TRANSIT_DESERT else ''}"))
    for row in stations.itertuples():
        docs.append((f"station:{row.STATION_ID}", "station", row.BOROUGH, f"{row.NAME} station",
                     f"{row.NAME} station ({row.STATION_ID}) is in {row.NEIGHBORHOOD}, {row.BOROUGH}, on the "
                     f"{row.LINES} line(s), served by routes {row.ROUTES or 'unknown'}."))
    for row in lines.itertuples():
        docs.append((f"line:{row.LINE}", "line", None, f"{row.LINE} line",
                     f"The {row.LINE} line ({row.DIVISION}) is served by routes {row.ROUTES or 'unknown'}, with "
                     f"{row.STATION_COUNT} stations over about {row.LENGTH_KM} km of station-to-station "
                     f"distance: {row.STATIONS}."))
    return pd.DataFrame(docs, columns=["DOC_ID", "KIND", "BOROUGH", "TITLE", "BODY"])


def sync():
    from snowflake.connector.pandas_tools import write_pandas

    tracts, stations, lines = build_tables()
    profiles = build_profiles(tracts, stations, lines)
    con = connect()
    try:
        for name, frame in [("TRACTS_RAW", tracts), ("STATIONS_RAW", stations),
                            ("LINES", lines), ("PROFILES", profiles)]:
            write_pandas(con, frame, name, auto_create_table=True, overwrite=True, quote_identifiers=False)
            print(f"loaded {name}: {len(frame)} rows")
        cur = con.cursor()
        for statement in [
            """create or replace table TRACTS as
               select * exclude GEOMETRY_WKT, try_to_geography(GEOMETRY_WKT, true) as GEOGRAPHY
               from TRACTS_RAW""",
            """create or replace table STATIONS as
               select *, st_makepoint(LONGITUDE, LATITUDE) as LOCATION from STATIONS_RAW""",
            "drop table TRACTS_RAW",
            "drop table STATIONS_RAW",
            """create or replace view NEIGHBORHOODS as
               select BOROUGH, NEIGHBORHOOD, count(*) as TRACTS, sum(POPULATION) as POPULATION,
                      sum(POPULATION * NEAREST_STATION_KM) / nullif(sum(POPULATION), 0) as AVG_NEAREST_STATION_KM,
                      sum(iff(IS_TRANSIT_DESERT, POPULATION, 0)) as DESERT_POPULATION,
                      median(MEDIAN_INCOME) as MEDIAN_OF_TRACT_INCOMES, max(NEED_SCORE) as MAX_NEED_SCORE
               from TRACTS group by BOROUGH, NEIGHBORHOOD""",
            "alter table PROFILES set change_tracking = true",
            f"""create or replace cortex search service {SEARCH_SERVICE}
                on BODY attributes KIND, BOROUGH warehouse = SUBWAY_WH target_lag = '1 day'
                as select DOC_ID, KIND, BOROUGH, TITLE, BODY from PROFILES""",
        ]:
            cur.execute(statement)
        print(cur.execute("select count(*), count_if(GEOGRAPHY is null) from TRACTS").fetchone(),
              "tracts (rows, missing geography)")
    finally:
        con.close()


if __name__ == "__main__":
    sync()
