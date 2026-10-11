"""LangChain chat models for the OpenEvals guardrails (Haiku 4.5, temperature 0) and the RAGAS evaluator (Opus 5.5).

A callback records every call's tokens and cost into the request metrics, so guardrail and evaluation spend shows up
next to the target's.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from mediguard.config import get_settings
from mediguard.llm.cost import cost_usd
from mediguard.models import LLMCall
from mediguard.observability import metrics


class UsageCallback(BaseCallbackHandler):
    def __init__(self, component: str, label: str, model: str):
        self.component, self.label, self.model = component, label, model
        self._t0: dict[UUID, float] = {}
        self.calls: list[LLMCall] = []

    def on_chat_model_start(self, serialized, messages, *, run_id: UUID, **kwargs: Any) -> None:
        self._t0[run_id] = time.perf_counter()

    def on_llm_end(self, response, *, run_id: UUID, **kwargs: Any) -> None:
        tin = tout = 0
        for gens in response.generations:
            for g in gens:
                um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                tin += um.get("input_tokens", 0)
                tout += um.get("output_tokens", 0)
        latency = (time.perf_counter() - self._t0.pop(run_id, time.perf_counter())) * 1000
        cost = cost_usd(self.model, tin, tout)
        call = LLMCall(component=self.component, label=self.label, model=self.model, input_tokens=tin,
                       output_tokens=tout, latency_ms=round(latency, 1), cost_usd=cost, cold_cost_usd=cost)
        self.calls.append(call)
        metrics.record_llm(call)


SAMPLING_OK = ("claude-haiku-4-5", "claude-sonnet-4-6")   # current models that still accept temperature


def accepts_temperature(model: str) -> bool:
    return model.startswith(SAMPLING_OK)


def chat_model(model: str, component: str, label: str, max_tokens: int = 1024, effort: str | None = None):
    """Temperature 0 where the model accepts it; the 5.5 models take no sampling parameters (effort instead)."""
    from langchain_anthropic import ChatAnthropic

    kw: dict = {"temperature": 0.0} if accepts_temperature(model) else {}
    if effort and not accepts_temperature(model):
        kw["effort"] = effort
    return ChatAnthropic(model=model, max_tokens=max_tokens, max_retries=3, timeout=180,
                         callbacks=[UsageCallback(component, label, model)], **kw)


def guard_chat_model(stage: str):
    return chat_model(get_settings().guard_model, "guardrail", f"{stage}_judge", max_tokens=512)
