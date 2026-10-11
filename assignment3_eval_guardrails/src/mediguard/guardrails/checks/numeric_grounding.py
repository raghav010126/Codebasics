"""Every safety-critical number in an answer must come from what the model was shown (incident #1, hallucinated dose).

Extracted from the answer: doses (mg, mg/kg, mcg, g, units, IU, mL, mL/hr, mmol/L, ...), rupee amounts (Indian lakh
grouping or lakh/crore words), percentages, temperatures, and codes (ICD-10, EXCL-nn, fault codes E/F/S-nn,
document refs). Each must occur in the passages or SQL rows (or the question). Doses are anchored: the dose must
appear in the same table row or sentence as the drug it is attributed to, so "Warfarin 5 mg" is not "grounded" by
"Glipizide 5 mg OD".

Severity: an ungrounded dose or code blocks; an ungrounded rupee amount blocks on the document route and warns on the
SQL route (models legitimately derive totals); percentages and temperatures warn.
Usage: ground_answer(answer, contexts, question, sql_rows) -> GroundingReport; also the tier-0 output check.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache

from mediguard.guardrails.runner import CheckContext
from mediguard.guardrails.verdict import Verdict, allow, block

_NUM = r"\d+(?:[.,]\d+)*"
_UNIT = (r"mg/kg/day|mg/kg/hr|mg/kg|mcg/kg/min|mcg/kg|units/kg/hr|units/kg|units|iu|ml/kg/hr|ml/kg|ml/hr|ml/h|"
         r"mmol/l|mg/l|mg/dl|meq/l|mg|mcg|g|ml")
DOSE_RE = re.compile(rf"(?<![\w.])({_NUM}(?:\s*-\s*{_NUM})?)\s?({_UNIT})(?![a-z/])", re.IGNORECASE)
MONEY_RE = re.compile(rf"(?:₹|\brs\.?|\binr)\s?({_NUM})(?:\s?(lakh|lakhs|crore|crores)\b)?", re.IGNORECASE)
PCT_RE = re.compile(rf"(?<![\w.])({_NUM})\s?%")
TEMP_RE = re.compile(rf"(?<![\w.])({_NUM})\s?°\s?[cf]\b", re.IGNORECASE)
CODE_RES = (
    re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d{1,2})?\b"),          # ICD-10 (I21.4, J18.9)
    re.compile(r"\bEXCL-\d{2}\b", re.IGNORECASE),
    re.compile(r"\b[EFS]-\d{2}\b"),                           # device fault codes
    re.compile(r"\b[A-Z]{3,5}-[A-Za-z]{2,10}-\d{3}\b"),       # document refs
)
_STOP = {"standard", "dose", "dosage", "initial", "loading", "maintenance", "maximum", "max", "minimum", "adult",
         "adults", "paediatric", "pediatric", "children", "child", "oral", "iv", "daily", "total", "bolus", "then",
         "infusion", "renal", "tier", "note", "first", "second", "line", "target", "trough", "start", "usual",
         "recommended", "protocol", "formulary", "the", "and", "or", "with", "per", "for", "if", "dose:", "range"}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("µ", "u").replace("μ", "u")
    text = re.sub(r"(?<=\d)\s?ug\b", " mcg", text)
    return re.sub(r"[‐-―−]", "-", text)


def _dec(num: str) -> Decimal | None:
    try:
        return Decimal(num.replace(",", ""))
    except InvalidOperation:
        return None


def numbers_in(text: str) -> set[Decimal]:
    out = set()
    for m in re.finditer(_NUM, _norm(text)):
        d = _dec(m.group(0))
        if d is not None:
            out.add(d.normalize())
    return out


_SEP = re.compile(r"\|[\s:|\-]+\|?")


def _segments(contexts: list[str]) -> list[tuple[str, str]]:
    """(segment, passage) pairs. A segment is a sentence, or a table row prefixed with its header row (paediatric
    tables name the drug in the column header, not in the row)."""
    out = []
    for c in contexts:
        lines = _norm(c).splitlines()
        block = "\n".join(lines).lower()
        header = None
        for i, line in enumerate(lines):
            st = line.strip()
            if st.startswith("|"):
                if _SEP.fullmatch(st):
                    continue
                nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if _SEP.fullmatch(nxt):
                    header = st
                out.append(((f"{header} {st}" if header and header != st else st).lower(), block))
            else:
                header = None
                out += [(x.lower(), block) for x in re.split(r"(?<=[.;])\s+", line) if x.strip()]
    return out


@lru_cache
def drug_lexicon() -> frozenset[str]:
    """Capitalised names in the first column of clinical/nursing tables (drugs, including ones the corpus never doses,
    such as Warfarin in the interactions table) and right before a dose anywhere in MediBot's corpus."""
    from mediguard.target.medibot.access_index import access_index

    cap = r"[A-Z][A-Za-z\-]{3,}"
    words = set()
    for c in access_index().chunks.values():
        text = _norm(c["text"])
        for line in text.splitlines():
            if line.strip().startswith("|") and (c["collection"] in ("clinical", "nursing") or DOSE_RE.search(line)):
                cell = line.strip().strip("|").split("|")[0]
                words |= {w.lower() for w in re.findall(cap, cell)}
        for m in DOSE_RE.finditer(text):
            before = re.findall(cap, text[max(0, m.start() - 30):m.start()])
            words |= {w.lower() for w in before[-1:]}
    return frozenset(w for w in words if w not in _STOP)


