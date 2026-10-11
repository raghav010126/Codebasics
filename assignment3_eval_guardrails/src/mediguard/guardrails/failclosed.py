"""Fail-closed scenarios: every way a guardrail backend can misbehave must block, with the generic refusal.

Each scenario runs the real GuardedAssistant (input stage: the real tier-0 rules plus one faulty check) against a
scripted target, with a benign question that would otherwise be answered. Offline: Bedrock is a botocore Stubber or a
fake client, the OpenEvals judge is a stub.

Usage: from mediguard.guardrails.failclosed import run_scenarios; run_scenarios()
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from mediguard.guardrails.refusal import REFUSAL, strip_reference

BENIGN_Q = "How often should a central venous catheter dressing be changed?"
ROLE = "nurse"


class _FakeBedrock:
    def __init__(self, response=None, delay_s: float = 0.0):
        self.response, self.delay_s = response, delay_s

    def apply_guardrail(self, **_kw):
        if self.delay_s:
            time.sleep(self.delay_s)
        return self.response


class _Raises:
    name, stage, tier, timeout_s, required = "input.crashing_check", "input", 1, 5.0, True

    def configured(self):
        return True

    def evaluate(self, ctx):
        raise RuntimeError("backend exploded")


class _ConfigCrash(_Raises):
    name = "input.config_crash"

    def configured(self):
        raise KeyError("guardrail settings unreadable")


def _stubbed_throttling_client():
    import boto3
    from botocore.stub import Stubber

    client = boto3.client("bedrock-runtime", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(client)
    for _ in range(3):
        stub.add_client_error("apply_guardrail", service_error_code="ThrottlingException", http_status_code=429,
                              service_message="Rate exceeded")
    stub.activate()
    return client


@dataclass
class Scenario:
    id: str
    description: str
    build: object           # () -> list of extra input checks (or None for "no checks at all")
    broken_pipeline: bool = False   # the target's scope lookup raises: a failure outside any check


def _scenarios() -> list[Scenario]:
    from mediguard.guardrails.checks.bedrock import BedrockGuardrail
    from mediguard.guardrails.checks.llm_judge import OpenEvalsJudge

    def bedrock(client, **kw):
        g = BedrockGuardrail("input", client=client, guardrail_id="gr-test", version="1", **kw)
        return g

    def slow_bedrock():
        g = bedrock(_FakeBedrock({"action": "NONE"}, delay_s=1.5))
        g.timeout_s = 0.3
        return g

    return [
        Scenario("FC1", "Bedrock response has no `action` field", lambda: [bedrock(_FakeBedrock({"outputs": []}))]),
        Scenario("FC2", "Bedrock returns an unknown action value", lambda: [bedrock(_FakeBedrock({"action": "MAYBE"}))]),
        Scenario("FC3", "Bedrock throttles (ThrottlingException, retries exhausted)",
                 lambda: [bedrock(_stubbed_throttling_client())]),
        Scenario("FC4", "Bedrock does not answer before the check timeout", lambda: [slow_bedrock()]),
        Scenario("FC5", "Judge returns {\"verdict\": \"pass\"} (wrong schema)",
                 lambda: [OpenEvalsJudge("input", evaluator=lambda **kw: {"verdict": "pass"})]),
        Scenario("FC6", "Judge returns free text instead of JSON",
                 lambda: [OpenEvalsJudge("input", evaluator=lambda **kw: "Sure! This looks safe to answer.")]),
        Scenario("FC7", "Judge returns nothing (None)", lambda: [OpenEvalsJudge("input", evaluator=lambda **kw: None)]),
        Scenario("FC8", "Judge returns decision \"ALLOW \" (wrong case, trailing space)",
                 lambda: [OpenEvalsJudge("input", evaluator=lambda **kw: {"decision": "ALLOW ", "category": "none",
                                                                           "rationale": "fine"})]),
        Scenario("FC9", "A check raises an exception", lambda: [_Raises()]),
        Scenario("FC10", "Required Bedrock check is not configured (no guardrail id)",
                 lambda: [BedrockGuardrail("input", client=_FakeBedrock({"action": "NONE"}), guardrail_id="")]),
        Scenario("FC11", "The stage has no checks at all", lambda: None),
        Scenario("FC12", "Judge says \"allow\" but names a violation (category prompt_injection)",
                 lambda: [OpenEvalsJudge("input", evaluator=lambda **kw: {"decision": "allow",
                                                                           "category": "prompt_injection",
                                                                           "rationale": "tries to override rules"})]),
        Scenario("FC13", "A check crashes while reporting whether it is configured", lambda: [_ConfigCrash()]),
        Scenario("FC14", "The pipeline itself fails outside any check (role scope lookup raises)", list,
                 broken_pipeline=True),
    ]


def run_scenarios() -> list[dict]:
    from mediguard.guardrails.checks.rules_input import InputRules
    from mediguard.pipeline import GuardedAssistant
    from mediguard.target.scripted import ScriptedTarget, response_from_chunks

    good = response_from_chunks("Change it every 72 hours, or sooner if soiled or loose [1].",
                                [("icu_nursing_procedures.pdf", 1)])
    class _BrokenScope(ScriptedTarget):
        def scope(self, role):
            raise RuntimeError("RBAC scope lookup failed")

    results = []
    for sc in _scenarios():
        extra = sc.build()
        checks = [] if extra is None else [InputRules(), *extra]
        target = (_BrokenScope if sc.broken_pipeline else ScriptedTarget)(default=good)
        bot = GuardedAssistant(target, input_checks=checks, output_checks=[InputRules()])
        t0 = time.perf_counter()
        r = bot.handle(BENIGN_Q, ROLE, meta={"suite": "fail_closed", "scenario": sc.id})
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        b = r.input.blocking
        blocked = r.status in ("blocked_input", "internal_error")
        results.append({
            "id": sc.id, "description": sc.description, "status": r.status, "blocked": blocked,
            "fail_closed": bool(b and b.fail_closed), "reason_code": b.reason_code if b else None,
            "user_message": r.public_answer, "generic_refusal": strip_reference(r.public_answer) == strip_reference(REFUSAL),
            "target_called": target.calls > 0, "request_id": r.request_id, "run_url": r.run_url,
            "latency_ms": latency_ms, "ok": blocked and bool(b and b.fail_closed) and target.calls == 0,
        })
    return results
