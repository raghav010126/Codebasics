"""Preflight: credentials, Bedrock guardrail, LangSmith, MediBot index, Qdrant lock, and the pristine target copy.

Usage: mediguard doctor
"""

from __future__ import annotations

import hashlib
import os

from mediguard.config import get_settings


def _pristine() -> tuple[bool, str]:
    s = get_settings()
    manifest = s.project_root / "target" / "medibot.sha256"
    bad = []
    for line in manifest.read_text().splitlines():
        digest, rel = line.split(maxsplit=1)
        p = s.medibot_dir / rel.removeprefix("./")
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            bad.append(rel)
    return not bad, "unmodified" if not bad else f"changed: {bad[:5]}"


def run() -> int:
    s = get_settings()
    rows: list[tuple[str, bool, str]] = []
    rows.append(("ANTHROPIC_API_KEY", bool(os.environ.get("ANTHROPIC_API_KEY")), "set" if os.environ.get(
        "ANTHROPIC_API_KEY") else "missing (target, judges)"))
    rows.append(("LangSmith tracing", s.tracing, f"project {s.langsmith_project}" if s.tracing else
                 "off (set LANGSMITH_API_KEY)"))
    try:
        import boto3

        ident = boto3.client("sts", region_name=s.aws_region).get_caller_identity()
        rows.append(("AWS credentials", True, f"account {ident['Account']} ({s.aws_region})"))
        aws_ok = True
    except Exception as e:  # noqa: BLE001
        rows.append(("AWS credentials", False, f"{type(e).__name__}: run `aws login`"))
        aws_ok = False
    if not s.bedrock_guardrail_id:
        rows.append(("Bedrock guardrail", False, "BEDROCK_GUARDRAIL_ID unset: run `mediguard guardrail-setup`"))
    elif aws_ok:
        try:
            import boto3

            g = boto3.client("bedrock", region_name=s.aws_region).get_guardrail(
                guardrailIdentifier=s.bedrock_guardrail_id, guardrailVersion=s.bedrock_guardrail_version)
            rows.append(("Bedrock guardrail", g.get("status") == "READY",
                         f"{g.get('name')} v{g.get('version')} {g.get('status')}"))
        except Exception as e:  # noqa: BLE001
            rows.append(("Bedrock guardrail", False, f"{type(e).__name__}: {e}"[:120]))
    chunks = s.medibot_cache / "chunks.jsonl"
    n = len(chunks.read_text().splitlines()) if chunks.exists() else 0
    rows.append(("MediBot index", n == 257 and (s.medibot_cache / "qdrant_local").exists(),
                 f"{n} chunks at {s.medibot_cache}" if n else "missing: run `mediguard target build-index`"))
    lock = s.medibot_cache / "qdrant_local" / ".lock"
    rows.append(("Qdrant lock", True, "lock file present (fine unless another process is running)" if lock.exists()
                 else "free"))
    ok, why = _pristine()
    rows.append(("target/medibot", ok, why))
    width = max(len(r[0]) for r in rows)
    for name, good, detail in rows:
        print(f"{'OK  ' if good else 'FAIL'} {name.ljust(width)}  {detail}")
    return 0 if all(r[1] for r in rows) else 1
