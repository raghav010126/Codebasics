"""Offline test setup: no tracing, no network; MediBot configured through the same bootstrap as production."""

from __future__ import annotations

import os

import pytest

os.environ["MEDIGUARD_TRACING"] = "0"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-not-used")

from mediguard.config import get_settings

get_settings.cache_clear()

HAS_INDEX = (get_settings().medibot_cache / "chunks.jsonl").exists()
needs_index = pytest.mark.skipif(not HAS_INDEX, reason="run `mediguard target build-index` first")


@pytest.fixture(scope="session", autouse=True)
def _medibot():
    from mediguard.target.medibot.bootstrap import prepare

    prepare()


def scope(role: str):
    from medibot import rbac

    from mediguard.models import Scope

    return Scope(role=role, collections=rbac.collections_for(role),
                 restricted_collections=rbac.restricted_collections_for(role), sql_tables=list(rbac.sql_tables_for(role)))


def ctx(question: str, role: str, answer: str = "", target=None):
    from mediguard.guardrails.runner import CheckContext

    return CheckContext("req_test", question, role, scope(role), target=target, answer=answer)
