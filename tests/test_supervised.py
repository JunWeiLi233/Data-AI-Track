import unittest

import numpy as np
import pandas as pd

from sklearn.metrics import roc_auc_score

from backend.supervised import (_precision_at_top, cluster_gold_summary, evaluate, feature_matrix, fit_model,
                                grouped_folds, out_of_fold_scores, training_frame)


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
        "job_minutes_mean": 20 + distance * 10 + rng.uniform(0, 5, n),
        "job_share_over_45": np.clip(distance / 5 + rng.uniform(-0.1, 0.1, n), 0, 1),
        "job_share_over_60": np.clip(distance / 7, 0, 1), "job_share_manhattan": rng.uniform(0, 1, n),
        "job_share_outside_nyc": rng.uniform(0, 0.3, n),
        "any_mode_job_minutes_mean": 18 + distance * 9 + rng.uniform(0, 5, n),
        "any_mode_job_share_over_45": np.clip(distance / 6 + rng.uniform(-0.1, 0.1, n), 0, 1),
        "any_mode_job_share_over_60": np.clip(distance / 8, 0, 1),
        "bus_trips_per_hour_400m": rng.uniform(0, 300, n), "bus_routes_400m": rng.integers(0, 20, n),
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
        models = {f"{m}:{f}" for m in ("logistic_regression", "gradient_boosting", "ensemble")
                  for f in ("clustering_features", "plus_travel_time", "plus_job_destinations", "plus_bus",
                            "plus_location")}
        self.assertEqual(set(result.index), models | {"cluster", "priority_score", "distance_only",
                                                      "transit_minutes_only", "job_share_over_45_only"})
        self.assertGreater(result.loc["logistic_regression:plus_travel_time", "roc_auc"], 0.85)
        self.assertGreater(result.loc["distance_only", "roc_auc"], 0.85)
        self.assertTrue(result.roc_auc_confident.between(0, 1).all())

    def test_feature_matrix(self):
        frame = synthetic(10)
        self.assertEqual(feature_matrix(frame, "clustering_features").shape, (10, 3))
        self.assertEqual(feature_matrix(frame, "plus_job_destinations").shape, (10, 9))
        self.assertEqual(feature_matrix(frame, "plus_bus").shape, (10, 14))
        x = feature_matrix(frame, "plus_location")
        self.assertEqual(x.shape, (10, 9 + 5 + 2))
        self.assertTrue((x[:, 9:14].sum(axis=1) == 1).all())
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
        cls.result = evaluate(cls.frame, n_splits=3, feature_sets=("plus_job_destinations",))

    def test_frame_and_metrics(self):
        self.assertFalse(self.frame.GEOID.duplicated().any())
        self.assertTrue(self.frame.underserved.notna().all())
        self.assertEqual(len(self.result), 8)
        self.assertTrue(self.frame.transit_minutes_to_jobs.gt(0).all())
        # Travel time carries real signal on the confident labels.
        result = self.result.set_index("method")
        self.assertGreater(result.loc["transit_minutes_only", "roc_auc_confident"], 0.75)
        # Knowing where residents work beats travel time to Manhattan alone.
        self.assertGreater(result.loc["logistic_regression:plus_job_destinations", "roc_auc"],
                           result.loc["transit_minutes_only", "roc_auc"])

    def test_folds_never_share_a_community_district(self):
        districts = self.frame.CDTA2020.to_numpy()
        folds = grouped_folds(self.frame)
        self.assertEqual(len(folds), 5)
        for train, test in folds:
            self.assertFalse(set(districts[train]) & set(districts[test]))
        self.assertEqual(sorted(np.concatenate([test for _, test in folds])), list(range(len(self.frame))))

    def test_shuffled_labels_score_like_random(self):
        # If anything leaked the label into the features or the tuning, a model
        # trained on shuffled labels would still score well.
        frame = self.frame.copy()
        frame["underserved"] = pd.array(np.random.default_rng(0).permutation(frame.underserved.astype(int)),
                                         dtype="Int64")
        scores = out_of_fold_scores(frame, n_splits=3, feature_sets=("plus_job_destinations",))
        y = frame.underserved.astype(int)
        for model in ("logistic_regression", "gradient_boosting"):
            # Chance-level AUC on ~2,200 tracts has a standard deviation of about 0.013.
            self.assertLess(abs(roc_auc_score(y, scores[f"{model}:plus_job_destinations"]) - 0.5), 0.06)

    def test_borough_holdout_beats_priority_score(self):
        # Train on four boroughs, rank tracts inside the fifth. Manhattan has one
        # underserved tract and Staten Island ~120 tracts, too few to test.
        x = feature_matrix(self.frame, "plus_job_destinations")
        y = self.frame.underserved.astype(int).to_numpy()
        borough = self.frame.BoroName.to_numpy()
        groups = self.frame.CDTA2020.to_numpy()
        for held_out in ("Bronx", "Brooklyn", "Queens"):
            test, train = borough == held_out, borough != held_out
            model = fit_model("logistic_regression", x[train], y[train], groups[train])
            auc = roc_auc_score(y[test], model.predict_proba(x[test])[:, 1])
            priority = roc_auc_score(y[test], self.frame.priority_score.to_numpy()[test])
            self.assertGreater(auc, 0.6, held_out)
            self.assertGreater(auc, priority, held_out)

    def test_cluster_summary_covers_frame(self):
        summary = cluster_gold_summary(self.frame)
        self.assertEqual(summary.tracts.sum(), len(self.frame))
        self.assertEqual(summary.cluster.tolist(), [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main()
