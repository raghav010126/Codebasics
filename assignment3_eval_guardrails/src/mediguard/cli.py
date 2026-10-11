"""mediguard command line.

Usage:
  mediguard target build-index [--seed-from PATH]
  mediguard ask --role doctor "What is the vancomycin dose?" [--json] [--explain] [--exhaustive]
  mediguard logs [--request ID | --blocks | --decisions | --latency | --tokens | --recent | --sql QUERY]
  mediguard guardrail-setup
  mediguard doctor
  mediguard redteam | eval | report | compare | baseline   (see --help of each)
Flags --no-bedrock / --no-openevals run a degraded configuration (recorded as such in traces and reports).
"""

from __future__ import annotations

import argparse
import json
import sys


def _guarded(args, mode="short_circuit"):
    from mediguard.pipeline import GuardedAssistant
    from mediguard.target.medibot.adapter import MediBotTarget

    return GuardedAssistant(MediBotTarget(model=getattr(args, "model", None)), mode=mode,
                            bedrock=not args.no_bedrock, openevals=not args.no_openevals)


def cmd_build_index(args) -> int:
    from pathlib import Path

    from mediguard.target.medibot.index_build import build_index

    print(json.dumps(build_index(Path(args.seed_from) if args.seed_from else None), indent=1))
    return 0


def cmd_ask(args) -> int:
    from mediguard.observability.tracing import flush

    bot = _guarded(args, "exhaustive" if args.exhaustive else "short_circuit")
    r = bot.handle(args.question, args.role)
    flush()
    if args.json:
        print(json.dumps(r.public(), indent=1, ensure_ascii=False))
    else:
        print(r.public_answer)
        if r.public_sources:
            print("\nSources: " + "; ".join(f"{s['source_document']} > {s['section_title']}" for s in r.public_sources))
    if args.explain:  # internal view for operators: never shown to end users
        print("\n--- internal (operator view) ---", file=sys.stderr)
        for stage in (r.input, r.output):
            if stage is None:
                continue
            for v in stage.verdicts:
                print(f"{stage.stage:6s} {v.check:24s} {v.decision:5s} {v.reason_code}"
                      f"{' (fail closed)' if v.fail_closed else ''}", file=sys.stderr)
        if r.target:
            print(f"target status={r.target.status} flags={r.target.flags} route={r.target.route}", file=sys.stderr)
        print(f"status={r.status} request_id={r.request_id} latency={r.latency_ms}ms tokens_in={r.metrics['tokens_in']}"
              f" tokens_out={r.metrics['tokens_out']} cost=${r.metrics['cost_usd']}", file=sys.stderr)
        print(f"trace: {r.run_url}", file=sys.stderr)
    return 0


def cmd_logs(args) -> int:
    from mediguard.observability import query

    if args.request:
        for e in query.request_timeline(args.request):
            print(json.dumps(e, ensure_ascii=False))
        return 0
    name = next((k for k in ("blocks", "decisions", "latency", "tokens", "recent") if getattr(args, k)), "recent")
    cols, rows = query.run(args.sql) if args.sql else query.run(query.QUERIES[name])
    print(query.format_table(cols, rows))
    return 0


def cmd_guardrail_setup(args) -> int:
    from mediguard.guardrails.bedrock_setup import create_or_update

    print(json.dumps(create_or_update(publish=not args.draft), indent=1))
    return 0


def cmd_doctor(args) -> int:
    from mediguard.doctor import run

    return run()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mediguard", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def guard_flags(sp):
        sp.add_argument("--no-bedrock", action="store_true", help="degraded: skip Bedrock Guardrails")
        sp.add_argument("--no-openevals", action="store_true", help="degraded: skip OpenEvals judges")
        sp.add_argument("--model", default=None, help="target model override (default MEDIGUARD_TARGET_MODEL)")

    t = sub.add_parser("target", help="target chatbot utilities")
    tsub = t.add_subparsers(dest="tcmd", required=True)
    b = tsub.add_parser("build-index", help="build MediBot's Qdrant index under ~/.cache/mediguard/medibot")
    b.add_argument("--seed-from", default=None, help="another MediBot .cache to reuse parsed Docling JSON from")
    b.set_defaults(func=cmd_build_index)

    a = sub.add_parser("ask", help="ask one question through the guarded pipeline")
    a.add_argument("question")
    a.add_argument("--role", required=True, choices=["doctor", "nurse", "billing_executive", "technician", "admin"])
    a.add_argument("--json", action="store_true", help="print the user-facing JSON only")
    a.add_argument("--explain", action="store_true", help="also print the internal verdicts (stderr)")
    a.add_argument("--exhaustive", action="store_true", help="run every check even after a block")
    guard_flags(a)
    a.set_defaults(func=cmd_ask)

    lg = sub.add_parser("logs", help="query the structured event log")
    lg.add_argument("--request", help="every event of one request id")
    for k in ("blocks", "decisions", "latency", "tokens", "recent"):
        lg.add_argument(f"--{k}", action="store_true")
    lg.add_argument("--sql", help="free-form DuckDB SQL over the `events` view")
    lg.set_defaults(func=cmd_logs)

    gs = sub.add_parser("guardrail-setup", help="create/update the Bedrock guardrail from config/guardrail_policy.yaml")
    gs.add_argument("--draft", action="store_true", help="do not publish a new version")
    gs.set_defaults(func=cmd_guardrail_setup)

    d = sub.add_parser("doctor", help="check credentials, index, guardrail and isolation")
    d.set_defaults(func=cmd_doctor)

    from mediguard.eval import cli as eval_cli

    eval_cli.register(sub, guard_flags)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
