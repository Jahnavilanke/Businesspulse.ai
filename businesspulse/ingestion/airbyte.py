"""Airbyte connector integration (optional).

Airbyte is the managed connector option for extracting source systems (files, databases,
SaaS APIs) into the bronze zone. Connector definitions live in ``ingestion/airbyte/*.json``;
this client registers a landed Airbyte sync as an ingestion batch so it gets the same
lineage, quality checks and dbt processing as native loads.

    python -m businesspulse.ingestion.airbyte sync --connection-id <uuid>   # trigger + wait
    python -m businesspulse.ingestion.airbyte register                       # register landed files

Requires ``BP_AIRBYTE_URL`` (e.g. http://localhost:8000/api/public/v1) and ``BP_AIRBYTE_TOKEN``.
"""
from __future__ import annotations

import argparse
import os
import time

import requests

from ..db import warehouse
from ..storage.object_store import ObjectStore
from . import registry
from .batch import BRONZE_PREFIX, SOURCE_NAME


def _api() -> tuple[str, dict]:
    url, token = os.environ.get("BP_AIRBYTE_URL"), os.environ.get("BP_AIRBYTE_TOKEN")
    if not url or not token:
        raise RuntimeError("Set BP_AIRBYTE_URL and BP_AIRBYTE_TOKEN to use the Airbyte integration")
    return url.rstrip("/"), {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def trigger_sync(connection_id: str, timeout_s: int = 3600) -> dict:  # pragma: no cover - needs Airbyte
    url, headers = _api()
    job = requests.post(f"{url}/jobs", json={"connectionId": connection_id, "jobType": "sync"}, headers=headers,
                        timeout=30).json()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = requests.get(f"{url}/jobs/{job['jobId']}", headers=headers, timeout=30).json()
        if status["status"] in {"succeeded", "failed", "cancelled"}:
            return status
        time.sleep(15)
    raise TimeoutError(f"Airbyte job {job['jobId']} did not finish in {timeout_s}s")


def register_landed(con, run_id: str, store: ObjectStore | None = None) -> list[dict]:
    """Register parquet files Airbyte wrote to the bronze prefix that are not yet registered."""
    store = store or ObjectStore()
    known = {r[0] for r in con.execute("select lake_key from ops.ingestion_batches where lake_key is not null").fetchall()}
    out = []
    for key in store.list(f"{BRONZE_PREFIX}/method=airbyte"):
        if key in known or not key.endswith(".parquet"):
            continue
        path = store.local_path(key)
        n = con.execute(f"select count(*) from read_parquet('{path.as_posix()}')").fetchone()[0]
        batch_id = registry.new_batch_id(SOURCE_NAME)
        registry.register_batch(con, batch_id=batch_id, source_system="Airbyte", source_name=SOURCE_NAME,
                                ingestion_method="airbyte", source_uri=key, content_hash=registry.file_sha256(path),
                                is_synthetic=False, row_count=n, status="loaded", lake_key=key, run_id=run_id,
                                started_at=registry.now(), finished_at=registry.now())
        out.append({"batch_id": batch_id, "key": key, "rows": n})
    return out


def main() -> None:  # pragma: no cover
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--connection-id", required=True)
    sub.add_parser("register")
    a = p.parse_args()
    if a.cmd == "sync":
        print(trigger_sync(a.connection_id))
    with warehouse() as con:
        registry.ensure_ops(con)
        print(register_landed(con, "airbyte-cli"))


if __name__ == "__main__":
    main()
