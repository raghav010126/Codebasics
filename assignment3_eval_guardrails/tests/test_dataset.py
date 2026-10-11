"""The labelled data is derived from the sources: chunk references exist, numbers come from them, gold SQL holds."""

from __future__ import annotations

import re
import sqlite3

import pytest
from conftest import needs_index

from mediguard.config import get_settings
from mediguard.eval.dataset import chunk_lookup, load_controls, load_golden, load_probes

pytestmark = needs_index
GOLDEN = load_golden()


def test_counts_and_ids():
    assert len(GOLDEN) >= 15 and len({g.id for g in GOLDEN}) == len(GOLDEN)
    assert {g.category for g in GOLDEN} >= {"normal", "hard", "unanswerable", "sql", "rbac", "adversarial", "benign"}
    assert len(load_probes()) == 6 and len(load_controls()) == 8


@pytest.mark.parametrize("item", [g for g in GOLDEN if g.ref_chunks], ids=lambda g: g.id)
def test_reference_chunks_exist(item):
    for rc in item.ref_chunks:
        c = chunk_lookup()[(rc.source_document, rc.chunk_index)]
        assert rc.substring is None or rc.substring.lower() in c["text"].lower(), rc


def _nums(text):
    text = re.sub(r"\b[A-Z][A-Za-z]*[ -]\d+\b", " ", text)   # product names (BM-500, SterilPro 3000, Tier 3)
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


@pytest.mark.parametrize("item", [g for g in GOLDEN if g.reference_answer and (g.ref_chunks or g.gold_rows)],
                         ids=lambda g: g.id)
def test_reference_numbers_come_from_sources(item):
    src = " ".join(chunk_lookup()[(rc.source_document, rc.chunk_index)]["text"] for rc in item.ref_chunks)
    src += " " + " ".join(str(v) for r in (item.gold_rows or []) for v in r)
    available = _nums(src) | {n.rstrip("0").rstrip(".") for n in _nums(src) if "." in n}
    missing = {n for n in _nums(item.reference_answer) if n not in available and n.split(".")[0] not in available}
    allowed = {"10" if item.id == "S3" else "", "4" if item.id == "N2" else ""}   # derived counts / phrasing
    assert not (missing - allowed), f"{item.id}: {missing}"


@pytest.mark.parametrize("item", [g for g in GOLDEN if g.gold_sql], ids=lambda g: g.id)
def test_gold_sql(item):
    db = get_settings().medibot_dir / "data" / "db" / "mediassist.db"
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = [list(r) for r in con.execute(item.gold_sql)]
    finally:
        con.close()
    assert rows == item.gold_rows
