"""API ingestion: ECB reference FX rates (base GBP) via the free Frankfurter API.

Optional source used only for currency-converted reporting. If the API is unreachable
the stage reports ``unavailable`` and the platform continues in GBP - nothing is
fabricated. Successful responses are cached in the lake, so re-runs are idempotent and
offline after the first successful call.
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import requests

from ..config import settings
from ..logging_utils import get_logger, log
from ..storage.object_store import ObjectStore
from . import registry

logger = get_logger("ingestion.api_fx")
SOURCE_NAME = "fx_rates_frankfurter"
CURRENCIES = ["USD", "EUR"]


def ingest_fx(con, run_id: str, start: str, end: str, store: ObjectStore | None = None, timeout: int = 20) -> dict:
    store = store or ObjectStore()
    if not settings.enable_fx_api:
        return {"status": "disabled"}
    raw_key = f"bronze/api/fx/{start}_{end}.json"
    silver_key = "silver/fx_rates/fx_monthly.parquet"
    if store.exists(raw_key):
        payload = json.loads(store.local_path(raw_key).read_text(encoding="utf-8"))
        cached = True
    else:
        url = f"{settings.fx_api_url}/{start}..{end}"
        try:
            r = requests.get(url, params={"from": "GBP", "to": ",".join(CURRENCIES)}, timeout=timeout)
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:
            log(logger, 30, "FX API unavailable - continuing in GBP only", error=str(exc))
            return {"status": "unavailable", "error": str(exc)}
        store.local_path(raw_key).write_text(json.dumps(payload), encoding="utf-8")
        store.sync_written(raw_key)
        cached = False
    rows = [(pd.Timestamp(day), cur, rate) for day, rates in payload.get("rates", {}).items() for cur, rate in rates.items()]
    if not rows:
        return {"status": "unavailable", "error": "empty response"}
    df = pd.DataFrame(rows, columns=["day", "currency", "rate"])
    df["month"] = df["day"].dt.to_period("M").dt.to_timestamp()
    monthly = df.groupby(["month", "currency"], as_index=False)["rate"].mean()
    monthly["source"] = "ECB reference rates via Frankfurter API"
    monthly.to_parquet(store.local_path(silver_key), index=False)
    store.sync_written(silver_key)
    content_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if not registry.find_loaded_batch(con, SOURCE_NAME, content_hash):
        registry.register_batch(
            con, batch_id=registry.new_batch_id(SOURCE_NAME), source_system="Frankfurter API", source_name=SOURCE_NAME,
            ingestion_method="api", source_uri=settings.fx_api_url, content_hash=content_hash, is_synthetic=False,
            row_count=len(monthly), status="loaded", lake_key=silver_key, run_id=run_id, started_at=registry.now(),
            finished_at=registry.now(), min_event_ts=monthly["month"].min(), max_event_ts=monthly["month"].max(),
        )
    return {"status": "loaded", "rows": len(monthly), "cached": cached}
