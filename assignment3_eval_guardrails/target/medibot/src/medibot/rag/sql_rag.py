"""SQL RAG over mediassist.db.

``sql_rag_chain(question) -> str`` is a plain function with three explicit steps:
  1. generate_sql   - LLM turns the question into SQL (sees only permitted tables)
  2. clean_sql      - extract just the SELECT from fences / prose; reject anything else
  3. execute_sql    - run on a read-only connection with a SQLite authorizer, then
     summarize()    - LLM turns the rows into a natural-language answer

Access control is layered: the LLM only sees permitted tables' schema, ``clean_sql``
rejects non-SELECT, and the authorizer denies reads of any other table at the engine level.
"""

import re
import sqlite3
from dataclasses import dataclass, field

from medibot import llm
from medibot.config import get_settings
from medibot.rag import prompts
from medibot.rbac import ALL_SQL_TABLES

CANNOT_ANSWER = "CANNOT_ANSWER"

# categorical columns whose distinct values are shown to the LLM
_HINT_COLUMNS = {
    "claims": ["status", "department", "claim_type", "insurer"],
    "maintenance_tickets": ["status", "category", "campus", "issue_type", "fault_code"],
}
_DATE_COLUMNS = {"claims": ["submitted_date", "resolved_date"], "maintenance_tickets": ["raised_date", "resolved_date"]}

_FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX)\b", re.I)
_NEXT_STATEMENT = re.compile(r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|REPLACE|VACUUM|SELECT|WITH)\b", re.I)


class SQLError(ValueError):
    """Generated SQL was unusable or not permitted."""


@dataclass
class SQLResult:
    answer: str
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    tables: tuple[str, ...] = ()
    answerable: bool = True


# ---------------------------------------------------------------- connection
def connect_readonly() -> sqlite3.Connection:
    uri = get_settings().db_path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _authorizer(allowed: tuple[str, ...]):
    allowed_set = set(allowed)

    def authorize(action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if arg1 in allowed_set else sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY  # everything else: writes, DDL, ATTACH, PRAGMA, ...

    return authorize


# ---------------------------------------------------------------- prompt context
def _schema_and_hints(conn: sqlite3.Connection, tables: tuple[str, ...]):
    ddl, hints, dates = [], [], []
    for t in tables:
        ddl.append(conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0])
        for col in _HINT_COLUMNS[t]:
            vals = [r[0] for r in conn.execute(f"SELECT DISTINCT {col} FROM {t} WHERE {col} IS NOT NULL ORDER BY 1")]
            hints.append(f"- {t}.{col}: {', '.join(map(str, vals))}")
        for col in _DATE_COLUMNS[t]:
            lo, hi = conn.execute(f"SELECT MIN({col}), MAX({col}) FROM {t}").fetchone()
            dates += [lo, hi]
    dates = [d for d in dates if d]
    return "\n".join(ddl), "\n".join(hints), min(dates), max(dates)




# ---------------------------------------------------------------- step 1
def generate_sql(question: str, tables: tuple[str, ...], error: str | None = None, previous: str | None = None) -> str:
    conn = connect_readonly()  # trusted introspection of permitted tables only (no authorizer needed)
    try:
        schema, hints, lo, hi = _schema_and_hints(conn, tables)
    finally:
        conn.close()
    system = prompts.SQL_SYSTEM.format(
        schema=schema, value_hints=hints, date_range=f"{lo} to {hi}", year=hi[:4], latest=hi,
        last_month=f"{hi[:7]} (the most recent month in the data)",
    )
    user = question
    if error:
        user += f"\n\nYour previous query failed.\nQuery: {previous}\nError: {error}\nReturn a corrected query."
    return llm.complete(system, user, effort="medium", max_tokens=2048)


# ---------------------------------------------------------------- step 2
def clean_sql(raw: str, max_rows: int | None = None) -> str:
    """Extract a single read-only SELECT from raw LLM output, or raise SQLError."""
    max_rows = max_rows or get_settings().sql_max_rows
    text = re.sub(r"<think>.*?</think>", " ", raw, flags=re.S | re.I)
    fence = re.search(r"```(?:sql|sqlite)?\s*(.*?)```", text, flags=re.S | re.I)
    if fence:
        text = fence.group(1)
    text = text.replace("```", " ")
    if CANNOT_ANSWER in text:
        raise SQLError(CANNOT_ANSWER)
    start = re.search(r"\b(WITH|SELECT)\b", text, flags=re.I)
    if not start:
        raise SQLError("No SELECT statement found in model output.")
    text = text[start.start():]
    if ";" in text:
        stmt, rest = text.split(";", 1)
        if _NEXT_STATEMENT.match(rest):
            raise SQLError("Multiple SQL statements are not allowed.")
    else:
        stmt = re.split(r"\n\s*\n", text, maxsplit=1)[0]  # drop trailing prose after a blank line
    stmt = stmt.strip()
    if _FORBIDDEN.search(re.sub(r"'[^']*'", "''", stmt)):
        raise SQLError("Only read-only SELECT queries are allowed.")
    if not re.search(r"\bLIMIT\b", stmt, flags=re.I):
        stmt += f" LIMIT {max_rows}"
    return stmt


# ---------------------------------------------------------------- step 3
def execute_sql(sql: str, tables: tuple[str, ...]) -> tuple[list[str], list[tuple]]:
    conn = connect_readonly()
    conn.set_authorizer(_authorizer(tables))
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description or []]
        return cols, cur.fetchall()
    except sqlite3.DatabaseError as e:
        raise SQLError(str(e)) from e
    finally:
        conn.close()


def summarize(question: str, sql: str, cols: list[str], rows: list[tuple]) -> str:
    shown = rows[:50]
    table = "\n".join([" | ".join(cols)] + [" | ".join("" if v is None else str(v) for v in r) for r in shown])
    more = f"\n(... {len(rows) - len(shown)} more rows)" if len(rows) > len(shown) else ""
    user = f"Question: {question}\n\nSQL used:\n{sql}\n\nResult ({len(rows)} rows):\n{table}{more}"
    return llm.complete(prompts.SQL_ANSWER_SYSTEM, user, effort="medium", max_tokens=2048)


# ---------------------------------------------------------------- orchestration
def run_sql_rag(question: str, tables: tuple[str, ...] = ALL_SQL_TABLES) -> SQLResult:
    """Full chain with one repair attempt; returns SQL + rows for the API to expose."""
    raw = generate_sql(question, tables)
    sql, err = "", None
    for attempt in range(2):
        try:
            sql = clean_sql(raw)
            cols, rows = execute_sql(sql, tables)
            break
        except SQLError as e:
            if str(e) == CANNOT_ANSWER:
                return SQLResult(
                    "I can't answer that from the data you have access to. "
                    f"Analytical questions for your role cover: {', '.join(tables)}.",
                    tables=tables, answerable=False,
                )
            err = str(e)
            if attempt == 1:
                return SQLResult(f"I couldn't build a valid query for that question ({err}). Try rephrasing it.",
                                 sql=sql, tables=tables, answerable=False)
            raw = generate_sql(question, tables, error=err, previous=sql or raw[:300])
    return SQLResult(summarize(question, sql, cols, rows), sql=sql, columns=cols, rows=rows, tables=tables)


def sql_rag_chain(question: str) -> str:
    """Natural-language question -> SQL -> execute -> natural-language answer."""
    return run_sql_rag(question).answer
