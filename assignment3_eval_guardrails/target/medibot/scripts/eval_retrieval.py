"""Compare dense-only vs BM25-only vs hybrid vs hybrid+rerank (no LLM calls).

Relevance label = (source_document, required substring): a retrieved chunk is relevant
if it comes from that document and contains the substring, so labels survive re-chunking.
Runs as `admin` (sees everything) so retrieval quality is measured without RBAC effects.
"""
import json
import logging

import medibot  # noqa: F401
from medibot.config import ROOT, get_settings
from medibot.retrieval.rerank import rerank
from medibot.retrieval.search import search
from medibot.retrieval.store import get_client

# (query, source_document, substring, kind)  kind: exact = codes/terms, concept = paraphrase
QUERIES = [
    ("What does fault code F-12 mean on the infusion pump?", "equipment_manual.pdf", "Drug library update required", "exact"),
    ("E-12 internal sensor failure action", "equipment_manual.pdf", "Internal sensor failure", "exact"),
    ("E-11 vacuum pump failure SterilPro", "equipment_manual.pdf", "Vacuum pump failure", "exact"),
    ("RadiPro MX-150 F-02 DR panel not detected", "equipment_manual.pdf", "DR panel not detected", "exact"),
    ("SterilPro 3000 E-01 door seal gasket", "equipment_manual.pdf", "Door seal failure", "exact"),
    ("ICD-10 I21.0 anterior wall STEMI package rate", "billing_codes.pdf", "I21.0", "exact"),
    ("J18.9 pneumonia unspecified package", "billing_codes.pdf", "J18.9", "exact"),
    ("Amoxicillin-Clavulanate standard dose", "drug_formulary.pdf", "Amoxicillin-Clavulanate", "exact"),
    ("Vancomycin trough monitoring dose", "drug_formulary.pdf", "Vancomycin", "exact"),
    ("Colistin CMO approval tier 4", "drug_formulary.pdf", "Colistin", "exact"),
    ("Glipizide second-line diabetes", "treatment_protocols.pdf", "Glipizide", "exact"),
    ("CURB-65 severity score pneumonia", "treatment_protocols.pdf", "One point each for", "exact"),
    ("Metformin 500 mg BD first-line", "treatment_protocols.pdf", "Metformin 500 mg BD", "exact"),
    ("PaCO2 pH ABG interpretation acidosis", "diagnostic_reference.pdf", "acidosis", "exact"),
    ("BM-500 monitor alarm parameter defaults", "equipment_manual.pdf", "BM-500", "exact"),
    ("claim rejection codes counter-response", "claim_submission_guide.md", "rejection", "exact"),
    ("How quickly must I request pre-auth for an emergency admission?", "claim_submission_guide.md", "6 hours", "concept"),
    ("What do I do when the infusion pump says the drug library is out of date?", "equipment_manual.pdf", "Drug library update required", "concept"),
    ("How often should a central line dressing be changed?", "icu_nursing_procedures.pdf", "72 hours", "concept"),
    ("what size cannula for a baby under 5kg", "icu_nursing_procedures.pdf", "24G", "concept"),
    ("When should I clean my hands around a patient?", "infection_control.pdf", "Before patient contact", "concept"),
    ("How many days of earned leave do nurses get?", "leave_policy.pdf", "Earned Leave", "concept"),
    ("What percentage of salary goes to provident fund?", "general_faqs.pdf", "12%", "concept"),
    ("Which drug should a diabetic patient start with?", "treatment_protocols.pdf", "Metformin", "concept"),
    ("What dose of paracetamol can a baby under 5 kg have?", "treatment_protocols.pdf", "60 mg Q6H", "concept"),
    ("Who must approve restricted antibiotics?", "drug_formulary.pdf", "CMO approval", "concept"),
]

logging.getLogger().setLevel(logging.ERROR)
client = get_client()


def relevant(hit, doc, sub):
    return hit.source_document == doc and sub.lower() in hit.text.lower()


# 1) Validate labels exist in the index
chunks = [json.loads(l) for l in open(get_settings().cache_dir / "chunks.jsonl")]
bad = [(q, d, s) for q, d, s, _ in QUERIES if not any(c["source_document"] == d and s.lower() in c["text"].lower() for c in chunks)]
if bad:
    raise SystemExit(f"Labels with no matching chunk: {bad}")

METHODS = ["dense", "sparse", "hybrid", "hybrid+rerank"]
ranks = {m: [] for m in METHODS}  # per-query rank of first relevant (None if absent in top-10)
detail = []
for q, doc, sub, kind in QUERIES:
    row = {"q": q, "kind": kind}
    cand = {m: search(q, "admin", mode=m, k=10, client=client) for m in ("dense", "sparse", "hybrid")}
    cand["hybrid+rerank"] = [r.hit for r in rerank(q, cand["hybrid"], top_k=10)]
    for m in METHODS:
        rank = next((i + 1 for i, h in enumerate(cand[m]) if relevant(h, doc, sub)), None)
        ranks[m].append(rank)
        row[m] = rank
    detail.append(row)


def metrics(rs):
    n = len(rs)
    return (
        sum(r == 1 for r in rs) / n,
        sum(r is not None and r <= 3 for r in rs) / n,
        sum(1 / r for r in rs if r) / n,
    )


def table(idx):
    out = ["| Method | Hit@1 | Hit@3 | MRR@10 |", "|---|---|---|---|"]
    for m in METHODS:
        h1, h3, mrr = metrics([ranks[m][i] for i in idx])
        out.append(f"| {m} | {h1:.0%} | {h3:.0%} | {mrr:.3f} |")
    return "\n".join(out)


allx = range(len(QUERIES))
exact = [i for i, (_, _, _, k) in enumerate(QUERIES) if k == "exact"]
concept = [i for i, (_, _, _, k) in enumerate(QUERIES) if k == "concept"]
md = [
    "# Retrieval evaluation", "",
    f"{len(QUERIES)} labelled queries ({len(exact)} exact-term/code, {len(concept)} natural-language paraphrase), run as `admin`. "
    "Relevant = right document AND chunk contains the labelled substring. Top-10 candidates per method.", "",
    "## All queries", table(allx), "", "## Exact-term / code queries", table(exact), "", "## Paraphrase queries", table(concept), "",
    "## Per-query rank of first relevant chunk (— = not in top 10)", "",
    "| Query | dense | bm25 | hybrid | hybrid+rerank |", "|---|---|---|---|---|",
]
for d in detail:
    md.append(f"| {d['q']} | " + " | ".join(str(d[m] or "—") for m in METHODS) + " |")
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "retrieval_eval.md").write_text("\n".join(md) + "\n")
print("\n".join(md[:14]))

client.close()
