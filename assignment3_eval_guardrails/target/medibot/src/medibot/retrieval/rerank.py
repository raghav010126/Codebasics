"""Cross-encoder reranking: score (query, chunk) jointly, keep the top few."""

import logging
from dataclasses import dataclass
from functools import lru_cache

from sentence_transformers import CrossEncoder

from medibot.config import get_settings
from medibot.retrieval.search import Hit

log = logging.getLogger(__name__)


@dataclass
class Reranked:
    hit: Hit
    score: float  # raw cross-encoder logit (higher = more relevant)
    original_rank: int  # 1-based position in the hybrid candidate list


@lru_cache
def reranker() -> CrossEncoder:
    return CrossEncoder(get_settings().rerank_model)


def rerank(query: str, hits: list[Hit], top_k: int | None = None) -> list[Reranked]:
    top_k = top_k or get_settings().rerank_top_k
    if not hits:
        return []
    scores = reranker().predict([(query, h.embed_text) for h in hits], show_progress_bar=False)
    ranked = sorted(
        (Reranked(hit=h, score=float(s), original_rank=i + 1) for i, (h, s) in enumerate(zip(hits, scores))),
        key=lambda r: r.score,
        reverse=True,
    )
    for new_rank, r in enumerate(ranked, 1):
        log.info(
            "rerank %2d (was %2d) score=%7.3f  %s > %s",
            new_rank, r.original_rank, r.score, r.hit.source_document, r.hit.section_title,
        )
    return ranked[:top_k]
