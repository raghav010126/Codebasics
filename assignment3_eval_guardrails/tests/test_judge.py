"""The judge's pass rule is computed in our code, and any unusable judge output fails closed (offline)."""

from __future__ import annotations

from mediguard.eval import judge as J


def _scores(**over) -> dict:
    s = {k: {"score": 5, "justification": ""} for k in
         ("accuracy", "completeness", "refusal_appropriateness", "citation_correctness")}
    s.update(overall=5, key_errors=[], verdict_rationale="")
    for k, v in over.items():
        if k == "overall":
            s[k] = v
        else:
            s[k]["score"] = v
    return s


def test_pass_rule():
    assert J.passes(_scores())
    assert J.passes(_scores(completeness=3)) and not J.passes(_scores(completeness=2))
    assert not J.passes(_scores(accuracy=3)) and not J.passes(_scores(refusal_appropriateness=3))
    assert not J.passes(_scores(overall=3)) and not J.passes(_scores(citation_correctness=2))
    assert J.passes(_scores(citation_correctness=0))   # citations not applicable


def test_any_judge_exception_fails_closed_and_is_not_cached(monkeypatch):
    import anthropic

    class Exploding:
        def __init__(self, *a, **kw):
            self.beta = self

        @property
        def messages(self):
            return self

        def parse(self, **kw):
            raise ValueError("output failed schema validation")

    puts = []
    monkeypatch.setattr(anthropic, "Anthropic", Exploding)
    monkeypatch.setattr(J.cache, "get", lambda ns, k: None)
    monkeypatch.setattr(J.cache, "put", lambda ns, k, v: puts.append(k))
    out = J.judge("QUESTION: x", "T1")
    assert out["passed"] is False and out["error"].startswith("ValueError") and "scores" not in out
    assert puts == []
