"""Runtime instrumentation of the unmodified MediBot: capture, LangSmith spans, metrics, record/replay.

MediBot's modules import functions by name, so each hook patches the name in the module that *calls* it:

| Patched name                          | Why it works                                   | Captures                          |
|---------------------------------------|------------------------------------------------|-----------------------------------|
| medibot.rag.router.classify           | pipeline calls router.classify                 | route, llm vs keyword fallback    |
| medibot.rag.doc_rag.search            | doc_rag imported search by name                | 10 hybrid (RRF) candidates        |
| medibot.rag.doc_rag.rerank            | doc_rag imported rerank by name                | all cross-encoder scores, top-k   |
| medibot.rag.doc_rag.answer_from_context | pipeline calls doc_rag.answer_from_context   | exact passages and prompt context |
| medibot.rag.sql_rag.run_sql_rag (+ generate/execute/summarize) | module globals             | SQL, rows, repair attempts        |
| medibot.llm._create                   | _call looks _create up at call time            | tokens, served model, replay      |

The guardrails' leak detector uses the unpatched medibot.retrieval.search.search / rerank.rerank, so its own
lookups never enter a request's capture.
"""

from __future__ import annotations

import os
import re
import time
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any

from mediguard.config import get_settings
from mediguard.llm.cost import cost_usd
from mediguard.models import LLMCall, Passage
from mediguard.observability import metrics
from mediguard.observability.tracing import span
from mediguard.target.medibot import replay

LABELS = (  # system-prompt prefix -> call label
    ("You route questions", "router"),
    ("You are MediBot, an internal assistant", "answer"),
    ("You translate questions", "nl2sql"),
    ("You are MediBot. Turn the SQL result", "sql_answer"),
)


@dataclass
class Capture:
    route: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    reranked: list[dict] = field(default_factory=list)
    passages: list[Passage] = field(default_factory=list)
    prompt_context: str | None = None
    note: str | None = None
    answer_called: bool = False
    sql: dict | None = None
    sql_attempts: int = 0
    llm_calls: list[LLMCall] = field(default_factory=list)
    replay_saved_ms: float = 0.0    # recorded minus actual latency of replayed calls
    fault: str | None = None


_capture: ContextVar[Capture | None] = ContextVar("medibot_capture", default=None)
_originals: dict[str, Any] = {}


def current() -> Capture | None:
    return _capture.get()


def begin(fault: str | None = None) -> tuple[Capture, object]:
    cap = Capture(fault=fault or os.environ.get("MEDIGUARD_FAULT") or None)
    return cap, _capture.set(cap)


def end(token) -> None:
    _capture.reset(token)


def label_for(system: str) -> str:
    return next((label for prefix, label in LABELS if system.startswith(prefix)), "other")


