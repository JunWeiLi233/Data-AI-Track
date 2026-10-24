import unittest

import numpy as np
import pandas as pd

from backend.supervised import (_precision_at_top, cluster_gold_summary, evaluate, feature_matrix,
                                training_frame)


def synthetic(n=200, seed=0):
    """Training-frame stand-in where far tracts are underserved more often."""
    rng = np.random.default_rng(seed)
    distance = rng.uniform(0.1, 5, n)
    boroughs = np.array(["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"])
    return pd.DataFrame({
        "GEOID": [f"360050{i:05d}" for i in range(n)],
        "nearest_station_distance_km": distance,
        "population_density_km2": rng.uniform(1000, 30000, n),
        "median_household_income": rng.uniform(20000, 200000, n),
        "transit_minutes_to_jobs": 10 + distance * 15 + rng.uniform(0, 5, n),
        "BoroName": boroughs[np.arange(n) % 5],
        "latitude": 40.7 + rng.uniform(0, 0.2, n), "longitude": -73.9 + rng.uniform(0, 0.2, n),
        "priority_score": distance / 5,
        "cluster": pd.array((distance > 2.5).astype(int), dtype="Int64"),
        "cluster_name": np.where(distance > 2.5, "Far", "Near"),
        "underserved": pd.array((distance + rng.normal(0, 0.5, n) > 3.5).astype(int), dtype="Int64"),
        "long_commute_share": distance / 10, "car_free_share": 0.5,
        "label_confident": np.arange(n) % 2 == 0,
        "CDTA2020": [f"BX{i % 10:02d}" for i in range(n)],
    })


class SyntheticSupervisedTests(unittest.TestCase):
    def test_learnable_signal_beats_random(self):
        result = evaluate(synthetic()).set_index("method")
        models = {f"{m}:{f}" for m in ("logistic_regression", "gradient_boosting")
                  for f in ("clustering_features", "plus_travel_time", "plus_location")}
        self.assertEqual(set(result.index), models | {"cluster", "priority_score", "distance_only",
                                                      "transit_minutes_only"})
        self.assertGreater(result.loc["logistic_regression:plus_travel_time", "roc_auc"], 0.85)
        self.assertGreater(result.loc["distance_only", "roc_auc"], 0.85)
        self.assertTrue(result.roc_auc_confident.between(0, 1).all())

    def test_feature_matrix(self):
        frame = synthetic(10)
        self.assertEqual(feature_matrix(frame, "clustering_features").shape, (10, 3))
        x = feature_matrix(frame, "plus_location")
        self.assertEqual(x.shape, (10, 4 + 5 + 2))
        self.assertTrue((x[:, 4:9].sum(axis=1) == 1).all())
        self.assertAlmostEqual(x[0, 3], np.log(frame.transit_minutes_to_jobs.iloc[0]))

    def test_deterministic(self):
        sets = ("plus_travel_time",)
        first, second = evaluate(synthetic(), feature_sets=sets), evaluate(synthetic(), feature_sets=sets)
        pd.testing.assert_frame_equal(first, second)

    def test_precision_at_top(self):
        y = np.array([1, 0, 0, 1, 0, 0, 0, 0])
        self.assertEqual(_precision_at_top(y, np.array([8, 7, 6, 5, 4, 3, 2, 1.])), 0.5)
        self.assertEqual(_precision_at_top(y, np.array([8, 1, 1, 7, 1, 1, 1, 1.])), 1.0)


class RealSupervisedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = training_frame()
        # One feature set and three folds keep this test quick; main() runs them all.
        cls.result = evaluate(cls.frame, n_splits=3, feature_sets=("plus_travel_time",))

    def test_frame_and_metrics(self):
        self.assertFalse(self.frame.GEOID.duplicated().any())
        self.assertTrue(self.frame.underserved.notna().all())
        self.assertEqual(len(self.result), 6)
        self.assertTrue(self.frame.transit_minutes_to_jobs.gt(0).all())
        # Travel time carries real signal on the confident labels.
        result = self.result.set_index("method")
        self.assertGreater(result.loc["transit_minutes_only", "roc_auc_confident"], 0.75)

    def test_cluster_summary_covers_frame(self):
        summary = cluster_gold_summary(self.frame)
        self.assertEqual(summary.tracts.sum(), len(self.frame))
        self.assertEqual(summary.cluster.tolist(), [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main()
