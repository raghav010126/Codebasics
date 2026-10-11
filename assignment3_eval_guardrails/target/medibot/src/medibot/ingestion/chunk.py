"""Hierarchical + token-aware chunking of Docling documents.

Pass 1 (structure): Docling's HierarchicalChunker splits along the document tree
(section -> subsection -> paragraph / table / list).
Pass 2 (size): HybridChunker splits oversized chunks by token count (tables are split
by row with the header repeated) and merges undersized siblings that share headings.

Tables are serialised as Markdown (not Docling's default "col = value" triplets) so
the LLM sees a real table. Each chunk's embedded text carries its heading path.
"""

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from docling_core.transforms.chunker.hierarchical_chunker import (
    ChunkingDocSerializer,
    ChunkingSerializerProvider,
)
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from docling_core.transforms.serializer.markdown import MarkdownTableSerializer
from docling_core.types.doc import DocItemLabel, DoclingDocument, SectionHeaderItem, TitleItem
from transformers import AutoTokenizer

from medibot.config import get_settings
from medibot.rbac import COLLECTION_ROLES

MAX_EMBED_TOKENS = 512  # bge-small context limit

# Running headers/footers repeated on every PDF page. Docling labels most as furniture;
# this is a belt-and-braces scrub (and an assertion target).
NOISE_RE = re.compile(
    r"^\s*(CONFIDENTIAL\s*[—-]\s*Internal use only.*|Page\s+\d+\s+of\s+\d+|MediAssist Health Network\s*)$",
    re.IGNORECASE | re.MULTILINE,
)
NOISE_CHECK_RE = re.compile(r"CONFIDENTIAL\s*[—-]\s*Internal use only|Page\s+\d+\s+of\s+\d+", re.IGNORECASE)
DOC_REF_RE = re.compile(r"Document ref:\s*([A-Za-z0-9\-]+)")


class MarkdownTableProvider(ChunkingSerializerProvider):
    """Chunking serializer that renders tables as Markdown."""

    def get_serializer(self, doc: DoclingDocument) -> ChunkingDocSerializer:
        return ChunkingDocSerializer(doc=doc, table_serializer=MarkdownTableSerializer())


@dataclass
class ChunkRecord:
    chunk_id: str
    text: str  # raw chunk body (what the LLM reads)
    embed_text: str  # heading-path + body (what is embedded / reranked)
    payload: dict = field(default_factory=dict)  # Qdrant payload (metadata)


_tokenizer = None
_chunker = None


def get_tokenizer() -> HuggingFaceTokenizer:
    global _tokenizer
    if _tokenizer is None:
        s = get_settings()
        _tokenizer = HuggingFaceTokenizer(
            tokenizer=AutoTokenizer.from_pretrained(s.dense_model),
            max_tokens=s.chunk_max_tokens,
        )
    return _tokenizer


def get_chunker() -> HybridChunker:
    global _chunker
    if _chunker is None:
        _chunker = HybridChunker(
            tokenizer=get_tokenizer(),
            merge_peers=True,
            repeat_table_header=True,
            serializer_provider=MarkdownTableProvider(),
        )
    return _chunker


def count_tokens(text: str) -> int:
    return get_tokenizer().count_tokens(text)


def _doc_title(doc: DoclingDocument, fallback: str) -> str:
    for item, _ in doc.iterate_items():
        if isinstance(item, (TitleItem, SectionHeaderItem)) and item.text.strip():
            return item.text.strip()
    return fallback


def _doc_ref(doc: DoclingDocument) -> str | None:
    for item, _ in doc.iterate_items():
        m = DOC_REF_RE.search(getattr(item, "text", "") or "")
        if m:
            return m.group(1)
    return None


def _chunk_type(chunk) -> str:
    labels = {d.label for d in chunk.meta.doc_items}
    if DocItemLabel.TABLE in labels:
        return "table"
    if DocItemLabel.CODE in labels:
        return "code"
    if labels and labels <= {DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE}:
        return "heading"
    return "text"


def _clean(text: str) -> str:
    text = NOISE_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_document(collection: str, path: Path, doc: DoclingDocument) -> list[ChunkRecord]:
    chunker = get_chunker()
    title = _doc_title(doc, path.stem)
    doc_ref = _doc_ref(doc)
    access_roles = list(COLLECTION_ROLES[collection])
    records: list[ChunkRecord] = []

    for chunk in chunker.chunk(dl_doc=doc):
        body = _clean(chunk.text)
        if not body:
            continue
        headings = list(chunk.meta.headings or [])
        # Heading path (+ document title when the path doesn't already start with it)
        context = _clean(chunker.contextualize(chunk))
        if not headings or headings[0] != title:
            context = f"{title}\n{context}"
        pages = sorted({p.page_no for d in chunk.meta.doc_items for p in d.prov})
        idx = len(records)
        records.append(
            ChunkRecord(
                chunk_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{collection}/{path.name}#{idx}")),
                text=body,
                embed_text=context,
                payload={
                    "source_document": path.name,
                    "collection": collection,
                    "access_roles": access_roles,
                    "section_title": headings[-1] if headings else title,
                    "heading_path": headings or [title],
                    "chunk_type": _chunk_type(chunk),
                    "page_numbers": pages,
                    "doc_title": title,
                    "doc_ref": doc_ref,
                    "chunk_index": idx,
                },
            )
        )
    return records


def validate_records(records: list[ChunkRecord]) -> list[str]:
    """Return a list of ingestion-quality problems (empty list = clean)."""
    problems: list[str] = []
    required = {"source_document", "collection", "access_roles", "section_title", "chunk_type"}
    for r in records:
        missing = required - {k for k, v in r.payload.items() if v}
        if missing:
            problems.append(f"{r.payload.get('source_document')}#{r.payload.get('chunk_index')}: missing {sorted(missing)}")
        if r.payload["chunk_type"] not in {"text", "table", "heading", "code"}:
            problems.append(f"{r.chunk_id}: bad chunk_type {r.payload['chunk_type']}")
        if NOISE_CHECK_RE.search(r.embed_text):
            problems.append(f"{r.payload['source_document']}#{r.payload['chunk_index']}: page header/footer noise")
        n = count_tokens(r.embed_text)
        if n > MAX_EMBED_TOKENS:
            problems.append(f"{r.payload['source_document']}#{r.payload['chunk_index']}: {n} tokens > {MAX_EMBED_TOKENS}")
    return problems
