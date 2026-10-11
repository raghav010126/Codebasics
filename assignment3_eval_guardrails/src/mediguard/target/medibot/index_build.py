"""Build MediBot's hybrid Qdrant index under ~/.cache/mediguard/medibot with MediBot's own ingestion code.

MediBot's scripts/ingest.py also writes docs/ingestion_report.md into its own tree, so we call the same library
functions (parse -> chunk -> validate -> index) and write only into our cache: the copied tree stays pristine.

Usage: mediguard target build-index [--seed-from ~/Downloads/medibot/.cache]
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from mediguard.target.medibot.bootstrap import prepare


def chunks_path() -> Path:
    from mediguard.config import get_settings

    return get_settings().medibot_cache / "chunks.jsonl"


def seed_docling(seed_from: Path) -> int:
    """Copy parsed Docling JSON from another MediBot cache (skips the slow PDF conversion).

    MediBot reuses a parse only if it is newer than the source document, so copies get fresh mtimes.
    Only ``docling/*.json`` is read; another cache's Qdrant folder is never opened (that takes a lock and writes).
    """
    from mediguard.config import get_settings

    src = seed_from.expanduser() / "docling"
    dst = get_settings().medibot_cache / "docling"
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src.glob("*.json")):
        shutil.copyfile(f, dst / f.name)
        n += 1
    return n


def build_index(seed_from: Path | None = None) -> dict:
    prepare()
    from medibot.config import get_settings as medibot_settings
    from medibot.ingestion.chunk import chunk_document, validate_records
    from medibot.ingestion.index import index_records
    from medibot.ingestion.parse import build_converter, discover_documents, parse_document
    from medibot.retrieval.store import make_client

    seeded = seed_docling(seed_from) if seed_from else 0
    converter = None
    records = []
    for collection, path in discover_documents():
        cached = medibot_settings().cache_dir / "docling" / f"{path.parent.name}__{path.stem}.json"
        if converter is None and not cached.exists():
            converter = build_converter()
        records += chunk_document(collection, path, parse_document(path, converter))
    problems = validate_records(records)
    if problems:
        raise SystemExit("Ingestion validation failed:\n" + "\n".join(problems))

    out = chunks_path()
    with out.open("w") as f:
        for r in records:
            f.write(json.dumps({"id": r.chunk_id, "text": r.text, "embed_text": r.embed_text, **r.payload}) + "\n")

    client = make_client()
    try:
        n = index_records(client, records, recreate=True)
    finally:
        client.close()
    return {"chunks": len(records), "indexed": n, "seeded_docling": seeded, "chunks_file": str(out)}


def load_chunks() -> list[dict]:
    """The indexed chunks (same payloads as Qdrant), written by build_index."""
    p = chunks_path()
    if not p.exists():
        raise FileNotFoundError(f"{p} missing: run `mediguard target build-index` first")
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
