"""Rough subway travel time from each tract to the two big job centers.

A trip is: walk to one of the few nearest stations, ride the network, then
reach Midtown or Lower Manhattan, whichever is quicker. The Staten Island
Railway reaches Manhattan by the ferry.

The network is the reconstructed lines (backend/network.py) plus one spanning
tree per train route over the stations entrances.csv says it serves. The route
trees carry trains across named-line junctions (the 4 from Jerome Av onto
Lexington Av) and let express routes skip local stops.

These are planning estimates, not schedules. Links are straight
station-to-station segments at an average subway speed with stops included.
Every walk between platforms in one complex costs a fixed transfer penalty.
Changing routes on one platform is free, and waits, service frequency and buses
are not modeled.
"""
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

from .network import MATCH_KM, _spanning_tree, build_lines, haversine_km

SUBWAY_KMH = 27.0
WALK_KMH = 4.8
WALK_DETOUR = 1.3  # street distance over straight-line distance
TRANSFER_KM = 0.3  # separate GTFS parents this close are one complex
TRANSFER_MINUTES = 5.0
NEAREST_STATIONS = 3
JOB_CENTERS = {"midtown": (40.7549, -73.9840), "lower_manhattan": (40.7095, -74.0090)}
JOB_CENTER_RADIUS_KM = 0.5
# St. George terminal to Whitehall St: 25-minute crossing plus boarding.
FERRY = ((40.6437, -74.0736), (40.7013, -74.0130), 30.0)


def _walk_minutes(km):
    return km * WALK_DETOUR / WALK_KMH * 60


def _route_edges(lat, lon, lines, entrances):
    """Spanning-tree links per route over the stations whose entrances list it.

    Lines missing from entrances.csv (Second Avenue, Staten Island Railway)
    add their stations to their own routes.
    """
    named = entrances.drop_duplicates(["Station Latitude", "Station Longitude"])
    elat = pd.to_numeric(named["Station Latitude"], errors="coerce").to_numpy("float64")
    elon = pd.to_numeric(named["Station Longitude"], errors="coerce").to_numpy("float64")
    distance = haversine_km(elat[:, None], elon[:, None], lat[None, :], lon[None, :])
    nearest, nearest_km = distance.argmin(axis=1), distance.min(axis=1)
    served = {}
    route_columns = [c for c in named.columns if c.startswith("Route")]
    for position, km, routes in zip(nearest, nearest_km, named[route_columns].itertuples(index=False)):
        if km <= MATCH_KM:
            for route in {r.strip() for r in routes if isinstance(r, str) and r.strip()}:
                served.setdefault(route, set()).add(int(position))
    for line in lines:
        for route in line.routes:
            served.setdefault(route, set()).update(line.stations)
    edges = []
    for route, group in sorted(served.items()):
        group = sorted(group)
        edges += [(group[i], group[j]) for i, j in _spanning_tree(lat[group], lon[group])]
    return edges


def _graph(lat, lon, lines, entrances):
    distance = haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    rail = {tuple(sorted(edge)) for line in lines for edge in line.edges}
    rail |= {tuple(sorted(edge)) for edge in _route_edges(lat, lon, lines, entrances)}
    rows, cols = (list(side) for side in zip(*sorted(rail)))
    minutes = (distance[rows, cols] / SUBWAY_KMH * 60).tolist()
    i, j = np.nonzero(np.triu(distance <= TRANSFER_KM, k=1))
    rows += i.tolist()
    cols += j.tolist()
    minutes += (_walk_minutes(distance[i, j]) + TRANSFER_MINUTES).tolist()
    (alat, alon), (blat, blon), ferry_minutes = FERRY
    rows.append(int(np.argmin(haversine_km(alat, alon, lat, lon))))
    cols.append(int(np.argmin(haversine_km(blat, blon, lat, lon))))
    minutes.append(ferry_minutes)
    n = len(lat)
    # A pair listed twice (rail and transfer) keeps its quicker link.
    frame = pd.DataFrame({"i": rows, "j": cols, "m": minutes}).groupby(["i", "j"]).m.min().reset_index()
    return coo_matrix((frame.m, (frame.i, frame.j)), shape=(n, n)).tocsr()


