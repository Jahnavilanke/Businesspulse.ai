"""Train, evaluate, track and persist all predictive models into the ``ml`` schema."""
from __future__ import annotations

import json

import pandas as pd

from ..db import warehouse
from ..ingestion import registry
from ..logging_utils import get_logger, log
from . import models
from .tracking import log_run

logger = get_logger("ml.train_all")


def _save(con, name: str, df: pd.DataFrame) -> None:
    con.register("_tmp_df", df)
    con.execute(f"create or replace table ml.{name} as select * from _tmp_df")
    con.unregister("_tmp_df")


def train_all(run_id: str = "adhoc") -> dict:
    summary = {}
    with warehouse() as con:
        registry.ensure_ops(con)
        con.execute("create schema if not exists ml")
        trained_at = registry.now()

        # ---- revenue forecast
        rf = models.revenue_forecast(con)
        for variant, m in rf["metrics"].items():
            log_run(con, run_id, "revenue_forecast", variant, "regression/time-series", m,
                    {"granularity": "weekly", "holdout_weeks": 12}, rf["validation"])
        _save(con, "revenue_forecast", rf["forecast"].assign(trained_at=trained_at))
        _save(con, "revenue_backtest", rf["backtest"])
        summary["revenue_forecast"] = {"best": rf["best"], "wape": rf["metrics"][rf["best"]]["wape"]}

        # ---- demand forecast
        dfc = models.demand_forecast(con)
        for variant, m in dfc["metrics"].items():
            log_run(con, run_id, "demand_forecast", variant, "regression/time-series", m,
                    {"products": dfc["products"], "holdout_weeks": 8}, dfc["validation"])
        _save(con, "demand_forecast", dfc["forecast"].assign(trained_at=trained_at))
        summary["demand_forecast"] = {k: v["wape"] for k, v in dfc["metrics"].items()}

        # ---- churn
        ch = models.churn_model(con)
        for variant in ["logistic_regression", "xgboost"]:
            log_run(con, run_id, "churn", variant, "classification", ch["metrics"][variant],
                    {"horizon_days": 90, "n_train": ch["n_train"], "n_test": ch["n_test"]}, ch["validation"],
                    artifacts={"feature_importance": ch["importance"].to_dict(orient="records")}
                    if variant == "xgboost" else None)
        _save(con, "churn_scores", ch["scores"].assign(trained_at=trained_at, model=ch["best"]))
        _save(con, "churn_feature_importance", ch["importance"])
        summary["churn"] = {"best": ch["best"], **{k: v for k, v in ch["metrics"][ch["best"]].items()},
                            **ch["metrics"]["base_rate"]}

        # ---- customer value (CLV horizon 180d)
        cv = models.clv_model(con)
        for variant, m in cv["metrics"].items():
            log_run(con, run_id, "clv_180d", variant, "regression", m, {"horizon_days": 180}, cv["validation"])
        _save(con, "clv_scores", cv["scores"].assign(trained_at=trained_at))
        summary["clv"] = cv["metrics"]

        meta = pd.DataFrame([{"model": k, "validation": v} for k, v in {
            "revenue_forecast": rf["validation"], "demand_forecast": dfc["validation"],
            "churn": ch["validation"], "clv_180d": cv["validation"]}.items()])
        _save(con, "model_validation", meta)
    log(logger, 20, "models trained", summary=json.dumps(summary, default=str))
    return summary


if __name__ == "__main__":
    print(json.dumps(train_all(), indent=2, default=str))
