"""The Streamlit UI, run headless against a scripted target: the user sees only the answer or the generic refusal;
the reasons appear only in the operator view."""

from __future__ import annotations

import pytest
from conftest import needs_index

from mediguard.guardrails.refusal import REFUSAL, strip_reference

pytestmark = needs_index


@pytest.fixture
def app(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from mediguard.target.medibot import adapter
    from mediguard.target.scripted import ScriptedTarget, response_from_chunks

    good = response_from_chunks("Change it every 72 hours [1].", [("icu_nursing_procedures.pdf", 1)])
    monkeypatch.setattr(adapter, "MediBotTarget", lambda: ScriptedTarget(default=good))
    monkeypatch.setenv("MEDIGUARD_TRACING", "0")
    at = AppTest.from_file("../src/mediguard/ui.py", default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    for t in at.toggle:
        if t.label in ("AWS Bedrock Guardrails", "OpenEvals judges"):   # offline: rules and local checks only
            t.set_value(False)
    return at.run()


def _markdown(at) -> list[str]:
    return [m.value for m in at.markdown]


def test_injection_shows_only_the_generic_refusal(app):
    next(b for b in app.button if b.label == "Prompt injection").click().run()
    assert not app.exception, app.exception
    md = _markdown(app)
    refusal = next(m for m in md if m.startswith("I can't help"))
    assert strip_reference(refusal) == strip_reference(REFUSAL) and "mode_switch" not in refusal
    assert any("Blocked at input" in m for m in md)
    reasons = [r["reason code"] for df in app.dataframe for r in df.value.to_dict("records")]
    assert "input.rules.mode_switch" in reasons      # only inside the operator view


def test_normal_question_is_answered_with_sources(app):
    app.chat_input[0].set_value("How often is the CVC dressing changed?").run()
    assert not app.exception, app.exception
    assert "Change it every 72 hours [1]." in _markdown(app)
    assert any(c.value.startswith("Sources:") for c in app.caption)
