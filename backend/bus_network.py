"""MTA bus service and door-to-door bus + subway travel time between tracts.

Source: the MTA's bus GTFS feeds (five boroughs plus MTA Bus Company), reduced
to one representative weekday morning, Tuesday 2026-10-20 from 6 to 10 am:
- data/raw/bus_stops.csv.gz: every stop served in that window;
- data/raw/bus_links.csv.gz: per route, each pair of consecutive stops with
  the median scheduled minutes between them and the number of trips.

A trip walks to a subway station (then waits SUBWAY_WAIT) or a bus stop (then
waits half the route's headway at that stop, at most MAX_BUS_WAIT), rides,
transfers on foot between nearby stops and stations, and walks to the
destination tract. Waits are charged on every boarding, so changing buses
costs a wait; the subway part is backend/travel_time.py's network. Schedules
are planned times, not real-time, and the LIRR, Metro-North and ferries other
than Staten Island's are not modeled.

Refresh the source files, from the repository root (reads ~55 MB, keeps ~260 KB):
    python -m backend.bus_network --download
"""
import argparse
import io
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra

from .data_loader import DEFAULT_DATA_DIR
from .network import build_lines, haversine_km
from .travel_time import NEAREST_STATIONS, _graph, _walk_minutes

FEED_URL = "https://rrgtfsfeeds.s3.amazonaws.com/{feed}.zip"
FEEDS = ("gtfs_bx", "gtfs_b", "gtfs_m", "gtfs_q", "gtfs_si", "gtfs_busco")
SERVICE_DATE = 20261020  # a Tuesday inside every feed's calendar
WINDOW = (6 * 3600, 10 * 3600)
WINDOW_MINUTES = (WINDOW[1] - WINDOW[0]) / 60
STOPS_FILE, LINKS_FILE = "bus_stops.csv.gz", "bus_links.csv.gz"
SUBWAY_WAIT = 4.0
MAX_BUS_WAIT = 15.0
MIN_HOP_MINUTES = 0.25  # schedules round some hops to zero
STOP_TRANSFER_KM = 0.25
STATION_TRANSFER_KM = 0.3
TRACT_STOP_KM = 0.6  # plus the NEAREST_STOPS nearest, for tracts with none this close
NEAREST_STOPS = 3
ACCESS_KM = 0.4
BATCH = 250
_cache = {}  # one matrix, keyed on its inputs; the Dijkstra runs take ~15 s


def _active_services(feed):
    calendar = pd.read_csv(feed.open("calendar.txt"), dtype=str)
    dates = pd.read_csv(feed.open("calendar_dates.txt"), dtype=str)
    weekday = calendar[(calendar.tuesday == "1") & (calendar.start_date.astype(int) <= SERVICE_DATE)
                       & (calendar.end_date.astype(int) >= SERVICE_DATE)]
    today = dates[dates.date.astype(int) == SERVICE_DATE]
    return ((set(weekday.service_id) | set(today[today.exception_type == "1"].service_id))
            - set(today[today.exception_type == "2"].service_id))


def _feed_links(feed):
    """Consecutive-stop hops departing inside WINDOW on the service date."""
    trips = pd.read_csv(feed.open("trips.txt"), dtype=str, usecols=["route_id", "service_id", "trip_id"])
    trips = trips[trips.service_id.isin(_active_services(feed))]
    times = pd.read_csv(feed.open("stop_times.txt"), dtype=str,
                        usecols=["trip_id", "departure_time", "stop_id", "stop_sequence"])
    times = times[times.trip_id.isin(trips.trip_id)]
    clock = times.departure_time.str.split(":", expand=True).astype(int)
    times = times.assign(seconds=clock[0] * 3600 + clock[1] * 60 + clock[2],
                         sequence=times.stop_sequence.astype(int)).merge(trips[["trip_id", "route_id"]], on="trip_id")
    times = times.sort_values(["trip_id", "sequence"])
    following = times.groupby("trip_id")[["stop_id", "seconds"]].shift(-1)
    hops = pd.DataFrame({"route": times.route_id, "a": times.stop_id, "b": following.stop_id,
                         "seconds": times.seconds, "minutes": (following.seconds - times.seconds) / 60})
    return hops[hops.b.notna() & hops.seconds.between(*WINDOW)]


