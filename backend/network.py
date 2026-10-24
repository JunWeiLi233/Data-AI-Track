"""Existing subway line geometry reconstructed from the station and entrance exports.

Neither export contains track shapes, so each named line (entrances.csv
Division + Line) is drawn as the minimum spanning tree of its stations by
haversine distance. Subway lines are paths with a few branches, so the tree
links consecutive stations; edges are straight segments, not track alignments.
"""
from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .scoring import EARTH_RADIUS_KM

# Entrance stations sit on their GTFS parent coordinates (median 0 m, max ~0.46 km).
MATCH_KM = 0.6
# Stations missing from entrances.csv join a line holding a nearby same-prefix stop,
# or failing that a line with a stop in the same complex.
PREFIX_JOIN_KM = 1.5
COMPLEX_KM = 0.3
# A route belongs to a line when it serves this share of the line's stations;
# entrance rows also list transfer routes at station complexes.
ROUTE_SHARE = 0.4
# Longest believable station-to-station link (Howard Beach to Broad Channel is 5.9 km).
MAX_EDGE_KM = 6.0
# GTFS prefixes absent from entrances.csv (Staten Island Railway, Second Avenue Subway).
UNMATCHED_LINES = {"S": ("SIR", "Staten Island Railway", ["SIR"]),
                   "Q": ("IND", "Second Avenue", ["Q"])}
ROUTE_COLORS = {**dict.fromkeys("123", "#EE352E"), **dict.fromkeys("456", "#00933C"),
                "7": "#B933AD", **dict.fromkeys("ACE", "#0039A6"), **dict.fromkeys("BDFM", "#FF6319"),
                "G": "#6CBE45", **dict.fromkeys("JZ", "#996633"), "L": "#A7A9AC",
                **dict.fromkeys("NQRW", "#FCCC0A"), **dict.fromkeys(["S", "GS", "FS", "H"], "#808183"),
                "SIR": "#0039A6"}


@dataclass
class Line:
    division: str
    name: str
    routes: list
    color: str
    stations: list  # positions in the valid-station frame
    edges: list     # (i, j) station position pairs


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype="float64")) for v in (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _spanning_tree(lat, lon):
    """Kruskal over all pairs of one line's stations, skipping implausible links."""
    n = len(lat)
    dist = haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    edges = []
    for i, j in sorted(((i, j) for i in range(n) for j in range(i + 1, n)), key=lambda e: dist[e]):
        if dist[i, j] > MAX_EDGE_KM:
            break
        a, b = root(i), root(j)
        if a != b:
            parent[a] = b
            edges.append((i, j))
    return edges


def _route_counts(stations):
    """Stations served per route, from one entrance row per station."""
    counts = Counter()
    for _, row in stations.iterrows():
        counts.update({row[c].strip() for c in stations.columns if c.startswith("Route") and row[c].strip()})
    return counts


def _routes(stations, systemwide):
    """A line's routes, most common first; ties favor routes rarer across the system
    (the 42nd St Shuttle's GS before the 7 that also stops at both its stations)."""
    counts = _route_counts(stations)
    ranked = sorted(counts, key=lambda route: (-counts[route], systemwide[route], route))
    return [route for route in ranked if counts[route] >= ROUTE_SHARE * len(stations)]


def build_lines(stations, entrances):
    """Return (valid station frame, [Line]) from load_data() stations and entrances."""
    stations = stations[stations.coordinates_valid].reset_index(drop=True)
    lat = stations.latitude.to_numpy("float64")
    lon = stations.longitude.to_numpy("float64")
    keys = stations.station_ids.map(min).tolist()

    named = entrances.drop_duplicates(["Division", "Line", "Station Latitude", "Station Longitude"])
    elat = pd.to_numeric(named["Station Latitude"], errors="coerce").to_numpy("float64")
    elon = pd.to_numeric(named["Station Longitude"], errors="coerce").to_numpy("float64")
    distance = haversine_km(elat[:, None], elon[:, None], lat[None, :], lon[None, :])
    nearest, nearest_km = distance.argmin(axis=1), distance.min(axis=1)

    members = {}
    for (division, name), position, km in zip(zip(named.Division, named.Line), nearest, nearest_km):
        if km <= MATCH_KM:
            members.setdefault((division, name), set()).add(int(position))
    assigned = set().union(*members.values())
    for position in sorted(set(range(len(stations))) - assigned):
        prefix = keys[position][0]
        options = [(float(haversine_km(lat[position], lon[position], lat[other], lon[other])),
                    keys[other][0] == prefix, line)
                   for line, group in members.items() for other in group]
        km, line = min(((km, line) for km, same, line in options if same),
                       default=(np.inf, None), key=lambda o: o[0])
        if km > PREFIX_JOIN_KM:
            km, line = min(((km, line) for km, _, line in options), key=lambda o: o[0])
            if km > COMPLEX_KM:
                line = UNMATCHED_LINES.get(prefix, ("Other", f"Line {prefix}", []))[:2]
        members.setdefault(line, set()).add(position)

    systemwide = _route_counts(named)
    lines = []
    for (division, name), group in sorted(members.items()):
        group = sorted(group, key=lambda p: keys[p])
        rows = named[(named.Division == division) & (named.Line == name)]
        routes = _routes(rows, systemwide) if len(rows) else next(
            (r for d, n, r in UNMATCHED_LINES.values() if (d, n) == (division, name)), [])
        color = next((ROUTE_COLORS[r] for r in routes if r in ROUTE_COLORS), "#5B6472")
        local = _spanning_tree(lat[group], lon[group])
        lines.append(Line(division, name, routes, color, group, [(group[i], group[j]) for i, j in local]))
    return stations, lines


def line_points(stations, lines, step_km=0.2):
    """Candidate sites spaced along every line edge, excluding the stations themselves."""
    lat = stations.latitude.to_numpy("float64")
    lon = stations.longitude.to_numpy("float64")
    names = stations.stop_name.tolist()
    rows = []
    for line in lines:
        for i, j in line.edges:
            length = float(haversine_km(lat[i], lon[i], lat[j], lon[j]))
            steps = int(length // step_km)
            for k in range(1, steps + 1):
                t = k / (steps + 1)
                rows.append({"latitude": lat[i] + t * (lat[j] - lat[i]),
                             "longitude": lon[i] + t * (lon[j] - lon[i]),
                             "line": line.name, "routes": line.routes,
                             "between": (names[i], names[j])})
    return pd.DataFrame(rows)


def lines_geojson(stations, lines):
    lat = stations.latitude.to_numpy("float64")
    lon = stations.longitude.to_numpy("float64")
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "properties": {"division": line.division, "line": line.name,
                       "routes": line.routes, "color": line.color},
        "geometry": {"type": "MultiLineString", "coordinates": [
            [[round(lon[i], 6), round(lat[i], 6)], [round(lon[j], 6), round(lat[j], 6)]]
            for i, j in line.edges]},
    } for line in lines]}
