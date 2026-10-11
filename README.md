# Codebasics

AI Engineering Cohort — assignment submissions.

## Assignments

- [Assignment 1 — Workout Plan Generator](assignment1_workout_planner/) — a Streamlit
  app that generates a personalized weekly workout plan from structured fitness inputs
  using the Groq API. See its [README](assignment1_workout_planner/README.md) for setup
  and run instructions.
- [Assignment 2 — MediBot](assignment2_medibot/) — an advanced RAG assistant for a hospital
  network: Docling structure-aware ingestion, hybrid dense + BM25 retrieval in Qdrant with
  cross-encoder reranking, role-based access control enforced at the vector-store query level,
  SQL RAG over a relational database, a FastAPI backend and a Streamlit UI. See its
  [README](assignment2_medibot/README.md) for architecture, setup, and the RBAC red-team results.
- [Assignment 3 — MediGuard](assignment3_eval_guardrails/) — an evaluation and guardrail pipeline wrapped
  around the unmodified MediBot: input/output guardrails (deterministic rules, AWS Bedrock Guardrails, OpenEvals
  judges) that fail closed, LangSmith tracing of the full request path with structured, queryable event logs, a
  repeatable RAGAS evaluation over a labelled question set, a Claude Opus 5.5 LLM-as-a-judge, deterministic heuristic
  checks, a single PASS/FAIL report, and a Streamlit chat UI. See its [README](assignment3_eval_guardrails/README.md).
