import sqlite3

import pytest

from medibot.rag import sql_rag
from medibot.rag.sql_rag import SQLError, clean_sql, execute_sql

BOTH = ("claims", "maintenance_tickets")


# ---------------------------------------------------------------- clean_sql
@pytest.mark.parametrize(
    "raw",
    [
        "SELECT count(*) FROM claims",
        "```sql\nSELECT count(*) FROM claims;\n```",
        "Here is the query:\n```\nSELECT count(*) FROM claims\n```\nThis counts all claims.",
        "Sure! SELECT count(*) FROM claims;\n\nThis query counts the claims.",
        "<think>need to count</think>SELECT count(*) FROM claims;",
        "   \n  select count(*) from claims  ;  ",
    ],
)
def test_clean_extracts_single_select(raw):
    out = clean_sql(raw)
    assert out.lower().startswith("select count(*) from claims")
    assert ";" not in out
    assert out.upper().endswith("LIMIT 200")


def test_clean_keeps_existing_limit():
    assert clean_sql("SELECT * FROM claims LIMIT 5").upper().endswith("LIMIT 5")


def test_clean_allows_cte():
    assert clean_sql("WITH a AS (SELECT 1) SELECT * FROM a").upper().startswith("WITH")


@pytest.mark.parametrize(
    "raw",
    [
        "SELECT 1; DROP TABLE claims",
        "SELECT 1; SELECT 2",
        "DELETE FROM claims",
        "UPDATE claims SET status='approved'",
        "No query here, sorry",
        "SELECT * FROM claims; ATTACH DATABASE 'x' AS y",
        "WITH x AS (SELECT 1) DELETE FROM claims",
    ],
)
def test_clean_rejects_unsafe(raw):
    with pytest.raises(SQLError):
        clean_sql(raw)


def test_clean_ignores_forbidden_words_inside_string_literals():
    assert "update" in clean_sql("SELECT * FROM maintenance_tickets WHERE resolution_note LIKE '%update%'").lower()


def test_cannot_answer_sentinel():
    with pytest.raises(SQLError, match="CANNOT_ANSWER"):
        clean_sql("CANNOT_ANSWER")


# ---------------------------------------------------------------- authorizer (engine-level RBAC)
def test_billing_cannot_read_tickets_even_with_handcrafted_sql():
    with pytest.raises(SQLError):
        execute_sql("SELECT count(*) FROM maintenance_tickets", ("claims",))
    with pytest.raises(SQLError):  # via subquery / join
        execute_sql("SELECT c.claim_id FROM claims c JOIN maintenance_tickets t ON 1=1", ("claims",))


def test_authorizer_blocks_writes_and_schema_access():
    for stmt in ("DELETE FROM claims", "DROP TABLE claims", "PRAGMA table_info(claims)",
                 "SELECT * FROM sqlite_master"):
        with pytest.raises((SQLError, sqlite3.DatabaseError)):
            execute_sql(stmt, BOTH)


def test_connection_is_read_only():
    conn = sql_rag.connect_readonly()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE x (a)")
    conn.close()


# ---------------------------------------------------------------- golden answers (SQL itself, no LLM)
GOLDEN = [
    ("SELECT count(*) FROM claims WHERE status='escalated'", [(8,)]),
    ("SELECT department, count(*) c FROM claims WHERE status='rejected' GROUP BY department ORDER BY c DESC LIMIT 1", [("cardiology", 5)]),
    ("SELECT insurer, sum(claimed_amount), sum(approved_amount) FROM claims GROUP BY insurer ORDER BY 2 DESC LIMIT 1",
     [("Bajaj Allianz", 1353000.0, 343800.0)]),
    ("SELECT category, count(*) c FROM maintenance_tickets WHERE status='open' GROUP BY category ORDER BY c DESC LIMIT 1", [("radiology", 4)]),
    ("SELECT count(*) FROM claims WHERE submitted_date LIKE '2024-12%'", [(4,)]),
    ("SELECT count(*) FROM claims WHERE status='escalated' AND submitted_date LIKE '2024-12%'", [(0,)]),
]


@pytest.mark.parametrize("sql,expected", GOLDEN)
def test_golden_queries_run_through_executor(sql, expected):
    _, rows = execute_sql(clean_sql(sql), BOTH)
    assert rows == expected


# ---------------------------------------------------------------- full chain with a stubbed LLM
def test_chain_three_steps(monkeypatch):
    calls = []

    def fake_complete(system, user, effort="medium", max_tokens=4096):
        calls.append(system[:20])
        if "single read-only SQLite" in system:
            return "```sql\nSELECT count(*) AS n FROM claims WHERE status = 'escalated';\n```\nCounts escalated claims."
        return "There are 8 escalated claims."

    monkeypatch.setattr(sql_rag.llm, "complete", fake_complete)
    res = sql_rag.run_sql_rag("How many claims are escalated?", ("claims",))
    assert res.rows == [(8,)] and res.columns == ["n"]
    assert "escalated" in res.sql and res.answer == "There are 8 escalated claims."
    assert sql_rag.sql_rag_chain("How many claims are escalated?") == "There are 8 escalated claims."
    assert len(calls) == 4  # generate + summarize, twice


def test_chain_cannot_answer(monkeypatch):
    monkeypatch.setattr(sql_rag.llm, "complete", lambda *a, **k: "CANNOT_ANSWER")
    res = sql_rag.run_sql_rag("What is the weather?", ("claims",))
    assert not res.answerable and res.sql == ""


def test_chain_repairs_once(monkeypatch):
    outputs = iter(["SELECT nope FROM claims", "SELECT count(*) FROM claims", "85 claims"])
    monkeypatch.setattr(sql_rag.llm, "complete", lambda *a, **k: next(outputs))
    res = sql_rag.run_sql_rag("count claims", ("claims",))
    assert res.rows == [(85,)] and res.answer == "85 claims"
