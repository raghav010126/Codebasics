"""Structured guardrail verdicts and the fail-closed coercion every check result goes through.

A verdict is only ever read from structured data: a Verdict, a dict, or a JSON string that validates against the
schema. Anything else (None, free text, malformed JSON, an unknown decision such as "pass" or "ALLOW ") becomes a
block with a fail_closed reason. Nothing is inferred from prefix-matching free text.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Stage = Literal["input", "output"]


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check: str
    stage: Stage
    decision: Literal["allow", "block"]
    reason_code: str = "ok"
    detail: str = ""                         # internal only: logged, never shown to the user
    confidence: float | None = None
    latency_ms: float = 0.0
    fail_closed: bool = False
    warnings: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class StageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Stage
    decision: Literal["allow", "block"]
    verdicts: list[Verdict]
    blocking: Verdict | None = None
    latency_ms: float = 0.0

    @property
    def warnings(self) -> list[str]:
        return [f"{v.check}:{w}" for v in self.verdicts for w in v.warnings]


def allow(check: str, stage: Stage, **kw: Any) -> Verdict:
    return Verdict(check=check, stage=stage, decision="allow", **kw)


def block(check: str, stage: Stage, reason_code: str, detail: str = "", **kw: Any) -> Verdict:
    return Verdict(check=check, stage=stage, decision="block", reason_code=reason_code, detail=detail, **kw)


def fail_closed(check: str, stage: Stage, why: str, detail: str = "") -> Verdict:
    return block(check, stage, f"fail_closed.{why}", detail[:500], fail_closed=True)


def coerce_verdict(raw: object, check: str, stage: Stage) -> Verdict:
    """Turn whatever a check returned into a Verdict; anything unusable blocks."""
    if isinstance(raw, Verdict):
        if raw.check != check or raw.stage != stage:
            return raw.model_copy(update={"check": check, "stage": stage})
        return raw
    if raw is None:
        return fail_closed(check, stage, "missing", "check returned no verdict")
    if isinstance(raw, (bytes, str)):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return fail_closed(check, stage, "malformed", f"verdict is not JSON: {e}")
    if not isinstance(raw, dict):
        return fail_closed(check, stage, "bad_type", f"verdict of type {type(raw).__name__}")
    try:
        return Verdict.model_validate({**raw, "check": check, "stage": stage})
    except ValidationError as e:
        return fail_closed(check, stage, "malformed", str(e).splitlines()[0])
