"""Central configuration (env-driven via pydantic-settings)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # LLM provider: "anthropic" or "groq"
    llm_provider: str = "anthropic"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    anthropic_model: str = "claude-opus-5-5"
    anthropic_api_key: str = ""  # empty -> SDK default resolution (env var / `ant auth login` profile)
    use_fallbacks: bool = True  # server-side refusal fallback (beta)

    # Qdrant
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_path: str = ""  # local / ":memory:" for tests
    collection_name: str = "mediassist_docs"

    # Paths
    data_dir: Path = ROOT / "data"
    db_path: Path = ROOT / "data" / "db" / "mediassist.db"
    cache_dir: Path = ROOT / ".cache"

    # Models (all local)
    dense_model: str = "BAAI/bge-small-en-v1.5"
    sparse_model: str = "Qdrant/bm25"
    rerank_model: str = "BAAI/bge-reranker-base"
    chunk_max_tokens: int = 384

    # Retrieval
    prefetch_limit: int = 20
    candidate_k: int = 10  # hybrid candidates
    rerank_top_k: int = 3  # chunks that reach the LLM
    router_override_score: float = 0.5  # restricted-only router targets are still refused unless allowed docs score above this
    rerank_min_score: float = 0.05  # bge-reranker sigmoid score (0-1); calibrated by scripts/calibrate_threshold.py

    # SQL
    sql_max_rows: int = 200

    # Auth
    jwt_secret: str = "dev-only-insecure-secret-change-me-please"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 120

    # Frontend
    backend_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
