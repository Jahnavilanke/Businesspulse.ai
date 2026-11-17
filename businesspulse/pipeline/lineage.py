"""OpenLineage event emission + lineage graph persistence.

Each pipeline task emits OpenLineage ``RunEvent`` JSON (spec 2-0-2) with its input and
output datasets. Events are always appended to ``data/lineage/openlineage_events.jsonl``
and, when ``OPENLINEAGE_URL`` is set (e.g. Marquez), POSTed to ``/api/v1/lineage``.
dbt model lineage is derived from ``dbt/target/manifest.json``. The combined graph is
stored in ``ops.lineage_edges`` for the Data Operations page.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import requests

from ..config import PROJECT_ROOT, settings
from ..logging_utils import get_logger, log

logger = get_logger("lineage")
PRODUCER = "https://github.com/businesspulse-ai/businesspulse"
SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/definitions/RunEvent"

LINEAGE_DDL = """
create table if not exists ops.lineage_edges (
    source_node varchar, target_node varchar, edge_type varchar, job varchar, run_id varchar, recorded_at timestamp
);
"""


def _dataset(name: str, namespace: str = "businesspulse") -> dict:
    return {"namespace": namespace, "name": name}


def emit(job: str, event_type: str, run_id: str, inputs: list[str] | None = None, outputs: list[str] | None = None,
         facets: dict | None = None) -> dict:
    ol_run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}/{job}"))
    event = {
        "eventType": event_type,
        "eventTime": datetime.now(timezone.utc).isoformat(),
        "producer": PRODUCER,
        "schemaURL": SCHEMA_URL,
        "run": {"runId": ol_run_id, "facets": facets or {}},
        "job": {"namespace": settings.openlineage_namespace, "name": job},
        "inputs": [_dataset(i) for i in inputs or []],
        "outputs": [_dataset(o) for o in outputs or []],
    }
    settings.lineage_dir.mkdir(parents=True, exist_ok=True)
    with open(settings.lineage_dir / "openlineage_events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    if settings.openlineage_url:
        try:
            requests.post(f"{settings.openlineage_url.rstrip('/')}/api/v1/lineage", json=event, timeout=5)
        except Exception as exc:  # lineage backend outages never break the pipeline
            log(logger, 30, "OpenLineage backend unreachable", error=str(exc))
    return event


def record_edges(con, job: str, run_id: str, inputs: list[str], outputs: list[str], edge_type: str = "pipeline") -> None:
    con.execute(LINEAGE_DDL)
    con.execute("delete from ops.lineage_edges where job = ? and edge_type = ?", [job, edge_type])
    rows = [[i, o, edge_type, job, run_id, datetime.now(timezone.utc).replace(tzinfo=None)] for i in inputs for o in outputs]
    if rows:
        con.executemany("insert into ops.lineage_edges values (?,?,?,?,?,?)", rows)


def record_dbt_lineage(con, run_id: str) -> int:
    manifest_path = PROJECT_ROOT / "dbt" / "target" / "manifest.json"
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    con.execute(LINEAGE_DDL)
    con.execute("delete from ops.lineage_edges where edge_type = 'dbt'")

    def label(uid: str) -> str:
        node = manifest["nodes"].get(uid) or manifest["sources"].get(uid) or {}
        schema = node.get("schema", "")
        return f"{schema}.{node.get('alias') or node.get('name', uid.split('.')[-1])}"

    rows = []
    for uid, node in manifest["nodes"].items():
        if node.get("resource_type") not in {"model", "seed"}:
            continue
        for parent in node.get("depends_on", {}).get("nodes", []):
            if parent.split(".")[0] in {"model", "seed", "source"}:
                rows.append([label(parent), label(uid), "dbt", "dbt_build", run_id, datetime.now(timezone.utc).replace(tzinfo=None)])
        sql = node.get("raw_code", "") or ""
        for lake_key in ["bronze/transactions", "quarantine/transactions", "silver/transactions",
                         "synthetic/inventory/inventory_snapshot", "synthetic/inventory/warehouses",
                         "synthetic/marketing/campaigns", "synthetic/marketing/campaign_daily", "silver/fx_rates"]:
            if f"lake('{lake_key}" in sql:
                rows.append([f"lake:{lake_key}", label(uid), "dbt", "dbt_build", run_id,
                             datetime.now(timezone.utc).replace(tzinfo=None)])
    if rows:
        con.executemany("insert into ops.lineage_edges values (?,?,?,?,?,?)", rows)
    return len(rows)
