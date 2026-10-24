import json
import os
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from backend.explanations import explain_tract


def row():
    return {'GEOID': '36081157101', 'population': 8073,
            'median_household_income': 90575, 'median_household_income_topcoded': False,
            'nearest_station_distance_km': 6.13412, 'distance_score': 0.98,
            'population_score': 0.84, 'income_disadvantage_score': 0.15,
            'priority_score': 0.772}


class ExplanationTests(unittest.TestCase):
    def test_mocked_external_response_uses_verified_facts(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'choices': [{'message': {'content': json.dumps({
                'sentence_order': ['population', 'distance', 'income']})}}]}).encode()
        env = {'LLM_API_KEY': 'unit-test-placeholder', 'LLM_PROVIDER': 'openai-compatible',
               'LLM_MODEL': 'test-model', 'LLM_BASE_URL': 'https://llm.example/v1'}
        with patch.dict(os.environ, env, clear=True), \
             patch('backend.explanations.urlopen', return_value=response) as call:
            result = explain_tract(row())
        self.assertEqual(result['source'], 'llm')
        self.assertIsNone(result['fallback_reason'])
        text = result['explanation']
        self.assertIn('6.134 km', text)
        self.assertIn('8,073', text)
        self.assertIn('$90,575', text)
        self.assertIn('0.7720', text)
        self.assertTrue(text.index('Reported population') < text.index('The tract reference point'))
        for weight in ('50%', '30%', '20%'):
            self.assertIn(weight, text)
        request = call.call_args.args[0]
        self.assertEqual(request.full_url, 'https://llm.example/v1/chat/completions')
        payload = json.loads(request.data)
        facts = json.loads(payload['messages'][1]['content'])
        self.assertEqual(facts['facts']['population'], 8073)
        self.assertEqual(facts['weights'], {'distance': 0.5, 'population': 0.3, 'income': 0.2})
        self.assertNotIn('unit-test-placeholder', json.dumps(result))

    def test_no_key_uses_local_explanation_without_network(self):
        with patch.dict(os.environ, {}, clear=True), patch('backend.explanations.urlopen') as call:
            result = explain_tract(row())
        call.assert_not_called()
        self.assertEqual(result['source'], 'fallback')
        self.assertEqual(result['fallback_reason'], 'no_api_key')
        self.assertIn('0.7720', result['explanation'])

    def test_missing_income_and_ineligible_tract_are_not_imputed(self):
        data = row()
        data.update(population=0, median_household_income=None, income_disadvantage_score=0.5,
                    population_score=0, priority_score=None)
        with patch.dict(os.environ, {}, clear=True):
            result = explain_tract(data)
        self.assertIn('no residential priority score or rank', result['explanation'])
        self.assertIn('income is unavailable', result['explanation'])
        self.assertIsNone(result['facts']['median_household_income'])
        self.assertIn('neutral income component', result['explanation'])

    def test_provider_failure_or_fabricated_prose_falls_back_safely(self):
        env = {'OPENAI_API_KEY': 'unit-test-placeholder'}
        with patch.dict(os.environ, env, clear=True), \
             patch('backend.explanations.urlopen', side_effect=URLError('private provider error')):
            result = explain_tract(row())
        self.assertEqual(result['source'], 'fallback')
        self.assertNotIn('private provider error', json.dumps(result))
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'choices': [{'message': {'content': '{"explanation":"Invented neighborhood costs $1M"}'}}]}).encode()
        with patch.dict(os.environ, env, clear=True), patch('backend.explanations.urlopen', return_value=response):
            result = explain_tract(row())
        self.assertEqual(result['source'], 'fallback')
        self.assertNotIn('Invented neighborhood', result['explanation'])
        self.assertNotIn('$1M', result['explanation'])

    def test_snowflake_uppercase_decimals_and_topcoded_income(self):
        data = {key.upper(): Decimal(str(value)) if isinstance(value, (int, float)) and not isinstance(value, bool)
                else value for key, value in row().items()}
        data['MEDIAN_HOUSEHOLD_INCOME'] = Decimal('250000')
        data['MEDIAN_HOUSEHOLD_INCOME_TOPCODED'] = True
        with patch.dict(os.environ, {}, clear=True):
            result = explain_tract(data)
        self.assertIn('at least $250,000', result['explanation'])
        json.dumps(result, allow_nan=False)

    def test_invalid_data_does_not_get_an_explanation(self):
        for change in ({'GEOID': 36081157101}, {'nearest_station_distance_km': float('inf')},
                       {'priority_score': 0.1}, {'weights': {'distance': 1, 'population': 0, 'income': 0}}):
            data = {**row(), **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                explain_tract(data)


if __name__ == '__main__':
    unittest.main()
