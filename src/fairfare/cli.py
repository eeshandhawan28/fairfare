from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from fairfare import tracing
from fairfare.agents.trace_reviewer import lint_trace, review_trace
from fairfare.data import load_closures, load_reference_prices
from fairfare.graph import run_audit
from fairfare.llm import LiteLLMClient, TracedLLM
from fairfare.models import TripBrief


def _print_issues(issues: list[dict[str, str]]) -> None:
    if not issues:
        print("No issues found.")
    for i in issues:
        print(f"[{i['severity'].upper()}] ({i['source']}) {i['span']}: {i['problem']}\n    -> {i['suggestion']}")


def cmd_audit(args: argparse.Namespace) -> None:
    brief = TripBrief(destination=args.destination, start=args.start, end=args.end)
    result = run_audit(LiteLLMClient(), brief, args.quote.read_text(),
                       load_reference_prices(), load_closures(), quote_file=str(args.quote))
    print(result["report"])
    print(f"Trace: {result['run_id']}  (fairfare traces show {result['run_id']})")


def cmd_traces(args: argparse.Namespace) -> None:
    if args.action == "list":
        for path in tracing.list_runs():
            s = tracing.summarize(tracing.load(str(path)))
            flag = " ERRORS" if s["errors"] else ""
            print(f"{path.stem}  spans={s['spans']} llm={s['llm_calls']} total={s['total_ms']}ms{flag}")
        return
    events = tracing.load(args.run)
    if args.action == "show":
        for e in events:
            if e["type"] == "span":
                indent = "  " * e.get("depth", 0)
                err = f" ERROR {e['error']}" if e.get("error") else ""
                print(f"{indent}{e['kind']:5} {e['name']} {e['latency_ms']}ms{err}")
            elif e["type"] in ("event", "feedback", "run_start", "run_end"):
                rest = {k: v for k, v in e.items() if k not in ("ts", "run_id", "type")}
                print(f"  {e['type']}: {rest}")
    elif args.action == "lint":
        _print_issues(lint_trace(events))
    elif args.action == "review":
        issues = lint_trace(events) + review_trace(TracedLLM(LiteLLMClient()), events)
        _print_issues(issues)
    elif args.action == "feedback":
        tracing.Tracer(run_id=events[0]["run_id"]).feedback(args.rating, args.note or "")
        print("Feedback recorded.")


def main() -> None:
    p = argparse.ArgumentParser(prog="fairfare", description="Audit travel-agent quotes.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="audit a quote text file")
    a.add_argument("quote", type=Path)
    a.add_argument("--destination", default="Kazakhstan")
    a.add_argument("--start", type=date.fromisoformat, required=True)
    a.add_argument("--end", type=date.fromisoformat, required=True)
    a.set_defaults(func=cmd_audit)

    t = sub.add_parser("traces", help="inspect and review run traces")
    t.add_argument("action", choices=["list", "show", "lint", "review", "feedback"])
    t.add_argument("run", nargs="?", help="run id (prefix ok)")
    t.add_argument("--rating", choices=["good", "bad"], default="good")
    t.add_argument("--note")
    t.set_defaults(func=cmd_traces)

    args = p.parse_args()
    if args.cmd == "traces" and args.action != "list" and not args.run:
        p.error("traces " + args.action + " needs a run id")
    args.func(args)


if __name__ == "__main__":
    main()
