"""Run a stage of guardrail checks cheapest-first and decide, failing closed.

The stage allows a request only if every check that ran returned a valid "allow". An exception, a timeout, a malformed
or missing verdict, an empty check list, or a required check that is not configured all block.
Modes: short_circuit (live traffic: stop after the first tier that blocks) and exhaustive (red team: run everything,
so every layer's verdict can be shown).
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from itertools import groupby
from typing import Any, Literal, Protocol

from mediguard.guardrails.verdict import StageDecision, Verdict, coerce_verdict, fail_closed
from mediguard.models import Scope, TargetResponse
from mediguard.observability.events import log_event
from mediguard.observability.tracing import span

Mode = Literal["short_circuit", "exhaustive"]


@dataclass
class CheckContext:
    request_id: str
    question: str
    role: str
    scope: Scope
    target: TargetResponse | None = None      # output stage only
    answer: str = ""                          # output stage: the text about to be shown
    extra: dict[str, Any] = field(default_factory=dict)


class Check(Protocol):
    name: str
    stage: str
    tier: int            # 0 deterministic rules, 1 local models / Bedrock, 2 LLM judges
    timeout_s: float
    required: bool

    def configured(self) -> bool: ...

    def evaluate(self, ctx: CheckContext) -> object: ...


def _run_one(check: Check, ctx: CheckContext, stage: str) -> Verdict:
    t0 = time.perf_counter()
    with span(f"check:{check.name}", "chain", inputs={"stage": stage, "role": ctx.role},
              metadata={"tier": check.tier}) as sp:
        try:
            raw = check.evaluate(ctx)
            v = coerce_verdict(raw, check.name, stage)
        except Exception as e:  # noqa: BLE001 - any failure inside a check blocks
            v = fail_closed(check.name, stage, f"error:{type(e).__name__}", str(e))
        v = v.model_copy(update={"latency_ms": round((time.perf_counter() - t0) * 1000, 1)})
        sp.end(outputs={"decision": v.decision, "reason_code": v.reason_code, "fail_closed": v.fail_closed,
                        "warnings": v.warnings, "evidence": v.evidence})
        return v


def run_stage(checks: Sequence[Check], ctx: CheckContext, stage: str, mode: Mode = "short_circuit") -> StageDecision:
    t0 = time.perf_counter()
    verdicts: list[Verdict] = []
    with span(f"guardrail:{stage}", "chain", inputs={"question": ctx.question, "role": ctx.role,
                                                       "answer": ctx.answer or None}, metadata={"mode": mode}) as sp:
        if not checks:
            verdicts.append(fail_closed("stage", stage, "no_checks", "no checks configured for this stage"))
        active: list[Check] = []
        for c in checks:
            try:
                ok = c.configured()
            except Exception as e:  # noqa: BLE001 - a check that cannot report its configuration blocks
                verdicts.append(fail_closed(c.name, stage, f"error:configured:{type(e).__name__}", str(e)))
                continue
            if ok:
                active.append(c)
            elif c.required:
                verdicts.append(fail_closed(c.name, stage, "unconfigured", f"required check {c.name} not configured"))
        stop = any(v.decision == "block" for v in verdicts) and mode == "short_circuit"
        for _tier, group in groupby(sorted(active, key=lambda c: c.tier), key=lambda c: c.tier):
            if stop:
                break
            group = list(group)
            # No `with` block: its exit would wait for a hung check, so a timeout would not bound the request.
            pool = ThreadPoolExecutor(max_workers=len(group))
            try:
                futures = [(c, pool.submit(contextvars.copy_context().run, _run_one, c, ctx, stage)) for c in group]
                started = time.monotonic()
                for c, fut in futures:
                    try:   # each check's timeout counts from submission, so the tier waits at most its longest one
                        verdicts.append(fut.result(timeout=max(0.0, started + c.timeout_s - time.monotonic())))
                    except FutureTimeout:
                        verdicts.append(fail_closed(c.name, stage, "timeout", f"no verdict within {c.timeout_s}s"))
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            if mode == "short_circuit" and any(v.decision == "block" for v in verdicts):
                stop = True
        blocking = next((v for v in verdicts if v.decision == "block"), None)
        decision = StageDecision(stage=stage, decision="block" if blocking else "allow", verdicts=verdicts,
                                 blocking=blocking, latency_ms=round((time.perf_counter() - t0) * 1000, 1))
        for v in verdicts:
            log_event("guardrail.check", request_id=ctx.request_id, stage=stage, check=v.check, decision=v.decision,
                      reason_code=v.reason_code, detail_internal=v.detail, fail_closed=v.fail_closed,
                      warnings=v.warnings, evidence=v.evidence, latency_ms=v.latency_ms, role=ctx.role)
        sp.tags(f"verdict:{decision.decision}")
        sp.end(outputs={"decision": decision.decision,
                        "blocking": blocking.reason_code if blocking else None,
                        "verdicts": [{"check": v.check, "decision": v.decision, "reason_code": v.reason_code}
                                     for v in verdicts]})
    return decision
