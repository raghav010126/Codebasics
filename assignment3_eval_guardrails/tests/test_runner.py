"""The stage runner: allow only when every check validly allows."""

from __future__ import annotations

import time

from conftest import ctx

from mediguard.guardrails.runner import run_stage
from mediguard.guardrails.verdict import allow, block


class Fake:
    def __init__(self, name, result=None, tier=0, delay=0.0, raises=None, configured=True, required=True, timeout=2.0):
        self.name, self.stage, self.tier, self.timeout_s, self.required = name, "input", tier, timeout, required
        self.result, self.delay, self.raises, self._configured, self.ran = result, delay, raises, configured, False

    def configured(self):
        return self._configured

    def evaluate(self, c):
        self.ran = True
        if self.delay:
            time.sleep(self.delay)
        if self.raises:
            raise self.raises
        return self.result if self.result is not None else allow(self.name, "input")


def test_all_allow():
    d = run_stage([Fake("a"), Fake("b", tier=1)], ctx("hi", "nurse"), "input")
    assert d.decision == "allow" and d.blocking is None


def test_no_checks_blocks():
    d = run_stage([], ctx("hi", "nurse"), "input")
    assert d.decision == "block" and d.blocking.reason_code == "fail_closed.no_checks"


def test_exception_blocks():
    d = run_stage([Fake("a", raises=ValueError("boom"))], ctx("hi", "nurse"), "input")
    assert d.blocking.reason_code == "fail_closed.error:ValueError" and d.blocking.fail_closed


def test_timeout_blocks():
    d = run_stage([Fake("slow", delay=1.0, timeout=0.1)], ctx("hi", "nurse"), "input")
    assert d.blocking.reason_code == "fail_closed.timeout"


def test_required_unconfigured_blocks_optional_skipped():
    d = run_stage([Fake("a"), Fake("needs-config", configured=False)], ctx("hi", "nurse"), "input")
    assert d.blocking.reason_code == "fail_closed.unconfigured"
    d = run_stage([Fake("a"), Fake("optional", configured=False, required=False)], ctx("hi", "nurse"), "input")
    assert d.decision == "allow"


def test_malformed_result_blocks():
    d = run_stage([Fake("a", result="looks fine to me")], ctx("hi", "nurse"), "input")
    assert d.blocking.reason_code == "fail_closed.malformed"


def test_short_circuit_skips_later_tiers_exhaustive_runs_all():
    later = Fake("later", tier=2)
    d = run_stage([Fake("rule", result=block("rule", "input", "x")), later], ctx("hi", "nurse"), "input")
    assert d.decision == "block" and not later.ran
    later = Fake("later", tier=2)
    d = run_stage([Fake("rule", result=block("rule", "input", "x")), later], ctx("hi", "nurse"), "input",
                  mode="exhaustive")
    assert later.ran and len(d.verdicts) == 2 and d.blocking.check == "rule"
