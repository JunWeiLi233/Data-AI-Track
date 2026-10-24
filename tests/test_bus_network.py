import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from backend.bus_network import (MAX_BUS_WAIT, WINDOW_MINUTES, _route_stops, bus_access_features, fetch_bus_network,
                                 load_bus, multimodal_minutes_matrix)
from backend.data_loader import load_data
from backend.travel_time import tract_minutes_matrix


def feed(path, service="WKD", start="20261001", end="20261231"):
    """A one-route feed: 3 stops, two trips at 7:00 and 7:30, one at noon (outside the window)."""
    files = {
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        f"{service},1,1,1,1,1,0,0,{start},{end}\nSAT,0,0,0,0,0,1,0,{start},{end}\n",
        "calendar_dates.txt": "service_id,date,exception_type\n",
        "trips.txt": "route_id,service_id,trip_id\nR1,WKD,t1\nR1,WKD,t2\nR1,WKD,t3\nR1,SAT,t4\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n1,A, 40.700000, -73.900000\n2,B,40.701,-73.900\n"
                     "3,C,40.702,-73.900\n9,Unused,40.8,-73.9\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                          + "".join(f"{t},{h}:00:00,{h}:00:00,1,1\n{t},{h}:02:00,{h}:02:00,2,2\n{t},{h}:05:00,{h}:05:00,3,3\n"
                                    for t, h in [("t1", "07"), ("t2", "07"), ("t3", "12"), ("t4", "07")])
                          .replace("t2,07:00", "t2,07:30").replace("t2,07:02", "t2,07:32").replace("t2,07:05", "t2,07:35"),
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)


class FetchTests(unittest.TestCase):
    def test_weekday_morning_links_and_waits(self):
        with tempfile.TemporaryDirectory() as root:
            sources = {}
            for name in ("gtfs_bx", "gtfs_b", "gtfs_m", "gtfs_q", "gtfs_si", "gtfs_busco"):
                sources[name] = Path(root) / f"{name}.zip"
                feed(sources[name])
            fetch_bus_network(root, sources)
            stops, links = load_bus(root)
        self.assertEqual(sorted(stops.stop_id), ["1", "2", "3"])
        hops = links.set_index(["a", "b"])
        # Six feeds repeat the route; noon and Saturday trips are left out.
        self.assertEqual(hops.loc[("1", "2"), "trips"], 12)
        self.assertEqual(hops.loc[("1", "2"), "minutes"], 2.0)
        self.assertEqual(hops.loc[("2", "3"), "minutes"], 3.0)
        waits = _route_stops(links).set_index("stop_id").wait
        self.assertAlmostEqual(waits["1"], min(0.5 * WINDOW_MINUTES / 12, MAX_BUS_WAIT))
        self.assertTrue(np.isinf(waits["3"]))  # the last stop can be left but not boarded


class RealBusNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_data()
        cls.tracts = cls.data.tracts
        cls.minutes = multimodal_minutes_matrix(cls.tracts, cls.data)

    def test_feeds_cover_the_city(self):
        stops, links = load_bus()
        self.assertGreater(len(stops), 10000)
        self.assertGreater(links.route.nunique(), 300)
        self.assertTrue(links.minutes.ge(0).all())

    def test_matrix_shape_and_plausibility(self):
        valid = self.tracts.geometry_status.eq("valid").to_numpy()
        inner = self.minutes[np.ix_(valid, valid)]
        self.assertTrue(np.isfinite(inner).all())
        self.assertTrue(np.isnan(self.minutes[~valid]).all())
        self.assertTrue((np.diag(inner) == 0).all())
        # Adding buses and waits changes trips both ways; the median stays in a believable range.
        self.assertTrue(30 < np.median(inner) < 90)

    def test_buses_help_where_the_subway_is_far(self):
        subway = tract_minutes_matrix(self.tracts, self.data)
        valid = self.tracts.geometry_status.eq("valid").to_numpy()
        far = valid & self.tracts.BoroName.eq("Queens").to_numpy()
        # Bus + subway is quicker on average from Queens, despite waits the subway-only matrix leaves out.
        self.assertLess(np.nanmedian(self.minutes[far][:, valid]), np.nanmedian(subway[far][:, valid]))

    def test_cached(self):
        again = multimodal_minutes_matrix(self.tracts, self.data)
        self.assertTrue(np.array_equal(again, self.minutes, equal_nan=True))
        again[0, 0] = 99
        self.assertNotEqual(multimodal_minutes_matrix(self.tracts, self.data)[0, 0], 99)

    def test_access_features(self):
        access = bus_access_features(self.tracts)
        valid = self.tracts.geometry_status.eq("valid")
        self.assertTrue(access[valid].notna().all().all())
        self.assertGreater(access.bus_trips_per_hour_400m[valid].median(), 20)
        self.assertLess(access.nearest_bus_stop_km[valid].median(), 0.3)


if __name__ == "__main__":
    unittest.main()
