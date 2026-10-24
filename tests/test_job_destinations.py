import gzip
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from backend.data_loader import load_data
from backend.job_destinations import FEATURES, fetch_lodes_flows, job_destination_features, load_flows


def lodes_file(path, rows):
    """rows: (work block, home block, jobs)."""
    text = "w_geocode,h_geocode,S000,SA01\n" + "".join(f"{w},{h},{n},0\n" for w, h, n in rows)
    with gzip.open(path, "wt") as handle:
        handle.write(text)


class FetchTests(unittest.TestCase):
    def test_aggregates_blocks_to_tracts_and_marks_outside_work(self):
        home = "360050001001000"
        with tempfile.TemporaryDirectory() as root:
            sources = {}
            for state, part, rows in [
                ("ny", "main", [("360610001001000", home, 2), ("360610001002000", home, 3),
                                ("361190001001000", home, 4), ("360610001001000", "360010001001000", 9)]),
                ("nj", "aux", [("340030001001000", home, 5)]),
                ("ct", "aux", [])]:
                sources[(state, part)] = Path(root) / f"{state}.csv.gz"
                lodes_file(sources[(state, part)], rows)
            flows = load_flows(fetch_lodes_flows(root, sources))
        self.assertEqual(flows.set_index("work").jobs.to_dict(),
                         {"36061000100": 5, "outside_34": 5, "outside_36": 4})
        self.assertEqual(set(flows.home), {"36005000100"})


class RealJobDestinationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_data()
        cls.features = job_destination_features(cls.data.tracts, cls.data)

    def test_shapes_and_ranges(self):
        self.assertEqual(list(self.features.columns), list(FEATURES))
        self.assertTrue(self.features.index.equals(self.data.tracts.index))
        shares = self.features[[c for c in FEATURES if c.startswith("job_share")]].stack()
        self.assertTrue(shares.between(0, 1).all())
        over = self.features.dropna()
        self.assertTrue((over.job_share_over_60 <= over.job_share_over_45).all())
        self.assertGreater(self.features.job_minutes_mean.notna().mean(), 0.95)

    def test_plausible_geography(self):
        borough = self.data.tracts.BoroName
        manhattan = self.features.job_share_manhattan.groupby(borough).median()
        self.assertGreater(manhattan["Manhattan"], manhattan["Staten Island"])
        minutes = self.features.job_minutes_mean.groupby(borough).median()
        self.assertLess(minutes["Manhattan"], minutes["Queens"])
        self.assertTrue(np.isfinite(minutes).all())


if __name__ == "__main__":
    unittest.main()
