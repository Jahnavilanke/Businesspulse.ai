"""Experiment tracking: MLflow when available (local file store by default) + an
``ops.model_runs`` table that the app reads, so results are visible without MLflow."""
from __future__ import annotations

import json
import os
import warnings

from ..config import settings
from ..ingestion import registry
from ..logging_utils import get_logger, log

logger = get_logger("ml.tracking")
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
# Local default is MLflow's file store (no DB server). Point MLFLOW_TRACKING_URI at the
# docker-compose MLflow server (postgres-backed) for the full tracking UI.
os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")


def _mlflow():
    try:
        import mlflow
    except ImportError:
        return None
    uri = settings.mlflow_tracking_uri or settings.mlruns_dir.resolve().as_uri()
    mlflow.set_tracking_uri(uri)
    return mlflow


def log_run(con, run_id: str, model_name: str, variant: str, task: str, metrics: dict, params: dict,
            validation: str, artifacts: dict | None = None) -> str | None:
    mlflow_run_id = None
    mlflow = _mlflow()
    if mlflow is not None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                mlflow.set_experiment(f"businesspulse-{model_name}")
                with mlflow.start_run(run_name=f"{variant}-{run_id}") as r:
                    mlflow.log_params({k: str(v)[:250] for k, v in params.items()})
                    mlflow.log_metrics({k: float(v) for k, v in metrics.items() if v is not None})
                    mlflow.set_tags({"pipeline_run_id": run_id, "task": task, "validation": validation,
                                     "data_basis": "actual"})
                    for name, obj in (artifacts or {}).items():
                        mlflow.log_dict(obj, f"{name}.json")
                    mlflow_run_id = r.info.run_id
        except Exception as exc:  # tracking must never break training
            log(logger, 30, "mlflow logging failed", error=str(exc))
    for k, v in metrics.items():
        con.execute("insert into ops.model_runs values (?,?,?,?,?,?,?,?,?,?)",
                    [run_id, model_name, variant, task, k, None if v is None else float(v), validation,
                     json.dumps(params, default=str), registry.now(), mlflow_run_id])
    return mlflow_run_id
