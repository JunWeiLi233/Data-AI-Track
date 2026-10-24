import io
import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch, MagicMock

from backend import copilot
from backend.copilot_data import EvidenceStore, TABLES
from backend.export_pipeline import SCHEMA
from backend.server import Handler


def plan(intent='rankings', borough='', geoid='', row_limit=5):
    return dict(intent=intent, borough=borough, geoid=geoid, row_limit=row_limit)


class FakeCursor:
    def __init__(self, selected=None, variant=False):
        self.plan = selected or plan()
        self.variant = variant
        self.executed, self.current, self.description, self.prompts = [], [], [], []
        self.missing_field = None
        self.empty_evidence = False
        self.multiple_runs = False
        self.fail_summary = False
        self.closed = False

    def execute(self, sql, parameters=(), timeout=None):
        self.executed.append((sql, tuple(parameters)))
        if sql.startswith('DESCRIBE TABLE'):
            table = next(table for table in TABLES if sql.endswith('"' + table + '"'))
            self.current = [('DATA', 'VARIANT')] if self.variant else [
                (field.upper(), kind) for field, (kind, _, _) in SCHEMA[table].items() if field != self.missing_field]
        elif sql.startswith('SELECT OBJECT_KEYS'):
            table = next(table for table in TABLES if '"' + table + '"' in sql)
            keys = [field for field in SCHEMA[table] if field != self.missing_field]
            self.current = [(json.dumps(keys),)]
        elif sql.startswith('SELECT AI_COMPLETE'):
            self.prompts.append(parameters[1])
            if 'TYPE OBJECT(intent' in sql:
                result = self.plan
            elif self.fail_summary:
                raise RuntimeError('provider password=private-placeholder')
            else:
                result = {'answer': 'The selected evidence shows tract 36081157101 has 8,073 residents. Distances are straight-line proxies.'}
            self.current = [(json.dumps({'value': result, 'error': None}),)]
        elif '"SCORING_RUNS"' in sql:
            row = ('run-1', 'dataset-1', json.dumps({'distance': 0.5, 'population': 0.3, 'income': 0.2}),
                   json.dumps({'distance_proxy': 'straight-line Haversine'}))
            self.description = [(key,) for key in ('SCORING_RUN_ID', 'DATASET_ID', 'WEIGHTS', 'METHODOLOGY')]
            self.current = [row, row] if self.multiple_runs else [row]
        else:
            if '"DESERT_POPULATION"' in sql:
                row = {'BOROUGH': 'Queens', 'RESIDENTIAL_TRACTS': 2, 'POPULATION': 120,
                       'DESERT_POPULATION': 70, 'POPULATION_WEIGHTED_DISTANCE_KM': Decimal('1.5'),
                       'TRACTS_WITHOUT_DISTANCE': 0}
            elif '"STATION_LOCATIONS"' in sql:
                row = {'DATASET_ID': 'dataset-1', 'STATION_LOCATION_ID': 'loc-1', 'CANONICAL_NAME': 'Existing station',
                       'LATITUDE': Decimal('40.7'), 'LONGITUDE': Decimal('-73.9'), 'IDENTITY_AMBIGUOUS': True}
            else:
                row = {'GEOID': '36081157101', 'BOROUGH': 'Queens', 'NEIGHBORHOOD': 'Bellerose',
                       'POPULATION': 8073, 'MEDIAN_HOUSEHOLD_INCOME': None, 'MEDIAN_HOUSEHOLD_INCOME_MISSING': True,
                       'MEDIAN_HOUSEHOLD_INCOME_TOPCODED': False, 'NEAREST_STATION_DISTANCE_KM': Decimal('6.134'),
                       'DISTANCE_SCORE': Decimal('0.98'), 'POPULATION_SCORE': Decimal('0.84'),
                       'INCOME_DISADVANTAGE_SCORE': Decimal('0.5'), 'INCOME_SCORE_NEUTRAL': True,
                       'PRIORITY_SCORE': Decimal('0.842'), 'RANK': 1}
            self.description = [(key,) for key in row]
            self.current = [] if self.empty_evidence else [tuple(row.values())]
        return self

    def fetchone(self):
        return self.current[0] if self.current else None

    def fetchall(self):
        return list(self.current)

    def fetchmany(self, count):
        return self.current[:count]

    def close(self):
        self.closed = True


