"""Qdrant client factory and the RBAC filter.

The RBAC filter is the security boundary: it is attached to every vector query so
restricted chunks never leave Qdrant.
"""

import atexit
from functools import lru_cache

from qdrant_client import QdrantClient, models

from medibot.config import get_settings
from medibot.rbac import validate_role

DENSE_DIM = 384
DENSE_NAME = "dense"
SPARSE_NAME = "bm25"


def make_client(url: str = "", api_key: str = "", path: str = "") -> QdrantClient:
    s = get_settings()
    url, api_key, path = url or s.qdrant_url, api_key or s.qdrant_api_key, path or s.qdrant_path
    if url:
        client = QdrantClient(url=url, api_key=api_key or None, timeout=60)
    else:
        path = path or str(s.cache_dir / "qdrant_local")
        client = QdrantClient(":memory:") if path == ":memory:" else QdrantClient(path=path)
    atexit.register(client.close)  # clean shutdown (releases the local-mode file lock)
    return client


@lru_cache
def get_client() -> QdrantClient:
    return make_client()


def rbac_filter(role: str) -> models.Filter:
    """Only points whose ``access_roles`` contains ``role``. ``must`` only -- never ``should``."""
    validate_role(role)
    return models.Filter(
        must=[models.FieldCondition(key="access_roles", match=models.MatchAny(any=[role]))]
    )


def ensure_collection(client: QdrantClient, name: str, recreate: bool = False) -> None:
    if recreate and client.collection_exists(name):
        client.delete_collection(name)
    if client.collection_exists(name):
        return
    client.create_collection(
        collection_name=name,
        vectors_config={DENSE_NAME: models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE)},
        sparse_vectors_config={SPARSE_NAME: models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    for field in ("access_roles", "collection", "source_document"):
        client.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD)
