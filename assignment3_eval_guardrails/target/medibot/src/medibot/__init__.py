"""MediBot: RBAC-aware hybrid RAG + SQL RAG for MediAssist Health Network."""

import os

# Corporate TLS proxies break Hugging Face / model downloads; trust the OS cert store.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover
    pass

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import logging

for _noisy in ("httpx", "httpcore", "huggingface_hub", "urllib3", "fastembed", "sentence_transformers"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
