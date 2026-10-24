import hashlib
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from backend.data_loader import DEFAULT_DATA_DIR, SubwayData, load_data
from backend.scoring import EARTH_RADIUS_KM, score_tracts
from backend.simulator import compare_scenarios, simulate_expansion


def fixture():
    return SubwayData(
        pd.DataFrame({'latitude': [0.], 'longitude': [0.], 'coordinates_valid': [True]}),
        pd.DataFrame(),
        pd.DataFrame({'GEOID': [f'36005000{i}00' for i in range(1, 6)],
                      'latitude': [0., 0., 0., 0., np.nan],
                      'longitude': [0., 1., 2., 3., 4.],
                      'population': [100, 200, 0, 400, 500],
                      'median_household_income': [100000., 50000., np.nan, np.nan, 20000.]}),
        {},
    )


class SimulatorTests(unittest.TestCase):
    def test_known_distance_benefits_and_weighted_denominator(self):
        data = fixture()
        result = simulate_expansion({'latitude': 0, 'longitude': 2}, data,
                                    min_distance_improvement_km=0.1)
        rows = result.tracts.set_index('GEOID')
        improvement = 2 * np.pi / 180 * EARTH_RADIUS_KM
        self.assertAlmostEqual(rows.loc['36005000400', 'distance_improvement_km'], improvement)
        self.assertAlmostEqual(rows.loc['36005000300', 'distance_improvement_km'], improvement)
        self.assertFalse(rows.loc['36005000300', 'benefited'])  # Zero population.
        self.assertTrue(pd.isna(rows.loc['36005000300', 'proposed_rank']))
        self.assertTrue(rows.loc['36005000400', 'benefited'])
        self.assertTrue(rows.loc['36005000400', 'income_score_neutral'])
        self.assertFalse(rows.loc['36005000500', 'residential_benefit_eligible'])
        summary = result.summary
        self.assertEqual(summary['residential_tracts_benefiting'], 1)
        self.assertEqual(summary['population_potentially_benefiting'], 400)
        self.assertEqual(summary['population_evaluated'], 700)
        self.assertEqual(summary['residential_tracts_evaluated'], 3)
        self.assertEqual(summary['residential_tracts_without_valid_distances'], 1)
        self.assertAlmostEqual(summary['population_weighted_average_distance_reduction_km'],
                               improvement * 400 / 700)
        self.assertEqual(len(result.tracts), len(data.tracts))

    def test_threshold_boundary_zero_and_high_threshold(self):
        data = fixture()
        station = {'latitude': 0, 'longitude': 2}
        zero = simulate_expansion(station, data, min_distance_improvement_km=0)
        reduction = zero.tracts.set_index('GEOID').loc['36005000400', 'distance_improvement_km']
        exact = simulate_expansion(station, data, min_distance_improvement_km=reduction)
        above = simulate_expansion(station, data, min_distance_improvement_km=reduction + 0.001)
        self.assertEqual(zero.summary['residential_tracts_benefiting'], 1)
        self.assertEqual(exact.summary['residential_tracts_benefiting'], 1)
        self.assertEqual(above.summary['residential_tracts_benefiting'], 0)
        self.assertEqual(above.summary['population_potentially_benefiting'], 0)
        self.assertEqual(zero.summary['population_weighted_average_distance_reduction_km'],
                         above.summary['population_weighted_average_distance_reduction_km'])
        self.assertFalse(zero.tracts.set_index('GEOID').loc['36005000100', 'benefited'])

    def test_multiple_stations_count_population_once_and_deduplicate(self):
        data = fixture()
        station = {'latitude': 0, 'longitude': 2}
        single = simulate_expansion(station, data)
        duplicate = simulate_expansion([station, station], data)
        assert_frame_equal(single.tracts, duplicate.tracts)
        self.assertEqual(duplicate.summary['proposed_station_locations'], 1)
        combined = simulate_expansion([station, {'latitude': 0, 'longitude': 3}], data)
        self.assertEqual(combined.summary['residential_tracts_benefiting'], 1)
        self.assertEqual(combined.summary['population_potentially_benefiting'], 400)

    def test_baseline_noop_existing_station_and_faraway_proposal(self):
        for stations in ([], {'latitude': 0, 'longitude': 0}, {'latitude': -80, 'longitude': 100}):
            with self.subTest(stations=stations):
                result = simulate_expansion(stations, fixture(), min_distance_improvement_km=0)
                self.assertEqual(result.summary['residential_tracts_benefiting'], 0)
                self.assertEqual(result.summary['population_weighted_average_distance_reduction_km'], 0)
                self.assertTrue((result.tracts.distance_improvement_km.dropna() == 0).all())

    def test_independent_scenarios_fixed_bounds_and_geoids(self):
        data = fixture()
        comparison = compare_scenarios({
            'east': [{'latitude': 0, 'longitude': 3}],
            'middle': [{'latitude': 0, 'longitude': 1}],
            'none': [],
        }, data, weights={'distance': 60, 'population': 30, 'income': 10},
            clip_quantiles=(0, 1))
        self.assertEqual(comparison.summary.scenario.tolist(), ['east', 'middle', 'none'])
        baseline = comparison.baseline.set_index('GEOID')
        for result in comparison.scenarios.values():
            rows = result.tracts.set_index('GEOID').loc[baseline.index]
            self.assertEqual(result.scoring_metadata['normalization_source'], 'baseline')
            self.assertEqual(result.scoring_metadata['normalization_bounds'],
                             comparison.baseline.attrs['scoring']['normalization_bounds'])
            self.assertEqual(result.scoring_metadata['weights'],
                             {'distance': 0.6, 'population': 0.3, 'income': 0.1})
            np.testing.assert_allclose(rows.baseline_nearest_station_distance_km,
                                       baseline.nearest_station_distance_km, equal_nan=True)
            for factor in ('population_score', 'income_disadvantage_score'):
                np.testing.assert_allclose(rows['baseline_' + factor], rows['proposed_' + factor])
            valid = rows.baseline_priority_score.notna()
            self.assertTrue((rows.loc[valid, 'proposed_priority_score'] <=
                             rows.loc[valid, 'baseline_priority_score'] + 1e-12).all())
        # east does not become part of middle: that scenario is evaluated independently.
        standalone = simulate_expansion({'latitude': 0, 'longitude': 1}, data,
                                        weights={'distance': 60, 'population': 30, 'income': 10},
                                        clip_quantiles=(0, 1))
        assert_frame_equal(standalone.tracts, comparison.scenarios['middle'].tracts)

    def test_input_frames_remain_unchanged(self):
        data = fixture()
        proposals = pd.DataFrame({'latitude': ['0'], 'longitude': ['3'], 'name': ['Station']})
        tracts, stations, before = data.tracts.copy(deep=True), data.stations.copy(deep=True), proposals.copy(deep=True)
        result = simulate_expansion(proposals, data)
        assert_frame_equal(data.tracts, tracts)
        assert_frame_equal(data.stations, stations)
        assert_frame_equal(proposals, before)
        self.assertEqual(result.proposed_stations.iloc[0]['name'], 'Station')

    def test_zero_missing_invalid_population_and_no_evaluable_mean(self):
        data = fixture()
        data.tracts['population'] = [0, np.nan, -1, np.inf, 1.5]
        result = simulate_expansion({'latitude': 0, 'longitude': 3}, data)
        self.assertEqual(len(result.tracts), 5)
        self.assertEqual(result.summary['residential_tracts_benefiting'], 0)
        self.assertEqual(result.summary['population_evaluated'], 0)
        self.assertIsNone(result.summary['population_weighted_average_distance_reduction_km'])
        self.assertEqual(result.summary['tracts_with_unknown_or_invalid_population'], 4)

    def test_invalid_inputs_fail_clearly(self):
        for proposal in ({'latitude': 91, 'longitude': 0}, {'latitude': 0, 'longitude': -181},
                         {'latitude': np.nan, 'longitude': 0}, {'latitude': 0, 'longitude': np.inf},
                         {'latitude': True, 'longitude': 0}, {'latitude': 0}, [(0, 1)], None):
            with self.subTest(proposal=proposal), self.assertRaises(ValueError):
                simulate_expansion(proposal, fixture())
        for value in (-1, np.inf, np.nan, 'bad'):
            with self.subTest(threshold=value), self.assertRaises(ValueError):
                simulate_expansion([], fixture(), min_distance_improvement_km=value)
        for scenarios in ({}, {'': []}, {1: []}, []):
            with self.subTest(scenarios=scenarios), self.assertRaises(ValueError):
                compare_scenarios(scenarios, fixture())

    def test_default_data_loaded_once_and_baseline_scored_once(self):
        with patch('backend.simulator.load_data', return_value=fixture()) as loader, \
             patch('backend.simulator.score_tracts', wraps=score_tracts) as scorer:
            compare_scenarios({'one': [], 'two': []})
            loader.assert_called_once_with()
            self.assertEqual(scorer.call_count, 3)  # One fit plus two fixed-bound applications.
            self.assertNotIn('normalization_bounds', scorer.call_args_list[0].kwargs)
            self.assertIn('normalization_bounds', scorer.call_args_list[1].kwargs)


