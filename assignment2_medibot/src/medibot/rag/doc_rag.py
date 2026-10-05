"""Document RAG: RBAC-filtered hybrid retrieval -> cross-encoder rerank -> cited answer."""

import re
from dataclasses import dataclass, field

from medibot import llm
from medibot.config import get_settings
from medibot.rag import prompts
from medibot.retrieval.rerank import Reranked, rerank
from medibot.retrieval.search import search


def retrieve(question: str, role: str) -> list[Reranked]:
    """Hybrid top-10 (RBAC filter inside Qdrant) narrowed to the reranked top-3."""
    s = get_settings()
    candidates = search(question, role, mode="hybrid", k=s.candidate_k)
    return rerank(question, candidates, top_k=s.rerank_top_k)


def build_context(chunks: list[Reranked]) -> str:
    return "\n\n".join(
        f"[{i}] Source: {c.hit.source_document} - {c.hit.section_title}\n{c.hit.embed_text}"
        for i, c in enumerate(chunks, 1)
    )


def answer_from_context(question: str, chunks: list[Reranked], note: str | None = None) -> str:
    user = f"Context passages:\n\n{build_context(chunks)}\n\nQuestion: {question}"
    answer = llm.complete(prompts.ANSWER_SYSTEM, user, effort="medium", max_tokens=4096)
    answer = re.sub(r"[【\[]\s*(\d+)\s*[】\]]", r"[\1]", answer).replace("\u202f", " ")  # uniform [n] citations
    return f"{answer}\n\n{note}" if note else answer


def sources_of(chunks: list[Reranked]) -> list[dict]:
    seen, out = set(), []
    for c in chunks:
        key = (c.hit.source_document, c.hit.section_title)
        if key not in seen:
            seen.add(key)
            out.append({"source_document": key[0], "section_title": key[1], "collection": c.hit.collection})
    return out
