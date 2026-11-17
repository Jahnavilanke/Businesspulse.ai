"""Batch registry, idempotency, incremental watermarks and the event (Kafka-compatible) path."""
from __future__ import annotations

import json

import pandas as pd

from businesspulse.ingestion import registry
from businesspulse.ingestion.batch import ingest_csv_drops
from businesspulse.ingestion.events import FileBus, consume
from conftest import raw_rows


def _csv(path, rows):
    raw_rows(rows).to_csv(path, index=False)


def test_csv_drop_is_idempotent(con, lake, tmp_path):
    drops = tmp_path / "drops"
    drops.mkdir()
    _csv(drops / "d1.csv", [{}, {"Invoice": "500002"}])
    first = ingest_csv_drops(con, "r1", store=lake, directory=drops)
    second = ingest_csv_drops(con, "r2", store=lake, directory=drops)
    assert first[0]["status"] == "loaded" and first[0]["rows"] == 2
    assert second[0]["status"] == "skipped_duplicate"
    assert con.execute("select count(*) from ops.ingestion_batches where status='loaded'").fetchone()[0] == 1


def test_watermark_filters_already_loaded_period(con, lake, tmp_path):
    drops = tmp_path / "drops"
    drops.mkdir()
    _csv(drops / "a.csv", [{"InvoiceDate": "2011-01-10 10:00"}])
    ingest_csv_drops(con, "r1", store=lake, directory=drops)
    assert registry.get_watermark(con, "uci_online_retail_ii") == pd.Timestamp("2011-01-10 10:00")
    _csv(drops / "b.csv", [{"Invoice": "500003", "InvoiceDate": "2011-01-09 09:00"},    # older -> ignored
                           {"Invoice": "500004", "InvoiceDate": "2011-01-11 09:00"}])   # newer -> loaded
    res = ingest_csv_drops(con, "r2", store=lake, directory=drops)
    loaded = [r for r in res if r["file"] == "b.csv"][0]
    assert loaded["rows"] == 1
    assert registry.get_watermark(con, "uci_online_retail_ii") == pd.Timestamp("2011-01-11 09:00")


def test_csv_contract_violation_marks_batch_failed(con, lake, tmp_path):
    drops = tmp_path / "drops"
    drops.mkdir()
    raw_rows([{}]).drop(columns=["Price"]).to_csv(drops / "bad.csv", index=False)
    res = ingest_csv_drops(con, "r1", store=lake, directory=drops)
    assert res[0]["status"] == "failed" and "Price" in res[0]["error"]
    assert con.execute("select status from ops.ingestion_batches").fetchone()[0] == "failed"


def test_schema_snapshot_detects_drift(con):
    assert registry.snapshot_schema(con, "x", "r1", {"a": "INT"}) == "baseline"
    assert registry.snapshot_schema(con, "x", "r2", {"a": "INT"}) == "none"
    drift = json.loads(registry.snapshot_schema(con, "x", "r3", {"a": "VARCHAR", "b": "INT"}))
    assert drift == {"added": ["b"], "removed": [], "type_changed": ["a"]}


def test_event_bus_consumes_into_bronze_with_dead_letter(con, lake, tmp_path, monkeypatch):
    from businesspulse.config import settings
    object.__setattr__(settings, "data_dir", tmp_path)            # dead-letter file under tmp
    try:
        bus = FileBus("tx", root=tmp_path / "events")
        good = json.loads(raw_rows([{}, {"Invoice": "500002"}]).to_json(orient="records", date_format="iso"))
        bus.produce(good + [{"Invoice": "bad"}])
        (tmp_path / "landing" / "events").mkdir(parents=True, exist_ok=True)
        res = consume(con, "r1", bus=bus, store=lake)
        assert res[0]["rows"] == 2 and res[0]["rejected"] == 1
        assert consume(con, "r2", bus=bus, store=lake) == []      # offset committed -> nothing re-read
        assert (tmp_path / "landing" / "events" / "dead_letter.jsonl").exists()
        assert lake.list("bronze/transactions/source=uci_online_retail_ii/method=kafka")
    finally:
        from businesspulse.config import PROJECT_ROOT
        object.__setattr__(settings, "data_dir", PROJECT_ROOT / "data")
