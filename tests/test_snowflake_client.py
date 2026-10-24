import unittest

import pandas as pd
from shapely.geometry import Point

from backend.snowflake_client import connection_settings, to_snowflake_frame


class SnowflakeClientTests(unittest.TestCase):
    def test_settings_require_account_user_and_credential(self):
        with self.assertRaisesRegex(RuntimeError, 'SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER'):
            connection_settings({})
        with self.assertRaisesRegex(RuntimeError, 'SNOWFLAKE_PASSWORD'):
            connection_settings({'SNOWFLAKE_ACCOUNT': 'a', 'SNOWFLAKE_USER': 'u'})

    def test_settings_apply_team_defaults_and_overrides(self):
        settings = connection_settings({'SNOWFLAKE_ACCOUNT': 'a', 'SNOWFLAKE_USER': 'u',
                                        'SNOWFLAKE_PASSWORD': 'p', 'SNOWFLAKE_SCHEMA': 'DEV'})
        self.assertEqual(settings['role'], 'TRANSIT_TEAM')
        self.assertEqual(settings['warehouse'], 'TRANSIT_WH')
        self.assertEqual(settings['database'], 'TRANSIT_GAP')
        self.assertEqual(settings['schema'], 'DEV')
        self.assertEqual(settings['password'], 'p')
        browser = connection_settings({'SNOWFLAKE_ACCOUNT': 'a', 'SNOWFLAKE_USER': 'u',
                                       'SNOWFLAKE_AUTHENTICATOR': 'externalbrowser'})
        self.assertNotIn('password', browser)

    def test_frame_cleaning(self):
        frame = pd.DataFrame({'Station Name': ['A'], 'station_ids': [('101', '102')],
                              'geometry': [Point(0, 0)], 'geometry_status': ['valid']})
        result = to_snowflake_frame(frame)
        self.assertEqual(list(result.columns), ['STATION_NAME', 'STATION_IDS', 'GEOMETRY_STATUS'])
        self.assertEqual(result.STATION_IDS[0], '101 | 102')
        self.assertEqual(str(result.GEOMETRY_STATUS.dtype), 'string')

    def test_colliding_names_raise(self):
        with self.assertRaisesRegex(ValueError, 'collide'):
            to_snowflake_frame(pd.DataFrame({'Entry': [1], 'ENTRY': [2]}))


if __name__ == '__main__':
    unittest.main()
