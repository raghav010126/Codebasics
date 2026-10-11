"""AWS Bedrock Guardrails (ApplyGuardrail) on the input and on the output.

Input: prompt-attack filter, content filters, denied topics (RoleEscalation, SystemInternals, ...), profanity, PII.
Output: the same policies plus contextual grounding on the document route (passages as grounding source, the question
as query, the answer as guarded content). The verdict comes only from the structured response: action NONE allows,
GUARDRAIL_INTERVENED blocks, and a missing or unknown action fails closed. Errors and timeouts fail closed in the runner.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from mediguard.config import get_settings
from mediguard.guardrails.runner import CheckContext
from mediguard.guardrails.verdict import Verdict, allow, block, fail_closed
from mediguard.observability import metrics
from mediguard.observability.tracing import span

MAX_SOURCE_CHARS = 100_000
MAX_QUERY_CHARS = 1_000


@lru_cache
def runtime_client():
    import boto3
    from botocore.config import Config

    cfg = Config(connect_timeout=3, read_timeout=8, retries={"max_attempts": 2, "mode": "standard"})
    return boto3.client("bedrock-runtime", region_name=get_settings().aws_region, config=cfg)


def interventions(resp: dict[str, Any]) -> list[str]:
    """Reason codes for every policy that blocked, read from the structured assessments."""
    codes: list[str] = []
    for a in resp.get("assessments", []) or []:
        for t in (a.get("topicPolicy") or {}).get("topics", []):
            if t.get("action") == "BLOCKED":
                codes.append(f"bedrock.topic.{t.get('name')}")
        for f in (a.get("contentPolicy") or {}).get("filters", []):
            if f.get("action") == "BLOCKED":
                codes.append(f"bedrock.content.{f.get('type')}")
        for w in (a.get("wordPolicy") or {}).get("managedWordLists", []) + (a.get("wordPolicy") or {}).get("customWords", []):
            if w.get("action") == "BLOCKED":
                codes.append("bedrock.word")
        sip = a.get("sensitiveInformationPolicy") or {}
        for e in sip.get("piiEntities", []):
            if e.get("action") in ("BLOCKED", "ANONYMIZED"):
                codes.append(f"bedrock.pii.{e.get('type')}")
        for r in sip.get("regexes", []):
            if r.get("action") in ("BLOCKED", "ANONYMIZED"):
                codes.append(f"bedrock.pii.{r.get('name')}")
        for g in (a.get("contextualGroundingPolicy") or {}).get("filters", []):
            if g.get("action") == "BLOCKED":
                codes.append(f"bedrock.grounding.{g.get('type')}")
    return codes


def detections(resp: dict[str, Any]) -> dict[str, Any]:
    """Non-blocking signals worth logging (detect-only PII, grounding scores)."""
    out: dict[str, Any] = {"pii_detected": [], "grounding": {}}
    for a in resp.get("assessments", []) or []:
        sip = a.get("sensitiveInformationPolicy") or {}
        out["pii_detected"] += [e.get("type") for e in sip.get("piiEntities", []) if e.get("detected")]
        out["pii_detected"] += [r.get("name") for r in sip.get("regexes", []) if r.get("detected")]
        for g in (a.get("contextualGroundingPolicy") or {}).get("filters", []):
            out["grounding"][g.get("type")] = {"score": g.get("score"), "threshold": g.get("threshold")}
    return out


def verdict_from_response(check: str, stage: str, resp: Any) -> Verdict:
    if not isinstance(resp, dict):
        return fail_closed(check, stage, "malformed", f"Bedrock response of type {type(resp).__name__}")
    action = resp.get("action")
    usage = resp.get("usage") or {}
    m = metrics.current()
    if m is not None:
        m.bedrock_text_units += sum(v for v in usage.values() if isinstance(v, int))
    latency = [((a.get("invocationMetrics") or {}).get("guardrailProcessingLatency"))
               for a in resp.get("assessments", []) or []]
    evidence = {"action": action, "action_reason": resp.get("actionReason"), **detections(resp),
                "usage": usage, "processing_ms": [x for x in latency if x is not None]}
    if action == "NONE":
        return allow(check, stage, evidence=evidence)
    if action == "GUARDRAIL_INTERVENED":
        codes = interventions(resp) or ["bedrock.intervened"]
        return block(check, stage, codes[0], f"Bedrock intervened: {codes}", evidence={**evidence, "codes": codes})
    return fail_closed(check, stage, "malformed", f"Bedrock action {action!r}")


class BedrockGuardrail:
    tier = 1
    timeout_s = 12.0
    required = True

    def __init__(self, stage: str, client=None, guardrail_id: str | None = None, version: str | None = None):
        s = get_settings()
        self.stage = stage
        self.name = f"{stage}.bedrock"
        self._client = client
        self.guardrail_id = guardrail_id if guardrail_id is not None else s.bedrock_guardrail_id
        self.version = version or s.bedrock_guardrail_version

    def configured(self) -> bool:
        return bool(self.guardrail_id)

    def content(self, ctx: CheckContext) -> list[dict]:
        if self.stage == "input":
            return [{"text": {"text": ctx.question}}]
        t = ctx.target
        blocks = []
        if t is not None and t.status == "ok" and t.retrieval_type == "hybrid_rag" and t.contexts:
            blocks.append({"text": {"text": "\n\n".join(t.contexts)[:MAX_SOURCE_CHARS], "qualifiers": ["grounding_source"]}})
            blocks.append({"text": {"text": ctx.question[:MAX_QUERY_CHARS], "qualifiers": ["query"]}})
            blocks.append({"text": {"text": ctx.answer, "qualifiers": ["guard_content"]}})
        else:
            blocks.append({"text": {"text": ctx.answer}})
        return blocks

    def evaluate(self, ctx: CheckContext) -> Verdict:
        if self.stage == "output" and not (ctx.answer or "").strip():
            return allow(self.name, self.stage)
        client = self._client or runtime_client()
        content = self.content(ctx)
        with span("bedrock.apply_guardrail", "tool",
                  inputs={"source": self.stage.upper(), "qualifiers": [b["text"].get("qualifiers", []) for b in content]},
                  metadata={"guardrail_id": self.guardrail_id, "guardrail_version": self.version}) as sp:
            resp = client.apply_guardrail(guardrailIdentifier=self.guardrail_id, guardrailVersion=self.version,
                                          source=self.stage.upper(), content=content)
            resp = {k: v for k, v in resp.items() if k != "ResponseMetadata"}
            v = verdict_from_response(self.name, self.stage, resp)
            sp.end(outputs={"action": resp.get("action") if isinstance(resp, dict) else None,
                            "decision": v.decision, "reason_code": v.reason_code, "evidence": v.evidence,
                            "assessments": resp.get("assessments") if isinstance(resp, dict) else None})
        return v
