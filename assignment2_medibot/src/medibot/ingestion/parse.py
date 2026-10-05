"""Structure-aware parsing of PDFs and Markdown with Docling.

Digital PDFs: OCR is off, table structure is on, and heading levels are inferred
(Docling otherwise flattens every PDF heading to level 1, which would drop the parent
section from repeated sub-headings like "Fault codes").
"""

import logging
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    HeadingHierarchyOptions,
    PdfPipelineOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DoclingDocument

from medibot.config import get_settings
from medibot.rbac import COLLECTIONS

log = logging.getLogger(__name__)

SUPPORTED = {".pdf", ".md"}


def build_converter() -> DocumentConverter:
    opts = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=True,
        generate_parsed_pages=True,  # needed for font-style heading inference
        heading_hierarchy_options=HeadingHierarchyOptions(enabled=True),
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def discover_documents(data_dir: Path | None = None) -> list[tuple[str, Path]]:
    """(collection, path) for every supported document; collection = parent folder."""
    data_dir = data_dir or get_settings().data_dir
    found: list[tuple[str, Path]] = []
    for collection in COLLECTIONS:
        for path in sorted((data_dir / collection).glob("*")):
            if path.suffix.lower() in SUPPORTED:
                found.append((collection, path))
    return found


def _cache_path(path: Path) -> Path:
    cache = get_settings().cache_dir / "docling"
    cache.mkdir(parents=True, exist_ok=True)
    return cache / f"{path.parent.name}__{path.stem}.json"


def parse_document(
    path: Path, converter: DocumentConverter | None = None, use_cache: bool = True
) -> DoclingDocument:
    cache = _cache_path(path)
    if use_cache and cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
        return DoclingDocument.load_from_json(cache)
    converter = converter or build_converter()
    log.info("Converting %s", path.name)
    doc = converter.convert(path).document
    doc.save_as_json(cache)
    return doc
