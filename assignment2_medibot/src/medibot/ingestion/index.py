"""Embed chunks (dense + BM25) and upsert them into Qdrant with RBAC metadata."""

from qdrant_client import QdrantClient, models

from medibot.config import get_settings
from medibot.ingestion.chunk import ChunkRecord
from medibot.retrieval.embeddings import (
    embed_dense_docs,
    embed_sparse_docs,
    sparse_text,
)
from medibot.retrieval.store import DENSE_NAME, SPARSE_NAME, ensure_collection


def index_records(client: QdrantClient, records: list[ChunkRecord], recreate: bool = True, batch: int = 64) -> int:
    name = get_settings().collection_name
    ensure_collection(client, name, recreate=recreate)

    # BM25 length normalisation uses the corpus mean document length
    lens = [len(sparse_text(r.embed_text).split()) for r in records]
    avg_len = sum(lens) / max(len(lens), 1)

    for i in range(0, len(records), batch):
        part = records[i : i + batch]
        texts = [r.embed_text for r in part]
        dense = embed_dense_docs(texts)
        sparse = embed_sparse_docs(texts, avg_len=avg_len)
        client.upsert(
            collection_name=name,
            points=[
                models.PointStruct(
                    id=r.chunk_id,
                    vector={DENSE_NAME: d, SPARSE_NAME: s},
                    payload={**r.payload, "text": r.text, "embed_text": r.embed_text},
                )
                for r, d, s in zip(part, dense, sparse)
            ],
        )
    return client.count(name, exact=True).count
