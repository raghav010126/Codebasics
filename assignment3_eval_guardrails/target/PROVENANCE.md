# Provenance of `target/medibot/`

`target/medibot/` is an unmodified copy of MediBot (Codebasics AI Engineering Bootcamp, assignment 2).

| | |
|---|---|
| Source repository | https://github.com/raghav010126/Codebasics |
| Path in source | `assignment2_medibot/` |
| Commit | `dbe20cf` |
| Copied with | `git archive dbe20cf assignment2_medibot \| tar -x` (committed files only: no `.env`, `.venv` or caches) |

`target/medibot.sha256` lists the SHA-256 of every file in the copy. `tests/test_target_pristine.py` fails if any file
is added, removed or changed, so every evaluation in this repository runs against MediBot exactly as submitted.
The evaluation and guardrail layer wraps MediBot from the outside (see `src/mediguard/target/medibot/`).
