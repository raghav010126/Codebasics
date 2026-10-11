"""Hash personal identifiers before they reach traces or logs.

Patient ids and names (MediBot's claims table holds patient names), plus personal identifiers a user may type: Aadhaar,
PAN, payment card (Luhn-valid), Indian mobile numbers and personal email addresses. The hospital's own published
contacts (toll-free numbers, @mediassist.in role mailboxes) stay readable: answering with them is MediBot's job.
Hashes are deterministic, so the same identifier gets the same tag across events and traces.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from functools import lru_cache
from typing import Any

from mediguard.config import get_settings

_ID_RE = re.compile(r"\b(PAT-\d{4,6}|CLM-\d{4}-\d{3,5})\b")
_START, _END = r"(?<![\w-])(?<!\d[\s-])", r"(?![\w-]|[\s-]\d)"   # not inside a longer id or digit group
_MOBILE_RE = re.compile(rf"{_START}(?:\+91[\s-]?)?[6-9]\d{{4}}[\s-]?\d{{5}}{_END}")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@([\w-]+(?:\.[\w-]+)+)\b")
ORG_EMAIL_DOMAIN = "mediassist.in"


def _tag(kind: str, value: str) -> str:
    return f"<{kind}#{hashlib.sha256(value.encode()).hexdigest()[:8]}>"


@lru_cache
def patient_names() -> tuple[str, ...]:
    db = get_settings().medibot_dir / "data" / "db" / "mediassist.db"
    if not db.exists():
        return ()
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        names = {r[0] for r in con.execute("SELECT DISTINCT patient_name FROM claims") if r[0]}
    finally:
        con.close()
    return tuple(sorted(names, key=len, reverse=True))


@lru_cache
def _names_re() -> re.Pattern | None:
    names = patient_names()
    return re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b") if names else None


@lru_cache
def _pii_res() -> dict[str, re.Pattern]:
    """config/rules.yaml's output PII patterns, anchored so they never match inside trace UUIDs or longer numbers."""
    from mediguard.guardrails.rules import rules

    pii = rules()["pii_block"]
    return {kind: re.compile(rf"{_START}(?:{pii[kind]}){_END}", re.IGNORECASE) for kind in ("card", "aadhaar", "pan")}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _card(m: re.Match) -> str:
    from mediguard.guardrails.rules import luhn_ok

    d = _digits(m.group(0))
    return _tag("CARD", d) if 13 <= len(d) <= 19 and luhn_ok(d) else m.group(0)


def _email(m: re.Match) -> str:
    domain = m.group(1).lower()
    org = domain == ORG_EMAIL_DOMAIN or domain.endswith("." + ORG_EMAIL_DOMAIN)
    return m.group(0) if org else _tag("EMAIL", m.group(0).lower())


def scrub_text(text: str) -> str:
    text = _ID_RE.sub(lambda m: _tag("ID", m.group(1)), text)
    pii = _pii_res()
    text = pii["card"].sub(_card, text)
    text = pii["aadhaar"].sub(lambda m: _tag("AADHAAR", _digits(m.group(0))), text)
    text = pii["pan"].sub(lambda m: _tag("PAN", m.group(0).upper()), text)
    text = _MOBILE_RE.sub(lambda m: _tag("PHONE", _digits(m.group(0))[-10:]), text)
    text = _EMAIL_RE.sub(_email, text)
    rx = _names_re()
    return rx.sub(lambda m: _tag("PATIENT", m.group(1)), text) if rx else text


def scrub(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value
