import hashlib
import tempfile
import unittest
from pathlib import Path

from backend.data_loader import DEFAULT_DATA_DIR, load_data, load_demographics, load_stations, load_tracts


class LoaderTests(unittest.TestCase):
    def test_supplied_files_and_read_only(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in DEFAULT_DATA_DIR.glob('*.csv')}
        data = load_data()
        self.assertEqual(len(data.tracts), 2325)
        self.assertEqual(data.report['physical_station_locations'], 496)
        self.assertEqual(data.report['station_parent_rows'], 501)
        self.assertTrue(data.tracts.GEOID.str.fullmatch(r'\d{11}').all())
        self.assertFalse(data.tracts.GEOID.duplicated().any())
        self.assertEqual(data.report['invalid_tract_geometries'], 0)
        self.assertEqual(data.report['income_topcoded_on_tracts'], 13)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_acs_missing_topcode_zero_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'income.csv'
            header = 'GEO_ID,NAME,B19013_001E,B19013_001M\n'
            path.write_text(header + 'Geography,Description,Estimate,MOE\n'
                            '1400000US36005000100,A,-,**\n'
                            '1400000US36005000200,B,"250,000+",**\n'
                            '1400000US36005000300,C,0,10\n')
            frame = load_demographics(path, 'B19013_001E', 'median_household_income')
            self.assertTrue(frame.iloc[0].median_household_income_missing)
            self.assertEqual(frame.iloc[1].median_household_income, 250000)
            self.assertTrue(frame.iloc[1].median_household_income_topcoded)
            self.assertEqual(frame.iloc[2].median_household_income, 0)
            path.write_text(header + '1400000US36005000100,A,10,1\n' * 2)
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                load_demographics(path, 'B19013_001E', 'median_household_income')

    def test_station_parents_exact_dedup_and_invalid_coordinates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'stops.csv'
            path.write_text('stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n'
                            'A,One,40,-74,1,\nAN,One,40,-74,0,A\n'
                            'B,Two,40,-74,1,\nC,Bad,999,-74,1,\n')
            frame = load_stations(path)
            self.assertEqual(len(frame), 2)
            self.assertEqual(frame.iloc[0].station_ids, ('A', 'B'))
            self.assertEqual(int(frame.coordinates_valid.sum()), 1)
            self.assertTrue(frame.loc[~frame.coordinates_valid, 'latitude'].isna().all())

    def test_invalid_geometry_is_retained_without_invented_point(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'tracts.csv'
            path.write_text('GEOID,the_geom\n36005000100,bad\n')
            frame = load_tracts(path)
            self.assertEqual(frame.iloc[0].geometry_status, 'invalid_wkt')
            self.assertTrue(frame.latitude.isna().all())


if __name__ == '__main__':
    unittest.main()
