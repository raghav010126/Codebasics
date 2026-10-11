"""Point MediBot at our caches and model before it is imported; MediBot itself is never edited.

MediBot reads everything from pydantic-settings, and environment variables take priority over its .env file, so the
whole integration is configuration: a local Qdrant index under ~/.cache/mediguard/medibot (not MediBot's Qdrant
Cloud), the target model, and the read-only database inside the copied tree.

Usage: from mediguard.target.medibot.bootstrap import prepare; prepare()
"""

from __future__ import annotations

import os
import sys

from mediguard.config import get_settings

_prepared = False


def medibot_env(model: str | None = None) -> dict[str, str]:
    s = get_settings()
    cache = s.medibot_cache
    return {
        "LLM_PROVIDER": "anthropic",
        "ANTHROPIC_MODEL": model or s.target_model,
        "USE_FALLBACKS": "true",
        "CACHE_DIR": str(cache),
        "QDRANT_URL": "",
        "QDRANT_API_KEY": "",
        "QDRANT_PATH": str(cache / "qdrant_local"),
        "DB_PATH": str(s.medibot_dir / "data" / "db" / "mediassist.db"),
        "DATA_DIR": str(s.medibot_dir / "data"),
        "TOKENIZERS_PARALLELISM": "false",
    }


def prepare(model: str | None = None) -> None:
    """Set MediBot's environment (idempotent). Must run before the first ``import medibot``."""
    global _prepared
    s = get_settings()
    if not sys.pycache_prefix:
        sys.pycache_prefix = str(s.home / "pycache")   # keep bytecode out of the copied tree and OneDrive
    env = medibot_env(model)
    if _prepared and "medibot.config" in sys.modules:
        current = sys.modules["medibot.config"].get_settings()
        if current.anthropic_model != env["ANTHROPIC_MODEL"]:
            raise RuntimeError("MediBot is already configured with another model; swap models in a new process")
        return
    s.medibot_cache.mkdir(parents=True, exist_ok=True)
    os.environ.update(env)
    import medibot  # noqa: F401  (truststore, quiet loggers)
    from medibot.config import get_settings as medibot_settings

    medibot_settings.cache_clear()
    ms = medibot_settings()
    checks = {
        "cache_dir": (str(ms.cache_dir), env["CACHE_DIR"]),
        "qdrant_path": (ms.qdrant_path, env["QDRANT_PATH"]),
        "qdrant_url": (ms.qdrant_url, ""),
        "anthropic_model": (ms.anthropic_model, env["ANTHROPIC_MODEL"]),
        "db_path": (str(ms.db_path), env["DB_PATH"]),
    }
    wrong = {k: v for k, v in checks.items() if v[0] != v[1]}
    if wrong:
        raise RuntimeError(f"MediBot settings did not take our environment: {wrong}")
    _prepared = True
