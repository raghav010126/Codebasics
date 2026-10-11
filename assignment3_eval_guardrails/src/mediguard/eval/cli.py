"""Evaluation subcommands registered on the main `mediguard` parser: eval, report, compare, baseline, redteam."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def _set_replay(args) -> None:
    from mediguard.config import get_settings

    if getattr(args, "fresh_target", False):
        os.environ["MEDIGUARD_REPLAY"] = "fresh"
    elif getattr(args, "replay_only", False):
        os.environ["MEDIGUARD_REPLAY"] = "replay_only"
    get_settings.cache_clear()


def _run_dir(name: str) -> Path:
    from mediguard.config import get_settings

    if name.startswith("latest:"):
        found = _latest(name.split(":", 1)[1])
        if found is None:
            raise SystemExit(f"no run with label {name.split(':', 1)[1]!r}")
        return found
    p = Path(name)
    return p if p.is_absolute() or p.exists() else get_settings().runs_dir / name


def _latest(label: str | None = None) -> Path | None:
    from mediguard.config import get_settings

    runs = sorted((p for p in get_settings().runs_dir.glob("*") if (p / "summary.json").exists()
                   and (label is None or p.name.endswith(f"-{label}"))), key=lambda p: p.name)
    return runs[-1] if runs else None


def cmd_eval(args) -> int:
    _set_replay(args)
    from mediguard.eval.runner import run

    baseline = Path(args.baseline) if args.baseline else None
    out = run(model=args.model, bedrock=not args.no_bedrock, openevals=not args.no_openevals, gate=not args.no_gate,
              fault=args.inject_fault, label=args.label, baseline=baseline,
              item_ids=args.items.split(",") if args.items else None, skip_ragas=args.skip_ragas,
              skip_judge=args.skip_judge)
    summary = json.loads((out / "summary.json").read_text())
    failed = [f"{g['area']}/{g['name']}" for g in summary["gates"] if not g["passed"]]
    print(f"{summary['verdict']}  {out / 'report.md'}")
    if failed:
        print("failed gates: " + ", ".join(failed))
    if args.publish:
        _publish(out, args.publish)
    return 0 if summary["verdict"] == "PASS" else 1


def _publish(run_dir: Path, name: str) -> None:
    from mediguard.config import get_settings

    dest = get_settings().project_root / "reports" / name
    dest.mkdir(parents=True, exist_ok=True)
    for f in ("report.md", "report.html", "summary.json"):
        shutil.copyfile(run_dir / f, dest / f)
    print(f"published to {dest}")


def cmd_report(args) -> int:
    from mediguard.eval.report import build

    run_dir = _run_dir(args.run) if args.run else _latest()
    s = build(run_dir, baseline=Path(args.baseline) if args.baseline else None,
              previous=_run_dir(args.previous) if args.previous else None)
    print(f"{s['verdict']}  {run_dir / 'report.md'}")
    if args.publish:
        _publish(run_dir, args.publish)
    return 0 if s["verdict"] == "PASS" else 1


def cmd_compare(args) -> int:
    from mediguard.eval.compare import compare

    res = compare(_run_dir(args.a), _run_dir(args.b), tolerance=args.tolerance)
    print(json.dumps({k: v for k, v in res.items() if k != "item_deltas"}, indent=1))
    return 0 if res["within_tolerance"] else 1


def cmd_baseline(args) -> int:
    from mediguard.config import get_settings

    run_dir = _run_dir(args.run) if args.run else _latest()
    dest = get_settings().project_root / "baselines" / "baseline.json"
    dest.parent.mkdir(exist_ok=True)
    s = json.loads((run_dir / "summary.json").read_text())
    keep = {k: s[k] for k in ("run_id", "manifest", "ragas", "judge", "items", "verdict") if k in s}
    for v in (keep.get("judge") or {}).get("items", {}).values():
        v.pop("scores", None)
    dest.write_text(json.dumps(keep, indent=1, default=str))
    print(f"baseline <- {run_dir.name} ({s['verdict']}) at {dest}")
    return 0


def cmd_redteam(args) -> int:
    _set_replay(args)
    from mediguard.eval.redteam import run

    out = run(bedrock=not args.no_bedrock, openevals=not args.no_openevals, break_bedrock=args.break_bedrock)
    print(out)
    return 0


def register(sub, guard_flags) -> None:
    e = sub.add_parser("eval", help="run the evaluation pipeline and write a PASS/FAIL report")
    guard_flags(e)
    e.add_argument("--fresh-target", action="store_true", help="re-call the target model (ignore recorded responses)")
    e.add_argument("--replay-only", action="store_true", help="fail if any target call is not recorded")
    e.add_argument("--no-gate", action="store_true", help="keep going after a failed fail-fast gate")
    e.add_argument("--baseline", help="baseline summary for the regression gates (baselines/baseline.json)")
    e.add_argument("--inject-fault", choices=["drop_top_chunk", "perturb_dose"], help="deliberately degrade the target")
    e.add_argument("--items", help="comma-separated item ids (default: all)")
    e.add_argument("--label", default="eval")
    e.add_argument("--skip-ragas", action="store_true")
    e.add_argument("--skip-judge", action="store_true")
    e.add_argument("--publish", metavar="NAME", help="copy report to reports/NAME/")
    e.set_defaults(func=cmd_eval)

    r = sub.add_parser("report", help="(re)build the report for a run")
    r.add_argument("run", nargs="?", help="run id or directory (default: latest)")
    r.add_argument("--baseline")
    r.add_argument("--previous", help="earlier run to compare against (repeatability section)")
    r.add_argument("--publish", metavar="NAME")
    r.set_defaults(func=cmd_report)

    c = sub.add_parser("compare", help="compare two runs (repeatability / drift)")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--tolerance", type=float, default=0.03)
    c.set_defaults(func=cmd_compare)

    b = sub.add_parser("baseline", help="save a run's summary as baselines/baseline.json")
    b.add_argument("run", nargs="?")
    b.set_defaults(func=cmd_baseline)

    rt = sub.add_parser("redteam", help="adversarial guardrail suite -> reports/guardrail_redteam.md")
    guard_flags(rt)
    rt.add_argument("--break-bedrock", action="store_true", help="also run once with a bogus guardrail id (live fail-closed)")
    rt.add_argument("--fresh-target", action="store_true")
    rt.set_defaults(func=cmd_redteam)
