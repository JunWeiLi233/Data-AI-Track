import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from backend.export_pipeline import SCHEMA, prepare_exports
from backend.snowflake_client import (connection_settings, create_table_sql, insert_sql,
                                      staging_frame, upload)


class SettingsTests(unittest.TestCase):
    def test_settings_require_account_user_and_credential(self):
        with self.assertRaisesRegex(RuntimeError, 'SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER'):
            connection_settings({})
        with self.assertRaisesRegex(RuntimeError, 'SNOWFLAKE_PASSWORD'):
            connection_settings({'SNOWFLAKE_ACCOUNT': 'a', 'SNOWFLAKE_USER': 'u'})

    def test_settings_apply_analytics_defaults_and_overrides(self):
        settings = connection_settings({'SNOWFLAKE_ACCOUNT': 'a', 'SNOWFLAKE_USER': 'u',
                                        'SNOWFLAKE_PASSWORD': 'p', 'SNOWFLAKE_SCHEMA': 'DEV'})
        self.assertNotIn('role', settings)
        self.assertNotIn('warehouse', settings)
        self.assertEqual(settings['database'], 'NYC_TRANSIT_PLANNER')
        self.assertEqual(settings['schema'], 'DEV')
        self.assertEqual(settings['password'], 'p')
        browser = connection_settings({'SNOWFLAKE_ACCOUNT': 'a', 'SNOWFLAKE_USER': 'u',
                                       'SNOWFLAKE_AUTHENTICATOR': 'externalbrowser'})
        self.assertNotIn('password', browser)
        configured = connection_settings({'SNOWFLAKE_ACCOUNT': 'a', 'SNOWFLAKE_USER': 'u',
            'SNOWFLAKE_PASSWORD': 'p', 'SNOWFLAKE_ROLE': 'MY_EXISTING_ROLE',
            'SNOWFLAKE_WAREHOUSE': 'MY_EXISTING_WH'})
        self.assertEqual(configured['role'], 'MY_EXISTING_ROLE')
        self.assertEqual(configured['warehouse'], 'MY_EXISTING_WH')


class SqlTests(unittest.TestCase):
    def test_create_table_uses_export_types_and_nullability(self):
        sql = create_table_sql('BASELINE_TRACT_SCORES')
        self.assertTrue(sql.startswith('CREATE OR REPLACE TABLE "BASELINE_TRACT_SCORES"'))
        self.assertIn('"GEOID" VARCHAR(11) NOT NULL', sql)
        self.assertIn('"PRIORITY_SCORE" FLOAT,', sql)
        self.assertIn('"RANK" NUMBER(18,0)\n', sql)

    def test_insert_parses_variants_and_casts_scalars(self):
        sql = insert_sql('SCORING_RUNS', 'SCORING_RUNS_STAGE')
        self.assertIn('PARSE_JSON("WEIGHTS")', sql)
        self.assertIn('"CREATED_AT_UTC"::TIMESTAMP_TZ', sql)
        self.assertTrue(sql.endswith('FROM "SCORING_RUNS_STAGE"'))

    def test_upper_case_names_stay_unique(self):
        for table, columns in SCHEMA.items():
            self.assertEqual(len({c.upper() for c in columns}), len(columns), table)


class FakeCursor:
    def __init__(self, counts):
        self.sql, self.counts, self.last = [], counts, None

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self.last = sql
        return self

    def fetchone(self):
        table = self.last.split('"')[1]
        return (self.counts[table],)


class ExportTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = prepare_exports(scoring_run_id='test-run')

    def test_staging_frames_serialize_like_write_pandas(self):
        for table, frame in self.bundle.tables.items():
            staged = staging_frame(table, frame)
            self.assertEqual(list(staged.columns), [c.upper() for c in SCHEMA[table]])
            staged.to_parquet(io.BytesIO())  # write_pandas stages Parquet files
        stations = staging_frame('STATION_LOCATIONS', self.bundle.tables['STATION_LOCATIONS'])
        self.assertIsInstance(json.loads(stations.ALIASES[0]), list)
        tracts = staging_frame('TRACTS_PROCESSED', self.bundle.tables['TRACTS_PROCESSED'])
        self.assertEqual(int(tracts.MEDIAN_HOUSEHOLD_INCOME.isna().sum()), 126)

    def test_upload_stages_inserts_and_checks_counts(self):
        expected = {table: len(frame) for table, frame in self.bundle.tables.items()}
        cursor = FakeCursor(expected)
        conn = SimpleNamespace(cursor=lambda: cursor)
        ok = SimpleNamespace(success=True)
        with mock.patch('snowflake.connector.pandas_tools.write_pandas', return_value=ok) as write:
            self.assertEqual(upload(conn, self.bundle), expected)
        self.assertEqual([c.args[2] for c in write.call_args_list], [t + '_STAGE' for t in SCHEMA])
        self.assertTrue(all(c.kwargs['table_type'] == 'temporary' for c in write.call_args_list))
        self.assertEqual(sum(s.startswith('INSERT INTO') for s in cursor.sql), 4)

    def test_upload_fails_on_row_count_mismatch(self):
        counts = {table: len(frame) for table, frame in self.bundle.tables.items()}
        counts['TRACTS_PROCESSED'] -= 1
        conn = SimpleNamespace(cursor=lambda: FakeCursor(counts))
        with mock.patch('snowflake.connector.pandas_tools.write_pandas',
                        return_value=SimpleNamespace(success=True)):
            with self.assertRaisesRegex(RuntimeError, 'TRACTS_PROCESSED: loaded 2324'):
                upload(conn, self.bundle)


if __name__ == '__main__':
    unittest.main()
