"""Prometheus metrics for pipeline health and data quality.

* ``write_textfile_metrics`` writes ``data/exports/metrics/businesspulse.prom`` after every
  run (node-exporter textfile collector format).
* ``python -m businesspulse.monitoring.metrics --serve 9108`` exposes the same metrics over
  HTTP for Prometheus to scrape (used by docker-compose's ``metrics-exporter`` service).
"""
from __future__ import annotations

import argparse
import time

from ..config import settings
from ..db import warehouse


def collect() -> list[tuple[str, str, dict, float]]:
    """Returns (name, help, labels, value) rows."""
    rows = []
    if not settings.warehouse_path.exists():
        return rows
    with warehouse(read_only=True) as con:
        last = con.execute("""select run_id, status, duration_s, epoch(started_at) from ops.pipeline_runs
                              where finished_at is not null order by started_at desc limit 1""").fetchone()
        if last:
            rows.append(("bp_pipeline_last_run_success", "1 if the last pipeline run succeeded", {},
                         1.0 if last[1] in ("success", "partial") else 0.0))
            rows.append(("bp_pipeline_last_run_duration_seconds", "Duration of the last run", {}, float(last[2] or 0)))
            rows.append(("bp_pipeline_last_run_timestamp_seconds", "Start time of the last run", {}, float(last[3])))
        for task, dur, status in con.execute("""select task, duration_s, status from ops.task_runs
                where run_id = (select run_id from ops.task_runs order by started_at desc limit 1)""").fetchall():
            rows.append(("bp_task_duration_seconds", "Task duration in the latest run", {"task": task, "status": status},
                         float(dur or 0)))
        dq = con.execute("""select dataset, completeness, validity, uniqueness, consistency, overall
                            from ops.dq_scores order by checked_at desc limit 1""").fetchone()
        if dq:
            for i, dim in enumerate(["completeness", "validity", "uniqueness", "consistency", "overall"], start=1):
                rows.append(("bp_data_quality_score", "Data quality score (0-1)", {"dataset": dq[0], "dimension": dim},
                             float(dq[i])))
        for src, n, failed in con.execute("""select source_name, count(*) filter (where status='loaded'),
                                             count(*) filter (where status='failed') from ops.ingestion_batches
                                             group by 1""").fetchall():
            rows.append(("bp_ingestion_batches_loaded", "Loaded ingestion batches", {"source": src}, float(n)))
            rows.append(("bp_ingestion_batches_failed", "Failed ingestion batches", {"source": src}, float(failed)))
        fresh = con.execute("select epoch(max(invoice_ts)) from gold.fact_sales").fetchone()
        if fresh and fresh[0]:
            rows.append(("bp_data_latest_event_timestamp_seconds", "Latest transaction timestamp in gold", {},
                         float(fresh[0])))
    try:
        from ..db import app_query
        a = app_query("select severity, count(*) n from alerts where status in ('Open','Acknowledged','Investigating') "
                      "group by 1")
        for r in a.itertuples():
            rows.append(("bp_open_alerts", "Open business alerts", {"severity": r.severity}, float(r.n)))
    except Exception:
        pass
    return rows


def render(rows) -> str:
    out, seen = [], set()
    for name, help_, labels, value in rows:
        if name not in seen:
            out += [f"# HELP {name} {help_}", f"# TYPE {name} gauge"]
            seen.add(name)
        lab = ",".join(f'{k}="{v}"' for k, v in labels.items())
        out.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")
    return "\n".join(out) + "\n"


def write_textfile_metrics() -> str:
    path = settings.exports_dir / "metrics" / "businesspulse.prom"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(collect()), encoding="utf-8")
    return str(path)


def serve(port: int) -> None:  # pragma: no cover - long-running
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = render(collect()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(body)

    HTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--serve", type=int)
    a = p.parse_args()
    if a.serve:
        serve(a.serve)
    else:
        print(write_textfile_metrics())
        time.sleep(0)
