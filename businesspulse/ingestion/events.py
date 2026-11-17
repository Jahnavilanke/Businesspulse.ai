"""Near-real-time transaction events (Kafka, with a file-based fallback broker).

Producer: publishes transaction events (the CSV-drop/workbook contract, one JSON per line).
Consumer: reads events in micro-batches, validates them against the contract, writes each
micro-batch to ``bronze/transactions/.../method=kafka`` and registers it, so events flow
through the same quality stage and dbt models as batch data.

Kafka is optional: when ``BP_KAFKA_BOOTSTRAP`` is unset (or ``kafka-python`` is not installed)
an append-only JSONL log under ``data/landing/events`` acts as the broker, with consumer
offsets stored next to it - enough to demonstrate and test the streaming path locally.

    python -m businesspulse.ingestion.events produce --file data/landing/drops/sample.csv
    python -m businesspulse.ingestion.events consume --max-batches 1
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

from ..config import settings
from ..db import warehouse
from ..logging_utils import get_logger, log
from ..storage.object_store import ObjectStore
from . import registry
from .acquire import EXPECTED_COLUMNS
from .batch import BRONZE_PREFIX, SOURCE_NAME, _finalize

logger = get_logger("ingestion.events")


class FileBus:
    """Minimal durable log + committed offset (fallback when Kafka is unavailable)."""

    def __init__(self, topic: str, root: Path | None = None):
        self.dir = (root or settings.landing_dir / "events")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = self.dir / f"{topic}.jsonl"
        self.offset_file = self.dir / f"{topic}.offset"

    def produce(self, events: Iterable[dict]) -> int:
        n = 0
        with open(self.log, "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, default=str) + "\n")
                n += 1
        return n

    def _offset(self) -> int:
        return int(self.offset_file.read_text()) if self.offset_file.exists() else 0

    def poll(self, max_records: int) -> tuple[list[dict], int]:
        if not self.log.exists():
            return [], self._offset()
        start = self._offset()
        out = []
        with open(self.log, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i < start:
                    continue
                if len(out) >= max_records:
                    break
                out.append(json.loads(line))
        return out, start + len(out)

    def commit(self, offset: int) -> None:
        self.offset_file.write_text(str(offset))


class KafkaBus:  # pragma: no cover - requires a broker
    def __init__(self, topic: str):
        from kafka import KafkaConsumer, KafkaProducer

        self.topic = topic
        self.producer = KafkaProducer(bootstrap_servers=settings.kafka_bootstrap,
                                      value_serializer=lambda v: json.dumps(v, default=str).encode())
        self.consumer = KafkaConsumer(topic, bootstrap_servers=settings.kafka_bootstrap, group_id="businesspulse-bronze",
                                      enable_auto_commit=False, auto_offset_reset="earliest",
                                      value_deserializer=lambda b: json.loads(b.decode()))

    def produce(self, events: Iterable[dict]) -> int:
        n = 0
        for e in events:
            self.producer.send(self.topic, e)
            n += 1
        self.producer.flush()
        return n

    def poll(self, max_records: int):
        batch = self.consumer.poll(timeout_ms=5000, max_records=max_records)
        return [r.value for recs in batch.values() for r in recs], None

    def commit(self, offset) -> None:
        self.consumer.commit()


def get_bus(topic: str | None = None, root: Path | None = None):
    topic = topic or settings.kafka_topic
    if settings.kafka_bootstrap:
        try:
            return KafkaBus(topic)
        except ImportError:
            log(logger, 30, "kafka-python not installed; using file bus")
    return FileBus(topic, root)


def events_from_csv(path: Path) -> Iterator[dict]:
    df = pd.read_csv(path, dtype={"Invoice": "string", "StockCode": "string"})
    for rec in df.to_dict(orient="records"):
        yield {k: (None if pd.isna(v) else v) for k, v in rec.items()}


def consume(con, run_id: str, bus=None, max_records: int = 10000, max_batches: int = 10,
            store: ObjectStore | None = None) -> list[dict]:
    """Drain events into bronze micro-batches. Invalid events are rejected to a dead-letter file."""
    bus = bus or get_bus()
    store = store or ObjectStore()
    results = []
    for _ in range(max_batches):
        events, offset = bus.poll(max_records)
        if not events:
            break
        good = [e for e in events if all(c in e for c in EXPECTED_COLUMNS)]
        bad = [e for e in events if not all(c in e for c in EXPECTED_COLUMNS)]
        if bad:
            dlq = settings.landing_dir / "events" / "dead_letter.jsonl"
            with open(dlq, "a", encoding="utf-8") as f:
                for e in bad:
                    f.write(json.dumps({"event": e, "error": "contract: missing columns"}, default=str) + "\n")
        content_hash = hashlib.sha256(json.dumps(good, sort_keys=True, default=str).encode()).hexdigest()
        if good and not registry.find_loaded_batch(con, SOURCE_NAME, content_hash):
            batch_id = registry.new_batch_id(SOURCE_NAME)
            df = _finalize(pd.DataFrame(good).astype({"Invoice": "string", "StockCode": "string"}), batch_id,
                           f"{settings.kafka_topic}", "kafka")
            key = f"{BRONZE_PREFIX}/method=kafka/{batch_id}.parquet"
            df.to_parquet(store.local_path(key), index=False)
            store.sync_written(key)
            registry.register_batch(con, batch_id=batch_id, source_system="Kafka" if isinstance(bus, KafkaBus) else "FileBus",
                                    source_name=SOURCE_NAME, ingestion_method="kafka", source_uri=settings.kafka_topic,
                                    content_hash=content_hash, is_synthetic=False, row_count=len(df), status="loaded",
                                    lake_key=key, run_id=run_id, started_at=registry.now(), finished_at=registry.now(),
                                    min_event_ts=df.InvoiceDate.min(), max_event_ts=df.InvoiceDate.max(),
                                    message=f"{len(bad)} events dead-lettered")
            results.append({"batch_id": batch_id, "rows": len(df), "rejected": len(bad)})
        bus.commit(offset)
    return results


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("produce")
    pr.add_argument("--file", required=True, help="CSV with the transaction contract columns")
    co = sub.add_parser("consume")
    co.add_argument("--max-batches", type=int, default=10)
    a = p.parse_args()
    bus = get_bus()
    if a.cmd == "produce":
        print(f"produced {bus.produce(events_from_csv(Path(a.file)))} events")
    else:
        with warehouse() as con:
            registry.ensure_ops(con)
            print(consume(con, "events-cli", bus, max_batches=a.max_batches))


if __name__ == "__main__":
    main()
