import unittest

import numpy as np
import pandas as pd
from shapely.geometry import box

from backend import api
from backend.clustering import _describe, cluster_summary, cluster_tracts
from backend.data_loader import load_data
from backend.scoring import score_tracts


def synthetic(rows):
    """Scored-tract stand-in: (distance_km, population, income) per row, 1 km-ish boxes in NYC."""
    return pd.DataFrame({
        "GEOID": [f"360050{i:05d}" for i in range(len(rows))],
        "geometry": [box(-73.9 + i * 0.02, 40.7, -73.89 + i * 0.02, 40.71) for i in range(len(rows))],
        "geometry_status": "valid",
        "nearest_station_distance_km": [r[0] for r in rows],
        "population": pd.array([r[1] for r in rows], dtype="Int64"),
        "median_household_income": pd.array([r[2] for r in rows], dtype="Int64"),
    })


class SyntheticClusteringTests(unittest.TestCase):
    def test_separates_obvious_groups_and_orders_by_distance(self):
        near = [(0.2 + i * 0.01, 9000 + i * 10, 30000 + i * 100) for i in range(5)]
        far = [(5.0 + i * 0.1, 900 + i * 10, 150000 + i * 100) for i in range(5)]
        result = cluster_tracts(synthetic(far + near), n_clusters=2)
        self.assertTrue((result.cluster.iloc[5:] == 0).all())
        self.assertTrue((result.cluster.iloc[:5] == 1).all())
        names = result.attrs["clustering"]["names"]
        self.assertIn("near the subway", names[0].lower())
        self.assertIn("far from the subway", names[1].lower())

    def test_excludes_without_imputing(self):
        rows = [(0.2 + i * 0.1, 5000 + i * 100, 50000 + i * 1000) for i in range(6)]
        frame = synthetic(rows + [(1.0, 0, 50000), (1.0, 5000, None), (1.0, 5000, 50000)])
        frame.loc[8, "geometry_status"] = "invalid_wkt"
        result = cluster_tracts(frame, n_clusters=2)
        self.assertTrue(result.cluster.iloc[6:].isna().all())
        self.assertEqual(result.attrs["clustering"]["excluded_tracts"], 3)
        self.assertTrue(pd.isna(result.median_household_income.iloc[7]))

    def test_rejects_bad_input(self):
        frame = synthetic([(1.0, 5000, 50000)] * 3)
        for n in (1, 2.5, True):
            with self.assertRaises(ValueError, msg=n):
                cluster_tracts(frame, n_clusters=n)
        with self.assertRaisesRegex(ValueError, "more than 3"):
            cluster_tracts(frame, n_clusters=3)
        with self.assertRaisesRegex(ValueError, "missing columns"):
            cluster_tracts(frame.drop(columns="geometry"))

    def test_describe_words(self):
        self.assertEqual(_describe((0, 0, 0)), "Typical")
        self.assertEqual(_describe((2, -2, 1)), "Very far from the subway, very spread out, higher income")
        self.assertEqual(_describe((-1, 1, -1)), "Near the subway, dense, lower income")


class RealDataClusteringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scored = score_tracts(load_data())
        cls.clustered = cluster_tracts(cls.scored)

    def test_counts_and_fit_metadata(self):
        info = self.clustered.attrs["clustering"]
        self.assertEqual(info["eligible_tracts"] + info["excluded_tracts"], 2325)
        self.assertEqual(sorted(self.clustered.cluster.dropna().unique()), [0, 1, 2, 3])
        self.assertTrue(0 < info["silhouette"] < 1)
        self.assertEqual(len(set(info["names"])), 4)
        self.assertTrue(self.clustered.loc[self.clustered.population.eq(0).fillna(False), "cluster"].isna().all())

    def test_clusters_ordered_by_station_distance(self):
        distances = [c["nearest_station_distance_km"] for c in self.clustered.attrs["clustering"]["centers"]]
        self.assertEqual(distances, sorted(distances))
        summary = cluster_summary(self.clustered)
        self.assertEqual(summary.tracts.sum(), self.clustered.attrs["clustering"]["eligible_tracts"])

    def test_row_order_does_not_change_result(self):
        shuffled = cluster_tracts(self.scored.sample(frac=1, random_state=7))
        a = self.clustered.set_index("GEOID").cluster
        b = shuffled.set_index("GEOID").cluster.reindex(a.index)
        self.assertTrue(a.equals(b))

    def test_api_payload(self):
        payload = api.tract_clusters()
        self.assertEqual(payload["n_clusters"], 4)
        self.assertEqual(sum(c["tracts"] for c in payload["clusters"]), len(payload["tracts"]))
        self.assertTrue(set(payload["tracts"].values()) <= {0, 1, 2, 3})
        self.assertTrue(all(isinstance(c["name"], str) for c in payload["clusters"]))


if __name__ == "__main__":
    unittest.main()
