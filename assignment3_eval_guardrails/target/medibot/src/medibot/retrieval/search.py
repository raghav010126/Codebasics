"""RBAC-filtered hybrid (dense + BM25) retrieval in a single Qdrant query."""

from dataclasses import dataclass, field

from qdrant_client import QdrantClient, models

from medibot.config import get_settings
from medibot.retrieval.embeddings import embed_dense_query, embed_sparse_query
from medibot.retrieval.store import DENSE_NAME, SPARSE_NAME, get_client, rbac_filter


class RBACLeakError(RuntimeError):
    """A returned chunk was outside the caller's role. Indicates a bug, never expected."""


@dataclass
class Hit:
    id: str
    score: float
    text: str
    embed_text: str
    payload: dict = field(default_factory=dict)

    @property
    def source_document(self) -> str:
        return self.payload["source_document"]

    @property
    def section_title(self) -> str:
        return self.payload["section_title"]

    @property
    def collection(self) -> str:
        return self.payload["collection"]


def run_query(
    client: QdrantClient,
    collection_name: str,
    dense: list[float],
    sparse: models.SparseVector,
    role: str,
    mode: str = "hybrid",
    k: int = 10,
    prefetch_limit: int = 20,
):
    """One Qdrant call. The RBAC filter is attached to every prefetch (and the outer query)."""
    flt = rbac_filter(role)
    common = dict(collection_name=collection_name, limit=k, with_payload=True)
    if mode == "dense":
        return client.query_points(query=dense, using=DENSE_NAME, query_filter=flt, **common).points
    if mode == "sparse":
        if not sparse.indices:
            return []
        return client.query_points(query=sparse, using=SPARSE_NAME, query_filter=flt, **common).points
    if mode != "hybrid":
        raise ValueError(f"unknown mode {mode!r}")
    # Hybrid: both vectors queried and RRF-fused server-side in one request.
    # The filter lives on each Prefetch -- that is what actually enforces access.
    prefetch = [
        models.Prefetch(query=dense, using=DENSE_NAME, filter=flt, limit=prefetch_limit),
    ]
    if sparse.indices:
        prefetch.append(models.Prefetch(query=sparse, using=SPARSE_NAME, filter=flt, limit=prefetch_limit))
    return client.query_points(
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        prefetch=prefetch,
        query_filter=flt,
        **common,
    ).points


def search(
    query: str,
    role: str,
    mode: str = "hybrid",
    k: int | None = None,
    client: QdrantClient | None = None,
) -> list[Hit]:
    s = get_settings()
    points = run_query(
        client or get_client(),
        s.collection_name,
        embed_dense_query(query),
        embed_sparse_query(query),
        role,
        mode=mode,
        k=k or s.candidate_k,
        prefetch_limit=s.prefetch_limit,
    )
    hits = [
        Hit(
            id=str(p.id),
            score=p.score,
            text=p.payload["text"],
            embed_text=p.payload["embed_text"],
            payload=p.payload,
        )
        for p in points
    ]
    # Bug alarm (not a filter): the Qdrant filter should make this impossible.
    for h in hits:
        if role not in h.payload.get("access_roles", []):
            raise RBACLeakError(f"{h.source_document} returned to role {role!r}")
    return hits
