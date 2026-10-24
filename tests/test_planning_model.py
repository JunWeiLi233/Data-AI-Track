import unittest

import numpy as np

from backend.planning_model import FEATURES, planning_features, predict, train_planning_model, validate
from backend.supervised import training_frame


class PlanningModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = training_frame()
        cls.checks = validate(cls.frame)
        # Fewer resamples than main() keeps the test quick.
        cls.model, cls.coefficients, cls.boot = train_planning_model(cls.frame, bootstraps=50)
        cls.predictions = predict(cls.model, cls.boot, cls.frame)

    def test_features(self):
        features = planning_features(self.frame)
        self.assertEqual(list(features.columns), list(FEATURES))
        self.assertTrue(np.isfinite(features.to_numpy()).all())
        # Manhattan is the baseline: no borough indicator set.
        manhattan = self.frame.BoroName.eq("Manhattan").to_numpy()
        self.assertTrue((features.iloc[:, 3:].sum(axis=1).to_numpy() == ~manhattan).all())

    def test_out_of_fold_ranking(self):
        self.assertGreater(self.checks["roc_auc"], 0.74)
        self.assertGreater(self.checks["roc_auc_confident"], 0.85)
        self.assertLess(self.checks["brier"], self.checks["brier_prevalence_only"])

    def test_calibrated_overall_and_by_borough(self):
        by_decile = self.checks["by_decile"]
        self.assertLess((by_decile.predicted - by_decile.observed).abs().max(), 0.1)
        by_borough = self.checks["by_borough"]
        self.assertLess((by_borough.predicted - by_borough.observed).abs().max(), 0.03)

    def test_coefficients_keep_their_sign(self):
        # Every tract feature's 95% interval excludes zero, with the expected direction.
        expected = {"log_station_distance": -1, "log_density": 1, "job_share_over_45": 1}
        for feature, sign in expected.items():
            row = self.coefficients.loc[feature]
            self.assertEqual(np.sign(row.coefficient), sign, feature)
            self.assertEqual(np.sign(row.low_95), np.sign(row.high_95), feature)

    def test_predictions_explain_themselves(self):
        p = self.predictions
        self.assertTrue(p.probability.between(0, 1).all())
        self.assertTrue((p.low_90 <= p.high_90).all())
        inside = p.probability.between(p.low_90, p.high_90)
        self.assertGreater(inside.mean(), 0.95)
        # Contributions plus the intercept give back the log-odds.
        contributions = p[[f"contribution_{f}" for f in FEATURES]].sum(axis=1)
        logit = np.log(p.probability / (1 - p.probability))
        self.assertTrue(np.allclose(contributions + self.model[-1].intercept_[0], logit))
        self.assertTrue(p.main_reason.isin(FEATURES[:3]).all())

    def test_deterministic(self):
        model, coefficients, boot = train_planning_model(self.frame, bootstraps=50)
        self.assertTrue(coefficients.equals(self.coefficients))
        self.assertTrue(predict(model, boot, self.frame).equals(self.predictions))


if __name__ == "__main__":
    unittest.main()