def fetch_bus_network(data_dir=DEFAULT_DATA_DIR, sources=None):
    """Reduce the GTFS feeds to STOPS_FILE and LINKS_FILE in data_dir.

    sources maps feed name to a local .zip path; missing ones download.
    """
    sources = sources or {}
    hops, stops = [], []
    for name in FEEDS:
        source = sources.get(name) or FEED_URL.format(feed=name)
        if str(source).startswith("https://"):
            with urllib.request.urlopen(source) as response:
                feed = zipfile.ZipFile(io.BytesIO(response.read()))
        else:
            feed = zipfile.ZipFile(source)
        with feed:
            hops.append(_feed_links(feed))
            stops.append(pd.read_csv(feed.open("stops.txt"), dtype=str, usecols=["stop_id", "stop_lat", "stop_lon"]))
    links = (pd.concat(hops).groupby(["route", "a", "b"])
             .agg(minutes=("minutes", "median"), trips=("minutes", "size")).reset_index())
    stops = pd.concat(stops).drop_duplicates("stop_id")
    stops = pd.DataFrame({"stop_id": stops.stop_id, "latitude": stops.stop_lat.str.strip().astype(float),
                          "longitude": stops.stop_lon.str.strip().astype(float)})
    stops = stops[stops.stop_id.isin(set(links.a) | set(links.b))]
    root = Path(data_dir)
    stops.to_csv(root / STOPS_FILE, index=False)
    links.to_csv(root / LINKS_FILE, index=False, float_format="%.3f")
    return root / STOPS_FILE, root / LINKS_FILE


def load_bus(data_dir=DEFAULT_DATA_DIR):
    """(stops, links) DataFrames, checked for the columns and joins the graph needs."""
    root = Path(data_dir)
    stops = pd.read_csv(root / STOPS_FILE, dtype={"stop_id": str})
    links = pd.read_csv(root / LINKS_FILE, dtype={"a": str, "b": str, "route": str})
    if stops.stop_id.duplicated().any():
        raise ValueError(f"{STOPS_FILE}: duplicate stop_id")
    if not (set(links.a) | set(links.b)) <= set(stops.stop_id):
        raise ValueError(f"{LINKS_FILE}: links to stops missing from {STOPS_FILE}")
    return stops, links


def _route_stops(links):
    """One node per route at each stop it serves, with that boarding's wait in minutes."""
    nodes = (pd.concat([links[["route", "a"]].rename(columns={"a": "stop_id"}),
                        links[["route", "b"]].rename(columns={"b": "stop_id"})])
             .drop_duplicates().reset_index(drop=True))
    departures = links.groupby(["route", "a"]).trips.sum()
    trips = departures.reindex(pd.MultiIndex.from_frame(nodes)).fillna(0).to_numpy()
    # A route's last stop has no departures, so it can be left but not boarded.
    nodes["wait"] = np.where(trips > 0, np.minimum(0.5 * WINDOW_MINUTES / np.maximum(trips, 1), MAX_BUS_WAIT), np.inf)
    return nodes


def multimodal_minutes_matrix(tracts, data, bus=None):
    """Square array of bus + subway minutes between valid tracts, in tracts row order.

    Rows and columns of tracts without a valid point are NaN; tracts close
    enough to walk between use the walk if quicker.
    """
    stops, links = load_bus() if bus is None else bus
    key = (tuple(tracts.GEOID), pd.util.hash_pandas_object(tracts[["latitude", "longitude"]], index=False).sum(),
           pd.util.hash_pandas_object(data.stations[["latitude", "longitude"]], index=False).sum(),
           pd.util.hash_pandas_object(links, index=False).sum())
    if key not in _cache:
        _cache.clear()
        _cache[key] = _multimodal_minutes(tracts, data, stops, links)
    return _cache[key].copy()


