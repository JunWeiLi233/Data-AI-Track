-- One-time setup for the planning copilot service user (run as ACCOUNTADMIN).
-- Replace <PUBLIC_KEY> with the body of the service user's RSA public key
-- (base64 between the BEGIN/END lines); the private key never leaves the app host.
use role accountadmin;

create warehouse if not exists SUBWAY_WH
  warehouse_size = 'XSMALL' auto_suspend = 60 auto_resume = true initially_suspended = true;
create database if not exists SUBWAY;
create schema if not exists SUBWAY.APP;

-- SUBWAY_READER: read-only access plus Cortex, used for model-written SQL.
-- SUBWAY_APP: loads tables and owns the Cortex Search service.
create role if not exists SUBWAY_READER;
create role if not exists SUBWAY_APP;
grant role SUBWAY_READER to role SUBWAY_APP;
grant role SUBWAY_APP to role SYSADMIN;

grant usage on warehouse SUBWAY_WH to role SUBWAY_READER;
grant usage on database SUBWAY to role SUBWAY_READER;
grant usage on schema SUBWAY.APP to role SUBWAY_READER;
grant select on future tables in schema SUBWAY.APP to role SUBWAY_READER;
grant select on future views in schema SUBWAY.APP to role SUBWAY_READER;
grant database role SNOWFLAKE.CORTEX_USER to role SUBWAY_READER;
grant create table, create view, create stage, create cortex search service
  on schema SUBWAY.APP to role SUBWAY_APP;

create user if not exists SUBWAY_APP_SVC
  type = service
  default_role = SUBWAY_APP
  default_warehouse = SUBWAY_WH
  default_namespace = 'SUBWAY.APP'
  default_secondary_roles = ()
  rsa_public_key = '<PUBLIC_KEY>'
  comment = 'NYC Subway Optimization app (key-pair auth)';
grant role SUBWAY_APP to user SUBWAY_APP_SVC;
