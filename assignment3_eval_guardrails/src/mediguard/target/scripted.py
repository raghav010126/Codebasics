"""A scripted stand-in target: returns preset responses, used to show the output guardrails catching bad answers
and the fail-closed paths without calling a real model."""

from __future__ import annotations

from mediguard.models import Passage, Scope, TargetResponse


class ScriptedTarget:
    name = "scripted"
    version = "fixture"
    model = "none"

    def __init__(self, responses: dict[str, TargetResponse] | None = None, default: TargetResponse | None = None):
        self.responses = responses or {}
        self.default = default or TargetResponse(status="ok", answer="ok", retrieval_type="hybrid_rag")
        self.calls = 0

    def scope(self, role: str) -> Scope:
        from medibot import rbac

        return Scope(role=role, collections=rbac.collections_for(role),
                     restricted_collections=rbac.restricted_collections_for(role),
                     sql_tables=list(rbac.sql_tables_for(role)))

    def respond(self, question: str, role: str) -> TargetResponse:
        self.calls += 1
        return self.responses.get(question, self.default).model_copy(deep=True)


def response_from_chunks(answer: str, chunk_refs: list[tuple[str, int]], **kw) -> TargetResponse:
    """A TargetResponse whose passages are real indexed chunks (as MediBot would have shown them)."""
    from mediguard.eval.dataset import chunk_lookup

    passages, contexts, sources = [], [], []
    for n, (doc, idx) in enumerate(chunk_refs, 1):
        c = chunk_lookup()[(doc, idx)]
        passages.append(Passage(n=n, chunk_id=c["id"], source_document=doc, section_title=c["section_title"],
                                collection=c["collection"], access_roles=c["access_roles"], chunk_index=idx,
                                text=c["text"], embed_text=c["embed_text"]))
        contexts.append(f"Source: {doc} - {c['section_title']}\n{c['embed_text']}")
        sources.append({"source_document": doc, "section_title": c["section_title"], "collection": c["collection"]})
    return TargetResponse(status=kw.pop("status", "ok"), answer=answer, retrieval_type=kw.pop("retrieval_type", "hybrid_rag"),
                          passages=passages, contexts=contexts, sources=sources, **kw)
