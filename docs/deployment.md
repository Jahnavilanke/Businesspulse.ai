# Deployment

## 1. Local (verified)

Requirements: Python 3.13, ~2 GB disk, internet for the first download.

```powershell
./scripts/bp.ps1 setup
./scripts/bp.ps1 pipeline        # ~2 minutes on a laptop
./scripts/bp.ps1 app             # http://localhost:8501
```

Useful runner flags: `--skip-ml`, `--only dbt_build detect_alerts`, `--full-refresh`
(rebuild incremental dbt models). Incremental data: drop CSV files with the workbook
columns into `data/landing/drops/` and re-run; rows at or below the watermark are ignored.
Streaming: `python -m businesspulse.ingestion.events produce --file <csv>` then `consume`.

## 2. Free hosting (GitHub Actions + Streamlit Community Cloud)

Free app hosts don't have the memory or time to run the pipeline, so the work is split:

1. `.github/workflows/build-data.yml` runs the full pipeline on GitHub's free runners and
   publishes `businesspulse-data.tar.gz` (warehouse + app state, ~100 MB) to the release
   tag `data-latest`. It runs on pushes that change the pipeline, monthly, or on demand.
2. Streamlit Community Cloud runs `app.py`. On first start the app sees no local warehouse
   and downloads the bundle (`businesspulse/bundle.py`, URL in `BP_DATA_BUNDLE_URL`; set it
   to `none` to disable).

Setup: share.streamlit.io → Create app → this repository, branch `main`, main file
`app.py`, **Advanced settings → Python 3.13**. App edits (requirements, decisions) are stored
on the host's disk and reset when the app restarts; sign-in is demo identities only.

## 3. Docker Compose (configured; not executed in the development environment)

```bash
cp .env.example .env            # set real passwords
docker compose up -d --build    # postgres, redis, minio, airflow (Celery), app
docker compose --profile monitoring --profile bi --profile ml --profile lineage --profile streaming up -d
```

| Service | URL |
|---|---|
| Streamlit app | http://localhost:8501 |
| Airflow | http://localhost:8080 (admin / `AIRFLOW_ADMIN_PASSWORD`) |
| MinIO console | http://localhost:9001 |
| Superset *(bi)* | http://localhost:8088 |
| Prometheus / Grafana *(monitoring)* | http://localhost:9090 · http://localhost:3000 |
| MLflow *(ml)* | http://localhost:5000 — set `MLFLOW_TRACKING_URI=http://mlflow:5000` |
| Marquez *(lineage)* | http://localhost:5002 — set `OPENLINEAGE_URL=http://marquez:5002` |
| Kafka *(streaming)* | `kafka:9092` — set `BP_KAFKA_BOOTSTRAP=kafka:9092`, `pip install kafka-python` |

Optional services are behind profiles, so the core platform always starts without them.
In compose the lake uses MinIO (`BP_STORAGE_BACKEND=s3`) and the gold layer is published to
Postgres (`publish_postgres`) for Superset / Power BI. Trigger the DAG
`businesspulse_daily` in Airflow; backfill with
`airflow dags backfill businesspulse_daily -s 2011-11-01 -e 2011-11-30`.

## 4. AWS (optional, nothing provisioned automatically)

| Service | Use | How |
|---|---|---|
| S3 | data lake + gold exports | `BP_STORAGE_BACKEND=s3`, `BP_S3_BUCKET`, standard AWS credential chain |
| Glue Data Catalog | table metadata for gold | `python cloud/aws/deploy.py --bucket <b> --apply` |
| Athena | serverless SQL on the gold parquet | queries in `cloud/aws/athena_queries.sql` |
| Redshift | warehouse at scale | `deploy.py --redshift-sql --iam-role <arn>` prints DDL + COPY for a cluster/workgroup you own; dbt target template in `dbt/profiles.yml` (requires `dbt-redshift`) |

`deploy.py` defaults to a **dry run** that prints the plan and DDL. Least-privilege IAM
policy: `cloud/aws/iam_policy.json` (replace `<bucket>`, `<region>`, `<account-id>`).
Costs: S3 storage of the exports is ~100 MB; Athena bills per data scanned (parquet keeps
typical queries to MBs); Redshift is the only always-on cost and is never created by the
project.
