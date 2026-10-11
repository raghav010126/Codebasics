# Spikes: what was verified before building, and what it decided

| # | Question | Result | Decision |
|---|---|---|---|
| S0 | Do ragas, openevals and langchain-anthropic install next to MediBot's pinned stack (anthropic 1.x, docling, torch)? | Resolves (221 packages). **ragas 0.4.3 imports `langchain_community.chat_models.vertexai`, removed in langchain-community 0.4.2.** | Pin `langchain-community==0.4.1` (still has the module, works with langchain-core 1.x). Keep MediBot's locked versions as `constraint-dependencies` so chunk ids stay identical. |
| S1 | Can MediBot run unmodified on Sonnet 5.5 with our caches, and do all hooks fire? | Yes. Settings come from env vars (env beats MediBot's `.env`); `build-index` gives **257 chunks with ids and text identical** to the submitted copy; RAG, SQL and RBAC-refusal requests all captured (route, 10 candidates, rerank scores, exact passages, SQL rows, tokens). | Patch names where they are *used* (`doc_rag.search`, `doc_rag.rerank`, `llm._create`, ...). Record/replay wraps `llm._create`: a `replay_only` re-run made **0 API calls** with identical answers. |
| S2 | Do our LangSmith spans nest, including MediBot's internals and worker threads? | Yes: one `guarded_request` trace holds `guardrail:input` → `target:medibot` → `router` → `llm:router` → `retriever:hybrid_rrf` → `rerank:cross_encoder` → `generation:answer` → `llm:answer` → `rbac:decision` → `guardrail:output`. Check threads keep the parent via `contextvars.copy_context()`. Feedback needs a session id (LangSmith deprecation). | Our own `Client(hide_inputs/outputs=scrub)`; feedback passes `session_id` and `start_time`. |
| S3 | Bedrock ApplyGuardrail shapes, detect-only PII, grounding qualifiers | Pending AWS login when this was written; see the red-team report for live verdicts. | Verdict read only from `action` (`NONE` / `GUARDRAIL_INTERVENED`); anything else fails closed. Dosing is not a denied topic. |
| S4 | Can OpenEvals use the 5.5 models? | OpenEvals calls LangChain `with_structured_output`, which forces `tool_choice`; Opus/Sonnet 5.5 reject forced tool choice and non-default temperature. | **Claude Haiku 4.5 at temperature 0** for the in-request OpenEvals judges. OpenEvals 0.2.0 ships `PROMPT_INJECTION_PROMPT`, `PII_LEAKAGE_PROMPT`, `RAG_GROUNDEDNESS_PROMPT`; our prompts adapt them and add the role scope. |
| S5 | RAGAS with Claude, repeatably | RAGAS's LangChain wrapper sets `temperature` on the model; the 5.5 models reject it. Haiku 4.5 at temperature 0 works but **mis-scores**: on run `dev3` it gave context precision 0 to N3, whose two passages were exactly the reference chunks (rerank 0.996 / 0.69), and answer relevancy 0 to complete answers H1/H2 ("noncommittal"). Re-scored side by side: Opus 5.5 gives H1 relevancy 0.98 / precision 1.0 and N3 precision 1.0. | **Opus 5.5 (effort low) as the RAGAS evaluator** with `bypass_temperature=True`; repeatability from RAGAS's `DiskCacheBackend`. Items whose correct answer says "the documents don't state this" (U1, U2, H4) are not scored on answer relevancy, because RAGAS scores any noncommittal answer 0 by design. (H5 was in this list until its reference was corrected; see below. It is now scored.) Embeddings: fastembed `bge-small-en-v1.5` from MediBot's cache. |
| S6 | Opus 5.5 judge with structured output | `client.beta.messages.parse(output_format=JudgeOut, fallbacks="default", output_config={"effort": "medium"})` works; about $0.02 per grade; wrong-but-confident probes get accuracy 1 with specific justifications. | Judge = Opus 5.5 (no temperature: rejected). Repeatability from a disk cache keyed on model + rubric version + prompt. |
| S7 | Model swap behaviour | MediBot always sends `output_config.effort`; Haiku 4.5 rejects it, so every call fails. The router then silently falls back to keyword routing, which still refuses RBAC questions, so the failure is partly hidden. | Heuristic H2 checks `router_degraded`, not only errors. See the swap-demo report. |

## Findings during the first evaluation runs

* **Fail-fast worked**: run `dev1` stopped at the heuristics gate (before any RAGAS/judge spend) because 4 benign
  items were blocked: 2 by numeric grounding, 2 by the OpenEvals input judge.
* **Numeric grounding false positives**: paediatric dosing tables name the drug in the column header, not the row;
  "COPD protocol" was taken as a drug. Fixed: table rows carry their header row; only drug-lexicon names must share
  the row; other names (protocols, drugs the corpus never doses) only need the same passage. The unit tests still
  catch "Warfarin 5 mg" (dose belongs to Glipizide) and wrong Vancomycin doses.
* **OpenEvals over-blocking**: Haiku treated an HR question (probation notice period) and a plain restricted question
  (a doctor asking about maintenance tickets) as off-topic abuse. Fixed by stating MediBot's full scope first and
  that "outside the user's collections" is never a reason to block (MediBot refuses those itself).
* **RAGAS evaluator quality**: run `dev3` (Haiku evaluator) failed the answer-relevancy gate (0.73 < 0.75) on
  evaluator errors, not answer quality. The fix was a stronger evaluator, not a lower threshold (thresholds were
  fixed before the first run and never changed).
* **RAGAS inside a LangSmith trace**: RAGAS looks for its own root run, which crashed (`IndexError`) when it ran
  inside our `eval_run` span; it now runs in a fresh context (metrics carried over so its cost is still counted).
* **RAGAS + LangChain FastEmbed**: RAGAS's usage telemetry needs `embeddings.model` to be a string; a 20-line
  fastembed adapter replaces LangChain's wrapper (`ragas_eval.LocalEmbeddings`).
* **A wrong reference answer, found by the model-swap run**: H5's reference said the notice period after
  confirmation was undocumented. Both Sonnet 5.5 and Sonnet 4.6 answered "60 days for clinical staff", and that is in
  `general_faqs.pdf` Q19, where "notice period" appears only in the heading, which is why my text search missed it.
  The judge had already scored the baseline's answer as accurate *against the passages* despite the bad reference.
  H5 is now a fully answerable cross-document item (staff handbook + FAQ).
* **Prompt-echo false positive**: MediBot's system prompt tells it to say it "could not find it in the documents
  available to the user"; an 8-gram echo check flagged that phrase in a correct abstention. The echo window is now
  10 words (a pasted prompt sentence still matches).
