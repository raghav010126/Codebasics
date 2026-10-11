"""MediBot as a guardrail/evaluation target: run handle_chat under instrumentation and describe what happened.

Usage: t = MediBotTarget(); r = t.respond("What is the vancomycin dose?", "doctor")
"""

from __future__ import annotations

import re
import time

from mediguard.config import get_settings
from mediguard.models import Scope, TargetResponse
from mediguard.observability.tracing import span
from mediguard.target.medibot import instrument
from mediguard.target.medibot.bootstrap import prepare

MEDIBOT_COMMIT = "dbe20cf"
_ABSTAIN = re.compile(r"\b(could ?n[o']t find|not (?:found|available|covered|mentioned|specified)|no information|"
                      r"does not (?:contain|include|specify|mention)|do not (?:contain|include|specify))\b", re.I)


def sql_context(sql: str, cols: list[str], rows: list[list]) -> str:
    """The result table exactly as MediBot's summarize() shows it to the model."""
    shown = rows[:50]
    table = "\n".join([" | ".join(cols)] + [" | ".join("" if v is None else str(v) for v in r) for r in shown])
    more = f"\n(... {len(rows) - len(shown)} more rows)" if len(rows) > len(shown) else ""
    return f"SQL used:\n{sql}\n\nResult ({len(rows)} rows):\n{table}{more}"


class MediBotTarget:
    name = "medibot"
    version = MEDIBOT_COMMIT

    def __init__(self, model: str | None = None):
        self.model = model or get_settings().target_model
        prepare(self.model)
        instrument.install()

    def scope(self, role: str) -> Scope:
        from medibot import rbac

        return Scope(role=role, collections=rbac.collections_for(role),
                     restricted_collections=rbac.restricted_collections_for(role),
                     sql_tables=list(rbac.sql_tables_for(role)))

    def respond(self, question: str, role: str) -> TargetResponse:
        from medibot.llm import LLMRefusal
        from medibot.rag.pipeline import handle_chat
        from medibot.retrieval.search import RBACLeakError

        cap, token = instrument.begin()
        t0 = time.perf_counter()
        result, error, status = None, None, None
        try:
            with span("target:medibot", "chain", inputs={"question": question, "role": role},
                      metadata={"medibot_commit": MEDIBOT_COMMIT, "target_model": self.model}) as sp:
                try:
                    result = handle_chat(question, role)
                except RBACLeakError as e:
                    status, error = "leak_guard", f"RBACLeakError: {e}"
                except LLMRefusal as e:
                    status, error = "model_refusal", f"LLMRefusal: {e}"
                except Exception as e:  # noqa: BLE001 - every failure becomes a status, never a crash
                    status, error = "error", f"{type(e).__name__}: {e}"[:500]
                resp = self._describe(cap, result, status, error, role)
                if resp.retrieval_type == "hybrid_rag" and cap.route is not None:
                    self._rbac_span(cap, role, resp)
                sp.meta(status=resp.status, flags=resp.flags)
                sp.end(outputs={"status": resp.status, "answer": resp.answer, "sources": resp.sources},
                       error=error)
        finally:
            instrument.end(token)
        wall = (time.perf_counter() - t0) * 1000
        resp.latency_ms = round(wall, 1)
        resp.recorded_latency_ms = round(wall + cap.replay_saved_ms, 1)
        return resp

    # -------------------------------------------------------------- helpers
    def _describe(self, cap, result, status, error, role) -> TargetResponse:
        flags = []
        route = cap.route or {}
        if route.get("source") == "keyword":
            flags.append("router_degraded")
        if any(c.served_model and not c.served_model.startswith(self.model) for c in cap.llm_calls):
            flags.append("served_by_fallback")
        if cap.sql_attempts > 1:
            flags.append("sql_repaired")
        if cap.note:
            flags.append("restricted_note")
        if cap.llm_calls and all(c.cached for c in cap.llm_calls):
            flags.append("replayed")
        rtype = result.retrieval_type if result is not None else ("sql_rag" if route.get("route") == "sql" else
                                                                  "hybrid_rag")
        if status is None:
            if result.blocked:
                status = "refused"
                flags.append("rbac_sql" if rtype == "sql_rag" else "rbac_collection")
            elif rtype == "hybrid_rag" and not cap.answer_called:
                status = "not_found"
            elif rtype == "sql_rag" and cap.sql is not None and not cap.sql["answerable"]:
                status = "sql_unanswerable"
            else:
                status = "ok"
        answer = result.answer if result is not None else ""
        if status == "ok" and _ABSTAIN.search(answer or ""):
            flags.append("llm_abstained")
        if rtype == "sql_rag" and cap.sql is not None:
            contexts = [sql_context(cap.sql["sql"], cap.sql["columns"], cap.sql["rows"])] if cap.sql["sql"] else []
        else:
            contexts = [f"Source: {p.source_document} - {p.section_title}\n{p.embed_text}" for p in cap.passages]
        return TargetResponse(
            status=status, flags=flags, answer=answer, retrieval_type=rtype,
            blocked=bool(result.blocked) if result is not None else False,
            sources=list(result.sources) if result is not None else [], route=cap.route,
            candidates=cap.reranked or cap.candidates, passages=cap.passages, contexts=contexts,
            sql_query=(cap.sql or {}).get("sql") or (result.sql_query if result is not None else None),
            sql_columns=(cap.sql or {}).get("columns", []), sql_rows=(cap.sql or {}).get("rows", []),
            llm_calls=cap.llm_calls, error=error)

    def _rbac_span(self, cap, role: str, resp: TargetResponse) -> None:
        """Re-state MediBot's refusal decision (pipeline._handle_rag) from captured data, for the trace."""
        from medibot import rbac
        from medibot.config import get_settings as medibot_settings

        ms = medibot_settings()
        allowed = rbac.collections_for(role)
        targets = (cap.route or {}).get("target_collections", [])
        restricted = [c for c in targets if c not in allowed]
        best = cap.reranked[0]["rerank_score"] if cap.reranked else 0.0
        inputs = {"role": role, "router_targets": targets, "restricted_targets": restricted,
                  "allowed_targets": [c for c in targets if c in allowed], "best_rerank_score": best,
                  "router_override_score": ms.router_override_score, "rerank_min_score": ms.rerank_min_score}
        with span("rbac:decision", "chain", inputs=inputs) as sp:
            sp.end(outputs={"status": resp.status, "flags": resp.flags})