def _multimodal_minutes(tracts, data, stops, links):
    stations, lines = build_lines(data.stations, data.entrances)
    slat, slon = stations.latitude.to_numpy("float64"), stations.longitude.to_numpy("float64")
    blat, blon = stops.latitude.to_numpy("float64"), stops.longitude.to_numpy("float64")
    lat = pd.to_numeric(tracts.latitude, errors="coerce").to_numpy("float64")
    lon = pd.to_numeric(tracts.longitude, errors="coerce").to_numpy("float64")
    valid = np.flatnonzero(np.isfinite(lat) & np.isfinite(lon))
    tlat, tlon = lat[valid], lon[valid]
    route_stops = _route_stops(links)
    n_stations, n_stops, n_route_stops, n_tracts = len(stations), len(stops), len(route_stops), len(valid)
    # Node blocks: stations, street-level stops, route stops, tract origins, tract destinations.
    stop0 = n_stations
    route0 = stop0 + n_stops
    origin0 = route0 + n_route_stops
    destination0 = origin0 + n_tracts
    rows, cols, minutes = [], [], []

    def add(a, b, m):
        rows.append(np.asarray(a))
        cols.append(np.asarray(b))
        minutes.append(np.asarray(m, dtype="float64"))

    subway = _graph(slat, slon, lines, data.entrances).tocoo()
    add(subway.row, subway.col, subway.data)
    add(subway.col, subway.row, subway.data)
    stop_position = pd.Series(np.arange(n_stops), index=stops.stop_id.to_numpy())
    node = pd.Series(np.arange(n_route_stops), index=pd.MultiIndex.from_frame(route_stops[["route", "stop_id"]]))
    a = node[pd.MultiIndex.from_arrays([links.route, links.a])].to_numpy()
    b = node[pd.MultiIndex.from_arrays([links.route, links.b])].to_numpy()
    add(route0 + a, route0 + b, np.maximum(links.minutes.to_numpy("float64"), MIN_HOP_MINUTES))
    street = stop_position[route_stops.stop_id].to_numpy()
    boardable = np.isfinite(route_stops.wait.to_numpy())
    add(stop0 + street[boardable], route0 + np.flatnonzero(boardable), route_stops.wait.to_numpy()[boardable])
    add(route0 + np.arange(n_route_stops), stop0 + street, np.zeros(n_route_stops))
    between = haversine_km(blat[:, None], blon[:, None], blat[None, :], blon[None, :])
    i, j = np.nonzero((between <= STOP_TRANSFER_KM) & (between > 0))
    add(stop0 + i, stop0 + j, _walk_minutes(between[i, j]))
    to_stops = haversine_km(slat[:, None], slon[:, None], blat[None, :], blon[None, :])
    i, j = np.nonzero(to_stops <= STATION_TRANSFER_KM)
    add(i, stop0 + j, _walk_minutes(to_stops[i, j]))
    add(stop0 + j, i, _walk_minutes(to_stops[i, j]) + SUBWAY_WAIT)
    to_stations = haversine_km(tlat[:, None], tlon[:, None], slat[None, :], slon[None, :])
    nearest = np.argsort(to_stations, axis=1)[:, :NEAREST_STATIONS]
    walk = _walk_minutes(np.take_along_axis(to_stations, nearest, axis=1)).ravel()
    tract = np.repeat(np.arange(n_tracts), NEAREST_STATIONS)
    add(origin0 + tract, nearest.ravel(), walk + SUBWAY_WAIT)
    add(nearest.ravel(), destination0 + tract, walk)
    to_bus = haversine_km(tlat[:, None], tlon[:, None], blat[None, :], blon[None, :])
    close = to_bus <= TRACT_STOP_KM
    close[np.repeat(np.arange(n_tracts), NEAREST_STOPS), np.argsort(to_bus, axis=1)[:, :NEAREST_STOPS].ravel()] = True
    i, j = np.nonzero(close)
    add(origin0 + i, stop0 + j, _walk_minutes(to_bus[i, j]))
    add(stop0 + j, destination0 + i, _walk_minutes(to_bus[i, j]))
    size = destination0 + n_tracts
    # Converting to csr would sum a link listed twice; a rider takes the quicker one.
    edges = (pd.DataFrame({"i": np.concatenate(rows), "j": np.concatenate(cols), "m": np.concatenate(minutes)})
             .groupby(["i", "j"]).m.min().reset_index())
    graph = coo_matrix((edges.m, (edges.i, edges.j)), shape=(size, size)).tocsr()
    result = np.empty((n_tracts, n_tracts))
    for start in range(0, n_tracts, BATCH):
        sources = origin0 + np.arange(start, min(n_tracts, start + BATCH))
        result[start:start + BATCH] = dijkstra(graph, directed=True, indices=sources)[:, destination0:]
    direct = haversine_km(tlat[:, None], tlon[:, None], tlat[None, :], tlon[None, :])
    full = np.full((len(tracts), len(tracts)), np.nan)
    full[np.ix_(valid, valid)] = np.minimum(result, _walk_minutes(direct))
    return full


def bus_access_features(tracts, bus=None):
    """Per tract: scheduled morning buses per hour and routes within ACCESS_KM, nearest stop km."""
    stops, links = load_bus() if bus is None else bus
    lat = pd.to_numeric(tracts.latitude, errors="coerce").to_numpy("float64")
    lon = pd.to_numeric(tracts.longitude, errors="coerce").to_numpy("float64")
    km = haversine_km(lat[:, None], lon[:, None], stops.latitude.to_numpy()[None, :], stops.longitude.to_numpy()[None, :])
    per_hour = (links.groupby("a").trips.sum() / (WINDOW_MINUTES / 60)).reindex(stops.stop_id).fillna(0).to_numpy()
    routes = links.groupby("a").route.agg(set).reindex(stops.stop_id).to_numpy()
    within = km <= ACCESS_KM
    result = pd.DataFrame({
        "bus_trips_per_hour_400m": within @ per_hour,
        "bus_routes_400m": [len(set().union(*(r for r in routes[row] if isinstance(r, set)))) for row in within],
        "nearest_bus_stop_km": km.min(axis=1),
    }, index=tracts.index)
    result[~np.isfinite(lat) | ~np.isfinite(lon)] = np.nan
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--download", action="store_true", help="rebuild the stop and link files from the GTFS feeds")
    args = parser.parse_args()
    if args.download:
        print("wrote", *fetch_bus_network())
    stops, links = load_bus()
    print(f"{len(stops)} stops, {len(links)} route-stop links, {links.route.nunique()} routes")


if __name__ == "__main__":
    main()
