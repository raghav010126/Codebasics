"""Consolidate a run into gates, an overall PASS/FAIL verdict, and report.md / report.html / summary.json.

Usage: mediguard report RUN_DIR [--baseline FILE] [--previous RUN_DIR]
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from mediguard.config import load_thresholds
from mediguard.eval.heuristics import NAMES, SEVERITY
from mediguard.eval.ragas_eval import METRICS

TEMPLATES = Path(__file__).parent / "templates"


def _load(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    vs = sorted(values)
    k = (len(vs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(vs) - 1)
    return round(vs[lo] + (vs[hi] - vs[lo]) * (k - lo), 1)


def gate(area: str, name: str, value: Any, threshold: Any, passed: bool, detail: str = "") -> dict:
    return {"area": area, "name": name, "value": value, "threshold": threshold, "passed": bool(passed), "detail": detail}


def _all_gate(area: str, name: str, value: Any, ok: bool, required: bool, detail: str = "") -> dict:
    """An "every case must hold" gate; thresholds.yaml can make it informational (required: false)."""
    return gate(area, name, value, "all" if required else "informational", ok or not required, detail)


def guardrail_section(rows: list[dict], heur: dict | None) -> dict:
    counts: dict[str, dict[str, Counter]] = {"input": defaultdict(Counter), "output": defaultdict(Counter)}
    statuses, reasons = Counter(), Counter()
    adversarial, benign_blocks, example = [], [], None
    for row in rows:
        r, it = row["response"], row["item"]
        statuses[r["status"]] += 1
        for stage in ("input", "output"):
            d = r.get(stage)
            if not d:
                continue
            for v in d["verdicts"]:
                counts[stage][v["check"]][v["decision"]] += 1
            if d.get("blocking"):
                reasons[d["blocking"]["reason_code"]] += 1
        blocked = r["status"] in ("blocked_input", "blocked_output")
        stage = (r.get("input") or {}).get("blocking") and "input" or ((r.get("output") or {}).get("blocking") and "output")
        if it["expected"] == "guardrail_block":
            ok = blocked and (it["expected_block_stage"] in (None, stage))
            adversarial.append({"id": it["id"], "role": it["role"], "question": it["question"], "blocked": blocked,
                                "stage": stage, "ok": ok,
                                "reason": ((r.get(stage or "input") or {}).get("blocking") or {}).get("reason_code"),
                                "verdicts": [(v["check"], v["decision"], v["reason_code"]) for v in
                                             (r.get("input") or {}).get("verdicts", [])],
                                "user_message": r["public_answer"], "run_url": r.get("run_url")})
            if ok and example is None:
                example = adversarial[-1]
        elif blocked:
            benign_blocks.append({"id": it["id"], "stage": stage,
                                  "reason": ((r.get(stage) or {}).get("blocking") or {}).get("reason_code")})
    non_adv = [row for row in rows if row["item"]["expected"] != "guardrail_block"]
    fc = (heur or {}).get("fail_closed", [])
    return {"counts": {s: {c: dict(v) for c, v in d.items()} for s, d in counts.items()}, "statuses": dict(statuses),
            "reasons": dict(reasons.most_common()), "adversarial": adversarial,
            "adversarial_block_rate": (sum(a["ok"] for a in adversarial) / len(adversarial)) if adversarial else None,
            "benign_false_blocks": benign_blocks,
            "benign_false_block_rate": (len(benign_blocks) / len(non_adv)) if non_adv else None,
            "fail_closed": fc, "fail_closed_ok": bool(fc) and all(x["ok"] for x in fc), "example_block": example}


def heuristic_section(heur: dict | None) -> dict:
    if not heur:
        return {}
    by: dict[str, Counter] = defaultdict(Counter)
    failures = []
    for iid, res in heur["items"].items():
        for r in res:
            key = "na" if r["passed"] is None else ("pass" if r["passed"] else "fail")
            by[r["check"]][key] += 1
            if r["passed"] is False:
                failures.append({"id": iid, **r})
    rate = {}
    for sev in ("critical", "standard"):
        applicable = [r for res in heur["items"].values() for r in res if r["severity"] == sev and r["passed"] is not None]
        rate[sev] = (sum(r["passed"] for r in applicable) / len(applicable)) if applicable else None
    controls = heur.get("controls", [])
    example = next((c for c in controls if c["id"] == "HC7"), controls[0] if controls else None)
    return {"by_check": {k: {"name": NAMES[k], "severity": SEVERITY[k], **dict(v)} for k, v in sorted(by.items())},
            "failures": failures, "rate": rate, "controls": controls,
            "controls_ok": bool(controls) and all(c["ok"] for c in controls), "example_failure": example,
            "index_health": heur.get("index_health")}


def judge_section(judge: dict | None) -> dict:
    if not judge:
        return {}
    items = judge["items"]
    scored = {k: v for k, v in items.items() if "scores" in v}
    crit = ("accuracy", "completeness", "refusal_appropriateness", "citation_correctness")
    means = {c: round(statistics.mean(v["scores"][c]["score"] for v in scored.values()
                                      if not (c == "citation_correctness" and v["scores"][c]["score"] == 0)), 2)
             if scored else None for c in crit}
    means["overall"] = round(statistics.mean(v["scores"]["overall"] for v in scored.values()), 2) if scored else None
    probes = judge.get("probes", {})
    return {"means": means, "pass_rate": (sum(v["passed"] for v in items.values()) / len(items)) if items else None,
            "items": items, "probes": probes, "errors": [k for k, v in items.items() if "error" in v],
            "calibration_ok": bool(probes) and all(p["caught"] for p in probes.values()),
            "fallbacks": [k for k, v in {**items, **probes}.items() if v.get("judge_fallback")]}


def performance_section(rows: list[dict], state: dict | None) -> dict:
    lat = [r["response"]["recorded_latency_ms"] for r in rows if r["response"].get("target")]
    wall = [r["response"]["latency_ms"] for r in rows]
    comp: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        for c, v in r["response"]["metrics"].get("by_component", {}).items():
            comp[c].update(v)
    tok_in = sum(r["response"]["metrics"]["tokens_in"] for r in rows)
    tok_out = sum(r["response"]["metrics"]["tokens_out"] for r in rows)
    em = (state or {}).get("eval_metrics", {}) or {}
    return {"requests": len(rows), "recorded_p50_ms": _pct(lat, 0.5), "recorded_p95_ms": _pct(lat, 0.95),
            "wall_p50_ms": _pct(wall, 0.5), "wall_p95_ms": _pct(wall, 0.95), "tokens_in": tok_in, "tokens_out": tok_out,
            "request_cost_usd": round(sum(r["response"]["metrics"]["cost_usd"] for r in rows), 4),
            "request_cold_cost_usd": round(sum(r["response"]["metrics"]["cold_cost_usd"] for r in rows), 4),
            "by_component": {k: {kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items()}
                             for k, v in comp.items()},
            "eval_cost_usd": round(em.get("cost_usd", 0.0), 4), "eval_cold_cost_usd": round(em.get("cold_cost_usd", 0.0), 4),
            "elapsed_s": (state or {}).get("elapsed_s")}


def regression_section(summary: dict, baseline: dict, th: dict) -> dict:
    drops = th["regression"]["max_drop"]
    cur, base = summary["ragas"].get("aggregates", {}), baseline.get("ragas", {}).get("aggregates", {})
    out = {"baseline_run": baseline.get("run_id"), "baseline_model": baseline.get("manifest", {}).get("target_model"),
           "metrics": []}
    for m in METRICS:
        c, b = (cur.get(m) or {}).get("mean"), (base.get(m) or {}).get("mean")
        if c is not None and b is not None:
            out["metrics"].append({"metric": m, "baseline": b, "current": c, "delta": round(c - b, 4),
                                   "max_drop": drops[m], "passed": (b - c) <= drops[m]})
    cj, bj = summary.get("judge") or {}, baseline.get("judge") or {}
    judge_pairs = {"judge_mean_overall": ((cj.get("means") or {}).get("overall"), (bj.get("means") or {}).get("overall")),
                   "judge_pass_rate": (cj.get("pass_rate"), bj.get("pass_rate"))}
    for key, (c, b) in judge_pairs.items():
        if c is not None and b is not None:
            out["metrics"].append({"metric": key, "baseline": round(b, 4), "current": round(c, 4),
                                   "delta": round(c - b, 4), "max_drop": drops[key], "passed": (b - c) <= drops[key]})
    cur_items = {r["id"]: r for r in summary["items"]}
    out["flipped"] = [{"id": r["id"], "baseline": r["judge_passed"], "current": cur_items[r["id"]]["judge_passed"]}
                      for r in baseline.get("items", []) if r["id"] in cur_items
                      and r.get("judge_passed") is not None and r["judge_passed"] != cur_items[r["id"]]["judge_passed"]]
    return out


def build(run_dir: Path, baseline: Path | None = None, previous: Path | None = None) -> dict:
    th = load_thresholds()
    man = _load(run_dir / "manifest.json") or {}
    state = _load(run_dir / "state.json") or {}
    rows = _jsonl(run_dir / "collected.jsonl")
    heur = _load(run_dir / "heuristics.json")
    rg = _load(run_dir / "ragas.json") or {}
    jd = _load(run_dir / "judge.json")

    G = guardrail_section(rows, heur)
    Hs = heuristic_section(heur)
    J = judge_section(jd)
    P = performance_section(rows, state)
    items = []
    for row in rows:
        it, r, obs = row["item"], row["response"], row["observation"]
        hres = (heur or {}).get("items", {}).get(it["id"], [])
        j = (J.get("items") or {}).get(it["id"], {})
        items.append({"id": it["id"], "category": it["category"], "role": it["role"], "question": it["question"],
                      "expected": it["expected"], "status": r["status"], "target_status": obs["target_status"],
                      "route": obs["retrieval_type"], "answer": r["public_answer"], "reason": obs["reason_code"],
                      "latency_ms": r["recorded_latency_ms"], "run_url": r.get("run_url"),
                      "heuristics_failed": [h["check"] for h in hres if h["passed"] is False],
                      "ragas": (rg.get("per_item") or {}).get(it["id"], {}),
                      "judge_passed": j.get("passed"), "judge": j.get("scores"), "judge_error": j.get("error")})

    gates = []
    hi, ih = Hs.get("index_health") or {}, th["heuristics"]["index_health"]
    gates.append(gate("heuristics", "index health (H0)", hi.get("evidence"),
                      f"{ih['chunks']} chunks, hit@3 >= {ih['min_hit_at_3']:.0%}", bool(hi.get("passed"))))
    gates.append(gate("pipeline", "all stages ran", state.get("aborted_at") or "complete", "complete",
                      not state.get("aborted_at"), "; ".join(state.get("abort_reason") or [])[:300]))
    if Hs:
        cr, sr = Hs["rate"]["critical"], Hs["rate"]["standard"]
        crit_fail = [f"{f['id']}:{f['check']}" for f in Hs["failures"] if f["severity"] == "critical"]
        std_fail = [f"{f['id']}:{f['check']}" for f in Hs["failures"] if f["severity"] == "standard"]
        gates.append(gate("heuristics", "critical checks pass rate", _r(cr), th["heuristics"]["critical_pass_rate"],
                          cr is not None and cr >= th["heuristics"]["critical_pass_rate"], ", ".join(crit_fail)))
        gates.append(gate("heuristics", "standard checks pass rate", _r(sr), th["heuristics"]["standard_pass_rate"],
                          sr is not None and sr >= th["heuristics"]["standard_pass_rate"], ", ".join(std_fail)))
        gates.append(_all_gate("heuristics", "negative controls caught",
                               f"{sum(c['ok'] for c in Hs['controls'])}/{len(Hs['controls'])}", Hs["controls_ok"],
                               th["heuristics"]["controls_all_caught"]))
    p95 = P["recorded_p95_ms"]
    gates.append(gate("heuristics", "latency p95 (ms)", p95, th["heuristics"]["latency_p95_ms"],
                      p95 is not None and p95 <= th["heuristics"]["latency_p95_ms"]))
    abr, bfr = G["adversarial_block_rate"], G["benign_false_block_rate"]
    gates.append(gate("guardrails", "adversarial block rate", _r(abr), th["guardrails"]["adversarial_block_rate"],
                      abr is not None and abr >= th["guardrails"]["adversarial_block_rate"],
                      ", ".join(a["id"] for a in G["adversarial"] if not a["ok"])))
    gates.append(gate("guardrails", "benign false-block rate", _r(bfr), th["guardrails"]["benign_false_block_rate"],
                      bfr is not None and bfr <= th["guardrails"]["benign_false_block_rate"],
                      ", ".join(f"{b['id']}({b['reason']})" for b in G["benign_false_blocks"])))
    gates.append(_all_gate("guardrails", "fail-closed scenarios block",
                           f"{sum(x['ok'] for x in G['fail_closed'])}/{len(G['fail_closed'])}", G["fail_closed_ok"],
                           th["guardrails"]["fail_closed_all_block"]))
    degraded = [] if man.get("openevals", True) else ["openevals"]
    if man.get("bedrock_guardrail", "").startswith(("disabled", "-")):
        degraded.append("bedrock")
    gates.append(gate("guardrails", "full guardrail configuration", ", ".join(degraded) or "bedrock + openevals + rules",
                      "no degraded layers", not degraded or th["guardrails"]["degraded_config_allowed"]))
    agg = rg.get("aggregates") or {}
    for mname in METRICS:
        mean = (agg.get(mname) or {}).get("mean")
        gates.append(gate("ragas", mname, mean, th["ragas"][mname], mean is not None and mean >= th["ragas"][mname],
                          _low_items(rg, mname, th["ragas"][mname])))
    nan_n = sum((agg.get(mn) or {}).get("nan", 0) for mn in METRICS)
    tot_n = sum((agg.get(mn) or {}).get("n", 0) for mn in METRICS)
    nan_rate = nan_n / tot_n if tot_n else None
    gates.append(gate("ragas", "NaN rate", _r(nan_rate), th["ragas"]["max_nan_rate"],
                      nan_rate is not None and nan_rate <= th["ragas"]["max_nan_rate"]))
    gates.append(gate("judge", "pass rate", _r(J.get("pass_rate")), th["judge"]["pass_rate"],
                      J.get("pass_rate") is not None and J["pass_rate"] >= th["judge"]["pass_rate"],
                      ", ".join(k for k, v in (J.get("items") or {}).items() if not v["passed"])))
    mo = (J.get("means") or {}).get("overall")
    gates.append(gate("judge", "mean overall score", mo, th["judge"]["mean_overall"],
                      mo is not None and mo >= th["judge"]["mean_overall"]))
    gates.append(_all_gate("judge", "calibration probes caught",
                           f"{sum(p['caught'] for p in (J.get('probes') or {}).values())}/{len(J.get('probes') or {})}",
                           bool(J.get("calibration_ok")), th["judge"]["calibration_all_caught"],
                           ", ".join(k for k, p in (J.get("probes") or {}).items() if not p["caught"])))

    summary = {"run_id": man.get("run_id", run_dir.name), "manifest": man, "state": state, "guardrails": G,
               "heuristics": Hs, "ragas": rg, "judge": J, "performance": P, "items": items}
    if baseline and Path(baseline).exists():
        R = regression_section(summary, json.loads(Path(baseline).read_text()), th)
        summary["regression"] = R
        for x in R["metrics"]:
            gates.append(gate("regression", f"{x['metric']} vs baseline", x["delta"], f">= -{x['max_drop']}",
                              x["passed"], f"baseline {x['baseline']} -> {x['current']}"))
    if previous and Path(previous).exists():
        from mediguard.eval.compare import compare

        summary["consistency"] = compare(Path(previous), run_dir)
    if state.get("aborted_at"):   # stages after the fail-fast gate never ran: say so instead of "n/a"
        for g in gates:
            if g["area"] in ("ragas", "judge") and g["value"] in (None, "0/0"):
                g["value"] = "not run"
                g["detail"] = f"pipeline stopped at the {state['aborted_at']} gate (fail fast)"
    summary["gates"] = gates
    summary["verdict"] = "PASS" if all(g["passed"] for g in gates) else "FAIL"
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]), trim_blocks=True,
                      lstrip_blocks=True)
    env.filters["fmt"] = _fmt
    for name in ("report.md", "report.html"):
        (run_dir / name).write_text(env.get_template(f"{name}.j2").render(s=summary, th=th))
    return summary


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


def _fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.0f}"
    return str(v)


def _low_items(rg: dict, metric: str, threshold: float) -> str:
    low = [f"{k}={v[metric]}" for k, v in (rg.get("per_item") or {}).items()
           if isinstance(v.get(metric), float) and v[metric] < threshold]
    return ("below threshold: " + ", ".join(low)) if low else ""
