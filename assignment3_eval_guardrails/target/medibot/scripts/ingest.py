"""Standalone ingestion: parse -> chunk -> validate -> report -> index into Qdrant.

Usage:  uv run python scripts/ingest.py [--no-index]
Run once before the demo (first run downloads Docling/embedding models).
"""
import argparse
import collections
import json
import logging

import medibot  # noqa: F401  (truststore)
from medibot.config import ROOT, get_settings
from medibot.ingestion.chunk import chunk_document, validate_records
from medibot.ingestion.parse import build_converter, discover_documents, parse_document

logging.basicConfig(level=logging.INFO, format="%(message)s")
ap = argparse.ArgumentParser()
ap.add_argument("--no-index", action="store_true")
args = ap.parse_args()

conv = build_converter()
records, rows = [], []
for collection, path in discover_documents():
    recs = chunk_document(collection, path, parse_document(path, conv))
    records += recs
    rows.append((collection, path.name, recs))

problems = validate_records(records)
if problems:
    raise SystemExit("Ingestion validation failed:\n" + "\n".join(problems))

s = get_settings()
s.cache_dir.mkdir(exist_ok=True)
with open(s.cache_dir / "chunks.jsonl", "w") as f:
    for r in records:
        f.write(json.dumps({"id": r.chunk_id, "text": r.text, "embed_text": r.embed_text, **r.payload}) + "\n")

# ---- ingestion report (evidence for the structural-parsing criterion) ----
lines = ["# Ingestion report", "", f"Total chunks: **{len(records)}** from {len(rows)} documents.", "",
         "| Collection | Document | Chunks | text | table | code | heading |", "|---|---|---|---|---|---|---|"]
for collection, name, recs in rows:
    c = collections.Counter(r.payload["chunk_type"] for r in recs)
    lines.append(f"| {collection} | {name} | {len(recs)} | {c['text']} | {c['table']} | {c['code']} | {c['heading']} |")
sample_text = next(r for r in records if r.payload["chunk_type"] == "text" and len(r.payload["heading_path"]) > 1)
sample_table = next(r for r in records if r.payload["source_document"] == "equipment_manual.pdf" and r.payload["chunk_type"] == "table")
for title, r in (("Sample text chunk (heading path carried into the embedded text)", sample_text),
                 ("Sample table chunk (Markdown table, device name carried in the path)", sample_table)):
    meta = {k: v for k, v in r.payload.items() if k not in ("text", "embed_text")}
    lines += ["", f"## {title}", "", "Embedded text:", "```", r.embed_text, "```", "Metadata:", "```json", json.dumps(meta, indent=2), "```"]
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "ingestion_report.md").write_text("\n".join(lines) + "\n")
print(f"Chunked {len(records)} chunks from {len(rows)} documents -> docs/ingestion_report.md")

if not args.no_index:
    from medibot.ingestion.index import index_records
    from medibot.retrieval.store import make_client
    client = make_client()
    n = index_records(client, records, recreate=True)
    where = s.qdrant_url or s.qdrant_path or str(s.cache_dir / "qdrant_local")
    client.close()
    print(f"Indexed {n} points into '{s.collection_name}' at {where}")
