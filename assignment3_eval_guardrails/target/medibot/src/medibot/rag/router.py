"""Question router: SQL (analytics) vs document RAG, plus which collections/tables it concerns.

The router only sees the question text. Its output drives UX (informative refusals) -
it is NOT the security boundary; the Qdrant RBAC filter and the SQL authorizer are.
"""

import logging
import re
from dataclasses import dataclass, field

from medibot import llm
from medibot.rag import prompts
from medibot.rbac import ALL_SQL_TABLES, COLLECTION_DESCRIPTIONS, COLLECTIONS

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["sql", "rag"]},
        "target_collections": {"type": "array", "items": {"type": "string", "enum": list(COLLECTIONS)}},
        "sql_tables": {"type": "array", "items": {"type": "string", "enum": list(ALL_SQL_TABLES)}},
    },
    "required": ["route", "target_collections", "sql_tables"],
    "additionalProperties": False,
}


@dataclass
class Route:
    route: str  # "sql" | "rag"
    target_collections: list[str] = field(default_factory=list)
    sql_tables: list[str] = field(default_factory=list)
    source: str = "llm"  # "llm" | "keyword"


# ---------------------------------------------------------------- keyword fallback
_ANALYTIC = re.compile(
    r"\b(how many|count|total|sum|average|avg|mean|number of|percentage|percent|rate of|highest|lowest|most|least|top \d+|per (?:department|campus|insurer|category|month))\b", re.I)
_CLAIM_WORDS = re.compile(r"\b(claims?|insurers?|approved amount|claimed amount|rejected|escalated|pending claims?)\b", re.I)
_TICKET_WORDS = re.compile(r"\b(tickets?|maintenance tickets?|campus(?:es)?|raised by|open tickets?)\b", re.I)
_COLLECTION_WORDS = {
    "billing": r"\b(billing|icd|package rate|insurance|insurer|claim|pre-?auth|reimbursement|co-?pay|tpa)\b",
    "clinical": r"\b(drug|dose|dosage|formulary|treatment|protocol|diagnos|lab(?:oratory)?|reference range|haemoglobin|medication|antibiotic|tier \d)\b",
    "nursing": r"\b(nursing|icu|ventilator|cannula|catheter|infection control|hand hygiene|ppe|sop|suction|restraint)\b",
    "equipment": r"\b(equipment|fault code|calibration|maintenance|autoclave|infusion pump|monitor|x-?ray|bm-500|driveflow|sterilpro|radipro)\b",
    "general": r"\b(leave|salary|payslip|handbook|code of conduct|holiday|provident|appraisal|dress code|policy)\b",
}


_RAW_SQL = re.compile(r"\bselect\b.+\bfrom\b|\b(?:from|join|table)\s+(?:claims|maintenance_tickets)\b|\b(?:claims|maintenance_tickets)\s+table\b", re.I | re.S)


def keyword_route(question: str) -> Route:
    tables = []
    if _CLAIM_WORDS.search(question) or re.search(r"\bclaims\b", question, re.I):
        tables.append("claims")
    if _TICKET_WORDS.search(question) or re.search(r"\bmaintenance_tickets\b", question, re.I):
        tables.append("maintenance_tickets")
    if _RAW_SQL.search(question):  # explicit SQL / table reference is always an analytics request
        return Route("sql", sql_tables=tables, source="keyword")
    if _ANALYTIC.search(question) and tables:
        return Route("sql", sql_tables=tables, source="keyword")
    targets = [c for c, pat in _COLLECTION_WORDS.items() if re.search(pat, question, re.I)]
    return Route("rag", target_collections=targets, source="keyword")


# ---------------------------------------------------------------- LLM router
def classify(question: str) -> Route:
    system = prompts.ROUTER_SYSTEM.format(
        collections="\n".join(f"- {c}: {d}" for c, d in COLLECTION_DESCRIPTIONS.items())
    )
    try:
        out = llm.structured(system, f"Question: {question}", SCHEMA, effort="low", max_tokens=1024)
        return Route(
            route=out["route"],
            target_collections=[c for c in out["target_collections"] if c in COLLECTIONS],
            sql_tables=[t for t in out["sql_tables"] if t in ALL_SQL_TABLES],
        )
    except llm.LLMError as e:  # includes refusals: fall back to deterministic routing
        log.warning("router LLM failed (%s); using keyword routing", e)
        return keyword_route(question)
