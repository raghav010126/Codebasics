"""The /chat decision flow: route -> RBAC UX checks -> SQL RAG or hybrid+rerank RAG.

Hard enforcement never depends on this module's routing: document chunks are filtered
inside Qdrant by role, and SQL runs under a per-role table authorizer.
"""

import re
from dataclasses import dataclass, field

from medibot import rbac
from medibot.config import get_settings
from medibot.rag import doc_rag, router, sql_rag


@dataclass
class ChatResult:
    answer: str
    retrieval_type: str  # "hybrid_rag" | "sql_rag"
    sources: list[dict] = field(default_factory=list)
    blocked: bool = False
    sql_query: str | None = None
    rerank: list[dict] = field(default_factory=list)


def _join(items: list[str]) -> str:
    return rbac._join(items)


def _handle_sql(question: str, role: str, route: router.Route) -> ChatResult:
    allowed = rbac.sql_tables_for(role)
    if not allowed:
        return ChatResult(rbac.sql_refusal(role), "sql_rag", blocked=True)
    wanted = route.sql_tables or list(allowed)
    denied = [t for t in wanted if t not in allowed]
    if denied:
        return ChatResult(rbac.sql_refusal(role, denied), "sql_rag", blocked=True)
    res = sql_rag.run_sql_rag(question, allowed)
    used = [t for t in allowed if re.search(rf"\b{t}\b", res.sql)] if res.sql else []
    sources = [{"source_document": "mediassist.db", "section_title": t, "collection": "database"} for t in used]
    return ChatResult(res.answer, "sql_rag", sources=sources, sql_query=res.sql or None)


def _handle_rag(question: str, role: str, route: router.Route) -> ChatResult:
    s = get_settings()
    allowed = rbac.collections_for(role)
    restricted_targets = [c for c in route.target_collections if c not in allowed]
    allowed_targets = [c for c in route.target_collections if c in allowed]

    chunks = doc_rag.retrieve(question, role)  # RBAC filter applied inside Qdrant
    best = chunks[0].score if chunks else 0.0
    scores = [
        {"source_document": c.hit.source_document, "section_title": c.hit.section_title,
         "score": round(c.score, 4), "hybrid_rank": c.original_rank}
        for c in chunks
    ]

    # Question is about restricted material only -> informative refusal
    # (unless allowed documents match it very strongly, i.e. the router likely mis-targeted).
    if restricted_targets and not allowed_targets and best < s.router_override_score:
        return ChatResult(rbac.collection_refusal(role, restricted_targets), "hybrid_rag", blocked=True)

    if not chunks or best < s.rerank_min_score:
        where = _join(allowed)
        return ChatResult(
            f"I couldn't find relevant information for that in the collections available to you ({where}).",
            "hybrid_rag",
        )

    note = None
    if restricted_targets:
        role_label = rbac.ROLE_LABELS[role]
        note = (f"_Note: part of your question concerns {_join(restricted_targets)} documents, which "
                f"{rbac._article(role_label)} {role_label} cannot access. This answer uses only your accessible collections._")
    answer = doc_rag.answer_from_context(question, chunks, note=note)
    return ChatResult(answer, "hybrid_rag", sources=doc_rag.sources_of(chunks), rerank=scores)


def handle_chat(question: str, role: str) -> ChatResult:
    rbac.validate_role(role)
    route = router.classify(question)
    if route.route == "sql":
        return _handle_sql(question, role, route)
    return _handle_rag(question, role, route)
