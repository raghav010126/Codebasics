"""Canned DuckDB queries over the JSONL event log: reconstruct a request, block counts, latency, tokens.

Usage: mediguard logs --request req_abc123 | --blocks | --latency | --tokens | --sql "select ..."
"""

from __future__ import annotations

import json

from mediguard.config import get_settings

VIEW = ("CREATE OR REPLACE VIEW events AS SELECT * FROM read_json_auto('{path}', format='newline_delimited', "
        "union_by_name=true, ignore_errors=true)")

QUERIES = {
    "blocks": """
        SELECT stage, blocking_check AS "check", reason_code, count(*) AS n
        FROM events WHERE event = 'guardrail.decision' AND decision = 'block'
        GROUP BY ALL ORDER BY n DESC""",
    "decisions": """
        SELECT stage, decision, count(*) AS n FROM events WHERE event = 'guardrail.decision' GROUP BY ALL ORDER BY ALL""",
    "latency": """
        SELECT count(*) AS requests,
               round(quantile_cont(latency_ms, 0.5)) AS p50_ms, round(quantile_cont(latency_ms, 0.95)) AS p95_ms,
               round(quantile_cont(recorded_latency_ms, 0.5)) AS p50_recorded_ms,
               round(quantile_cont(recorded_latency_ms, 0.95)) AS p95_recorded_ms
        FROM events WHERE event = 'request.completed'""",
    "tokens": """
        SELECT request_id, role, status, tokens_in, tokens_out, round(cost_usd, 5) AS cost_usd,
               round(cold_cost_usd, 5) AS cold_cost_usd, llm_calls, cached_calls
        FROM events WHERE event = 'request.completed' ORDER BY ts DESC LIMIT 25""",
    "recent": """
        SELECT ts, request_id, role, status, reason_code, round(latency_ms) AS ms, run_url
        FROM events WHERE event = 'request.completed' ORDER BY ts DESC LIMIT 20""",
}


def connect():
    import duckdb

    path = get_settings().events_path
    if not path.exists():
        raise FileNotFoundError(f"no events yet at {path}")
    con = duckdb.connect()
    con.execute(VIEW.format(path=str(path).replace("'", "''")))
    return con


def run(sql: str) -> tuple[list[str], list[tuple]]:
    con = connect()
    cur = con.execute(sql)
    return [d[0] for d in cur.description], cur.fetchall()


def request_timeline(request_id: str) -> list[dict]:
    """Every event of one request, oldest first: what it saw, what it decided, and why."""
    path = get_settings().events_path
    out = []
    for line in path.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("request_id") == request_id:
            out.append(e)
    return out


def format_table(cols: list[str], rows: list[tuple]) -> str:
    cells = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(cols)]
    line = " | ".join(c.ljust(w) for c, w in zip(cols, widths))
    sep = "-+-".join("-" * w for w in widths)
    return "\n".join([line, sep] + [" | ".join(v.ljust(w) for v, w in zip(r, widths)) for r in cells])