class SimulatorIntegrationTests(unittest.TestCase):
    def test_actual_data_scenarios_and_raw_hashes(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in DEFAULT_DATA_DIR.glob('*.csv')}
        data = load_data()
        ranked = score_tracts(data)
        target = ranked.iloc[0]
        first = {'latitude': float(target.latitude), 'longitude': float(target.longitude)}
        second = {'latitude': float(ranked.iloc[1].latitude), 'longitude': float(ranked.iloc[1].longitude)}
        comparison = compare_scenarios({'one_station': [first], 'two_stations': [first, second]}, data)
        self.assertEqual(len(comparison.baseline), 2325)
        self.assertEqual(len(comparison.summary), 2)
        for result in comparison.scenarios.values():
            self.assertEqual(len(result.tracts), 2325)
            self.assertEqual(int(result.tracts.population.eq(0).sum()), 82)
            self.assertFalse(result.tracts.loc[result.tracts.population.eq(0), 'benefited'].any())
            self.assertTrue(result.tracts.distance_improvement_km.ge(0).all())
            self.assertGreater(result.summary['residential_tracts_benefiting'], 0)
            self.assertGreater(result.summary['population_potentially_benefiting'], 0)
            row = result.tracts.set_index('GEOID').loc[target.GEOID]
            self.assertAlmostEqual(row.proposed_nearest_station_distance_km, 0)
            self.assertAlmostEqual(row.distance_improvement_km, target.nearest_station_distance_km)
        one = comparison.scenarios['one_station'].tracts.set_index('GEOID')
        two = comparison.scenarios['two_stations'].tracts.set_index('GEOID').loc[one.index]
        self.assertTrue((two.proposed_nearest_station_distance_km <= one.proposed_nearest_station_distance_km).all())
        self.assertGreaterEqual(comparison.scenarios['two_stations'].summary['population_potentially_benefiting'],
                                comparison.scenarios['one_station'].summary['population_potentially_benefiting'])
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})


if __name__ == '__main__':
    unittest.main()
