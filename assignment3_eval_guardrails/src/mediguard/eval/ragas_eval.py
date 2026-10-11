"""RAGAS metrics: faithfulness, answer relevancy, context precision (with reference) and context recall.

Evaluator: Claude Opus 5.5 at low effort (MEDIGUARD_RAGAS_MODEL), a different and stronger model than the target. A
first version used Haiku 4.5 at temperature 0, but it scored perfect retrievals 0 on context precision and flagged
complete answers as "noncommittal" (answer relevancy 0); see docs/spikes.md. The 5.5 models reject sampling
parameters, so RAGAS's temperature setting is bypassed and repeatability comes from RAGAS's DiskCacheBackend.
Embeddings: fastembed BAAI/bge-small-en-v1.5 from MediBot's own model cache.
Contexts are exactly what MediBot's model saw: the reranked passages (document route) or the SQL and result table.
"""

from __future__ import annotations

import contextvars
import math
import re
import warnings
from typing import Any

from mediguard.config import get_settings
from mediguard.eval.dataset import GoldenItem
from mediguard.llm.langchain_models import chat_model
from mediguard.observability import metrics

METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


def clean_response(text: str) -> str:
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"\n*_Note:.*?_\s*$", "", text, flags=re.DOTALL)
    return re.sub(r"\*\*|__", "", text).strip()


def _metric_objects(names: set[str]):
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    from ragas.metrics._answer_relevance import ResponseRelevancy
    from ragas.metrics._context_precision import LLMContextPrecisionWithReference
    from ragas.metrics._context_recall import LLMContextRecall
    from ragas.metrics._faithfulness import Faithfulness

    table = {"faithfulness": Faithfulness(), "answer_relevancy": ResponseRelevancy(),
             "context_precision": LLMContextPrecisionWithReference(), "context_recall": LLMContextRecall()}
    return {n: m for n, m in table.items() if n in names}


class LocalEmbeddings:
    """LangChain-style embeddings over fastembed (same model as MediBot), with `model` as a string for RAGAS."""

    def __init__(self, name: str, cache_dir: str):
        from fastembed import TextEmbedding

        self.model = name
        self._m = TextEmbedding(name, cache_dir=cache_dir)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._m.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._m.query_embed(text))).tolist()

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


def _models():
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    from ragas.cache import DiskCacheBackend
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    s = get_settings()
    # RAGAS's cache key covers the prompt but not the model: namespace it so a different evaluator never reuses answers
    cache = DiskCacheBackend(cache_dir=str(s.home / "cache" / "ragas" / s.ragas_model))
    from mediguard.llm.langchain_models import accepts_temperature

    sampling = accepts_temperature(s.ragas_model)
    llm = LangchainLLMWrapper(chat_model(s.ragas_model, "ragas", "ragas", max_tokens=16000, effort="low"), cache=cache,
                              bypass_temperature=not sampling, bypass_n=True)
    emb = LangchainEmbeddingsWrapper(LocalEmbeddings("BAAI/bge-small-en-v1.5", str(s.medibot_cache / "fastembed")),
                                     cache=cache)
    return llm, emb


def score(rows: list[tuple[GoldenItem, dict]]) -> dict[str, Any]:
    """rows: (item, collected observation dict). Returns per-question scores and per-metric aggregates."""
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    from ragas import EvaluationDataset, SingleTurnSample, evaluate
    from ragas.run_config import RunConfig

    llm, emb = _models()
    per_item: dict[str, dict[str, float | None]] = {}
    groups: dict[tuple[str, ...], list[tuple[GoldenItem, dict]]] = {}
    for item, obs in rows:
        if not item.ragas or obs["status"] != "answered" or obs["target_status"] != "ok" or not obs["contexts"]:
            per_item[item.id] = {"skipped": "not answered with contexts" if item.ragas else "not a RAGAS item"}
            continue
        groups.setdefault(tuple(sorted(item.ragas)), []).append((item, obs))
    for names, group in groups.items():
        samples = [SingleTurnSample(user_input=item.question, retrieved_contexts=obs["contexts"],
                                    response=clean_response(obs["public_answer"] or ""),
                                    reference=item.reference_answer or "") for item, obs in group]
        chosen = _metric_objects(set(names))
        result = _isolated(evaluate, EvaluationDataset(samples=samples), metrics=list(chosen.values()), llm=llm,
                           embeddings=emb, run_config=RunConfig(max_workers=4, timeout=180, max_retries=3),
                           show_progress=False)
        for (item, _obs), scores in zip(group, result.scores):
            per_item[item.id] = {n: _num(scores.get(m.name)) for n, m in chosen.items()}
    aggregates = {}
    for n in METRICS:
        vals = [v[n] for v in per_item.values() if n in v]
        good = [x for x in vals if x is not None]
        aggregates[n] = {"mean": round(sum(good) / len(good), 4) if good else None, "n": len(vals),
                         "nan": len(vals) - len(good), "min": round(min(good), 4) if good else None}
    return {"per_item": per_item, "aggregates": aggregates, "evaluator": get_settings().ragas_model,
            "embeddings": "BAAI/bge-small-en-v1.5 (fastembed)"}


def _isolated(fn, *args, **kwargs):
    """Run RAGAS outside our LangSmith trace context: RAGAS looks for its own root run, which must have no parent.
    The request metrics are carried over so RAGAS's token cost is still counted."""
    m = metrics.current()

    def inner():
        if m is not None:
            metrics._current.set(m)
        return fn(*args, **kwargs)

    return contextvars.Context().run(inner)


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else round(f, 4)
