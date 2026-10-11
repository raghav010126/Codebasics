"""The evaluation pipeline: one repeatable script, stages persisted under ~/.cache/mediguard/runs/<run_id>/.

  0 manifest      git SHA, MediBot commit + copy hash, dataset/threshold hashes, models, package versions
  1 smoke         index health (H0) + N7/S1/A1 through the guarded pipeline; abort on any critical failure
  2 collect       every golden item through GuardedAssistant (input guardrails -> MediBot -> output guardrails)
  3 heuristics    H1-H12 on every item + the negative controls + the fail-closed suite; abort on critical failures
  4 ragas         faithfulness, answer relevancy, context precision, context recall
  5 judge         Opus 5.5 rubric scores on the golden items + calibration probes
  6 report        gates against thresholds.yaml (+ optional baseline) -> report.md / report.html / summary.json

Usage: mediguard eval [--fresh-target | --replay-only] [--no-gate] [--baseline FILE] [--label NAME]
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from mediguard.config import get_settings
from mediguard.eval import heuristics as H
from mediguard.eval.dataset import GoldenItem, chunk_context, load_controls, load_golden, load_probes
from mediguard.observability import metrics
from mediguard.observability.events import log_event
from mediguard.observability.tracing import feedback, flush, span

SMOKE = ("N7", "S1", "A1")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=get_settings().project_root, timeout=5, check=False).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def manifest(run_id: str, model: str, bedrock: bool, openevals: bool, fault: str | None) -> dict:
    s = get_settings()
    root = s.project_root
    pkgs = {p: _version(p) for p in ("ragas", "openevals", "langsmith", "anthropic", "langchain-anthropic", "boto3")}
    return {
        "run_id": run_id, "started_at": datetime.now(UTC).isoformat(timespec="seconds"), "git_sha": _git_sha(),
        "medibot_commit": "dbe20cf", "medibot_copy_sha": _sha(root / "target" / "medibot.sha256"),
        "dataset_sha": _sha(root / "data" / "golden.jsonl"), "probes_sha": _sha(root / "data" / "judge_probes.jsonl"),
        "controls_sha": _sha(root / "data" / "heuristic_controls.jsonl"), "thresholds_sha": _sha(s.thresholds_path),
        "rules_sha": _sha(root / "config" / "rules.yaml"), "target_model": model, "judge_model": s.judge_model,
        "judge_effort": s.judge_effort, "guard_model": s.guard_model, "ragas_model": s.ragas_model,
        "bedrock_guardrail": f"{s.bedrock_guardrail_id or '-'}:{s.bedrock_guardrail_version}" if bedrock else "disabled",
        "openevals": openevals, "replay_mode": s.replay_mode, "fault": fault, "packages": pkgs,
        "python": sys.version.split()[0],
    }


def _version(pkg: str) -> str | None:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def _ask(bot, item: GoldenItem, run_id: str) -> dict:
    r = bot.handle(item.question, item.role, meta={"run_id": run_id, "item_id": item.id, "suite": "eval"})
    obs = H.from_guarded(item, r)
    return {"item": item.model_dump(), "observation": obs.to_dict(), "response": r.model_dump(mode="json")}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows))


def _heuristics_for(row: dict) -> list[dict]:
    o = H.Observation(**row["observation"])
    return [{"check": r.check, "name": H.NAMES[r.check], "severity": r.severity, "passed": r.passed,
             "evidence": r.evidence} for r in H.evaluate(o)]


def _check_budget(state: dict, rows: list[dict], m, budget: float, stage: str) -> None:
    """MEDIGUARD_BUDGET_USD: stop before an LLM-heavy stage once this run has spent more than the budget."""
    spent = round(sum((r["response"].get("metrics") or {}).get("cost_usd", 0.0) for r in rows)
                  + m.totals()["cost_usd"], 4)
    if spent > budget and not state["aborted_at"]:
        state.update(aborted_at="budget", abort_reason=[f"spent ${spent} before {stage} > MEDIGUARD_BUDGET_USD ${budget}"])


def run(*, model: str | None = None, bedrock: bool = True, openevals: bool = True, gate: bool = True,
        fault: str | None = None, label: str = "eval", baseline: Path | None = None, item_ids: list[str] | None = None,
        skip_ragas: bool = False, skip_judge: bool = False) -> Path:
    from mediguard.pipeline import GuardedAssistant
    from mediguard.target.medibot.adapter import MediBotTarget

    s = get_settings()
    if fault:
        os.environ["MEDIGUARD_FAULT"] = fault
    run_id = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{label}"
    out = s.runs_dir / run_id
    out.mkdir(parents=True, exist_ok=True)
    target = MediBotTarget(model=model)
    bot = GuardedAssistant(target, bedrock=bedrock, openevals=openevals)
    man = manifest(run_id, target.model, bedrock, openevals, fault)
    (out / "manifest.json").write_text(json.dumps(man, indent=1))
    golden = [g for g in load_golden() if not item_ids or g.id in item_ids]
    by_id = {g.id: g for g in golden}
    m, mtoken = metrics.start()
    t0 = time.perf_counter()
    state: dict[str, Any] = {"run_id": run_id, "aborted_at": None, "abort_reason": None}
    try:
        with span("eval_run", "chain", inputs={"run_id": run_id, "items": len(golden)}, metadata=man,
                  tags=["eval", f"label:{label}"]) as root:
            # 1. smoke + index health
            log_event("eval.stage", run_id=run_id, stage="smoke")
            h0 = H.index_health()
            collected: dict[str, dict] = {}
            for iid in SMOKE:
                if iid in by_id:
                    collected[iid] = _ask(bot, by_id[iid], run_id)
            smoke_fail = [] if h0.passed else [f"H0 {h0.evidence}"]
            for iid, row in collected.items():
                smoke_fail += [f"{iid} {r['check']} {r['evidence']}" for r in _heuristics_for(row)
                               if r["passed"] is False and r["severity"] == "critical"]
            if smoke_fail and gate:
                state.update(aborted_at="smoke", abort_reason=smoke_fail)
            # 2. collect
            if not state["aborted_at"]:
                log_event("eval.stage", run_id=run_id, stage="collect")
                for g in golden:
                    if g.id not in collected:
                        collected[g.id] = _ask(bot, g, run_id)
            rows = [collected[g.id] for g in golden if g.id in collected]
            _write_jsonl(out / "collected.jsonl", rows)
            # 3. heuristics (+ controls + fail-closed suite)
            log_event("eval.stage", run_id=run_id, stage="heuristics")
            from mediguard.guardrails.failclosed import run_scenarios

            heur = {"index_health": {"passed": h0.passed, "evidence": h0.evidence},
                    "items": {row["item"]["id"]: _heuristics_for(row) for row in rows},
                    "controls": H.run_controls(load_controls()), "fail_closed": run_scenarios()}
            (out / "heuristics.json").write_text(json.dumps(heur, indent=1, default=str))
            critical = [f"{iid} {r['check']}" for iid, res in heur["items"].items() for r in res
                        if r["passed"] is False and r["severity"] == "critical"]
            if critical and gate and not state["aborted_at"]:
                state.update(aborted_at="heuristics", abort_reason=critical)
            # 4. RAGAS
            _check_budget(state, rows, m, s.budget_usd, "ragas")
            if not state["aborted_at"] and not skip_ragas:
                log_event("eval.stage", run_id=run_id, stage="ragas")
                from mediguard.eval import ragas_eval

                with span("ragas", "chain", inputs={"items": len(rows)}):
                    rg = ragas_eval.score([(GoldenItem.model_validate(r["item"]), r["observation"]) for r in rows])
                (out / "ragas.json").write_text(json.dumps(rg, indent=1))
            # 5. judge
            _check_budget(state, rows, m, s.budget_usd, "judge")
            if not state["aborted_at"] and not skip_judge:
                log_event("eval.stage", run_id=run_id, stage="judge")
                (out / "judge.json").write_text(json.dumps(_judge_all(rows, target), indent=1))
            state["elapsed_s"] = round(time.perf_counter() - t0, 1)
            state["eval_metrics"] = m.totals()
            (out / "state.json").write_text(json.dumps(state, indent=1, default=str))
            # 6. report
            from mediguard.eval import report

            summary = report.build(out, baseline=baseline)
            root.meta(verdict=summary["verdict"], failed_gates=[g["name"] for g in summary["gates"] if not g["passed"]])
            feedback(root, "eval.verdict", 1.0 if summary["verdict"] == "PASS" else 0.0,
                     ", ".join(g["name"] for g in summary["gates"] if not g["passed"]))
            root.end(outputs={"verdict": summary["verdict"], "report": str(out / "report.md")})
            log_event("eval.stage", run_id=run_id, stage="done", verdict=summary["verdict"])
    finally:
        metrics.stop(mtoken)
        flush()
    return out


def _judge_all(rows: list[dict], target, workers: int = 4) -> dict:
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    from mediguard.eval.judge import build_prompt, judge

    jobs: list[tuple[str, str, object]] = []          # (kind, id, prompt)
    for row in rows:
        it, obs = row["item"], row["observation"]
        if it["expected"] == "guardrail_block":
            continue
        jobs.append(("item", it["id"], build_prompt(
            role=it["role"], collections=target.scope(it["role"]).collections, question=it["question"],
            expected=it["expected"], reference=it["reference_answer"], contexts=obs["contexts"],
            answer=obs["public_answer"] or "", route=obs["retrieval_type"] or "none")))
    probe_by_id = {p.id: p for p in load_probes()}
    for p in probe_by_id.values():
        jobs.append(("probe", p.id, build_prompt(
            role=p.role, collections=target.scope(p.role).collections, question=p.question, expected=p.expected,
            reference=p.reference_answer, contexts=[chunk_context(c) for c in p.context_chunks], answer=p.answer,
            route=p.retrieval_type)))
    contexts = [contextvars.copy_context() for _ in jobs]   # copied here, in the caller: trace parent + metrics
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda jc: jc[1].run(judge, jc[0][2], jc[0][1]), zip(jobs, contexts)))
    items, probes = {}, {}
    for (kind, jid, _prompt), res in zip(jobs, results):
        if kind == "item":
            items[jid] = res
        else:
            p = probe_by_id[jid]
            probes[jid] = {**res, "must": p.must, "caught": res["passed"] == (p.must == "pass"), "why": p.why,
                           "answer": p.answer}
    return {"items": items, "probes": probes}
