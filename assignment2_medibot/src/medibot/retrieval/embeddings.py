"""Local dense (semantic) and sparse (BM25) embedders, loaded once."""

import re
from functools import lru_cache

from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import models

from medibot.config import get_settings

# Chains joined by "." or "-" that contain a digit: I21.4, F-12, BM-500, CURB-65, 15-20
_CODE_RE = re.compile(r"[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)+")
DEFAULT_AVG_LEN = 150.0


def sparse_text(text: str) -> str:
    """Make clinical/asset codes first-class BM25 terms.

    FastEmbed's BM25 tokenizer splits on non-word characters, so ``I21.4`` becomes
    ``i21`` + ``4`` and ``F-12`` becomes ``f`` + ``12`` -- both too generic. We append
    the joined form (``I21_4``, ``F_12``) so exact-code queries match exactly while the
    split tokens remain searchable. Applied identically at index and query time.
    """
    codes = {
        m.group(0).replace(".", "_").replace("-", "_")
        for m in _CODE_RE.finditer(text)
        if any(c.isdigit() for c in m.group(0))
    }
    return f"{text} {' '.join(sorted(codes))}" if codes else text


@lru_cache
def dense_model() -> TextEmbedding:
    s = get_settings()
    return TextEmbedding(s.dense_model, cache_dir=str(s.cache_dir / "fastembed"))


@lru_cache
def sparse_model(avg_len: float = DEFAULT_AVG_LEN) -> SparseTextEmbedding:
    s = get_settings()
    return SparseTextEmbedding(s.sparse_model, cache_dir=str(s.cache_dir / "fastembed"), avg_len=avg_len)


def embed_dense_docs(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in dense_model().embed(texts, batch_size=32)]


def embed_sparse_docs(texts: list[str], avg_len: float = DEFAULT_AVG_LEN) -> list[models.SparseVector]:
    return [
        models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())
        for e in sparse_model(avg_len).embed([sparse_text(t) for t in texts])
    ]


def embed_dense_query(query: str) -> list[float]:
    return next(iter(dense_model().query_embed(query))).tolist()


def embed_sparse_query(query: str) -> models.SparseVector:
    e = next(iter(sparse_model().query_embed(sparse_text(query))))
    return models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())
