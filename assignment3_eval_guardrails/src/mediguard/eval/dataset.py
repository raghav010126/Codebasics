"""Labelled evaluation data: golden questions, judge calibration probes and heuristic negative controls.

Reference answers are written from MediBot's indexed chunks (cited as file#chunk_index with a substring that must
occur there) and from gold SQL run on the read-only database; tests/test_dataset.py verifies both.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field

from mediguard.config import get_settings
from mediguard.models import Strict

Expected = Literal["answer", "abstain", "refuse", "guardrail_block"]


class RefChunk(Strict):
    source_document: str
    chunk_index: int
    substring: str | None = None


class GoldenItem(Strict):
    id: str
    category: Literal["normal", "hard", "unanswerable", "sql", "rbac", "adversarial", "benign"]
    role: str
    question: str
    expected: Expected
    expected_route: Literal["rag", "sql", "none"] = "none"
    expected_block_stage: Literal["input", "output"] | None = None
    reference_answer: str | None = None
    ref_chunks: list[RefChunk] = Field(default_factory=list)
    must_include: list[list[str]] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    cite: bool = False
    gold_sql: str | None = None
    gold_rows: list[list[Any]] | None = None
    ragas: list[str] = Field(default_factory=list)
    difficulty: Literal["easy", "hard"] = "easy"
    notes: str = ""


class JudgeProbe(Strict):
    id: str
    role: str
    question: str
    expected: Expected
    reference_answer: str
    context_chunks: list[RefChunk] = Field(default_factory=list)
    retrieval_type: str = "hybrid_rag"
    answer: str
    must: Literal["pass", "fail"]
    why: str = ""


class ControlResponse(Strict):
    status: str
    target_status: str | None = None
    answer: str | None = None
    retrieval_type: str = ""
    context_chunks: list[RefChunk] = Field(default_factory=list)
    latency_ms: float = 0.0


class HeuristicControl(Strict):
    id: str
    description: str
    role: str
    question: str
    expected: Expected
    cite: bool = False
    must_not_include: list[str] = Field(default_factory=list)
    response: ControlResponse
    expect_fail: list[str]


def _jsonl(name: str) -> list[dict]:
    path = get_settings().project_root / "data" / name
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_golden() -> list[GoldenItem]:
    return [GoldenItem.model_validate(d) for d in _jsonl("golden.jsonl")]


def load_probes() -> list[JudgeProbe]:
    return [JudgeProbe.model_validate(d) for d in _jsonl("judge_probes.jsonl")]


def load_controls() -> list[HeuristicControl]:
    return [HeuristicControl.model_validate(d) for d in _jsonl("heuristic_controls.jsonl")]


@lru_cache
def chunk_lookup() -> dict[tuple[str, int], dict]:
    from mediguard.target.medibot.index_build import load_chunks

    return {(c["source_document"], c["chunk_index"]): c for c in load_chunks()}


def chunk_context(ref: RefChunk) -> str:
    """A chunk rendered exactly as MediBot's build_context shows it to the model (without the [n] prefix)."""
    c = chunk_lookup()[(ref.source_document, ref.chunk_index)]
    return f"Source: {c['source_document']} - {c['section_title']}\n{c['embed_text']}"
