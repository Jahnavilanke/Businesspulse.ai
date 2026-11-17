#!/bin/sh
# Initialise Superset and register the read-only gold layer in PostgreSQL as a database.
# Gold tables are published to Postgres by the pipeline's publish_postgres stage (BP_POSTGRES_DSN).
set -e
superset db upgrade
superset fab create-admin --username admin --firstname BI --lastname Admin \
  --email admin@example.invalid --password "${ADMIN_PASSWORD:-admin}" || true
superset init
# Connect in the UI: Settings > Database connections > PostgreSQL
#   postgresql://bi_reader:<password>@postgres:5432/businesspulse  (schema: gold)
superset run -h 0.0.0.0 -p 8088 --with-threads
