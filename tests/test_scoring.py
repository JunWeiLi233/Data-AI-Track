import hashlib
import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from backend.data_loader import DEFAULT_DATA_DIR, SubwayData, load_data
from backend.scoring import nearest_station_distances, score_tracts


def sample():
    tracts = pd.DataFrame({
        'GEOID': ['36005000100', '36005000200', '36005000300'],
        'latitude': [0., 0., 0.], 'longitude': [0., 1., 2.],
        'population': [100., 200., 300.],
        'median_household_income': [100., 50., 25.]})
    stations = pd.DataFrame({'latitude': [0.], 'longitude': [0.]})
    return SubwayData(stations, pd.DataFrame(), tracts, {})


class ScoringTests(unittest.TestCase):
    def test_known_geographic_distances_and_nearest_station(self):
        tracts = pd.DataFrame({'latitude': [0., 40., 91.], 'longitude': [1., -73., 0.]}, index=[8, 3, 6])
        stations = pd.DataFrame({'latitude': [0., 40., 999.], 'longitude': [0., -73., 0.]})
        distance = nearest_station_distances(tracts, stations)
        self.assertEqual(distance.index.tolist(), [8, 3, 6])
        self.assertAlmostEqual(distance.iloc[0], 111.19508, places=4)
        self.assertEqual(distance.iloc[1], 0.)
        self.assertTrue(np.isnan(distance.iloc[2]))
        tracts = pd.DataFrame({'latitude': [60.], 'longitude': [1.]})
        stations = pd.DataFrame({'latitude': [60.], 'longitude': [0.]})
        self.assertAlmostEqual(nearest_station_distances(tracts, stations).iloc[0], 55.597, places=2)

    def test_formula_reciprocal_income_and_rank(self):
        data = sample()
        before = data.tracts.copy(deep=True)
        scored = score_tracts(data, clip_quantiles=(0, 1)).set_index('GEOID')
        mid = scored.loc['36005000200']
        self.assertAlmostEqual(mid.distance_score, 0.5)
        self.assertAlmostEqual(mid.population_score, 0.5)
        self.assertAlmostEqual(mid.income_disadvantage_score, 1 / 3)
        self.assertAlmostEqual(mid.priority_score, 0.5 * 0.5 + 0.3 * 0.5 + 0.2 / 3)
        self.assertEqual(scored.loc['36005000300', 'rank'], 1)
        self.assertEqual(scored.loc['36005000100', 'priority_score'], 0.)
        self.assertEqual(scored.loc['36005000300', 'priority_score'], 1.)
        assert_frame_equal(data.tracts, before)

    def test_missing_and_nonpositive_income_neutral_without_imputation(self):
        data = sample()
        data.tracts['median_household_income'] = [pd.NA, 0, -1]
        scored = score_tracts(data, clip_quantiles=(0, 1)).set_index('GEOID')
        self.assertTrue(scored.income_score_neutral.all())
        self.assertTrue((scored.income_disadvantage_score == 0.5).all())
        self.assertTrue(scored.priority_score.notna().all())
        self.assertTrue(pd.isna(scored.loc['36005000100', 'median_household_income']))
        self.assertEqual(scored.loc['36005000200', 'median_household_income'], 0)
        self.assertAlmostEqual(scored.loc['36005000100', 'priority_score'], 0.1)

    def test_configurable_weights_change_ranking(self):
        data = sample()
        data.tracts['population'] = [300., 200., 100.]
        distance = score_tracts(data, {'distance': 100, 'population': 0, 'income': 0})
        population = score_tracts(data, {'distance': 0, 'population': 10, 'income': 0})
        self.assertEqual(distance.iloc[0].GEOID, '36005000300')
        self.assertEqual(population.iloc[0].GEOID, '36005000100')
        self.assertEqual(population.attrs['scoring']['weights']['population'], 1.)
        for weights in ({'distance': 1}, {'distance': -1, 'population': 1, 'income': 1},
                        {'distance': 0, 'population': 0, 'income': 0},
                        {'distance': np.nan, 'population': 1, 'income': 1}):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                score_tracts(data, weights)

    def test_invalid_active_components_unranked_and_zero_weight_disabled(self):
        data = sample()
        data.tracts.loc[0, 'latitude'] = np.nan
        data.tracts.loc[1, 'population'] = np.inf
        scored = score_tracts(data).set_index('GEOID')
        self.assertTrue(pd.isna(scored.loc['36005000100', 'rank']))
        self.assertTrue(pd.isna(scored.loc['36005000200', 'priority_score']))
        scored = score_tracts(data, {'distance': 0, 'population': 0, 'income': 1})
        self.assertTrue(scored.priority_score.notna().all())
        with self.assertRaisesRegex(ValueError, 'valid coordinates'):
            nearest_station_distances(data.tracts, data.stations.iloc[:0])

    def test_constant_components_and_ties(self):
        data = sample()
        data.tracts[['longitude', 'population', 'median_household_income']] = [0, 100, 50]
        scored = score_tracts(data)
        self.assertTrue((scored.priority_score == 0.5).all())
        self.assertEqual(scored['rank'].tolist(), [1, 1, 1])
        self.assertEqual(scored.GEOID.tolist(), sorted(data.tracts.GEOID))

    def test_outlier_clipping_and_configuration(self):
        data = sample()
        data.tracts['population'] = [0, 100, 1000000]
        scored = score_tracts(data, clip_quantiles=(0, 0.5)).set_index('GEOID')
        self.assertEqual(scored.loc['36005000200', 'population_score'], 1.)
        self.assertEqual(scored.loc['36005000300', 'population_score'], 1.)
        self.assertEqual(scored.loc['36005000300', 'population'], 1000000)
        for bounds in ((0.5, 0.5), (-0.1, 1), (0, 1.1), (np.nan, 1)):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                score_tracts(data, clip_quantiles=bounds)

    def test_zero_population_retained_but_not_ranked(self):
        data = sample()
        data.tracts.loc[2, 'population'] = 0
        data.tracts.loc[2, 'median_household_income'] = np.nan
        before = data.tracts.copy(deep=True)
        for weights in (None, {'distance': 1, 'population': 0, 'income': 0}):
            with self.subTest(weights=weights):
                scored = score_tracts(data, weights, clip_quantiles=(0, 1))
                self.assertEqual(len(scored), len(data.tracts))
                self.assertEqual(set(scored.GEOID), set(data.tracts.GEOID))
                zero = scored.set_index('GEOID').loc['36005000300']
                self.assertEqual(zero.population, 0)
                self.assertFalse(zero.score_eligible)
                self.assertTrue(pd.isna(zero.priority_score))
                self.assertTrue(pd.isna(zero['rank']))
                self.assertGreater(zero.nearest_station_distance_km, 200)
                self.assertEqual(zero.distance_score, 1.)
                self.assertEqual(zero.population_score, 0.)
                self.assertEqual(zero.income_disadvantage_score, 0.5)
                self.assertTrue(zero.income_score_neutral)
                positive = scored.loc[scored.population.gt(0)]
                self.assertEqual(positive['rank'].tolist(), [1, 2])
                # Zero population stays in the normalization reference set.
                self.assertEqual(scored.attrs['scoring']['normalization_bounds']['population'],
                                 {'lower': 0., 'upper': 200.})
                if weights is None:
                    self.assertEqual(scored.attrs['scoring']['weights'],
                                     {'distance': 0.5, 'population': 0.3, 'income': 0.2})
                    for row in positive.itertuples():
                        self.assertAlmostEqual(row.priority_score, 0.5 * row.distance_score
                            + 0.3 * row.population_score + 0.2 * row.income_disadvantage_score)
        assert_frame_equal(data.tracts, before)

    def test_all_zero_population_still_returns_geographic_rows(self):
        data = sample()
        data.tracts['population'] = 0
        scored = score_tracts(data)
        self.assertEqual(len(scored), 3)
        self.assertTrue(scored['rank'].isna().all())
        self.assertTrue(scored.priority_score.isna().all())
        self.assertFalse(scored.score_eligible.any())
        self.assertTrue(scored.nearest_station_distance_km.notna().all())
        self.assertTrue(scored.distance_score.notna().all())

    def test_baseline_roundtrip_and_subset_keep_scores(self):
        data = sample()
        baseline = score_tracts(data, clip_quantiles=(0, 1))
        bounds = baseline.attrs['scoring']['normalization_bounds']
        reused = score_tracts(data, normalization_bounds=bounds)
        cols = ['GEOID', 'distance_score', 'population_score',
                'income_disadvantage_score', 'priority_score', 'rank']
        assert_frame_equal(baseline[cols], reused[cols])
        subset = SubwayData(data.stations, data.entrances, data.tracts.iloc[[1]].copy(), {})
        scored = score_tracts(subset, normalization_bounds=bounds)
        self.assertAlmostEqual(scored.iloc[0].priority_score,
                               baseline.set_index('GEOID').loc['36005000200', 'priority_score'])
        self.assertEqual(scored.attrs['scoring']['normalization_source'], 'baseline')
        self.assertEqual(scored.attrs['scoring']['normalization_bounds'], bounds)

    def test_expansion_with_fixed_baseline_does_not_increase_need(self):
        data = sample()
        baseline = score_tracts(data, clip_quantiles=(0, 1)).set_index('GEOID')
        bounds = baseline.attrs['scoring']['normalization_bounds']
        stations = pd.concat([data.stations, pd.DataFrame({'latitude': [0.], 'longitude': [2.]})])
        scenario = SubwayData(stations, data.entrances, data.tracts, {})
        after = score_tracts(scenario, normalization_bounds=bounds).set_index('GEOID')
        after = after.loc[baseline.index]
        self.assertTrue((after.priority_score <= baseline.priority_score + 1e-12).all())
        self.assertAlmostEqual(after.loc['36005000300', 'distance_score'], 0.)
        self.assertLess(after.loc['36005000300', 'priority_score'],
                        baseline.loc['36005000300', 'priority_score'])
        for column in ('population_score', 'income_disadvantage_score'):
            np.testing.assert_allclose(after[column], baseline[column])

    def test_baseline_bounds_validation_and_degenerate_reference(self):
        data = sample()
        for bounds in ({}, {'distance': {'lower': 2, 'upper': 1},
                           'population': {'lower': 0, 'upper': 1},
                           'income': {'lower': 0, 'upper': 1}},
                       {key: {'lower': 0, 'upper': np.inf}
                        for key in ('distance', 'population', 'income')}):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                score_tracts(data, normalization_bounds=bounds)
        data.tracts['median_household_income'] = np.nan
        baseline = score_tracts(data)
        reused = score_tracts(data, normalization_bounds=baseline.attrs['scoring']['normalization_bounds'])
        self.assertTrue((reused.income_disadvantage_score == 0.5).all())
        self.assertTrue(reused.income_score_neutral.all())
        data.tracts[['longitude', 'population', 'median_household_income']] = [0, 100, 50]
        baseline = score_tracts(data)
        reused = score_tracts(data, normalization_bounds=baseline.attrs['scoring']['normalization_bounds'])
        self.assertTrue((reused.priority_score == 0.5).all())

    def test_real_data_scores_and_raw_hashes(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in DEFAULT_DATA_DIR.glob('*.csv')}
        scored = score_tracts(load_data())
        self.assertEqual(len(scored), 2325)
        self.assertTrue(scored.priority_score.dropna().between(0, 1).all())
        self.assertEqual(int(scored.income_score_neutral.sum()), 126)
        zero = scored.population.eq(0)
        self.assertEqual(int(zero.sum()), 82)
        self.assertTrue(scored.loc[zero, 'rank'].isna().all())
        self.assertTrue(scored.loc[zero, 'priority_score'].isna().all())
        self.assertTrue(scored.loc[~zero, 'rank'].notna().all())
        self.assertTrue(scored.loc[~zero, 'priority_score'].is_monotonic_decreasing)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})


if __name__ == '__main__':
    unittest.main()
