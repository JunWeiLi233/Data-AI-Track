"""Schema-inspected, bounded SELECT templates for the existing analytics tables."""
import json
import os

DATABASE = 'NYC_TRANSIT_PLANNER'
SCHEMA = 'ANALYTICS'
TABLES = ('TRACTS_PROCESSED', 'STATION_LOCATIONS', 'BASELINE_TRACT_SCORES', 'SCORING_RUNS')
BOROUGHS = ('Manhattan', 'Bronx', 'Brooklyn', 'Queens', 'Staten Island')


def _ident(value):
    return '"' + value.replace('"', '""') + '"'


def table_name(table):
    if table not in TABLES:
        raise ValueError('Unsupported analytics table')
    return '.'.join(map(_ident, (DATABASE, SCHEMA, table)))


def _key_map(keys):
    result = {}
    for key in keys:
        if not isinstance(key, str):
            raise RuntimeError('Snowflake schema contains a non-string field name')
        logical = key.lower()
        if logical in result:
            raise RuntimeError('Snowflake schema has ambiguous case-sensitive fields')
        result[logical] = key
    return result


def _json_value(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


class EvidenceStore:
    """Inspect each actual table before resolving fields. Creates no resources."""
    def __init__(self, cursor):
        self.cursor = cursor
        self.layouts = {}
        self.used_tables = []

    def inspect(self):
        for table in TABLES:
            self.cursor.execute('DESCRIBE TABLE ' + table_name(table), timeout=20)
            columns = {row[0]: str(row[1]).upper() for row in self.cursor.fetchall()}
            flat = _key_map(columns)
            data_column = flat.get('data')
            nested = {}
            if data_column and columns[data_column].startswith(('VARIANT', 'OBJECT')):
                data = _ident(data_column)
                self.cursor.execute('SELECT OBJECT_KEYS(' + data + ') FROM ' + table_name(table)
                                    + ' WHERE IS_OBJECT(' + data + ') LIMIT 1', timeout=20)
                row = self.cursor.fetchone()
                if row and row[0] is not None:
                    keys = _json_value(row[0])
                    if not isinstance(keys, list):
                        raise RuntimeError('Unable to inspect JSON object keys in ' + table)
                    nested = _key_map(keys)
            self.layouts[table] = (flat, data_column, nested)

    def source(self, table, fields):
        """Build a normalized inline projection using inspected field names only."""
        if table not in self.layouts:
            raise RuntimeError('Analytics table must be inspected before querying')
        flat, data, nested = self.layouts[table]
        projections = []
        for logical, sql_type in fields.items():
            if logical in flat:
                expression = _ident(flat[logical])
            elif logical in nested:
                expression = _ident(data) + ':' + _ident(nested[logical])
                expression = f'IFF(IS_NULL_VALUE({expression}), NULL, {expression})'
            else:
                raise RuntimeError(f'{table} is missing required field {logical}; verify its actual schema')
            projections.append(f'{expression}::{sql_type} AS {_ident(logical.upper())}')
        if table not in self.used_tables:
            self.used_tables.append(table)
        return '(SELECT ' + ', '.join(projections) + ' FROM ' + table_name(table) + ')'

    def execute(self, sql, parameters=()):
        self.cursor.execute(sql, tuple(parameters), timeout=20)
        columns = [column[0] for column in self.cursor.description]
        return columns, [dict(zip(columns, row)) for row in self.cursor.fetchmany(50)]

    def selected_run(self):
        runs = self.source('SCORING_RUNS', {'scoring_run_id': 'VARCHAR', 'dataset_id': 'VARCHAR',
                                          'weights': 'VARCHAR', 'methodology': 'VARCHAR'})
        sql = 'SELECT * FROM ' + runs
        run_id = os.getenv('SNOWFLAKE_SCORING_RUN_ID', '').strip()
        params = ()
        if run_id:
            sql += ' WHERE "SCORING_RUN_ID" = %s'
            params = (run_id,)
        columns, rows = self.execute(sql + ' LIMIT 2', params)
        if not rows:
            raise RuntimeError('No matching SCORING_RUNS row; check SNOWFLAKE_SCORING_RUN_ID')
        if len(rows) != 1:
            raise RuntimeError('Multiple scoring runs exist; set SNOWFLAKE_SCORING_RUN_ID')
        run = rows[0]
        if not run['SCORING_RUN_ID'] or not run['DATASET_ID']:
            raise RuntimeError('Scoring run identifiers are missing')
        run['WEIGHTS'] = _json_value(run['WEIGHTS'])
        run['METHODOLOGY'] = _json_value(run['METHODOLOGY'])
        return run, sql

    def retrieve(self, plan, run):
        intent, borough, limit = plan['intent'], plan['borough'], plan['row_limit']
        if intent == 'methodology':
            return None, [], ['SCORING_RUN_ID', 'WEIGHTS', 'METHODOLOGY'], [
                {key: run[key] for key in ('SCORING_RUN_ID', 'WEIGHTS', 'METHODOLOGY')}]
        if intent == 'stations':
            stations = self.source('STATION_LOCATIONS', {
                'dataset_id': 'VARCHAR', 'station_location_id': 'VARCHAR', 'canonical_name': 'VARCHAR',
                'latitude': 'FLOAT', 'longitude': 'FLOAT', 'identity_ambiguous': 'BOOLEAN'})
            sql = 'SELECT * FROM ' + stations + ' WHERE "DATASET_ID" = %s ORDER BY "STATION_LOCATION_ID" LIMIT %s'
            params = [run['DATASET_ID'], limit]
        else:
            tracts = self.source('TRACTS_PROCESSED', {
                'geoid': 'VARCHAR', 'dataset_id': 'VARCHAR', 'borough': 'VARCHAR', 'neighborhood': 'VARCHAR',
                'population': 'NUMBER(18,0)', 'median_household_income': 'FLOAT',
                'median_household_income_missing': 'BOOLEAN', 'median_household_income_topcoded': 'BOOLEAN'})
            scores = self.source('BASELINE_TRACT_SCORES', {
                'geoid': 'VARCHAR', 'dataset_id': 'VARCHAR', 'scoring_run_id': 'VARCHAR',
                'nearest_station_distance_km': 'FLOAT', 'distance_score': 'FLOAT', 'population_score': 'FLOAT',
                'income_disadvantage_score': 'FLOAT', 'income_score_neutral': 'BOOLEAN',
                'priority_score': 'FLOAT', 'rank': 'NUMBER(18,0)'})
            joined = (f' FROM {tracts} t JOIN {scores} s ON t."GEOID" = s."GEOID"'
                      ' AND t."DATASET_ID" = s."DATASET_ID"'
                      ' WHERE s."SCORING_RUN_ID" = %s AND s."DATASET_ID" = %s')
            params = [run['SCORING_RUN_ID'], run['DATASET_ID']]
            if borough:
                joined += ' AND t."BOROUGH" = %s'
                params.append(borough)
            if intent == 'borough_summary':
                sql = ('SELECT t."BOROUGH", COUNT(*) AS "RESIDENTIAL_TRACTS", SUM(t."POPULATION") AS "POPULATION",'
                       ' SUM(IFF(s."NEAREST_STATION_DISTANCE_KM" > %s, t."POPULATION", 0)) AS "DESERT_POPULATION",'
                       ' SUM(t."POPULATION" * s."NEAREST_STATION_DISTANCE_KM")'
                       ' / NULLIF(SUM(IFF(s."NEAREST_STATION_DISTANCE_KM" IS NOT NULL, t."POPULATION", 0)), 0) AS "POPULATION_WEIGHTED_DISTANCE_KM",'
                       ' SUM(IFF(s."NEAREST_STATION_DISTANCE_KM" IS NULL, 1, 0)) AS "TRACTS_WITHOUT_DISTANCE"'
                       + joined + ' AND t."POPULATION" > 0 GROUP BY t."BOROUGH" ORDER BY t."BOROUGH" LIMIT %s')
                params = [1.2] + params + [limit]
            elif intent in ('rankings', 'tract'):
                sql = ('SELECT t."GEOID", t."BOROUGH", t."NEIGHBORHOOD", t."POPULATION",'
                       ' t."MEDIAN_HOUSEHOLD_INCOME", t."MEDIAN_HOUSEHOLD_INCOME_MISSING",'
                       ' t."MEDIAN_HOUSEHOLD_INCOME_TOPCODED", s."NEAREST_STATION_DISTANCE_KM",'
                       ' s."DISTANCE_SCORE", s."POPULATION_SCORE", s."INCOME_DISADVANTAGE_SCORE",'
                       ' s."INCOME_SCORE_NEUTRAL", s."PRIORITY_SCORE", s."RANK"' + joined)
                if intent == 'tract':
                    sql += ' AND t."GEOID" = %s'
                    params.append(plan['geoid'])
                else:
                    sql += ' AND t."POPULATION" > 0 AND s."PRIORITY_SCORE" IS NOT NULL'
                sql += ' ORDER BY s."PRIORITY_SCORE" DESC NULLS LAST, t."GEOID" LIMIT %s'
                params.append(limit)
            else:
                raise ValueError('Unsupported evidence intent')
        columns, rows = self.execute(sql, params)
        return sql, params, columns, rows
