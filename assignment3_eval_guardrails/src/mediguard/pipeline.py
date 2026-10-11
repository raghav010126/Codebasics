"""GuardedAssistant: input guardrails -> target chatbot -> output guardrails, traced and logged end to end.

The user only ever sees the target's answer or one generic refusal carrying a request id. Every guardrail decision,
with its internal reason, goes to the trace and the structured event log, so any request can be reconstructed from
its id alone.

Usage:
    from mediguard.pipeline import GuardedAssistant
    from mediguard.target.medibot.adapter import MediBotTarget
    bot = GuardedAssistant(MediBotTarget())
    r = bot.handle("What is the vancomycin dose?", role="doctor"); print(r.public())
"""

from __future__ import annotations

import time
import uuid
from contextlib import suppress
from typing import Any, Literal

from pydantic import Field

from mediguard.config import get_settings
from mediguard.guardrails.refusal import refusal, unavailable
from mediguard.guardrails.runner import Check, CheckContext, Mode, run_stage
from mediguard.guardrails.verdict import StageDecision, fail_closed
from mediguard.models import Strict, TargetResponse
from mediguard.observability import metrics
from mediguard.observability.events import log_event
from mediguard.observability.tracing import feedback, span
from mediguard.target.base import TargetAdapter

Status = Literal["answered", "refused_by_target", "blocked_input", "blocked_output", "blocked_target", "target_error",
                 "internal_error"]


class GuardedResponse(Strict):
    request_id: str
    role: str
    question: str
    status: Status
    public_answer: str
    public_sources: list[dict[str, Any]] = Field(default_factory=list)
    input: StageDecision
    output: StageDecision | None = None
    target: TargetResponse | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    recorded_latency_ms: float = 0.0
    trace_id: str | None = None
    run_url: str | None = None
    degraded: list[str] = Field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.status.startswith("blocked")

    @property
    def blocking(self):
        for stage in (self.input, self.output):
            if stage is not None and stage.blocking is not None:
                return stage.blocking
        return None

    def public(self) -> dict[str, Any]:
        """Exactly what the end user receives: no verdicts, no reasons."""
        return {"request_id": self.request_id, "answer": self.public_answer, "sources": self.public_sources}


def default_checks(stage: str, *, bedrock: bool = True, openevals: bool = True, paraphrase: bool = True) -> list[Check]:
    from mediguard.guardrails.checks.bedrock import BedrockGuardrail
    from mediguard.guardrails.checks.leak_detector import LeakDetector, LeakParaphrase
    from mediguard.guardrails.checks.llm_judge import OpenEvalsJudge
    from mediguard.guardrails.checks.numeric_grounding import NumericGrounding
    from mediguard.guardrails.checks.rules_input import InputRules
    from mediguard.guardrails.checks.rules_output import OutputRules

    if stage == "input":
        checks: list[Check] = [InputRules()]
    else:
        checks = [OutputRules(), LeakDetector(), NumericGrounding()]
        if paraphrase:
            checks.append(LeakParaphrase())
    if bedrock:
        checks.append(BedrockGuardrail(stage))
    if openevals:
        checks.append(OpenEvalsJudge(stage))
    return checks


