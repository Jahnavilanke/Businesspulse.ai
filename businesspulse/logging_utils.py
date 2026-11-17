"""Structured JSON logging used by pipeline, app and services."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from .config import settings

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str) -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger("businesspulse")
        root.setLevel(settings.log_level)
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(JsonFormatter())
        file_handler = logging.FileHandler(settings.logs_dir / "businesspulse.jsonl", encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(stream)
        root.addHandler(file_handler)
        root.propagate = False
        _CONFIGURED = True
    return logging.getLogger(f"businesspulse.{name}")


def log(logger: logging.Logger, level: int, msg: str, **ctx) -> None:
    logger.log(level, msg, extra={"ctx": ctx})
