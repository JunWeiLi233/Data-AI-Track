import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import snowflake_client, snowflake_store


class LocalCopilotConnectionTests(unittest.TestCase):
    def test_root_env_login_uses_copilot_context_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            env_file = Path(folder) / '.env'
            env_file.write_text(
                'SNOWFLAKE_ACCOUNT=unit-test-account\nSNOWFLAKE_USER=unit-test-user\n'
                'SNOWFLAKE_PASSWORD=unit-test-placeholder\n'
                'SNOWFLAKE_DATABASE=TRANSIT_GAP\nSNOWFLAKE_SCHEMA=PUBLIC\n'
                'SNOWFLAKE_WAREHOUSE=TRANSIT_WH\nSNOWFLAKE_ROLE=TRANSIT_TEAM\n')
            other = Path(folder) / 'another-directory'
            other.mkdir()
            previous = Path.cwd()
            try:
                os.chdir(other)
                with mock.patch.dict(os.environ, {}, clear=True), \
                     mock.patch.object(snowflake_client, 'ENV_FILE', env_file), \
                     mock.patch('snowflake.connector.connect') as connector:
                    snowflake_store.connect(role='SUBWAY_READER')
                    settings = connector.call_args.kwargs
            finally:
                os.chdir(previous)
        self.assertEqual(settings['account'], 'unit-test-account')
        self.assertEqual(settings['user'], 'unit-test-user')
        self.assertEqual(settings['password'], 'unit-test-placeholder')
        self.assertEqual(settings['database'], 'SUBWAY')
        self.assertEqual(settings['schema'], 'APP')
        self.assertEqual(settings['warehouse'], 'SUBWAY_WH')
        self.assertEqual(settings['role'], 'SUBWAY_READER')
        self.assertNotIn('connection_name', settings)
        self.assertFalse(settings['enable_server_session_keep_alive_auto_detection'])

    def test_process_credentials_take_precedence_and_default_role_is_app(self):
        with tempfile.TemporaryDirectory() as folder:
            env_file = Path(folder) / '.env'
            env_file.write_text('SNOWFLAKE_ACCOUNT=file-account\nSNOWFLAKE_USER=file-user\nSNOWFLAKE_PASSWORD=file-placeholder\n')
            env = {'SNOWFLAKE_ACCOUNT': 'process-account', 'SNOWFLAKE_USER': 'process-user',
                   'SNOWFLAKE_AUTHENTICATOR': 'externalbrowser', 'SNOWFLAKE_PASSWORD': ''}
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(snowflake_client, 'ENV_FILE', env_file), \
                 mock.patch('snowflake.connector.connect') as connector:
                snowflake_store.connect()
                settings = connector.call_args.kwargs
        self.assertEqual(settings['account'], 'process-account')
        self.assertEqual(settings['user'], 'process-user')
        self.assertEqual(settings['authenticator'], 'externalbrowser')
        self.assertNotIn('password', settings)
        self.assertEqual(settings['role'], 'SUBWAY_APP')

    def test_missing_local_credentials_fail_before_connector_call(self):
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(snowflake_client, 'ENV_FILE', Path(folder) / '.env'), \
             mock.patch('snowflake.connector.connect') as connector:
            with self.assertRaisesRegex(RuntimeError, 'Missing SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER'):
                snowflake_store.connect()
        connector.assert_not_called()



if __name__ == '__main__':
    unittest.main()
