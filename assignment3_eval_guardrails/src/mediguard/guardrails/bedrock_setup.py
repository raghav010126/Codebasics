"""Create or update the Bedrock guardrail from config/guardrail_policy.yaml and publish a version (idempotent).

Usage: mediguard guardrail-setup [--draft]
"""

from __future__ import annotations

from typing import Any

import yaml

from mediguard.config import get_settings


def load_policy() -> dict[str, Any]:
    return yaml.safe_load((get_settings().project_root / "config" / "guardrail_policy.yaml").read_text())


def request_params(policy: dict[str, Any]) -> dict[str, Any]:
    pii = policy["pii"]
    entities = [{"type": t, "action": "BLOCK"} for t in pii["block"]]
    entities += [{"type": t, "action": "NONE", "inputAction": "NONE", "outputAction": "NONE"} for t in pii["detect"]]
    tier = {"tierConfig": {"tierName": policy.get("tier", "CLASSIC")}}
    xregion = ({"crossRegionConfig": {"guardrailProfileIdentifier": policy["cross_region_profile"]}}
               if policy.get("cross_region_profile") else {})
    return {**xregion,
        "name": policy["name"],
        "description": policy["description"],
        "blockedInputMessaging": policy["blocked_input_message"],
        "blockedOutputsMessaging": policy["blocked_output_message"],
        "topicPolicyConfig": {"topicsConfig": [
            {"name": t["name"], "definition": t["definition"][:200], "examples": [e[:100] for e in t["examples"]][:5],
             "type": "DENY"} for t in policy["denied_topics"]], **tier},
        "contentPolicyConfig": {"filtersConfig": [
            {"type": k, "inputStrength": v[0], "outputStrength": v[1]} for k, v in policy["content_filters"].items()],
            **tier},
        "wordPolicyConfig": {"managedWordListsConfig": [{"type": t} for t in policy["managed_word_lists"]]},
        "sensitiveInformationPolicyConfig": {
            "piiEntitiesConfig": entities,
            "regexesConfig": [{"name": r["name"], "pattern": r["pattern"], "action": r["action"]} for r in pii["regexes"]],
        },
        "contextualGroundingPolicyConfig": {"filtersConfig": [
            {"type": k, "threshold": v} for k, v in policy["contextual_grounding"].items()]},
    }


def create_or_update(publish: bool = True, client=None) -> dict[str, Any]:
    import boto3

    s = get_settings()
    bedrock = client or boto3.client("bedrock", region_name=s.aws_region)
    policy = load_policy()
    params = request_params(policy)
    existing = next((g for page in bedrock.get_paginator("list_guardrails").paginate()
                     for g in page.get("guardrails", []) if g["name"] == policy["name"]), None)
    if existing:
        gid = existing["id"]
        bedrock.update_guardrail(guardrailIdentifier=gid, **params)
        action = "updated"
    else:
        gid = bedrock.create_guardrail(**params)["guardrailId"]
        action = "created"
    version = "DRAFT"
    if publish:
        version = bedrock.create_guardrail_version(guardrailIdentifier=gid,
                                                   description="mediguard-guardrail-setup")["version"]
    return {"action": action, "BEDROCK_GUARDRAIL_ID": gid, "BEDROCK_GUARDRAIL_VERSION": version,
            "region": s.aws_region, "note": "add both values to .env"}
