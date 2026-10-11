"""Small sha256-keyed JSON disk cache for evaluator calls (judge), so re-scoring unchanged answers is free and identical."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from mediguard.config import get_settings


def key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _path(namespace: str, k: str) -> Path:
    return get_settings().home / "cache" / namespace / k[:2] / f"{k}.json"


def get(namespace: str, k: str) -> Any | None:
    p = _path(namespace, k)
    return json.loads(p.read_text()) if p.exists() else None


def put(namespace: str, k: str, value: Any) -> None:
    p = _path(namespace, k)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, default=str))
    tmp.replace(p)
