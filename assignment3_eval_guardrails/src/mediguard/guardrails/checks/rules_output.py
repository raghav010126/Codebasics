"""Tier-0 output rules: blocked PII, internal vocabulary and system-prompt echo, and unsafe target states."""

from __future__ import annotations

import re
from functools import lru_cache

from mediguard.guardrails.refusal import REFUSAL, UNAVAILABLE
from mediguard.guardrails.rules import compiled, luhn_ok, rules
from mediguard.guardrails.runner import CheckContext
from mediguard.guardrails.verdict import Verdict, allow, block
from mediguard.target.medibot.access_index import shingles

ECHO_N = 10   # MediBot's prompt tells it to say "could not find it in the documents available to the user" (8 words)


@lru_cache
def _prompt_shingles() -> set[tuple[str, ...]]:
    from medibot.rag import prompts

    texts = [prompts.ANSWER_SYSTEM, prompts.ROUTER_SYSTEM, prompts.SQL_SYSTEM, prompts.SQL_ANSWER_SYSTEM,
             REFUSAL, UNAVAILABLE]
    return set().union(*(shingles(t, ECHO_N) for t in texts))


class OutputRules:
    name = "output.rules"
    stage = "output"
    tier = 0
    timeout_s = 2.0
    required = True

    def configured(self) -> bool:
        return True

    def evaluate(self, ctx: CheckContext) -> Verdict:
        if ctx.target is not None and ctx.target.status in ("leak_guard", "model_refusal"):
            return block(self.name, "output", f"output.rules.target_{ctx.target.status}", ctx.target.error or "")
        ans = ctx.answer or ""
        cfg, low = rules(), ans.lower()
        pii = cfg["pii_block"]
        for kind in ("aadhaar", "pan"):
            if compiled(pii[kind]).search(ans):
                return block(self.name, "output", f"output.rules.pii_{kind}", f"{kind} number in answer")
        for m in compiled(pii["card"]).finditer(ans):
            digits = re.sub(r"\D", "", m.group(0))
            if 13 <= len(digits) <= 19 and luhn_ok(digits):
                return block(self.name, "output", "output.rules.pii_card", "payment card number in answer")
        terms = [t for t in cfg["internal_terms"] if t in low]
        if terms:
            return block(self.name, "output", "output.rules.internal_terms", f"internal vocabulary {terms}")
        echo = shingles(ans, ECHO_N) & _prompt_shingles()
        if echo:
            return block(self.name, "output", "output.rules.prompt_echo", f"{len(echo)} {ECHO_N}-grams from prompts")
        return allow(self.name, "output")