@dataclass
class Claim:
    kind: str            # dose | money | percent | temperature | code
    raw: str
    values: list[Decimal]
    unit: str = ""
    line: str = ""
    grounded: bool = False
    why: str = ""


@dataclass
class GroundingReport:
    claims: list[Claim] = field(default_factory=list)

    @property
    def ungrounded(self) -> list[Claim]:
        return [c for c in self.claims if not c.grounded]


def _line_of(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end != -1 else len(text)]


def extract_claims(answer: str) -> list[Claim]:
    text = _norm(re.sub(r"\[\d+\]", " ", answer))
    claims: list[Claim] = []
    for m in DOSE_RE.finditer(text):
        vals = [d for d in (_dec(x) for x in re.split(r"\s*-\s*", m.group(1))) if d is not None]
        claims.append(Claim("dose", m.group(0), vals, m.group(2).lower(), _line_of(text, m.start())))
    for m in MONEY_RE.finditer(text):
        v = _dec(m.group(1))
        if v is None:
            continue
        mult = {"lakh": 100000, "lakhs": 100000, "crore": 10**7, "crores": 10**7}.get((m.group(2) or "").lower(), 1)
        claims.append(Claim("money", m.group(0), [v * mult], line=_line_of(text, m.start())))
    for rx, kind in ((PCT_RE, "percent"), (TEMP_RE, "temperature")):
        for m in rx.finditer(text):
            v = _dec(m.group(1))
            if v is not None:
                claims.append(Claim(kind, m.group(0), [v], line=_line_of(text, m.start())))
    for rx in CODE_RES:
        for m in rx.finditer(text):
            claims.append(Claim("code", m.group(0), [], line=_line_of(text, m.start())))
    return claims


def _dose_in(seg: str, claim: Claim) -> bool:
    nums = "\\s*-\\s*".join(re.escape(str(v)) for v in claim.values)
    return re.search(rf"(?<![\d.]){nums}\s?{re.escape(claim.unit)}(?![a-z])", seg) is not None


def _anchors(claim: Claim, question: str) -> tuple[set[str], set[str]]:
    """(drug names, other names) the dose is attributed to. Drug names (lexicon) must share the passage row/sentence
    with the dose; an unknown capitalised name written directly before the dose ("Oxycodone 10 mg", "COPD 625 mg")
    must at least occur in the same passage. Labels ("**Monitoring:** ... 15-20 mg/L") are not anchors."""
    lex = drug_lexicon()
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", claim.line)}
    drugs = {w for w in words if w in lex}
    if not drugs:
        drugs = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", question) if w.lower() in lex}
    before = claim.line[:claim.line.lower().find(claim.raw.lower())] if claim.raw.lower() in claim.line.lower() else ""
    adjacent = re.search(r"\b([A-Z][A-Za-z\-]{3,})\s*$", re.sub(r"[*_`]", "", before))   # "Oxycodone 10 mg"
    word = adjacent.group(1).lower() if adjacent else ""
    other = {word} if word and word not in _STOP and word not in lex else set()
    return drugs, other


def ground_answer(answer: str, contexts: list[str], question: str = "", sql_rows: list[list] | None = None
                  ) -> GroundingReport:
    claims = extract_claims(answer)
    pool = [*contexts, " ".join(str(v) for r in (sql_rows or []) for v in r)]
    segs = _segments(pool)
    ctx_text = _norm("\n".join(pool)).lower()
    ctx_nums = numbers_in("\n".join(pool)) | numbers_in(question)
    for c in claims:
        if c.kind == "dose":
            hits = [(seg, block) for seg, block in segs if _dose_in(seg, c)]
            if not hits:
                c.grounded, c.why = _dose_in(_norm(question).lower(), c), "dose not in passages"
                continue
            drugs, other = _anchors(c, question)
            ok_drug = not drugs or any(t in seg for seg, _ in hits for t in drugs)
            ok_other = not other or any(t in block for _, block in hits for t in other)
            c.grounded = ok_drug and ok_other
            c.why = "" if c.grounded else f"dose not attributed to {sorted(drugs | other)} in passages"
        elif c.kind == "code":
            c.grounded = c.raw.lower() in ctx_text or c.raw.lower() in question.lower()
            c.why = "" if c.grounded else "code not in passages"
        else:
            c.grounded = all(v.normalize() in ctx_nums for v in c.values)
            c.why = "" if c.grounded else f"{c.kind} not in passages"
    return GroundingReport(claims)


class NumericGrounding:
    name = "output.numeric_grounding"
    stage = "output"
    tier = 0
    timeout_s = 5.0
    required = True

    def configured(self) -> bool:
        return True

    def evaluate(self, ctx: CheckContext) -> Verdict:
        t = ctx.target
        if t is None or t.status != "ok" or not (ctx.answer or "").strip():
            return allow(self.name, "output")
        report = ground_answer(ctx.answer, t.contexts, ctx.question, t.sql_rows)
        sql = t.retrieval_type == "sql_rag"
        blocking = [c for c in report.ungrounded
                    if c.kind in ("dose", "code") or (c.kind == "money" and not sql)]
        warn = [f"ungrounded_{c.kind}:{c.raw}" for c in report.ungrounded if c not in blocking]
        evidence = {"claims": len(report.claims), "ungrounded": [f"{c.kind}:{c.raw} ({c.why})" for c in report.ungrounded]}
        if blocking:
            c = blocking[0]
            return block(self.name, "output", f"output.numeric.ungrounded_{c.kind}", f"{c.raw}: {c.why}",
                         evidence=evidence, warnings=warn)
        return allow(self.name, "output", warnings=warn, evidence=evidence)
