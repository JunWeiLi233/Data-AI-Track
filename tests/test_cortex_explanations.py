import json
import os
import types
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from backend import cortex_explanations as cortex
from backend.server import Handler

GEOID = '36081157101'
ROW = (Decimal('0.772'), Decimal('6.13412'), Decimal('0.98'), Decimal('0.84'), Decimal('0.15'))


class CortexExplanationTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value
        self.cursor.fetchmany.return_value = [ROW]
        self.cursor.sfqid = 'cortex-query-id'
        self.cursor.fetchone.return_value = (json.dumps({'sentence_order': ['income', 'distance', 'population']}),)

    def test_metrics_bound_query_and_mocked_cortex_completion(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(cortex, '_connect', return_value=self.connection):
            result = cortex.get_tract_explanation(GEOID)
        self.assertEqual(set(result), {'geoid', 'priority_score', 'ai_explanation',
                                       'explanation_source', 'fallback_reason', 'cortex_query_id', 'cortex_model'})
        self.assertEqual(result['explanation_source'], 'snowflake_cortex')
        self.assertIsNone(result['fallback_reason'])
        self.assertEqual(result['cortex_query_id'], 'cortex-query-id')
        self.assertNotIn('Data-based fallback', result['ai_explanation'])
        self.assertEqual(result['priority_score'], 0.772)
        self.assertIn('6.134 km', result['ai_explanation'])
        for text in ('50%', '30%', '20%', '0.9800', '0.8400', '0.1500'):
            self.assertIn(text, result['ai_explanation'])
        self.assertTrue(result['ai_explanation'].index('income disadvantage score') <
                        result['ai_explanation'].index('Nearest-station distance'))
        read, completion = self.cursor.execute.call_args_list
        self.assertIn('NYC_TRANSIT_PLANNER.ANALYTICS.BASELINE_TRACT_SCORES', read.args[0])
        self.assertIn('WHERE DATA:"GEOID"::VARCHAR = %s', read.args[0])
        for field in cortex.METRICS:
            self.assertIn(f'DATA:"{field}"', read.args[0])
        self.assertIn('IFF(IS_NULL_VALUE(DATA:"priority_score"), NULL, DATA:"priority_score")::FLOAT', read.args[0])
        self.assertEqual(read.args[1], (GEOID,))
        self.assertIn('AI_COMPLETE(', completion.args[0])
        self.assertIn('model => %s', completion.args[0])
        self.assertIn('response_format => TYPE OBJECT(sentence_order ARRAY(VARCHAR))', completion.args[0])
        self.assertIn('show_details => FALSE', completion.args[0])
        self.assertIn('return_error_details => TRUE', completion.args[0])
        self.assertEqual(completion.args[1][0], 'llama3.3-70b')
        prompt = completion.args[1][1]
        self.assertIn('6.13412', prompt)
        self.assertIn('Never invent', prompt)
        self.cursor.close.assert_called_once()
        self.connection.close.assert_called_once()

    def test_double_encoded_json_matches_reported_typeerror(self):
        value = {'sentence_order': ['income', 'distance', 'population']}
        self.cursor.fetchone.return_value = (json.dumps(json.dumps(value)),)
        with patch.dict(os.environ, {}, clear=True), patch.object(cortex, '_connect', return_value=self.connection):
            result = cortex.get_tract_explanation(GEOID)
        self.assertEqual(result['explanation_source'], 'snowflake_cortex')
        self.assertIsNone(result['fallback_reason'])
        self.assertNotIn('Data-based fallback', result['ai_explanation'])
        self.assertEqual(result['cortex_query_id'], 'cortex-query-id')

    def test_native_encoded_and_fenced_structured_values(self):
        body = {'sentence_order': ['distance', 'income', 'population']}
        for payload in (body, {'value': body, 'error': None},
                        json.dumps({'value': body, 'error': None}),
                        {'value': json.dumps(json.dumps(body)), 'error': None},
                        '```json\n' + json.dumps(body) + '\n```'):
            with self.subTest(payload_type=type(payload).__name__):
                self.cursor.fetchone.return_value = (payload,)
                with patch.dict(os.environ, {}, clear=True), patch.object(cortex, '_connect', return_value=self.connection):
                    result = cortex.get_tract_explanation(GEOID)
                self.assertEqual(result['explanation_source'], 'snowflake_cortex')
                self.assertIsNone(result['fallback_reason'])

    def test_response_validation_failures_have_safe_distinct_diagnostics(self):
        invalid = [(None, 'empty_completion'), ('{"sentence_order":', 'invalid_json_or_truncated_output'),
                   ({'sentence_order': ['distance', 'distance', 'income']}, 'invalid_sentence_order'),
                   ({'sentence_order': ['distance', 'population', 'income'], 'cost': 'private-secret'}, 'unexpected_response_fields')]
        for payload, expected_reason in invalid:
            self.cursor.fetchone.return_value = (payload,)
            with self.subTest(reason=expected_reason), patch.dict(os.environ, {}, clear=True), \
                 patch.object(cortex, '_connect', return_value=self.connection), \
                 self.assertLogs(cortex.LOGGER, level='ERROR') as captured:
                result = cortex.get_tract_explanation(GEOID)
            self.assertEqual(result['explanation_source'], 'fallback')
            self.assertEqual(result['fallback_reason'], 'cortex_response_validation')
            self.assertIn(expected_reason, '\n'.join(captured.output))
            self.assertNotIn('private-secret', '\n'.join(captured.output))
            self.assertNotIn('private-secret', result['ai_explanation'])

    def test_sql_permissions_and_model_failures_are_distinguished(self):
        cases = [('SQL compilation error: unexpected argument', '42000', 'cortex_sql_error'),
                 ('Insufficient privileges for CORTEX_USER', '42501', 'cortex_permissions_error'),
                 ('Unknown model llama3.3-70b', 'P0000', 'cortex_model_unavailable')]
        for message, state, expected in cases:
            error = RuntimeError(message)
            error.sqlstate, error.sfqid = state, 'failed-cortex-query-id'
            self.cursor.execute.side_effect = [None, error]
            with self.subTest(expected=expected), patch.dict(os.environ, {}, clear=True), \
                 patch.object(cortex, '_connect', return_value=self.connection), \
                 self.assertLogs(cortex.LOGGER, level='ERROR') as captured:
                result = cortex.get_tract_explanation(GEOID)
            self.assertEqual(result['fallback_reason'], expected)
            self.assertEqual(result['explanation_source'], 'fallback')
            self.assertEqual(result['cortex_query_id'], 'failed-cortex-query-id')
            self.assertIn('cortex_sql', '\n'.join(captured.output))
            self.assertIn(expected, '\n'.join(captured.output))

    def test_service_error_envelope_is_not_treated_as_bad_json(self):
        self.cursor.fetchone.return_value = ({'value': None,
            'error': 'Model llama3.3-70b is not available; "token": "not-an-env-secret"'},)
        with patch.dict(os.environ, {}, clear=True), patch.object(cortex, '_connect', return_value=self.connection), \
             self.assertLogs(cortex.LOGGER, level='ERROR') as captured:
            result = cortex.get_tract_explanation(GEOID)
        self.assertEqual(result['fallback_reason'], 'cortex_model_unavailable')
        self.assertEqual(result['explanation_source'], 'fallback')
        log = '\n'.join(captured.output)
        self.assertIn('cortex_service', log)
        self.assertNotIn('not-an-env-secret', log)

    def test_missing_tract_and_ambiguous_scoring_runs(self):
        for rows, status in (([], 404), ([ROW, ROW], 409)):
            self.cursor.fetchmany.return_value = rows
            with self.subTest(status=status), patch.dict(os.environ, {}, clear=True), \
                 patch.object(cortex, '_connect', return_value=self.connection):
                with self.assertRaises(cortex.ExplanationError) as error:
                    cortex.get_tract_explanation(GEOID)
                self.assertEqual(error.exception.status, status)
        self.cursor.fetchone.assert_not_called()

    def test_missing_credentials_and_invalid_geoid(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(cortex.ExplanationError) as error:
                cortex.get_tract_explanation(GEOID)
            self.assertEqual(error.exception.status, 503)
        with patch.object(cortex, '_connect') as connect:
            with self.assertRaises(ValueError):
                cortex.get_tract_explanation("x' OR 1=1 --")
            connect.assert_not_called()

    def test_query_error_is_sanitized_and_cortex_error_falls_back(self):
        self.cursor.execute.side_effect = RuntimeError('private-password-secret')
        with patch.dict(os.environ, {'SNOWFLAKE_PASSWORD': 'private-password-secret'}, clear=True), \
             patch.object(cortex, '_connect', return_value=self.connection):
            with self.assertRaises(cortex.ExplanationError) as error:
                cortex.get_tract_explanation(GEOID)
        self.assertEqual(error.exception.status, 503)
        self.assertNotIn('private-password-secret', str(error.exception))
        self.cursor.execute.side_effect = [None, RuntimeError('private-password-secret')]
        with patch.dict(os.environ, {'SNOWFLAKE_PASSWORD': 'private-password-secret'}, clear=True), \
             patch.object(cortex, '_connect', return_value=self.connection):
            result = cortex.get_tract_explanation(GEOID)
        self.assertIn('Data-based fallback', result['ai_explanation'])
        self.assertNotIn('private-password-secret', json.dumps(result))
        self.assertEqual(result['priority_score'], 0.772)

    def test_generated_claims_rejected_and_null_priority_retained(self):
        self.cursor.fetchone.return_value = ('{"sentence_order":["income","distance","population"],"cost":"$1 million"}',)
        self.cursor.fetchmany.return_value = [(None, *ROW[1:])]
        with patch.dict(os.environ, {}, clear=True), patch.object(cortex, '_connect', return_value=self.connection):
            result = cortex.get_tract_explanation(GEOID)
        self.assertIsNone(result['priority_score'])
        self.assertIn('no residential priority score', result['ai_explanation'])
        self.assertIn('Data-based fallback', result['ai_explanation'])
        self.assertNotIn('$1 million', result['ai_explanation'])
        json.dumps(result, allow_nan=False)

    def test_query_error_diagnostics_redact_credentials_and_preserve_codes(self):
        error = RuntimeError("SQL compilation error: invalid identifier 'PRIORITY_SCORE'; password=not-env-secret; "
                             "user configured-password Bearer other-secret https://account.example/?token=url-secret")
        error.errno, error.sqlstate, error.sfqid = 904, '42000', 'test-query-id'
        self.cursor.execute.side_effect = error
        with patch.dict(os.environ, {'SNOWFLAKE_PASSWORD': 'configured-password'}, clear=True), \
             patch.object(cortex, '_connect', return_value=self.connection), \
             self.assertLogs(cortex.LOGGER, level='ERROR') as captured:
            with self.assertRaises(cortex.ExplanationError):
                cortex.get_tract_explanation(GEOID)
        log = '\n'.join(captured.output)
        for value in ('invalid identifier', '904', '42000', 'test-query-id', 'tract_query', 'sql_query'):
            self.assertIn(value, log)
        for secret in ('configured-password', 'not-env-secret', 'other-secret', 'url-secret', 'account.example'):
            self.assertNotIn(secret, log)

    def test_authentication_and_permissions_are_distinct_diagnostics(self):
        fake = types.ModuleType('snowflake')
        fake.connector = MagicMock()
        error = RuntimeError('Incorrect username or password for private-user; token=private-token')
        error.errno, error.sqlstate = 390100, '28000'
        fake.connector.connect.side_effect = error
        env = {'SNOWFLAKE_ACCOUNT': 'private-account', 'SNOWFLAKE_USER': 'private-user',
               'SNOWFLAKE_PASSWORD': 'private-password'}
        with patch.dict(os.environ, env, clear=True), patch.dict('sys.modules', {'snowflake': fake}), \
             self.assertLogs(cortex.LOGGER, level='ERROR') as captured:
            with self.assertRaises(cortex.ExplanationError) as failure:
                cortex._connect()
        log = '\n'.join(captured.output)
        self.assertIn('authentication', log)
        self.assertIn('connect', log)
        self.assertEqual(failure.exception.status, 503)
        for secret in ('private-user', 'private-password', 'private-account', 'private-token'):
            self.assertNotIn(secret, log)
        error = RuntimeError('Insufficient privileges to operate on table')
        error.sqlstate = '42501'
        self.cursor.execute.side_effect = error
        with patch.object(cortex, '_connect', return_value=self.connection), \
             self.assertLogs(cortex.LOGGER, level='ERROR') as captured:
            with self.assertRaises(cortex.ExplanationError):
                cortex.get_tract_explanation(GEOID)
        self.assertIn('permissions_or_object_access', '\n'.join(captured.output))

    def test_env_connection_parameters_and_run_filter(self):
        fake = types.ModuleType('snowflake')
        fake.connector = MagicMock()
        env = {'SNOWFLAKE_ACCOUNT': 'test-account', 'SNOWFLAKE_USER': 'test-user',
               'SNOWFLAKE_TOKEN': 'unit-test-placeholder', 'SNOWFLAKE_ROLE': 'test-role',
               'SNOWFLAKE_WAREHOUSE': 'test-warehouse', 'SNOWFLAKE_SCORING_RUN_ID': 'run-id'}
        with patch.dict(os.environ, env, clear=True), patch.dict('sys.modules', {'snowflake': fake}):
            cortex._connect()
            kwargs = fake.connector.connect.call_args.kwargs
            self.assertEqual(kwargs['authenticator'], 'oauth')
            self.assertEqual(kwargs['token'], 'unit-test-placeholder')
            self.assertEqual(kwargs['warehouse'], 'test-warehouse')
            self.assertNotIn('password', kwargs)
            with patch.object(cortex, '_connect', return_value=self.connection):
                cortex.get_tract_explanation(GEOID)
        query = self.cursor.execute.call_args_list[0]
        self.assertIn('AND DATA:"scoring_run_id"::VARCHAR = %s', query.args[0])
        self.assertEqual(query.args[1], (GEOID, 'run-id'))


class ExplanationRouteTests(unittest.TestCase):
    def handler(self, path):
        handler = Handler.__new__(Handler)
        handler.path = path
        handler._json = MagicMock()
        return handler

    def test_get_route_returns_mocked_snowflake_result(self):
        handler = self.handler('/api/tracts/' + GEOID + '/explanation')
        expected = {'geoid': GEOID, 'priority_score': 0.772, 'ai_explanation': 'Verified metrics.'}
        with patch('backend.server.cortex_explanations.get_tract_explanation', return_value=expected) as call:
            handler.do_GET()
        call.assert_called_once_with(GEOID)
        handler._json.assert_called_once_with(200, expected)

    def test_route_reports_accepted_cortex_provenance(self):
        handler = self.handler('/api/tracts/' + GEOID + '/explanation')
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchmany.return_value = [ROW]
        cursor.sfqid = 'cortex-query-id'
        cursor.fetchone.return_value = (json.dumps({'value': {'sentence_order': ['income', 'distance', 'population']}, 'error': None}),)
        with patch.dict(os.environ, {}, clear=True), patch.object(cortex, '_connect', return_value=connection):
            handler.do_GET()
        status, result = handler._json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(result['explanation_source'], 'snowflake_cortex')
        self.assertEqual(result['cortex_query_id'], 'cortex-query-id')
        self.assertIsNone(result['fallback_reason'])
        self.assertNotIn('Data-based fallback', result['ai_explanation'])
        self.assertIn('AI_COMPLETE', cursor.execute.call_args_list[1].args[0])

    def test_route_service_errors_and_invalid_geoid(self):
        handler = self.handler('/api/tracts/' + GEOID + '/explanation')
        with patch('backend.server.cortex_explanations.get_tract_explanation',
                   side_effect=cortex.ExplanationError(404, 'Census tract not found')):
            handler.do_GET()
        self.assertEqual(handler._json.call_args.args[0], 404)
        self.assertIsNone(handler._json.call_args.args[1]['ai_explanation'])
        handler = self.handler('/api/tracts/bad/explanation')
        handler.do_GET()
        self.assertEqual(handler._json.call_args.args[0], 400)


if __name__ == '__main__':
    unittest.main()
