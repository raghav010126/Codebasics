"""Compare two evaluation runs: repeatability (same system twice) or drift (baseline vs a changed system).

Usage: mediguard compare RUN_A RUN_B
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from mediguard.eval.ragas_eval import METRICS


def _answer(text: str | None) -> str | None:
    """Answer text without per-request ids (refusals carry a fresh request id every run)."""
    return re.sub(r"req_[0-9a-z]+", "req_…", text) if text else text


def _summary(run: Path) -> dict:
    return json.loads((run / "summary.json").read_text())


def _heur(run: Path) -> dict:
    p = run / "heuristics.json"
    return json.loads(p.read_text())["items"] if p.exists() else {}


def compare(a: Path, b: Path, tolerance: float = 0.03) -> dict:
    sa, sb = _summary(a), _summary(b)
    ra, rb = (sa.get("ragas") or {}).get("per_item", {}), (sb.get("ragas") or {}).get("per_item", {})
    metric_deltas, item_deltas = [], []
    for m in METRICS:
        ma = ((sa.get("ragas") or {}).get("aggregates", {}).get(m) or {}).get("mean")
        mb = ((sb.get("ragas") or {}).get("aggregates", {}).get(m) or {}).get("mean")
        if ma is not None and mb is not None:
            metric_deltas.append({"metric": m, "a": ma, "b": mb, "delta": round(mb - ma, 4)})
        for iid in sorted(set(ra) & set(rb)):
            x, y = ra[iid].get(m), rb[iid].get(m)
            if isinstance(x, float) and isinstance(y, float) and abs(x - y) > 1e-9:
                item_deltas.append({"id": iid, "metric": m, "a": x, "b": y, "delta": round(y - x, 4)})
    ja, jb = (sa.get("judge") or {}), (sb.get("judge") or {})
    for key in ("pass_rate",):
        if ja.get(key) is not None and jb.get(key) is not None:
            metric_deltas.append({"metric": f"judge_{key}", "a": ja[key], "b": jb[key], "delta": round(jb[key] - ja[key], 4)})
    oa, ob = (ja.get("means") or {}).get("overall"), (jb.get("means") or {}).get("overall")
    if oa is not None and ob is not None:
        metric_deltas.append({"metric": "judge_mean_overall", "a": oa, "b": ob, "delta": round(ob - oa, 4)})
    ha, hb = _heur(a), _heur(b)
    disagreements = []
    for iid in sorted(set(ha) & set(hb)):
        da = {r["check"]: r["passed"] for r in ha[iid]}
        db = {r["check"]: r["passed"] for r in hb[iid]}
        disagreements += [{"id": iid, "check": c, "a": da[c], "b": db.get(c)} for c in da if da[c] != db.get(c)]
    answers_a = {i["id"]: _answer(i["answer"]) for i in sa.get("items", [])}
    changed = [i["id"] for i in sb.get("items", []) if answers_a.get(i["id"]) not in (None, _answer(i["answer"]))]
    max_abs = max((abs(d["delta"]) for d in metric_deltas), default=0.0)
    return {"a": sa["run_id"], "b": sb["run_id"], "metric_deltas": metric_deltas, "item_deltas": item_deltas,
            "heuristic_disagreements": disagreements, "answers_changed": changed, "max_abs_delta": round(max_abs, 4),
            "identical": not item_deltas and not disagreements and not changed and max_abs == 0.0,
            "within_tolerance": max_abs <= tolerance and not disagreements, "tolerance": tolerance,
            "verdicts": [sa.get("verdict"), sb.get("verdict")]}
