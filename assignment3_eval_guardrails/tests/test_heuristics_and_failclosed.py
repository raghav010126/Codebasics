"""Heuristics catch every synthetic bad response; every guardrail failure mode blocks."""

from __future__ import annotations

from conftest import needs_index

from mediguard.eval.dataset import load_controls
from mediguard.eval.heuristics import run_controls
from mediguard.guardrails.failclosed import run_scenarios
from mediguard.guardrails.refusal import REFUSAL, strip_reference

pytestmark = needs_index


def test_controls_fail_exactly_as_listed():
    bad = [r for r in run_controls(load_controls()) if not r["ok"]]
    assert bad == []


def test_fail_closed_matrix():
    results = run_scenarios()
    assert len(results) >= 14
    assert all(r["ok"] for r in results), [r for r in results if not r["ok"]]
    assert {strip_reference(r["user_message"]) for r in results} == {strip_reference(REFUSAL)}
    by_id = {r["id"]: r for r in results}
    assert by_id["FC4"]["latency_ms"] < 1200        # 0.3 s timeout; the hung 1.5 s check is not waited for
    assert by_id["FC12"]["reason_code"] == "fail_closed.inconsistent"
    assert by_id["FC13"]["reason_code"].startswith("fail_closed.error:configured")
    assert by_id["FC14"]["status"] == "internal_error"


def test_pipeline_failure_refuses_and_logs_the_error(monkeypatch):
    from mediguard import pipeline
    from mediguard.target.scripted import ScriptedTarget

    events = []
    monkeypatch.setattr(pipeline, "log_event", lambda name, **kw: events.append((name, kw)))

    class Broken(ScriptedTarget):
        def scope(self, role):
            raise RuntimeError("scope lookup exploded")

    r = pipeline.GuardedAssistant(Broken(), input_checks=[], output_checks=[]).handle("CVC dressing?", "nurse")
    assert r.status == "internal_error" and strip_reference(r.public_answer) == strip_reference(REFUSAL)
    assert "exploded" not in r.public_answer and set(r.public()) == {"request_id", "answer", "sources"}
    failed = [kw for name, kw in events if name == "request.failed"]
    assert failed and failed[0]["error_type"] == "RuntimeError" and "exploded" in failed[0]["error"]