class GuardedAssistant:
    def __init__(self, target: TargetAdapter, input_checks: list[Check] | None = None,
                 output_checks: list[Check] | None = None, mode: Mode = "short_circuit", *, bedrock: bool = True,
                 openevals: bool = True, paraphrase: bool = True):
        self.target = target
        self.input_checks = input_checks if input_checks is not None else default_checks(
            "input", bedrock=bedrock, openevals=openevals)
        self.output_checks = output_checks if output_checks is not None else default_checks(
            "output", bedrock=bedrock, openevals=openevals, paraphrase=paraphrase)
        self.mode = mode
        self.degraded = [name for name, on in (("bedrock", bedrock), ("openevals", openevals)) if not on]

    def handle(self, question: str, role: str, request_id: str | None = None, mode: Mode | None = None,
               meta: dict[str, Any] | None = None, probe_target: bool = False) -> GuardedResponse:
        """Answer one question as one role. probe_target also runs the target and output stage after an input block
        (red team only: shows the next layers' behaviour); the user still gets the refusal."""
        s = get_settings()
        rid = request_id or f"req_{uuid.uuid4().hex[:12]}"
        mode = mode or self.mode
        meta = meta or {}
        m, token = metrics.start()
        t0 = time.perf_counter()
        try:
            scope = self.target.scope(role)
            root_meta = {"request_id": rid, "target": self.target.name, "target_version": self.target.version,
                         "target_model": self.target.model, "guardrail_id": s.bedrock_guardrail_id or None,
                         "guardrail_version": s.bedrock_guardrail_version, "mode": mode, "degraded": self.degraded,
                         **meta}
            with span("guarded_request", "chain", inputs={"question": question, "role": role}, metadata=root_meta,
                      tags=[f"role:{role}", f"target:{self.target.name}"]) as root:
                log_event("request.received", request_id=rid, trace_id=root.trace_id, role=role, question=question,
                          **meta)
                ctx = CheckContext(request_id=rid, question=question, role=role, scope=scope)
                ts = time.perf_counter()
                inp = run_stage(self.input_checks, ctx, "input", mode)
                m.stage_ms["input_guardrails"] = (time.perf_counter() - ts) * 1000
                self._log_stage(rid, inp, role)
                feedback(root, "guardrail.input", 1.0 if inp.decision == "allow" else 0.0,
                         inp.blocking.reason_code if inp.blocking else "allow")

                target, out = None, None
                if inp.decision == "allow" or probe_target:
                    ts = time.perf_counter()
                    target = self.target.respond(question, role)
                    m.stage_ms["target"] = (time.perf_counter() - ts) * 1000
                    log_event("target.response", request_id=rid, role=role, status=target.status, flags=target.flags,
                              route=target.route, retrieval_type=target.retrieval_type, answer=target.answer,
                              chunks=[{"n": p.n, "chunk_id": p.chunk_id, "source": p.source_document,
                                       "section": p.section_title, "rerank": p.rerank_score} for p in target.passages],
                              sql=target.sql_query, error=target.error, latency_ms=target.latency_ms,
                              recorded_latency_ms=target.recorded_latency_ms)
                    if target.status not in ("error",):
                        ctx.target, ctx.answer = target, target.answer
                        ts = time.perf_counter()
                        out = run_stage(self.output_checks, ctx, "output", mode)
                        m.stage_ms["output_guardrails"] = (time.perf_counter() - ts) * 1000
                        self._log_stage(rid, out, role)
                        feedback(root, "guardrail.output", 1.0 if out.decision == "allow" else 0.0,
                                 out.blocking.reason_code if out.blocking else "allow")

                status, public, sources = self._final(rid, inp, target, out)
                wall = (time.perf_counter() - t0) * 1000
                recorded = wall + ((target.recorded_latency_ms - target.latency_ms) if target else 0.0)
                totals = m.totals()
                blocking = (inp.blocking or (out.blocking if out else None))
                resp = GuardedResponse(
                    request_id=rid, role=role, question=question, status=status, public_answer=public,
                    public_sources=sources, input=inp, output=out, target=target, metrics=totals,
                    latency_ms=round(wall, 1), recorded_latency_ms=round(recorded, 1), trace_id=root.trace_id,
                    degraded=self.degraded)
                root.tags(f"status:{status}", f"verdict:{'block' if resp.blocked else 'allow'}",
                          *( [f"stage:{blocking.stage}", f"reason:{blocking.reason_code}"] if blocking else []),
                          *( [f"target_status:{target.status}"] if target else []))
                root.meta(latency_ms=resp.latency_ms, recorded_latency_ms=resp.recorded_latency_ms, **totals)
                root.end(outputs={**resp.public(), "status": status,
                                  "usage_metadata": {"input_tokens": totals["tokens_in"],
                                                     "output_tokens": totals["tokens_out"],
                                                     "total_tokens": totals["tokens_in"] + totals["tokens_out"]}})
                resp.run_url = root.url()
                log_event("request.completed", request_id=rid, trace_id=root.trace_id, run_url=resp.run_url,
                          role=role, status=status, verdict="block" if resp.blocked else "allow",
                          blocking_stage=blocking.stage if blocking else None,
                          blocking_check=blocking.check if blocking else None,
                          reason_code=blocking.reason_code if blocking else None,
                          target_status=target.status if target else None,
                          target_flags=target.flags if target else [], public_answer=public,
                          latency_ms=resp.latency_ms, recorded_latency_ms=resp.recorded_latency_ms,
                          models=sorted({c.model for c in m.llm_calls}), degraded=self.degraded, **totals, **meta)
                return resp
        except Exception as e:  # noqa: BLE001 - a failure in the pipeline itself fails closed, never as a traceback
            return self._internal_error(rid, question, role, e, t0, m, meta)
        finally:
            metrics.stop(token)

    def _internal_error(self, rid: str, question: str, role: str, err: Exception, t0: float, m,
                        meta: dict[str, Any]) -> GuardedResponse:
        """The user gets the generic refusal; the error type and message go only to the event log."""
        v = fail_closed("pipeline", "input", f"error:{type(err).__name__}", str(err)[:300])
        wall = round((time.perf_counter() - t0) * 1000, 1)
        totals = m.totals()
        with suppress(Exception):  # the log itself may be what failed; the user still gets the refusal
            log_event("request.failed", request_id=rid, role=role, question=question, error_type=type(err).__name__,
                      error=str(err)[:300], latency_ms=wall, **meta)
            log_event("request.completed", request_id=rid, role=role, status="internal_error", verdict="block",
                      blocking_stage=None, blocking_check="pipeline", reason_code=v.reason_code, target_status=None,
                      target_flags=[], public_answer=refusal(rid), latency_ms=wall, recorded_latency_ms=wall,
                      models=sorted({c.model for c in m.llm_calls}), degraded=self.degraded, **totals, **meta)
        return GuardedResponse(
            request_id=rid, role=role, question=question, status="internal_error", public_answer=refusal(rid),
            input=StageDecision(stage="input", decision="block", verdicts=[v], blocking=v), metrics=totals,
            latency_ms=wall, recorded_latency_ms=wall, degraded=self.degraded)

    @staticmethod
    def _final(rid: str, inp: StageDecision, target: TargetResponse | None, out: StageDecision | None):
        if inp.decision == "block":
            return "blocked_input", refusal(rid), []
        if target is None or target.status == "error":
            return "target_error", unavailable(rid), []
        if target.status in ("leak_guard", "model_refusal"):
            return "blocked_target", refusal(rid), []
        if out is not None and out.decision == "block":
            return "blocked_output", refusal(rid), []
        status = "refused_by_target" if target.status == "refused" else "answered"
        return status, target.answer, target.sources

    @staticmethod
    def _log_stage(rid: str, d: StageDecision, role: str) -> None:
        b = d.blocking
        log_event("guardrail.decision", request_id=rid, stage=d.stage, decision=d.decision, role=role,
                  blocking_check=b.check if b else None, reason_code=b.reason_code if b else None,
                  detail_internal=b.detail if b else None, fail_closed=bool(b and b.fail_closed),
                  checks=[{"check": v.check, "decision": v.decision, "reason_code": v.reason_code,
                           "latency_ms": v.latency_ms} for v in d.verdicts],
                  warnings=d.warnings, latency_ms=d.latency_ms)
