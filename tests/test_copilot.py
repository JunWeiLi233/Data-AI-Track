import os
import unittest

from backend import copilot


class SqlGuardTests(unittest.TestCase):
    def test_accepts_single_select_or_with(self):
        self.assertEqual(copilot.clean_sql("```sql\nSELECT 1 AS x;\n```"), "SELECT 1 AS x")
        self.assertTrue(copilot.clean_sql("with t as (select 1) select * from t"))

    def test_declines_and_rejects_unsafe_sql(self):
        for text in ["NONE", "", "select 1; drop table TRACTS", "delete from TRACTS",
                     "select * from TRACTS where 1=1 union select 1 from x; create table y (a int)",
                     "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x", "show tables", "call foo()"]:
            self.assertIsNone(copilot.clean_sql(text), text)


class ParsingTests(unittest.TestCase):
    def test_completion_json_string_is_unquoted(self):
        self.assertEqual(copilot.parse_completion('"Hello\\nworld"'), "Hello\nworld")
        self.assertEqual(copilot.parse_completion("plain text"), "plain text")

    def test_recommendations_are_compacted(self):
        payload = {"goal": "access", "impact": {"residents_added": 5},
                   "stations": [{"rank": 1, "name": "A", "latitude": 40.7, "residents": 10}]}
        compact = copilot._compact(payload)
        self.assertEqual(compact["stations"][0]["neighborhood"], "A")
        self.assertEqual(compact["stations"][0]["residents_newly_within_10_min_walk"], 10)
        self.assertNotIn("latitude", compact["stations"][0])
        self.assertIsNone(copilot._compact("not a dict"))

    def test_rejects_empty_or_long_questions(self):
        for question in ["", "   ", "x" * 1001]:
            with self.assertRaises(ValueError):
                copilot.ask(question)


@unittest.skipUnless(os.environ.get("SNOWFLAKE_LIVE_TESTS"), "set SNOWFLAKE_LIVE_TESTS=1 to call Snowflake")
class LiveCopilotTests(unittest.TestCase):
    def test_grounded_answer_with_sql(self):
        result = copilot.ask("How many residents live in transit-desert tracts in each borough?")
        self.assertTrue(result["answer"])
        self.assertTrue(result["sources"])
        self.assertIsNotNone(result["sql"])
        self.assertIsNone(result["sql_error"])
        self.assertGreaterEqual(len(result["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
