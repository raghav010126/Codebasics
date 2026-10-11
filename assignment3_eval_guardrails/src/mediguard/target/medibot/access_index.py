"""What each MediBot role must never see, derived from the indexed chunks, MediBot's red-team markers and the DB.

* restricted-only tokens: codes (BILL-CODE-010, I21.4, F-12) and capitalised names (Niva Bupa, Central Venous
  Catheter) that occur in some chunk the role cannot read and in no chunk it can read;
* restricted-only shingles: word 6-grams of unreadable chunks minus those of readable chunks (verbatim copying);
* database identifiers: patient names, CLM-/PAT- ids (claims), TKT-/EQ- ids and staff names (maintenance tickets).

Usage: idx = access_index(); idx.restricted_tokens["nurse"]
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache

from mediguard.config import get_settings
from mediguard.models import ROLES
from mediguard.target.medibot.index_build import load_chunks
from mediguard.target.medibot.vendored_data import markers

CODE_RE = re.compile(r"\b[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)+\b")
NAME_RE = re.compile(r"\b[A-Z][a-zA-Z]+(?:[ -][A-Z][a-zA-Z0-9]+)+\b")
WORD_RE = re.compile(r"[a-z0-9]+")
SHINGLE = 6


def code_tokens(text: str) -> set[str]:
    return {m.group(0) for m in CODE_RE.finditer(text)
            if any(c.isdigit() for c in m.group(0)) and any(c.isalpha() for c in m.group(0))}


def name_tokens(text: str) -> set[str]:
    return {m.group(0) for m in NAME_RE.finditer(text)}


def shingles(text: str, n: int = SHINGLE) -> set[tuple[str, ...]]:
    words = WORD_RE.findall(text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


@dataclass
class AccessIndex:
    chunks: dict[str, dict]
    readable: dict[str, set[str]]                     # role -> chunk ids it can read
    readable_text: dict[str, str]                     # role -> lowercased text of everything it can read
    restricted_tokens: dict[str, set[str]]
    restricted_shingles: dict[str, set[tuple[str, ...]]]
    markers: dict[str, list[str]]
    claims_ids: set[str] = field(default_factory=set)
    patient_names: set[str] = field(default_factory=set)
    ticket_ids: set[str] = field(default_factory=set)
    staff_names: set[str] = field(default_factory=set)

    def readable_by(self, role: str, value: str) -> bool:
        return value.lower() in self.readable_text[role]


def _db_identifiers() -> dict[str, set[str]]:
    db = get_settings().medibot_dir / "data" / "db" / "mediassist.db"
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        claims = con.execute("SELECT claim_id, patient_id, patient_name FROM claims").fetchall()
        tickets = con.execute("SELECT ticket_id, equipment_id, raised_by FROM maintenance_tickets").fetchall()
    finally:
        con.close()
    return {"claims_ids": {c for r in claims for c in r[:2] if c}, "patient_names": {r[2] for r in claims if r[2]},
            "ticket_ids": {c for r in tickets for c in r[:2] if c}, "staff_names": {r[2] for r in tickets if r[2]}}


@lru_cache
def access_index() -> AccessIndex:
    chunks = {c["id"]: c for c in load_chunks()}
    readable = {r: {cid for cid, c in chunks.items() if r in c["access_roles"]} for r in ROLES}
    tokens = {cid: code_tokens(c["text"]) | name_tokens(c["text"]) for cid, c in chunks.items()}
    sh = {cid: shingles(c["text"]) for cid, c in chunks.items()}
    restricted_tokens, restricted_shingles, readable_text = {}, {}, {}
    for r in ROLES:
        mine, other = readable[r], set(chunks) - readable[r]
        readable_text[r] = "\n".join(chunks[cid]["embed_text"] for cid in mine).lower()
        seen = {t.lower() for cid in mine for t in tokens[cid]}
        restricted_tokens[r] = {t for cid in other for t in tokens[cid] if t.lower() not in seen}
        seen_sh = set().union(*(sh[cid] for cid in mine)) if mine else set()
        restricted_shingles[r] = set().union(*(sh[cid] for cid in other)) - seen_sh if other else set()
    return AccessIndex(chunks=chunks, readable=readable, readable_text=readable_text,
                       restricted_tokens=restricted_tokens, restricted_shingles=restricted_shingles,
                       markers=markers(), **_db_identifiers())
