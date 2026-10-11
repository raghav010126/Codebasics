"""LangSmith spans for the whole request path, with a no-op fallback when tracing is not configured.

Every span opened inside a request nests under the active parent through langsmith's context variables, including
spans opened in worker threads that copy the context (the guardrail runner does).
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from mediguard.config import get_settings
from mediguard.observability.scrub import scrub


@lru_cache
def client():
    s = get_settings()
    if not s.tracing:
        return None
    from langsmith import Client

    return Client(hide_inputs=scrub, hide_outputs=scrub)


@dataclass
class Span:
    run: Any = None

    @property
    def id(self) -> str | None:
        return str(self.run.id) if self.run is not None else None

    @property
    def trace_id(self) -> str | None:
        return str(self.run.trace_id) if self.run is not None else None

    def end(self, outputs: dict | None = None, error: str | None = None) -> None:
        if self.run is not None:
            self.run.end(outputs=outputs, error=error)

    def meta(self, **metadata: Any) -> None:
        if self.run is not None and metadata:
            self.run.add_metadata(metadata)

    def tags(self, *tags: str) -> None:
        if self.run is not None and tags:
            self.run.add_tags(list(tags))

    def url(self) -> str | None:
        if self.run is None:
            return None
        try:
            return self.run.get_url()
        except Exception:  # noqa: BLE001 - a missing URL must never break a request
            return None


@contextmanager
def span(name: str, run_type: str = "chain", inputs: dict | None = None, metadata: dict | None = None,
         tags: list[str] | None = None):
    c = client()
    if c is None:
        yield Span()
        return
    from langsmith import trace, tracing_context

    project = get_settings().langsmith_project
    with tracing_context(enabled=True, client=c, project_name=project), \
            trace(name, run_type=run_type, inputs=inputs or {}, metadata=metadata or {}, tags=tags or [],
                  client=c, project_name=project) as rt:
        yield Span(rt)


@lru_cache
def project_id() -> str | None:
    c = client()
    if c is None:
        return None
    try:
        return str(c.read_project(project_name=get_settings().langsmith_project).id)
    except Exception:  # noqa: BLE001 - the project is created by the first traced run
        return None


def feedback(sp: Span, key: str, score: float | None, comment: str = "") -> None:
    c = client()
    if c is None or sp.run is None:
        return
    sid = getattr(sp.run, "session_id", None) or project_id()
    with suppress(Exception):  # feedback is best effort
        c.create_feedback(run_id=sp.run.id, trace_id=sp.run.trace_id, key=key, score=score, comment=comment,
                          session_id=sid, start_time=sp.run.start_time)


def flush() -> None:
    c = client()
    if c is not None:
        with suppress(Exception):
            c.flush()
