# Observability: reconstructing any request from its id

Every request gets a `request_id` (`req_…`). The user sees only the answer, or the generic refusal *with that id*.
From the id alone a reviewer can answer **what did the system see, what did it decide, and why**, without re-running
anything.

## 1. The trace (LangSmith, project `mediguard-assignment3`)

One `guarded_request` trace per request:

```
guarded_request                         tags: role:*, status:*, verdict:allow|block, stage:*, reason:*, target_status:*
├─ guardrail:input
│  ├─ check:input.rules                 decision, reason_code, warnings, evidence
│  ├─ check:input.bedrock               decision + evidence (action, PII detected, usage)
│  │  └─ bedrock.apply_guardrail        guardrail id/version, action, full assessments
│  └─ check:input.openevals             Haiku verdict + category/rationale (LangChain run nested)
├─ target:medibot                       status, flags, MediBot commit, target model
│  ├─ router → llm:router               route + target collections (or keyword fallback)
│  ├─ retriever:hybrid_rrf              10 candidates (Document format) with access_roles, RRF scores
│  ├─ rerank:cross_encoder              all 10 cross-encoder scores, the 3 kept
│  ├─ generation:answer → llm:answer    the exact numbered passages + prompt, tokens, served model
│  │  (SQL route: sql_rag → sql:generate → llm:nl2sql → sql:execute → generation:sql_answer → llm:sql_answer)
│  └─ rbac:decision                     restricted vs allowed targets, best score vs MediBot's thresholds
└─ guardrail:output
   ├─ check:output.rules · check:output.leak · check:output.numeric_grounding
   ├─ check:output.leak_paraphrase · check:output.bedrock (+ bedrock.apply_guardrail: grounding scores)
   └─ check:output.openevals
```

Root metadata: request id, target name/version/model, guardrail id + version, mode, degraded layers, then per-stage
latency, tokens in/out per component, cost and cache hits. (The list of every model called is on `request.completed`.) Guardrail verdicts are also attached as feedback (`guardrail.input`,
`guardrail.output`), so blocked traffic can be filtered:

* blocked requests: `and(eq(name, "guarded_request"), has(tags, "verdict:block"))`
* fail-closed blocks: `has(tags, "reason:fail_closed.timeout")` (or any other `reason:` tag)
* a role: `has(tags, "role:nurse")`

Personal identifiers are hashed in traces and logs (`src/mediguard/observability/scrub.py`): patient names and
CLM-/PAT- ids, plus Aadhaar, PAN, Luhn-valid card numbers, Indian mobile numbers and personal email addresses. The
hospital's own published contacts (toll-free numbers, `@mediassist.in` mailboxes) stay readable, since giving them out
is MediBot's job. Hashes are deterministic (`<AADHAAR#a383f321>`), so the same identifier can be followed across events.

## 2. The event log (`~/.cache/mediguard/logs/events.jsonl`)

Structured JSON lines, one per event:

| Event | Key fields |
|---|---|
| `request.received` | request_id, trace_id, role, question |
| `guardrail.check` | stage, check, decision, reason_code, detail_internal, fail_closed, warnings, evidence (Bedrock action/PII/grounding scores, numeric claims, judge category and rationale), latency_ms |
| `guardrail.decision` | stage, decision, blocking_check, reason_code, checks[], latency_ms |
| `target.response` | status, flags, route, retrieval_type, chunks[{n, chunk_id, source, section, rerank}], sql, answer, latency |
| `request.failed` | error_type, error (a failure in the pipeline itself; the user still got the refusal) |
| `request.completed` | status, verdict, blocking stage/check/reason, public_answer, latency_ms, recorded_latency_ms, tokens_in/out, cost_usd, cold_cost_usd, llm_calls, cached_calls, by_component, stage_ms, run_url |
| `eval.stage` | run_id, stage (smoke, collect, heuristics, ragas, judge, done), verdict |

Queries (DuckDB over the JSONL):

```bash
make logs ARGS="--request req_1a2b3c4d5e6f"   # full timeline of one request
make logs ARGS="--blocks"                      # block counts by stage / check / reason
make logs ARGS="--latency"                     # p50 / p95 wall-clock and recorded latency
make logs ARGS="--tokens"                      # tokens and cost per request
make logs ARGS='--sql "select role, count(*) from events where event = '"'"'request.completed'"'"' group by 1"'
```

The guardrail metrics are therefore *queryable*: latency and token usage per request are fields on
`request.completed`, and every decision is a `guardrail.check` / `guardrail.decision` row.