class CopilotTests(unittest.TestCase):
    def ask(self, cursor, **kwargs):
        connection = MagicMock()
        connection.cursor.return_value = cursor
        with patch.dict(os.environ, {}, clear=True), patch.object(copilot.snowflake_client, 'connect', return_value=connection) as connect:
            result = copilot.ask(**{'question': 'Which tracts need investment?', **kwargs})
        self.assertEqual(connect.call_args.kwargs['database'], 'NYC_TRANSIT_PLANNER')
        self.assertEqual(connect.call_args.kwargs['schema'], 'ANALYTICS')
        self.assertNotIn('role', connect.call_args.kwargs)
        connection.close.assert_called_once()
        self.assertTrue(cursor.closed)
        return result

    def test_flat_schema_rankings_and_frontend_contract(self):
        cursor = FakeCursor(plan(borough='Queens', row_limit=3))
        result = self.ask(cursor)
        self.assertEqual(result['model'], 'llama3.3-70b')
        self.assertEqual(result['answer_source'], 'cortex')
        self.assertIsNone(result['sql_error'])
        self.assertEqual(result['rows'][0]['GEOID'], '36081157101')
        self.assertIsNone(result['rows'][0]['MEDIAN_HOUSEHOLD_INCOME'])
        self.assertEqual(len([sql for sql, _ in cursor.executed if sql.startswith('DESCRIBE')]), 4)
        self.assertTrue(cursor.executed[0][0].startswith('DESCRIBE'))
        evidence = next((sql, args) for sql, args in cursor.executed if '"BOROUGH" = %s' in sql)
        self.assertEqual(evidence[1], ('run-1', 'dataset-1', 'Queens', 3))
        self.assertIn('t."DATASET_ID" = s."DATASET_ID"', evidence[0])
        self.assertIn('t."POPULATION" > 0', evidence[0])
        for key in ('answer', 'model', 'sources', 'sql', 'sql_error', 'columns', 'rows'):
            self.assertIn(key, result)
        for sql, _ in cursor.executed:
            self.assertNotIn('SUBWAY', sql)
            self.assertNotIn('SEARCH_PREVIEW', sql.upper())
        json.dumps(result, allow_nan=False)

    def test_variant_schema_uses_observed_json_keys(self):
        cursor = FakeCursor(plan('tract', geoid='36081157101'), variant=True)
        result = self.ask(cursor)
        self.assertEqual(result['answer_source'], 'cortex')
        self.assertIn('"DATA":"GEOID"', result['sql'])
        self.assertIn('"DATA":"priority_score"', result['sql'])
        self.assertIn('IS_NULL_VALUE', result['sql'])
        self.assertTrue(any(sql.startswith('SELECT OBJECT_KEYS') for sql, _ in cursor.executed))
        self.assertIn('36081157101', next(args for sql, args in cursor.executed if sql == result['sql']))

    def test_borough_summary_and_station_queries(self):
        cursor = FakeCursor(plan('borough_summary'))
        result = self.ask(cursor, question='How many people live in transit deserts by borough?')
        self.assertEqual(result['rows'][0]['DESERT_POPULATION'], 70)
        self.assertIn('GROUP BY', result['sql'])
        parameters = next(args for sql, args in cursor.executed if sql == result['sql'])
        self.assertEqual(parameters, (1.2, 'run-1', 'dataset-1', 5))
        result = self.ask(FakeCursor(plan('stations')))
        self.assertTrue(result['rows'][0]['IDENTITY_AMBIGUOUS'])
        self.assertIn('"STATION_LOCATIONS"', result['sql'])

    def test_methodology_and_recommendations_preserve_history_and_context(self):
        result = self.ask(FakeCursor(plan('methodology')), question='How are scores calculated?')
        self.assertEqual(result['rows'][0]['WEIGHTS']['distance'], 0.5)
        self.assertIn('"SCORING_RUNS"', result['sql'])
        cursor = FakeCursor(plan('recommendations'))
        recs = {'goal': 'access', 'stations': [{'rank': 1, 'name': 'Shown place', 'residents': 20}], 'impact': {}}
        result = self.ask(cursor, question='Explain those suggestions', history=[{'role': 'user', 'text': 'Previous question'}], recommendations=recs)
        self.assertIn('Previous question', cursor.prompts[0])
        self.assertIn('residents_newly_within_10_min_walk', cursor.prompts[1])
        self.assertIn('Shown place', cursor.prompts[1])
        self.assertIsNone(result['sql'])
        self.assertEqual(result['sources'][0]['kind'], 'app_context')

    def test_unsupported_missing_rows_or_recommendations_do_not_invent(self):
        result = self.ask(FakeCursor(plan('unsupported')), question='What will construction cost?')
        self.assertEqual(result['answer_source'], 'unsupported')
        self.assertEqual(result['rows'], [])
        cursor = FakeCursor()
        cursor.empty_evidence = True
        result = self.ask(cursor)
        self.assertEqual(result['answer_source'], 'missing_data')
        self.assertEqual(len(cursor.prompts), 1)
        result = self.ask(FakeCursor(plan('recommendations')))
        self.assertEqual(result['answer_source'], 'missing_data')

    def test_injected_plan_values_cannot_be_executed_as_sql(self):
        for bad in ({**plan(), 'sql': 'DROP TABLE X'}, plan(borough="Queens'; DROP TABLE X"),
                    plan('tract', geoid="x' OR 1=1"), plan(row_limit=1000)):
            cursor = FakeCursor(bad)
            result = self.ask(cursor)
            self.assertEqual(result['answer_source'], 'fallback')
            self.assertFalse(any('"TRACTS_PROCESSED" t' in sql for sql, _ in cursor.executed))
            self.assertTrue(all('DROP TABLE' not in sql for sql, _ in cursor.executed))

    def test_schema_and_run_selection_fail_closed(self):
        cursor = FakeCursor()
        cursor.multiple_runs = True
        with self.assertRaisesRegex(RuntimeError, 'scoring run'):
            self.ask(cursor)
        cursor = FakeCursor()
        cursor.missing_field = 'dataset_id'
        with self.assertRaisesRegex(RuntimeError, 'analytics tables'):
            self.ask(cursor)
        store = EvidenceStore(FakeCursor())
        with self.assertRaisesRegex(RuntimeError, 'inspected'):
            store.source('TRACTS_PROCESSED', {'geoid': 'VARCHAR'})

    def test_answer_failure_keeps_real_evidence_and_sanitizes_errors(self):
        cursor = FakeCursor()
        cursor.fail_summary = True
        with self.assertLogs('backend.cortex_explanations', level='ERROR') as logs:
            result = self.ask(cursor)
        self.assertEqual(result['answer_source'], 'fallback')
        self.assertEqual(result['rows'][0]['POPULATION'], 8073)
        self.assertNotIn('private-placeholder', json.dumps(result))
        self.assertNotIn('private-placeholder', '\n'.join(logs.output))

    def test_shared_helper_configuration_error_remains_actionable(self):
        with patch.object(copilot.snowflake_client, 'connect', side_effect=RuntimeError('Missing SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER')):
            with self.assertRaisesRegex(RuntimeError, 'Missing SNOWFLAKE_ACCOUNT'):
                copilot.ask('Explain scores')

    def test_parsing_compaction_and_question_validation(self):
        self.assertEqual(copilot.parse_completion('"Hello\\nworld"'), 'Hello\nworld')
        self.assertEqual(copilot.parse_completion('plain text'), 'plain text')
        self.assertEqual(copilot._compact({'stations': [{'name': 'A', 'residents': 10}]})['stations'][0]['neighborhood'], 'A')
        for value in ('', '  ', 'x' * 1001, None):
            with self.assertRaises(ValueError):
                copilot.ask(value)

    def test_http_chat_contract_remains_unchanged(self):
        handler = Handler.__new__(Handler)
        handler.path = '/api/chat'
        request = {'question': 'Explain scores', 'history': [], 'recommendations': {}}
        raw = json.dumps(request).encode()
        handler.headers = {'Content-Length': str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler._json = MagicMock()
        response = {'answer': 'Evidence answer', 'model': 'llama3.3-70b', 'sources': [], 'sql': None,
                    'sql_error': None, 'columns': [], 'rows': []}
        with patch('backend.server.copilot.ask', return_value=response) as ask:
            handler.do_POST()
        ask.assert_called_once_with('Explain scores', {}, [])
        handler._json.assert_called_once_with(200, response)


@unittest.skipUnless(os.environ.get('SNOWFLAKE_LIVE_TESTS'), 'set SNOWFLAKE_LIVE_TESTS=1 with local credentials')
class LiveCopilotTests(unittest.TestCase):
    def test_real_borough_question(self):
        result = copilot.ask('How many residents live in transit-desert tracts in each borough?')
        self.assertEqual(result['answer_source'], 'cortex')
        self.assertTrue(result['sources'])
        self.assertTrue(result['rows'])
        self.assertIsNone(result['sql_error'])


if __name__ == '__main__':
    unittest.main()
