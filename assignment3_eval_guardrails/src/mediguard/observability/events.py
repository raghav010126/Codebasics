"""Structured, append-only event log (JSON lines) for every request, guardrail decision and eval stage.

Usage: from mediguard.observability.events import log_event; log_event("guardrail.decision", request_id=..., ...)
Query it with `mediguard logs` (DuckDB) or jq.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from mediguard.config import get_settings
from mediguard.observability.scrub import scrub


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(record.payload, default=str, ensure_ascii=False)


@lru_cache
def _logger() -> logging.Logger:
    s = get_settings()
    s.logs_dir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("mediguard.events")
    log.setLevel(logging.INFO)
    log.propagate = False
    handler = logging.FileHandler(s.events_path, encoding="utf-8")
    handler.setFormatter(_JsonFormatter())
    log.addHandler(handler)
    return log


def log_event(event: str, **fields: Any) -> dict:
    payload = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "event": event, **scrub(fields)}
    _logger().info(event, extra={"payload": payload})
    return payload
