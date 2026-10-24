import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from shapely import wkt

from backend.data_loader import DEFAULT_DATA_DIR, SubwayData, load_data
from backend.export_pipeline import (EXPECTED_COUNTS, SCHEMA, export_processed_data,
                                     prepare_exports, validate_entrance_geography,
                                     validate_export_tables)


class ExportPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = prepare_exports(scoring_run_id='integration-baseline')

    def test_expected_counts_keys_and_baseline_metadata(self):
        tables = self.bundle.tables
        self.assertEqual({key: len(value) for key, value in tables.items()}, EXPECTED_COUNTS)
        self.assertTrue(self.bundle.validation['passed'])
        tracts = tables['TRACTS_PROCESSED']
        scores = tables['BASELINE_TRACT_SCORES']
        run = tables['SCORING_RUNS'].iloc[0]
        self.assertTrue(tracts.GEOID.map(lambda value: isinstance(value, str) and len(value) == 11).all())
        self.assertEqual(set(tracts.GEOID), set(scores.GEOID))
        self.assertTrue(scores.scoring_run_id.eq(run.scoring_run_id).all())
        self.assertEqual(run.weights, {'distance': 0.5, 'population': 0.3, 'income': 0.2})
        self.assertEqual(run.normalization_bounds, run.scoring_metadata['normalization_bounds'])
        self.assertEqual(tuple(run.clip_quantiles), (0.01, 0.99))
        self.assertEqual(len(run.source_provenance['files']), 5)
        self.assertFalse(run.source_provenance['source_vintages_confirmed'])
        self.assertEqual(set(run.source_provenance['code_sha256']),
                         {'data_loader.py', 'scoring.py', 'export_pipeline.py'})

    def test_nulls_and_zero_population_are_preserved(self):
        tracts = self.bundle.tables['TRACTS_PROCESSED']
        scores = self.bundle.tables['BASELINE_TRACT_SCORES']
        self.assertEqual(int(tracts.median_household_income.isna().sum()), 126)
        self.assertEqual(int(tracts.median_household_income_topcoded.sum()), 13)
        self.assertEqual(int(scores.priority_score.isna().sum()), 82)
        self.assertEqual(int(scores['rank'].isna().sum()), 82)
        self.assertEqual(int(tracts.population.eq(0).sum()), 82)
        self.assertEqual(int(scores.income_score_neutral.sum()), 126)
        self.assertEqual(int(scores.score_eligible.sum()), 2243)

    def test_boundaries_are_wkt_strings(self):
        tracts = self.bundle.tables['TRACTS_PROCESSED']
        for text in tracts.boundary_wkt:
            self.assertIsInstance(text, str)
            geometry = wkt.loads(text)
            self.assertTrue(geometry.is_valid)
            self.assertEqual(geometry.geom_type, 'MultiPolygon')
        self.assertNotIn('geometry', tracts.columns)

    def test_stable_locations_alias_arrays_and_ambiguous_names(self):
        stations = self.bundle.tables['STATION_LOCATIONS']
        self.assertEqual(int(stations.identity_ambiguous.sum()), 5)
        self.assertFalse(stations.physical_identity_verified.any())
        self.assertEqual(sum(stations.gtfs_parent_ids.map(len)), 501)
        aqueduct = stations.loc[stations.gtfs_parent_ids.map(lambda ids: 'H01' in ids)].iloc[0]
        self.assertTrue(aqueduct.identity_ambiguous)
        self.assertTrue(aqueduct.multiple_source_names)
        self.assertEqual(aqueduct.gtfs_parent_ids, ['H01', 'H02'])
        self.assertEqual(aqueduct.aliases, sorted(aqueduct.aliases))
        data = load_data()
        shuffled = data.stations.iloc[::-1].copy()
        shuffled['station_ids'] = shuffled.station_ids.map(lambda ids: tuple(reversed(ids)))
        shuffled['station_names'] = shuffled.station_names.map(lambda names: tuple(reversed(names)))
        changed = SubwayData(shuffled, data.entrances, data.tracts, data.report)
        with patch('backend.export_pipeline.load_data', return_value=changed):
            again = prepare_exports(scoring_run_id='reordered')
        assert_frame_equal(stations, again.tables['STATION_LOCATIONS'])

    def test_future_entrance_validation_preserves_source_values(self):
        data = load_data()
        before = data.entrances.copy(deep=True)
        flagged = validate_entrance_geography(data.entrances)
        assert_frame_equal(data.entrances, before)
        assert_frame_equal(flagged[before.columns], before)
        bad = flagged.loc[flagged.geography_review_required]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad.iloc[0]['Entrance Longitude'], '73.993327')
        self.assertFalse(bad.iloc[0].nyc_bbox_valid)
        self.assertTrue(bad.iloc[0].export_coordinates_valid)
        self.assertGreater(bad.iloc[0].station_reference_distance_km, 10000)
        with self.assertRaises(ValueError):
            validate_entrance_geography(data.entrances, max_station_separation_km=-1)

    def test_entrance_flags_invalid_coordinates_and_reference(self):
        entrances = pd.DataFrame({'latitude': [40.75, 40.75, np.inf, 91.],
                                 'longitude': [-73.99, 73.99, -73.99, -73.99],
                                 'Station Latitude': ['40.75', '', '40.75', '40.75'],
                                 'Station Longitude': ['-73.99'] * 4}, index=[4, 2, 9, 0])
        flagged = validate_entrance_geography(entrances)
        self.assertEqual(flagged.nyc_bbox_valid.tolist(), [True, False, False, False])
        self.assertEqual(flagged.geography_review_required.tolist(), [False, True, True, True])
        self.assertTrue(pd.isna(flagged.loc[2, 'station_reference_distance_km']))
        self.assertEqual(flagged.index.tolist(), entrances.index.tolist())

    def test_validation_rejects_bad_counts_duplicates_and_foreign_keys(self):
        tables = deepcopy(self.bundle.tables)
        tables['TRACTS_PROCESSED'] = tables['TRACTS_PROCESSED'].iloc[1:]
        with self.assertRaisesRegex(ValueError, 'expected 2325'):
            validate_export_tables(tables)
        tables = deepcopy(self.bundle.tables)
        tables['STATION_LOCATIONS'].loc[1, 'station_location_id'] = tables['STATION_LOCATIONS'].loc[0, 'station_location_id']
        with self.assertRaisesRegex(ValueError, 'Duplicate station_location_id'):
            validate_export_tables(tables)
        for column, value, message in [('GEOID', '36005999999', 'foreign key'),
                                       ('scoring_run_id', 'missing-run', 'foreign key')]:
            tables = deepcopy(self.bundle.tables)
            tables['BASELINE_TRACT_SCORES'].loc[0, column] = value
            with self.subTest(column=column), self.assertRaisesRegex(ValueError, message):
                validate_export_tables(tables)

    def test_validation_rejects_nonfinite_and_out_of_nyc_coordinates(self):
        for longitude in (73.99, -181., np.inf):
            tables = deepcopy(self.bundle.tables)
            tables['STATION_LOCATIONS'].loc[0, 'longitude'] = longitude
            with self.subTest(longitude=longitude), self.assertRaises(ValueError):
                validate_export_tables(tables)
        tables = deepcopy(self.bundle.tables)
        tables['BASELINE_TRACT_SCORES'].loc[0, 'distance_score'] = 2
        with self.assertRaisesRegex(ValueError, 'outside 0-1'):
            validate_export_tables(tables)

    def test_jsonl_roundtrip_dictionary_and_no_overwrites(self):
        raw_hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in DEFAULT_DATA_DIR.glob('*.csv')}
        protected = [Path(__file__).parents[1] / 'backend' / name
                     for name in ('data_loader.py', 'scoring.py', 'simulator.py')]
        code_hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
        with tempfile.TemporaryDirectory() as folder:
            # Reuse an already prepared real bundle; writing must preserve exact values.
            with patch('backend.export_pipeline.prepare_exports', return_value=self.bundle):
                output = export_processed_data(folder)
            self.assertEqual(output.name, 'integration-baseline')
            dictionary = json.loads((output / 'data_dictionary.json').read_text())
            report = json.loads((output / 'validation_report.json').read_text())
            self.assertTrue(report['raw_source_hashes_unchanged'])
            parsed = {}
            for name, columns in SCHEMA.items():
                text = (output / (name + '.jsonl')).read_text()
                self.assertNotIn('NaN', text)
                self.assertNotIn('Infinity', text)
                records = [json.loads(line) for line in text.splitlines()]
                parsed[name] = records
                self.assertEqual(len(records), EXPECTED_COUNTS[name])
                self.assertEqual(set(records[0]), set(columns))
                self.assertEqual({item['column'] for item in dictionary['tables'][name]}, set(columns))
                self.assertEqual(hashlib.sha256((output / (name + '.jsonl')).read_bytes()).hexdigest(),
                                 report['file_sha256'][name + '.jsonl'])
            self.assertEqual(sum(row['median_household_income'] is None for row in parsed['TRACTS_PROCESSED']), 126)
            self.assertEqual(sum(row['rank'] is None for row in parsed['BASELINE_TRACT_SCORES']), 82)
            self.assertTrue(all(isinstance(row['GEOID'], str) for row in parsed['TRACTS_PROCESSED']))
            self.assertTrue(all(isinstance(row['aliases'], list) for row in parsed['STATION_LOCATIONS']))
            table_hash = hashlib.sha256((output / 'TRACTS_PROCESSED.jsonl').read_bytes()).hexdigest()
            with patch('backend.export_pipeline.prepare_exports', return_value=self.bundle):
                with self.assertRaises(FileExistsError):
                    export_processed_data(folder)
            self.assertEqual(table_hash, hashlib.sha256((output / 'TRACTS_PROCESSED.jsonl').read_bytes()).hexdigest())
        self.assertEqual(raw_hashes, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in raw_hashes})
        self.assertEqual(code_hashes, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected})

    def test_raw_output_path_and_unsafe_run_ids_are_rejected(self):
        for path in (DEFAULT_DATA_DIR, DEFAULT_DATA_DIR / 'processed'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                export_processed_data(path)
        for value in ('../escape', '', '/tmp/bad', 'a/b'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare_exports(scoring_run_id=value)


if __name__ == '__main__':
    unittest.main()
