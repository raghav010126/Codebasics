"""Request-scoped metrics: every model call (target, guardrail, judge) and per-stage latency."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field

from mediguard.models import LLMCall


@dataclass
class RequestMetrics:
    llm_calls: list[LLMCall] = field(default_factory=list)
    stage_ms: dict[str, float] = field(default_factory=dict)
    bedrock_text_units: int = 0

    def totals(self) -> dict:
        calls = self.llm_calls
        return {
            "tokens_in": sum(c.input_tokens for c in calls),
            "tokens_out": sum(c.output_tokens for c in calls),
            "cost_usd": round(sum(c.cost_usd for c in calls), 6),
            "cold_cost_usd": round(sum(c.cold_cost_usd for c in calls), 6),
            "llm_calls": len(calls),
            "cached_calls": sum(c.cached for c in calls),
            "bedrock_text_units": self.bedrock_text_units,
            "by_component": {comp: {"tokens_in": sum(c.input_tokens for c in calls if c.component == comp),
                                    "tokens_out": sum(c.output_tokens for c in calls if c.component == comp),
                                    "cost_usd": round(sum(c.cost_usd for c in calls if c.component == comp), 6)}
                             for comp in sorted({c.component for c in calls})},
            "stage_ms": {k: round(v, 1) for k, v in self.stage_ms.items()},
        }


_current: ContextVar[RequestMetrics | None] = ContextVar("mediguard_metrics", default=None)


def start() -> tuple[RequestMetrics, object]:
    m = RequestMetrics()
    return m, _current.set(m)


def stop(token) -> None:
    _current.reset(token)


def current() -> RequestMetrics | None:
    return _current.get()


def record_llm(call: LLMCall) -> None:
    m = _current.get()
    if m is not None:
        m.llm_calls.append(call)
