"""Run the SQL RAG chain on benchmark questions and check each answer against ground truth.

Ground truth comes from direct SQL on the database (see GOLDEN in tests/test_sql_rag.py), so this
verifies the LLM->SQL->answer path end to end. Requires ANTHROPIC_API_KEY.
Usage: uv run python scripts/sql_benchmark.py
"""
import sys

import medibot  # noqa: F401
from medibot.llm import LLMError
from medibot.rag.sql_rag import run_sql_rag

BOTH = ("claims", "maintenance_tickets")
# (question, tables, strings that must all appear in the answer or result rows; a tuple = any of these spellings)
CASES = [
    ("How many claims are escalated?", ("claims",), ["8"]),
    ("Which department has the most rejected claims?", ("claims",), ["cardiology"]),
    ("Which insurer has the highest total claimed amount, and how much of it was approved?", ("claims",), ["Bajaj Allianz"]),
    ("Which equipment category has the most open maintenance tickets?", BOTH, ["radiology"]),
    ("What is the average number of days to resolve resolved tickets for each campus?", BOTH, ["Hyderabad", "8.5"]),
    ("How many claims were submitted last month?", ("claims",), ["4"]),
    ("How many claims were escalated last month?", ("claims",), ["0"]),
    ("Compare the approval rate of cashless and reimbursement claims.", ("claims",), [("46.7", "46.67"), "64"]),
]

fails = 0
for q, tables, must in CASES:
    try:
        res = run_sql_rag(q, tables)
    except LLMError as e:
        sys.exit(f"LLM unavailable: {e}")
    haystack = (res.answer + " " + " ".join(str(v) for r in res.rows for v in r)).lower()
    ok = res.answerable and all(any(a.lower() in haystack for a in ((m,) if isinstance(m, str) else m)) for m in must)
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {q}\n      SQL: {res.sql}\n      -> {res.answer[:200]}\n")
print(f"{len(CASES) - fails}/{len(CASES)} passed")
sys.exit(1 if fails else 0)
