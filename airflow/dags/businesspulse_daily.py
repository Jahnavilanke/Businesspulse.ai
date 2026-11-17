"""BusinessPulse AI - daily ELT DAG (Airflow 2.9+ / 3.x, CeleryExecutor in docker-compose).

Each task calls the same stage function the local runner uses (businesspulse.pipeline.stages),
so behaviour is identical in both modes.

* Retries with exponential backoff on every task; deterministic failures (failed tests /
  contracts) raise NonRetryableError and are marked failed without retry.
* Idempotent: ingestion de-duplicates by content hash, silver is a pure function of bronze,
  dbt fact_sales is incremental (delete+insert on line_id) and alerts upsert by id.
* Backfills: `airflow dags backfill businesspulse_daily -s 2011-11-01 -e 2011-11-30`
  re-runs the DAG per logical date; the run is logged with mode=backfill and the dbt
  incremental window absorbs late data. Full rebuild: trigger with conf {"full_refresh": true}.
* Failure recovery: tasks resume from the failed task ("Clear" in the UI); each stage's
  inputs are durable files in the lake, so upstream work is never repeated.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.exceptions import AirflowFailException
from airflow.operators.python import PythonOperator

DEFAULT_ARGS = {
    "owner": "data-engineering",
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
    "execution_timeout": timedelta(minutes=45),
}


def _run(stage_name: str, **context):
    from businesspulse.ingestion import registry
    from businesspulse.db import warehouse
    from businesspulse.pipeline import stages

    conf = (context.get("dag_run").conf or {}) if context.get("dag_run") else {}
    run_id = f"airflow-{context['run_id']}"
    mode = "backfill" if context["dag_run"].run_type == "backfill" else "incremental"
    ctx = stages.RunContext(run_id=run_id, mode=mode, window_start=context["data_interval_start"].date(),
                            window_end=context["data_interval_end"].date(), skip_ml=conf.get("skip_ml", False),
                            full_refresh=conf.get("full_refresh", False))
    with warehouse() as con:
        registry.ensure_ops(con)
        con.execute("insert or ignore into ops.pipeline_runs (run_id, pipeline, trigger, mode, window_start, window_end, "
                    "status, started_at) values (?,?,?,?,?,?,?,?)",
                    [run_id, "businesspulse_daily", "airflow", mode, ctx.window_start, ctx.window_end, "running",
                     registry.now()])
    fn = dict(stages.STAGES)[stage_name]
    t0 = registry.now()
    try:
        result = fn(ctx)
        status = "success"
        return result
    except stages.NonRetryableError as exc:
        status = "failed"
        raise AirflowFailException(str(exc)) from exc
    except Exception:
        status = "failed"
        raise
    finally:
        with warehouse() as con:
            con.execute("insert into ops.task_runs values (?,?,?,?,?,?,?,?,?,?)",
                        [run_id, stage_name, context["ti"].try_number, status, t0, registry.now(),
                         (registry.now() - t0).total_seconds(), None, None, ""])


def _finish(**context):
    from businesspulse.db import warehouse
    from businesspulse.ingestion import registry
    from businesspulse.monitoring.metrics import write_textfile_metrics

    run_id = f"airflow-{context['run_id']}"
    states = {ti.task_id: ti.state for ti in context["dag_run"].get_task_instances() if ti.task_id != "finish_run"}
    failed = sorted(t for t, s in states.items() if s in ("failed", "upstream_failed"))
    optional = {"ingest_api", "publish_postgres"}
    status = "success" if not failed else ("partial" if set(failed) <= optional else "failed")
    with warehouse() as con:
        con.execute("update ops.pipeline_runs set status=?, finished_at=?, message=?, "
                    "duration_s=epoch(?::timestamp) - epoch(started_at) where run_id=?",
                    [status, registry.now(), ",".join(failed), registry.now(), run_id])
    write_textfile_metrics()
    if status == "failed":
        raise AirflowFailException(f"pipeline failed at: {failed}")


with DAG(
    dag_id="businesspulse_daily",
    description="Extract -> validate -> bronze -> silver/quarantine -> dbt gold -> ML -> alerts -> exports",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2011, 11, 1),
    schedule="0 5 * * *",           # daily 05:00 UTC; NFR-001 freshness SLA 07:00
    catchup=False,
    max_active_runs=1,              # DuckDB has a single writer
    tags=["businesspulse", "elt"],
    doc_md=__doc__,
) as dag:
    tasks = {}
    for name, _ in __import__("businesspulse.pipeline.stages", fromlist=["STAGES"]).STAGES:
        tasks[name] = PythonOperator(task_id=name, python_callable=_run, op_kwargs={"stage_name": name},
                                     retries=0 if name == "dbt_build" else DEFAULT_ARGS["retries"])
    finish = PythonOperator(task_id="finish_run", python_callable=_finish, trigger_rule="all_done")

    tasks["extract_sources"] >> tasks["ingest_raw"] >> tasks["clean_and_validate"]
    tasks["extract_sources"] >> tasks["ingest_api"]
    tasks["clean_and_validate"] >> tasks["generate_synthetic"] >> tasks["dbt_build"]
    tasks["ingest_api"] >> tasks["dbt_build"]
    tasks["dbt_build"] >> tasks["train_models"] >> tasks["detect_alerts"]
    tasks["dbt_build"] >> tasks["refresh_exports"] >> tasks["publish_postgres"]
    [tasks["detect_alerts"], tasks["publish_postgres"]] >> finish
