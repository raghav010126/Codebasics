"""Settings for the evaluation and guardrail pipeline, read from the environment and the project .env.

Usage: from mediguard.config import get_settings; s = get_settings()
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _flag(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    project_root: Path
    home: Path                       # caches, runs and logs (outside OneDrive)
    medibot_dir: Path                # unmodified copy of MediBot (target/medibot)
    target_model: str                # model MediBot answers with
    judge_model: str
    judge_effort: str
    guard_model: str                 # OpenEvals guardrail judges
    ragas_model: str                 # RAGAS evaluator LLM
    bedrock_guardrail_id: str
    bedrock_guardrail_version: str
    aws_region: str
    langsmith_project: str
    tracing: bool
    replay_mode: str                 # replay | fresh | replay_only
    budget_usd: float
    thresholds_path: Path = field(default=PROJECT_ROOT / "thresholds.yaml")

    @property
    def medibot_cache(self) -> Path:
        return self.home / "medibot"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def runs_dir(self) -> Path:
        return self.home / "runs"

    @property
    def events_path(self) -> Path:
        return self.logs_dir / "events.jsonl"

    @property
    def spend_path(self) -> Path:
        return self.home / "spend.jsonl"


@lru_cache
def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    home = Path(_env("MEDIGUARD_HOME", str(Path.home() / ".cache" / "mediguard"))).expanduser()
    return Settings(
        project_root=PROJECT_ROOT,
        home=home,
        medibot_dir=PROJECT_ROOT / "target" / "medibot",
        target_model=_env("MEDIGUARD_TARGET_MODEL", "claude-sonnet-5-5"),
        judge_model=_env("MEDIGUARD_JUDGE_MODEL", "claude-opus-5-5"),
        judge_effort=_env("MEDIGUARD_JUDGE_EFFORT", "medium"),
        guard_model=_env("MEDIGUARD_GUARD_MODEL", "claude-haiku-4-5"),
        ragas_model=_env("MEDIGUARD_RAGAS_MODEL", "claude-opus-5-5"),
        bedrock_guardrail_id=_env("BEDROCK_GUARDRAIL_ID", ""),
        bedrock_guardrail_version=_env("BEDROCK_GUARDRAIL_VERSION", "") or "DRAFT",   # an empty .env line too
        aws_region=_env("AWS_REGION", _env("AWS_DEFAULT_REGION", "us-east-1")),
        langsmith_project=_env("LANGSMITH_PROJECT", "mediguard-assignment3"),
        tracing=_flag("MEDIGUARD_TRACING", True) and bool(os.environ.get("LANGSMITH_API_KEY")),
        replay_mode=_env("MEDIGUARD_REPLAY", "replay"),
        budget_usd=float(_env("MEDIGUARD_BUDGET_USD", "20")),
    )


def load_thresholds() -> dict:
    """thresholds.yaml: every pass threshold the report gates on."""
    import yaml

    return yaml.safe_load(get_settings().thresholds_path.read_text())
