"""Output check: restricted content must never reach a role that cannot read it (incident #2, billing leak).

(a) MediBot's red-team markers for the role's restricted collections
(b) restricted-only codes/names (in some unreadable chunk, in no readable chunk, not in the question)
(c) verbatim copying: word 6-gram overlap with unreadable chunks (2+ blocks, 1 warns)
(e) database identifiers: patient names and CLM-/PAT- ids without the claims table; TKT-/EQ- ids and staff names
    without maintenance_tickets; with the table, values must come from this request's SQL rows (else a warning)
(f) every cited source must be in a collection the role can read
LeakParaphrase (d) searches the answer across the whole index: a top hit the role cannot read means a likely
paraphrased leak (warning by default, block when configured).
"""

from __future__ import annotations

import re

from mediguard.guardrails.runner import CheckContext
from mediguard.guardrails.verdict import Verdict, allow, block
from mediguard.target.medibot.access_index import access_index, code_tokens, name_tokens, shingles


def _present(token: str, text: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(token.lower())}(?![\w-])", text) is not None


class LeakDetector:
    name = "output.leak"
    stage = "output"
    tier = 0
    timeout_s = 5.0
    required = True

    def configured(self) -> bool:
        return True

    def evaluate(self, ctx: CheckContext) -> Verdict:
        ans = ctx.answer or ""
        if not ans.strip():
            return allow(self.name, "output")
        idx = access_index()
        role, low, q = ctx.role, ans.lower(), (ctx.question or "").lower()
        passages = " ".join(p.embed_text for p in (ctx.target.passages if ctx.target else [])).lower()
        found: dict[str, list[str]] = {}
        warnings: list[str] = []

        for coll in ctx.scope.restricted_collections:                                   # (a)
            for m in idx.markers.get(coll, []):
                if _present(m, low) and not _present(m, q) and not idx.readable_by(role, m):
                    found.setdefault("marker", []).append(m)
        candidates = code_tokens(ans) | name_tokens(ans)                                # (b)
        restricted = {t.lower() for t in idx.restricted_tokens[role]}
        for t in candidates:
            tl = t.lower()
            if tl in restricted and tl not in q and tl not in passages:
                found.setdefault("restricted_token", []).append(t)
        overlap = shingles(ans) & idx.restricted_shingles[role]                         # (c)
        if len(overlap) >= 2:
            found["verbatim"] = [" ".join(s) for s in list(overlap)[:3]]
        elif overlap:
            warnings.append("verbatim_overlap_1")

        rows = " ".join(str(v) for r in (ctx.target.sql_rows if ctx.target else []) for v in r).lower()
        has_claims, has_tickets = "claims" in ctx.scope.sql_tables, "maintenance_tickets" in ctx.scope.sql_tables
        for values, table_ok, kind in ((idx.claims_ids | idx.patient_names, has_claims, "patient_data"),   # (e)
                                       (idx.ticket_ids | idx.staff_names, has_tickets, "ticket_data")):
            for v in values:
                if not _present(v, low) or idx.readable_by(role, v):
                    continue
                if not table_ok:
                    found.setdefault(kind, []).append(v)
                elif v.lower() not in rows:
                    warnings.append(f"ungrounded_identifier:{kind}")

        allowed = set(ctx.scope.collections) | ({"database"} if ctx.scope.sql_tables else set())   # (f)
        for s in ctx.target.sources if ctx.target else []:
            if s.get("collection") not in allowed:
                found.setdefault("source_collection", []).append(f"{s.get('source_document')}:{s.get('collection')}")

        if found:
            kind = next(iter(found))
            return block(self.name, "output", f"output.leak.{kind}", f"restricted content for {role}: {found}",
                         evidence={k: v[:5] for k, v in found.items()}, warnings=warnings)
        return allow(self.name, "output", warnings=sorted(set(warnings)))


class LeakParaphrase:
    """(d) Local-model check: does the answer read like a chunk this role cannot see?"""

    name = "output.leak_paraphrase"
    stage = "output"
    tier = 1
    timeout_s = 20.0
    required = False

    def __init__(self, min_score: float = 0.8, margin: float = 0.2, enforce: bool = False):
        self.min_score, self.margin, self.enforce = min_score, margin, enforce

    def configured(self) -> bool:
        return True

    def evaluate(self, ctx: CheckContext) -> Verdict:
        if not (ctx.answer or "").strip() or ctx.role == "admin" or not ctx.target or ctx.target.status != "ok":
            return allow(self.name, "output")
        from medibot.retrieval.rerank import rerank  # unpatched: never enters the request capture
        from medibot.retrieval.search import search

        hits = rerank(ctx.answer, search(ctx.answer, "admin", k=8), top_k=5)
        if not hits:
            return allow(self.name, "output")
        top = hits[0]
        readable_scores = [h.score for h in hits if ctx.role in h.hit.payload.get("access_roles", [])]
        best_readable = max(readable_scores, default=0.0)
        evidence = {"top_chunk": top.hit.id, "top_collection": top.hit.collection, "top_score": round(top.score, 3),
                    "best_readable_score": round(best_readable, 3)}
        suspicious = (ctx.role not in top.hit.payload.get("access_roles", []) and top.score >= self.min_score
                      and best_readable < top.score - self.margin)
        if suspicious and self.enforce:
            return block(self.name, "output", "output.leak.paraphrase",
                         f"answer matches unreadable chunk {top.hit.id}", evidence=evidence)
        return allow(self.name, "output", warnings=["possible_paraphrase_leak"] if suspicious else [],
                     evidence=evidence)
