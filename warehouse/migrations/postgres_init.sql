-- Executed once by the postgres container: one database per service and a read-only BI role.
create database airflow;
create database mlflow;
create database marquez;
\connect businesspulse
create schema if not exists gold;
create schema if not exists airbyte_raw;
-- Read-only role for Superset / Power BI. Set its password manually:
--   alter role bi_reader password '<secret>';
do $$ begin
  if not exists (select from pg_roles where rolname = 'bi_reader') then create role bi_reader login; end if;
end $$;
grant usage on schema gold to bi_reader;
alter default privileges in schema gold grant select on tables to bi_reader;
