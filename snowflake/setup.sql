-- One-time Snowflake setup for the team. Run as ACCOUNTADMIN in a Snowsight worksheet.
-- Safe to re-run: every statement uses IF NOT EXISTS or is idempotent.
USE ROLE ACCOUNTADMIN;

-- Shared role: everyone on the team works through this role.
CREATE ROLE IF NOT EXISTS TRANSIT_TEAM;
GRANT ROLE TRANSIT_TEAM TO ROLE SYSADMIN;
SET me = CURRENT_USER();
GRANT ROLE TRANSIT_TEAM TO USER IDENTIFIER($me);

-- Smallest warehouse; suspends after 60 idle seconds to save trial credits.
CREATE WAREHOUSE IF NOT EXISTS TRANSIT_WH
  WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE;
GRANT USAGE, OPERATE ON WAREHOUSE TRANSIT_WH TO ROLE TRANSIT_TEAM;

-- Database owned by the team role so anyone can create and replace tables.
CREATE DATABASE IF NOT EXISTS TRANSIT_GAP;
GRANT OWNERSHIP ON DATABASE TRANSIT_GAP TO ROLE TRANSIT_TEAM COPY CURRENT GRANTS;
GRANT OWNERSHIP ON SCHEMA TRANSIT_GAP.PUBLIC TO ROLE TRANSIT_TEAM COPY CURRENT GRANTS;

-- Cortex AI functions (AI_COMPLETE, AI_SUMMARIZE_AGG, Cortex Analyst, ...).
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE TRANSIT_TEAM;

-- If a Cortex model is "not available in your region", uncomment to allow cross-region inference.
-- ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';

-- Teammates: copy once per person, fill in the name and a temporary password.
-- CREATE USER IF NOT EXISTS EMILY
--   PASSWORD = '<temporary password>' MUST_CHANGE_PASSWORD = TRUE
--   DEFAULT_ROLE = TRANSIT_TEAM DEFAULT_WAREHOUSE = TRANSIT_WH
--   DEFAULT_NAMESPACE = TRANSIT_GAP.PUBLIC;
-- GRANT ROLE TRANSIT_TEAM TO USER EMILY;