# ------------------------------------------------------------------ model calls (+ replay)
def _create(client, use_fallbacks: bool, **kwargs):
    import anthropic

    cap = current()
    system = kwargs.get("system", "") or ""
    label = label_for(system)
    model = kwargs.get("model", "")
    effort = (kwargs.get("output_config") or {}).get("effort")
    mode = get_settings().replay_mode
    key = replay.request_key(use_fallbacks, kwargs)
    record = replay.load(key) if mode != "fresh" else None
    user = (kwargs.get("messages") or [{}])[0].get("content", "")
    inputs = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    meta = {"ls_provider": "anthropic", "ls_model_name": model, "effort": effort, "label": label,
            "fallbacks": use_fallbacks}
    with span(f"llm:{label}", "llm", inputs=inputs, metadata=meta) as sp:
        t0 = time.perf_counter()
        cached = record is not None
        if record is None:
            if mode == "replay_only":
                raise replay.ReplayMiss(f"no recorded response for {label} call ({key[:12]})")
            try:
                resp = _originals["_create"](client, use_fallbacks, **kwargs)
                record = replay.record_response(resp, use_fallbacks, (time.perf_counter() - t0) * 1000)
                replay.save(key, record)
            except anthropic.BadRequestError as e:   # deterministic: record it so swaps replay offline
                record = replay.record_error(e, (time.perf_counter() - t0) * 1000)
                replay.save(key, record)
            except Exception as e:                   # transient (auth, network, 5xx): surface, never cache
                _record(cap, LLMCall(component="target", label=label, model=model,
                                     latency_ms=(time.perf_counter() - t0) * 1000, error=f"{type(e).__name__}: {e}"[:300]))
                raise
        elapsed = (time.perf_counter() - t0) * 1000
        latency = record.get("latency_ms", elapsed)
        if cached and cap is not None:
            cap.replay_saved_ms += max(latency - elapsed, 0.0)
        if "error" in record:
            call = LLMCall(component="target", label=label, model=model, latency_ms=latency, cached=cached,
                           error=f"BadRequestError: {record['error']['message'][:200]}")
            _record(cap, call)
            sp.end(error=call.error)
            replay.rebuild(record)            # raises the (recorded) API error into MediBot's own handling
        resp = replay.rebuild(record)
        u = resp.usage
        tin, tout = u.input_tokens or 0, u.output_tokens or 0
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        served = resp.model
        cold = cost_usd(served or model, tin, tout, cache_read)
        call = LLMCall(component="target", label=label, model=model, served_model=served, input_tokens=tin,
                       output_tokens=tout, cache_read_tokens=cache_read, latency_ms=round(latency, 1),
                       cached=cached, cost_usd=0.0 if cached else cold, cold_cost_usd=cold,
                       stop_reason=resp.stop_reason)
        _record(cap, call)
        text = "".join(getattr(b, "text", "") for b in resp.content if b.type == "text")
        sp.meta(served_model=served, cached=cached, latency_ms=round(latency, 1), stop_reason=resp.stop_reason)
        sp.end(outputs={"content": text, "usage_metadata": {"input_tokens": tin, "output_tokens": tout,
                                                              "total_tokens": tin + tout}})
        return resp


def _record(cap: Capture | None, call: LLMCall) -> None:
    if cap is not None:
        cap.llm_calls.append(call)
    metrics.record_llm(call)


# ------------------------------------------------------------------ routing, retrieval, rerank, generation
def _classify(question: str):
    with span("router", "chain", inputs={"question": question}) as sp:
        route = _originals["classify"](question)
        cap = current()
        if cap is not None:
            cap.route = asdict(route)
        sp.end(outputs=asdict(route))
        return route


def _hit_doc(h, rank: int, **extra) -> dict:
    p = h.payload
    return {"chunk_id": h.id, "source_document": p["source_document"], "section_title": p["section_title"],
            "collection": p["collection"], "access_roles": list(p.get("access_roles", [])),
            "chunk_index": p.get("chunk_index"), "rank": rank, **extra}


def _search(query: str, role: str, mode: str = "hybrid", k: int | None = None, client=None):
    with span("retriever:hybrid_rrf", "retriever", inputs={"query": query, "role": role, "mode": mode, "k": k},
              metadata={"rbac_filter": f"access_roles contains {role}"}) as sp:
        hits = _originals["search"](query, role, mode=mode, k=k, client=client)
        docs = [_hit_doc(h, i, rrf_score=round(h.score, 4)) for i, h in enumerate(hits, 1)]
        cap = current()
        if cap is not None:
            cap.candidates = docs
        sp.end(outputs={"documents": [{"type": "Document", "page_content": h.embed_text[:400], "metadata": d}
                                      for h, d in zip(hits, docs)]})
        return hits


def _rerank(query: str, hits: list, top_k: int | None = None):
    from medibot.config import get_settings as medibot_settings

    top_k = top_k or medibot_settings().rerank_top_k
    with span("rerank:cross_encoder", "chain", inputs={"query": query, "candidates": len(hits), "top_k": top_k},
              metadata={"model": medibot_settings().rerank_model}) as sp:
        ranked = _originals["rerank"](query, hits, top_k=len(hits) or None) if hits else []
        cap = current()
        scored = [_hit_doc(r.hit, i, rerank_score=round(r.score, 4), hybrid_rank=r.original_rank)
                  for i, r in enumerate(ranked, 1)]
        if cap is not None:
            cap.reranked = scored
            if cap.fault == "drop_top_chunk" and len(ranked) > 1:
                ranked = ranked[1:]
        kept = ranked[:top_k]
        sp.end(outputs={"scored": scored, "kept": [r.hit.id for r in kept]})
        return kept


