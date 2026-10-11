"""Tier-0 input rules: injection signatures, role claims, raw SQL, guardrail probing, obfuscation, length."""

from __future__ import annotations

from mediguard.guardrails.normalize import normalize
from mediguard.guardrails.rules import compiled, rules
from mediguard.guardrails.runner import CheckContext
from mediguard.guardrails.verdict import Verdict, allow, block


def claimed_roles(text: str) -> list[str]:
    """Canonical roles a message claims to hold ("I'm actually the admin" -> ["admin"])."""
    cfg = rules()["role_claim"]
    found = []
    for m in compiled(cfg["pattern"]).finditer(text):
        phrase = m.group("role").strip(" .-")
        for role, names in cfg["synonyms"].items():
            if any(phrase == n or phrase.startswith(n + " ") for n in names):
                found.append(role)
                break
    return found


class InputRules:
    name = "input.rules"
    stage = "input"
    tier = 0
    timeout_s = 2.0
    required = True

    def configured(self) -> bool:
        return True

    def evaluate(self, ctx: CheckContext) -> Verdict:
        cfg = rules()
        q = ctx.question or ""
        if not q.strip():
            return block(self.name, "input", "input.rules.empty", "empty question")
        if len(q) > cfg["max_chars"]:
            return block(self.name, "input", "input.rules.too_long", f"{len(q)} chars > {cfg['max_chars']}")
        n = normalize(q)
        if "control_chars" in n.flags:
            return block(self.name, "input", "input.rules.control_chars", "control characters in input")
        if "invisible_chars" in n.flags:
            return block(self.name, "input", "input.rules.obfuscation", "zero-width or bidi characters in input",
                         evidence={"flags": n.flags})
        if n.decoded:
            return block(self.name, "input", "input.rules.encoded_payload", "base64/hex payload in input",
                         evidence={"decoded": [d[:120] for d in n.decoded]})
        hits = []
        for text in n.scan_texts:
            for rule in cfg["injection"]:
                if compiled(rule["pattern"]).search(text):
                    hits.append(rule["id"])
            if compiled(cfg["raw_sql"]["pattern"]).search(text):
                hits.append(cfg["raw_sql"]["id"])
        claims = [r for r in claimed_roles(n.text) if r != ctx.role]
        if claims:
            hits.append(cfg["role_claim"]["id"])
        if hits:
            hits = list(dict.fromkeys(hits))
            return block(self.name, "input", f"input.rules.{hits[0]}", f"matched {hits}",
                         evidence={"rules": hits, "claimed_roles": claims, "flags": n.flags})
        return allow(self.name, "input", warnings=[f"flag:{f}" for f in n.flags])
