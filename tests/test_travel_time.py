import unittest

import numpy as np

from backend.data_loader import load_data
from backend.network import build_lines
from backend.travel_time import _walk_minutes, station_minutes_to_jobs, transit_minutes_to_jobs


class TravelTimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_data()
        cls.stations, cls.lines = build_lines(cls.data.stations, cls.data.entrances)
        cls.minutes = station_minutes_to_jobs(cls.stations, cls.lines, cls.data.entrances)

    def station(self, name):
        return self.minutes[np.flatnonzero(self.stations.stop_name == name)].min()

    def test_every_station_reaches_a_job_center(self):
        self.assertTrue(np.isfinite(self.minutes).all())
        self.assertEqual(self.station("Times Sq - 42 St"), 0)

    def test_plausible_trips(self):
        # Express routes cross named-line junctions (4 train via Lexington Av).
        self.assertLess(self.station("149 St - Grand Concourse"), 30)
        self.assertLess(self.station("Flushing - Main St"), 45)
        # Staten Island needs the ferry; the far end of the SIR is the slowest.
        self.assertGreater(self.station("Tottenville"), 60)
        self.assertLess(self.station("Atlantic Av - Barclays Ctr"), self.station("Canarsie - Rockaway Pkwy"))

    def test_tracts(self):
        minutes = transit_minutes_to_jobs(self.data.tracts, self.data)
        valid = self.data.tracts.geometry_status.eq("valid")
        self.assertTrue(minutes[valid].gt(0).all())
        self.assertTrue(minutes[~valid].isna().all())
        self.assertAlmostEqual(_walk_minutes(1.0), 1.3 / 4.8 * 60)


if __name__ == "__main__":
    unittest.main()
