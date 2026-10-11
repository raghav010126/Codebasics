"""Record/replay cache for MediBot's Anthropic calls, so an unchanged system re-runs identically.

MediBot cannot set a temperature (Sonnet 5.5 rejects sampling parameters), so repeatability comes from replaying the
recorded response for a byte-identical request. The key covers model, system prompt, messages (which contain the
retrieved passages), output config and the fallback flag: any change to retrieval, prompts or the model is a miss.
Deterministic 400 errors are recorded too, so a swap to a model that rejects MediBot's request replays offline.

Modes (MEDIGUARD_REPLAY): replay (read-through, record misses), fresh (always call, overwrite), replay_only (a miss
raises ReplayMiss; proves a run made no API calls).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mediguard.config import get_settings


class ReplayMiss(RuntimeError):
    """replay_only mode and no recorded response for this request."""


def request_key(use_fallbacks: bool, kwargs: dict[str, Any]) -> str:
    blob = json.dumps({"provider": "anthropic", "use_fallbacks": use_fallbacks, **kwargs}, sort_keys=True,
                      default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _path(key: str) -> Path:
    return get_settings().home / "target_llm" / key[:2] / f"{key}.json"


def load(key: str) -> dict | None:
    p = _path(key)
    return json.loads(p.read_text()) if p.exists() else None


def save(key: str, record: dict) -> None:
    p = _path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {**record, "recorded_at": datetime.now(UTC).isoformat(timespec="seconds")}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False))
    tmp.replace(p)


def record_response(resp: Any, use_fallbacks: bool, latency_ms: float) -> dict:
    return {"kind": "beta" if use_fallbacks else "std", "response": resp.to_dict(), "latency_ms": latency_ms}


def record_error(err: Exception, latency_ms: float) -> dict:
    return {"error": {"status": getattr(err, "status_code", 400), "message": getattr(err, "message", str(err)),
                      "body": getattr(err, "body", None)}, "latency_ms": latency_ms}


def rebuild(record: dict) -> Any:
    """The recorded response object, or raise the recorded API error."""
    import anthropic

    if "error" in record:
        import httpx2

        e = record["error"]
        req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.BadRequestError(e["message"], response=httpx2.Response(e["status"], request=req),
                                        body=e.get("body"))
    if record["kind"] == "beta":
        from anthropic.types.beta import BetaMessage

        return BetaMessage.model_validate(record["response"])
    from anthropic.types import Message

    return Message.model_validate(record["response"])