def _answer_from_context(question: str, chunks: list, note: str | None = None):
    from medibot.rag import doc_rag

    context = doc_rag.build_context(chunks)
    cap = current()
    if cap is not None:
        cap.answer_called = True
        cap.note = note
        cap.prompt_context = context
        cap.passages = [
            Passage(n=i, chunk_id=c.hit.id, source_document=c.hit.source_document, section_title=c.hit.section_title,
                    collection=c.hit.collection, access_roles=list(c.hit.payload.get("access_roles", [])),
                    chunk_index=c.hit.payload.get("chunk_index"), text=c.hit.text, embed_text=c.hit.embed_text,
                    rerank_score=round(c.score, 4), hybrid_rank=c.original_rank)
            for i, c in enumerate(chunks, 1)]
    with span("generation:answer", "chain", inputs={"question": question, "context": context, "note": note}) as sp:
        answer = _originals["answer_from_context"](question, chunks, note=note)
        if cap is not None and cap.fault == "perturb_dose":
            answer = re.sub(r"\b(\d+(?:\.\d+)?)\s?mg\b", lambda m: f"{float(m.group(1)) * 2:g} mg", answer, count=1)
        sp.end(outputs={"answer": answer})
        return answer


# ------------------------------------------------------------------ SQL RAG
def _run_sql_rag(question: str, tables=None):
    from medibot.rbac import ALL_SQL_TABLES

    tables = tables if tables is not None else ALL_SQL_TABLES
    with span("sql_rag", "chain", inputs={"question": question, "tables": list(tables)}) as sp:
        res = _originals["run_sql_rag"](question, tables)
        cap = current()
        if cap is not None:
            cap.sql = {"sql": res.sql, "columns": list(res.columns), "rows": [list(r) for r in res.rows],
                       "answerable": res.answerable, "tables": list(res.tables)}
        sp.end(outputs={"sql": res.sql, "row_count": len(res.rows), "answerable": res.answerable})
        return res


def _generate_sql(question: str, tables, error: str | None = None, previous: str | None = None):
    cap = current()
    if cap is not None:
        cap.sql_attempts += 1
    with span("sql:generate", "chain", inputs={"question": question, "repair_error": error}) as sp:
        raw = _originals["generate_sql"](question, tables, error=error, previous=previous)
        sp.end(outputs={"raw": raw})
        return raw


def _execute_sql(sql: str, tables):
    with span("sql:execute", "tool", inputs={"sql": sql, "tables": list(tables)}) as sp:
        try:
            cols, rows = _originals["execute_sql"](sql, tables)
        except Exception as e:
            sp.end(error=str(e))
            raise
        sp.end(outputs={"columns": cols, "row_count": len(rows), "rows_preview": [list(r) for r in rows[:10]]})
        return cols, rows


def _summarize(question: str, sql: str, cols, rows):
    with span("generation:sql_answer", "chain", inputs={"question": question, "sql": sql}) as sp:
        out = _originals["summarize"](question, sql, cols, rows)
        sp.end(outputs={"answer": out})
        return out


# ------------------------------------------------------------------ install
def install() -> None:
    """Patch MediBot in memory (idempotent). Nothing is written to its files."""
    if _originals:
        return
    from medibot import llm
    from medibot.rag import doc_rag, router, sql_rag

    patches = [
        (llm, "_create", _create), (router, "classify", _classify), (doc_rag, "search", _search),
        (doc_rag, "rerank", _rerank), (doc_rag, "answer_from_context", _answer_from_context),
        (sql_rag, "run_sql_rag", _run_sql_rag), (sql_rag, "generate_sql", _generate_sql),
        (sql_rag, "execute_sql", _execute_sql), (sql_rag, "summarize", _summarize),
    ]
    for module, name, wrapper in patches:
        _originals[name] = getattr(module, name)
        _originals[f"{name}.__module__"] = module
        setattr(module, name, wrapper)


def uninstall() -> None:
    for name in [n for n in _originals if not n.endswith(".__module__")]:
        setattr(_originals[f"{name}.__module__"], name, _originals[name])
    _originals.clear()
