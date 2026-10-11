"""Records passed between the target adapter, the guardrails, the pipeline and the evaluation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ROLES = ("doctor", "nurse", "billing_executive", "technician", "admin")
TargetStatus = Literal["ok", "refused", "not_found", "sql_unanswerable", "error", "leak_guard", "model_refusal"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Passage(Strict):
    """A chunk that reached the target LLM, numbered exactly as in its prompt."""

    n: int
    chunk_id: str
    source_document: str
    section_title: str
    collection: str
    access_roles: list[str]
    chunk_index: int | None = None
    text: str
    embed_text: str
    rerank_score: float | None = None
    hybrid_rank: int | None = None


class LLMCall(Strict):
    component: str                   # target | guardrail | judge | ragas
    label: str                       # router | answer | nl2sql | sql_answer | input_judge | ...
    model: str
    served_model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: float = 0.0
    cached: bool = False
    cost_usd: float = 0.0
    cold_cost_usd: float = 0.0
    stop_reason: str | None = None
    error: str | None = None


class TargetResponse(Strict):
    status: TargetStatus
    flags: list[str] = Field(default_factory=list)
    answer: str = ""
    retrieval_type: str = ""
    blocked: bool = False
    sources: list[dict[str, Any]] = Field(default_factory=list)
    route: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    passages: list[Passage] = Field(default_factory=list)
    contexts: list[str] = Field(default_factory=list)
    sql_query: str | None = None
    sql_columns: list[str] = Field(default_factory=list)
    sql_rows: list[list[Any]] = Field(default_factory=list)
    llm_calls: list[LLMCall] = Field(default_factory=list)
    latency_ms: float = 0.0
    recorded_latency_ms: float = 0.0  # latency as originally recorded (differs from latency_ms on cache replay)
    error: str | None = None


class Scope(Strict):
    role: str
    collections: list[str]
    restricted_collections: list[str]
    sql_tables: list[str]
