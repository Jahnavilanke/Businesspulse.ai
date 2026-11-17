"""Local pipeline runner with retries, run/task logging and an execution report.

Usage:
    python -m businesspulse.pipeline.runner                 # full run
    python -m businesspulse.pipeline.runner --skip-ml       # faster, no model training
    python -m businesspulse.pipeline.runner --only dbt_build detect_alerts
    python -m businesspulse.pipeline.runner --full-refresh  # rebuild incremental dbt models
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
import uuid

from ..config import settings
from ..business_analysis.store import seed_defaults
from ..db import migrate_app_db, warehouse
from ..ingestion import registry
from ..logging_utils import get_logger, log
from .stages import OPTIONAL_STAGES, STAGES, NonRetryableError, RunContext

logger = get_logger("pipeline.runner")


def _record_task(run_id, task, attempt, status, started, finished, message="", rows_in=None, rows_out=None):
    with warehouse() as con:
        registry.ensure_ops(con)
        con.execute(
            "insert into ops.task_runs values (?,?,?,?,?,?,?,?,?,?)",
            [run_id, task, attempt, status, started, finished, (finished - started).total_seconds(), rows_in,
             rows_out, message[:2000]],
        )


def run_pipeline(only: list[str] | None = None, skip_ml: bool = False, full_refresh: bool = False,
                 mode: str = "full", trigger: str = "manual", max_retries: int = 2) -> dict:
    settings.ensure_dirs()
    migrate_app_db()
    seed_defaults()          # KPI registry + clearly-flagged demo BA records (idempotent)
    run_id = f"run-{registry.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    ctx = RunContext(run_id=run_id, mode=mode, skip_ml=skip_ml, full_refresh=full_refresh)
    started = registry.now()
    with warehouse() as con:
        registry.ensure_ops(con)
        con.execute("insert into ops.pipeline_runs (run_id, pipeline, trigger, mode, status, started_at) values (?,?,?,?,?,?)",
                    [run_id, "businesspulse_daily", trigger, mode, "running", started])
    log(logger, 20, "pipeline started", run_id=run_id, mode=mode)

    status, message = "success", ""
    for name, fn in STAGES:
        if only and name not in only:
            continue
        for attempt in range(1, max_retries + 2):
            t0 = registry.now()
            try:
                result = fn(ctx)
                ctx.results[name] = result
                rows_out = None
                if isinstance(result, dict):
                    rows_out = result.get("silver_rows") or result.get("rows")
                _record_task(run_id, name, attempt, "success", t0, registry.now(),
                             json.dumps(result, default=str)[:2000], rows_out=rows_out)
                log(logger, 20, "stage succeeded", stage=name, attempt=attempt)
                break
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                _record_task(run_id, name, attempt, "failed", t0, registry.now(), err + "\n" + traceback.format_exc()[-1200:])
                log(logger, 40, "stage failed", stage=name, attempt=attempt, error=err)
                if attempt <= max_retries and not isinstance(exc, (ValueError, FileNotFoundError, NonRetryableError)):
                    time.sleep(min(2 ** attempt, 10))   # exponential backoff for transient errors
                    continue
                ctx.results[name] = {"status": "failed", "error": err}
                if name in OPTIONAL_STAGES:
                    status = "partial"
                    break
                status, message = "failed", f"{name}: {err}"
                break
        if status == "failed":
            break

    finished = registry.now()
    with warehouse() as con:
        con.execute("update ops.pipeline_runs set status = ?, finished_at = ?, duration_s = ?, message = ? where run_id = ?",
                    [status, finished, (finished - started).total_seconds(), message[:2000], run_id])
    report = {"run_id": run_id, "status": status, "duration_s": round((finished - started).total_seconds(), 1),
              "message": message, "stages": ctx.results}
    report_path = settings.data_dir / "exports" / "pipeline_reports" / f"{run_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    try:
        from ..monitoring.metrics import write_textfile_metrics

        write_textfile_metrics()
    except Exception as exc:  # monitoring must never fail the run
        log(logger, 30, "metrics export failed", error=str(exc))
    log(logger, 20, "pipeline finished", run_id=run_id, status=status, report=str(report_path))
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Run the BusinessPulse AI data pipeline")
    p.add_argument("--only", nargs="*", help="run only these stages")
    p.add_argument("--skip-ml", action="store_true")
    p.add_argument("--full-refresh", action="store_true")
    p.add_argument("--mode", default="full", choices=["full", "incremental", "backfill"])
    args = p.parse_args()
    report = run_pipeline(only=args.only, skip_ml=args.skip_ml, full_refresh=args.full_refresh, mode=args.mode)
    print(json.dumps({k: v for k, v in report.items() if k != "stages"}, indent=2))
    raise SystemExit(0 if report["status"] in {"success", "partial"} else 1)


if __name__ == "__main__":
    main()
