# BusinessPulse AI

**End-to-end business intelligence, analytics and decision-intelligence platform for a
multi-channel retailer** — from raw source files to governed KPIs, statistics, forecasts,
root-cause investigation, alerts, what-if simulation and a tracked decision register.

```
DATA COLLECTION → INGESTION → ENGINEERING → QUALITY → WAREHOUSE → SQL ANALYTICS → BI
              → PREDICTIVE ANALYTICS → RECOMMENDATIONS → DECISION TRACKING
```

> **Data honesty.** The primary data is the real **UCI Online Retail II** dataset
> (CC BY 4.0, 1,067,371 invoice lines, 2009-12-01 → 2011-12-09). No public inventory or
> marketing data exists for this retailer, so those modules run on **clearly labelled,
> seeded synthetic data**. Every synthetic row carries `is_synthetic = true`, every page
> that uses it shows a banner, and nothing synthetic is ever joined to real transactions
> or presented as real performance. Gross profit is shown as *unavailable* because the
> source has no product cost. Set `BP_ENABLE_SYNTHETIC=false` to disable those modules.

---

## Quick start (local, no Docker needed)

```powershell
./scripts/bp.ps1 setup      # .venv + requirements (Python 3.13)
./scripts/bp.ps1 pipeline   # download → validate → bronze/silver/gold → ML → alerts → exports (~2 min)
./scripts/bp.ps1 app        # http://localhost:8501 – pick a demo identity in the sidebar
./scripts/bp.ps1 test       # 50+ tests incl. headless render of every page
```

Linux/macOS: `make setup pipeline app test`. Full platform with Airflow/Celery/Postgres/
Redis/MinIO: `docker compose up -d` (see [docs/deployment.md](docs/deployment.md)).

## What the latest local run produced (real numbers, not illustrations)

| Area | Result |
|---|---|
| Ingestion | 1,067,371 bronze rows from 2 Excel sheets; re-run skipped as duplicate (content hash) |
| Quality | 40,317 rows quarantined (34,335 exact duplicates incl. the Dec-2010 sheet overlap, 6,152 zero-price product lines, 3,457 stock write-offs, 17 test records, 1 sign error); 1,027,054 silver rows; quality score 0.962 vs contract minimum 0.90; 16/16 expectations passed |
| Warehouse | dbt: 28 models + 48 data tests, all passing; star schema with 5,939 customers, 4,739 products, £18.93M net product revenue |
| Revenue forecast (12-week holdout) | seasonal naive WAPE **0.148** beat XGBoost (0.186) and 4-week MA (0.418) → seasonal naive used |
| Demand forecast (top 50 SKUs, 8 weeks) | 4-week MA WAPE **0.528** beat XGBoost (0.590) → baseline used |
| Churn (90-day, out-of-time) | logistic regression ROC AUC **0.789** / PR AUC 0.810; XGBoost 0.786 / 0.801 |
| CLV (180-day, out-of-time) | XGBoost-Tweedie Spearman 0.589 vs persistence 0.567, but MAE worse (580 vs 549) |
| Alerts | 13 on actual data (7 returns spikes, 4 revenue declines, 2 retention drops) + 15 on synthetic data |

Advanced models did **not** always win; the platform reports that and uses the better baseline.

## Platform map

| Layer | Implementation |
|---|---|
| Acquisition | `businesspulse/ingestion/acquire.py` – download, workbook validation, provenance manifest (sha256, license, DOI) |
| Ingestion | Excel (full load), CSV drops (incremental, high-watermark), REST API (ECB FX via Frankfurter), Kafka events (`ingestion/events.py`, file-bus fallback), Airbyte connector configs + client |
| Storage | S3-style lake (`data/lake`, or S3/MinIO with `BP_STORAGE_BACKEND=s3`): bronze → quarantine → silver; DuckDB warehouse; SQLite app state (Postgres in compose) |
| Orchestration | `airflow/dags/businesspulse_daily.py` (CeleryExecutor, retries + backoff, backfills, single writer) and the identical local runner `python -m businesspulse.pipeline.runner` |
| Quality & governance | 15 row-level rules with quarantine, GE-format expectation suite (native DuckDB evaluator, optional GX), quality scores, schema-drift snapshots, data contract gate, OpenLineage events, data dictionary, glossary |
| Analytics engineering | dbt project: bronze / silver / gold, 8 required facts+dims, 5 metric marts, incremental `fact_sales`, reusable metric macros, generic + singular business tests |
| Analytics | governed KPI layer, EDA/profiling, trend/seasonality, hypothesis tests, cohorts, RFM + K-Means, variance, robust-z + Isolation Forest anomalies |
| ML | forecasting, demand, churn (SHAP), CLV; time-based validation; MLflow + `ops.model_runs` |
| Decision intelligence | grounded insight engine (optional Claude phrasing with numeric grounding check), alert workflow, root-cause toolkit, simulator, BA workspace, decision register |
| BI | 14-page Streamlit app, Superset profile, Power BI star-schema export + DAX library |
| Monitoring | JSON logs, Prometheus metrics/exporter + alert rules, Grafana dashboard |
| Cloud (optional) | S3 / Glue / Athena / Redshift via `cloud/aws/deploy.py` (dry run by default) |

## Two end-to-end workflows on the same trusted data

**Data Analyst:** ingest → clean → validate (Data Operations) → query (SQL Studio) →
trends & EDA → KPIs → statistics → forecasts (Predictive) → dashboards → export.
**Business Analyst:** requirements → KPI targets → performance gaps → root cause →
process documentation → improvements → simulated impact → decision register → outcome
evaluation. Step-by-step: [docs/workflows.md](docs/workflows.md).

## Roles (synthetic demo identities)

| Role | Access |
|---|---|
| Data Analyst | dashboards, DA workspace (SQL Studio), Data Operations |
| Business Analyst | dashboards, BA workspace (edit requirements/processes/decisions), simulator |
| Business Manager | dashboards, BA workspace (approve decisions, set targets), simulator |
| Administrator | everything + audit log |

`BP_AUTH_MODE=password` switches to password login with SHA-256 hashes from env vars.

## Documentation

* [Architecture](docs/architecture.md) · [Datasets & provenance](docs/datasets.md) ·
  [Data quality](docs/data_quality.md) · [Data dictionary](docs/data_dictionary.md)
* [Models & evaluation](docs/model_card.md) · [Workflows](docs/workflows.md)
* [Deployment: local, Docker, AWS](docs/deployment.md) · [Power BI](docs/powerbi/README.md)

## Known limitations

* Single online channel; no stores, product cost, budget, CRM or loyalty data in the source
  (derived attributes are documented; unavailable metrics are shown as unavailable).
* The dataset ends 2011-12-09; "freshness" is measured against that.
* Docker Compose, Airflow, Kafka broker, Superset, Prometheus/Grafana, MLflow server,
  Marquez and AWS were **configured but not executed** in the development environment
  (no Docker available). The Airflow DAG structure is tested with stubs; the Kafka path is
  tested with the file-bus fallback; the AWS script is tested in dry-run mode.