def station_minutes_to_jobs(stations, lines, entrances):
    """Minutes from every valid station to the nearer job center's stations."""
    lat = stations.latitude.to_numpy("float64")
    lon = stations.longitude.to_numpy("float64")
    graph = _graph(lat, lon, lines, entrances)
    pieces = connected_components(graph, directed=False)[0]
    if pieces != 1:
        raise ValueError(f"subway network splits into {pieces} disconnected pieces")
    sources = sorted({int(s) for clat, clon in JOB_CENTERS.values()
                      for s in np.flatnonzero(haversine_km(clat, clon, lat, lon) <= JOB_CENTER_RADIUS_KM)})
    return dijkstra(graph, directed=False, indices=sources, min_only=True)


def _access(lat, lon, slat, slon):
    """Nearest station positions and walking minutes to them, for valid points."""
    km = haversine_km(lat[:, None], lon[:, None], slat[None, :], slon[None, :])
    nearest = np.argsort(km, axis=1)[:, :NEAREST_STATIONS]
    return nearest, _walk_minutes(np.take_along_axis(km, nearest, axis=1))


def tract_minutes_matrix(tracts, data):
    """Square array of minutes between valid tracts, in tracts row order.

    Walk to one of the origin's nearest stations, ride, walk from one of the
    destination's; tracts close enough to walk between use the walk if quicker.
    Rows and columns of tracts without a valid point are NaN.
    """
    stations, lines = build_lines(data.stations, data.entrances)
    slat = stations.latitude.to_numpy("float64")
    slon = stations.longitude.to_numpy("float64")
    between = dijkstra(_graph(slat, slon, lines, data.entrances), directed=False)
    lat = pd.to_numeric(tracts.latitude, errors="coerce").to_numpy("float64")
    lon = pd.to_numeric(tracts.longitude, errors="coerce").to_numpy("float64")
    valid = np.flatnonzero(np.isfinite(lat) & np.isfinite(lon))
    nearest, walk = _access(lat[valid], lon[valid], slat, slon)
    minutes = np.full((len(valid), len(valid)), np.inf)
    for a in range(nearest.shape[1]):
        for b in range(nearest.shape[1]):
            minutes = np.minimum(minutes, walk[:, a, None] + between[nearest[:, a][:, None], nearest[None, :, b]]
                                 + walk[None, :, b])
    direct = haversine_km(lat[valid, None], lon[valid, None], lat[None, valid], lon[None, valid])
    result = np.full((len(tracts), len(tracts)), np.nan)
    result[np.ix_(valid, valid)] = np.minimum(minutes, _walk_minutes(direct))
    return result


def transit_minutes_to_jobs(tracts, data):
    """Series of estimated minutes from each tract's interior point; NaN without a valid point."""
    stations, lines = build_lines(data.stations, data.entrances)
    to_jobs = station_minutes_to_jobs(stations, lines, data.entrances)
    slat = stations.latitude.to_numpy("float64")
    slon = stations.longitude.to_numpy("float64")
    lat = pd.to_numeric(tracts.latitude, errors="coerce").to_numpy("float64")
    lon = pd.to_numeric(tracts.longitude, errors="coerce").to_numpy("float64")
    result = np.full(len(tracts), np.nan)
    valid = np.flatnonzero(np.isfinite(lat) & np.isfinite(lon))
    for start in range(0, len(valid), 256):
        positions = valid[start:start + 256]
        nearest, walk = _access(lat[positions], lon[positions], slat, slon)
        result[positions] = (walk + to_jobs[nearest]).min(axis=1)
    return pd.Series(result, index=tracts.index, name="transit_minutes_to_jobs")
