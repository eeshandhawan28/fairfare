from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from fairfare import tracing
from fairfare.agents.trace_reviewer import lint_trace, review_trace
from fairfare.booking.models import BookingRequest
from fairfare.booking.queue import BookingQueue
from fairfare.data import load_closures, load_reference_prices
from fairfare.graph import run_audit
from fairfare.llm import LiteLLMClient, TracedLLM
from fairfare.models import TripBrief
from fairfare.pack import html_to_pdf
from fairfare.planning.graph import run_plan
from fairfare.tools.fetch import CachedFetcher, HttpFetcher
from fairfare.tools.search import get_search


def _print_issues(issues: list[dict[str, str]]) -> None:
    if not issues:
        print("No issues found.")
    for i in issues:
        print(f"[{i['severity'].upper()}] ({i['source']}) {i['span']}: {i['problem']}\n    -> {i['suggestion']}")


def cmd_audit(args: argparse.Namespace) -> None:
    brief = TripBrief(destination=args.destination, start=args.start, end=args.end)
    live = (get_search(), CachedFetcher(HttpFetcher())) if args.live else (None, None)
    result = run_audit(LiteLLMClient(), brief, args.quote.read_text(), load_reference_prices(), load_closures(),
                       search=live[0], fetcher=live[1], quote_file=str(args.quote), live=args.live)
    print(result["report"])
    print(f"Trace: {result['run_id']}  (fairfare traces show {result['run_id']})")


def cmd_plan(args: argparse.Namespace) -> None:
    brief = TripBrief(**json.loads(args.brief.read_text()))
    result = run_plan(LiteLLMClient(), get_search(), CachedFetcher(HttpFetcher()), brief,
                      static_notices=load_closures())
    out = Path(args.out) / result["run_id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "pack.md").write_text(result["markdown"])
    (out / "pack.html").write_text(result["html"])
    (out / "pack.txt").write_text(result["whatsapp"])
    if args.pdf and html_to_pdf(result["html"], str(out / "pack.pdf")):
        print(f"PDF: {out / 'pack.pdf'}")
    print(result["markdown"])
    print(f"Saved to {out}. Trace: {result['run_id']}")


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    if args.host not in ("127.0.0.1", "localhost") and not os.getenv("FAIRFARE_API_KEY"):
        raise SystemExit("Refusing to listen on a non-local address without FAIRFARE_API_KEY set.")
    if args.demo:  # offline synthetic world: try the whole UI with no network, keys or model
        from fairfare.api.app import Deps, create_app
        from fairfare.demo import FakeExtractorLLM, demo_world

        search, fetcher = demo_world()
        print("DEMO MODE: fixture pages and a stand-in extractor; results are synthetic.")
        uvicorn.run(create_app(Deps(llm=FakeExtractorLLM(), search=search, fetcher=fetcher)),
                    host=args.host, port=args.port)
        return
    uvicorn.run("fairfare.api.app:create_app", factory=True, host=args.host, port=args.port)


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
        _print_issues(lint_trace(events) + review_trace(TracedLLM(LiteLLMClient()), events))
    elif args.action == "feedback":
        tracing.Tracer(run_id=events[0]["run_id"]).feedback(args.rating, args.note or "")
        print("Feedback recorded.")


def cmd_book(args: argparse.Namespace) -> None:
    q = BookingQueue()
    if args.action == "draft":
        req = BookingRequest(venue=args.venue, kind=args.kind, city=args.city or "", start=args.start,
                             end=args.end, party_size=args.party, channel=args.channel,
                             contact=args.contact or "", language=args.language)
        req = q.draft(req)
        print(f"{req.id}\n\n{req.message}\n\nReview it, then: fairfare book approve {req.id} --by <your name>")
    elif args.action == "list":
        for r in q.list():
            print(f"{r.id}  {r.status:10} {r.kind:10} {r.venue}")
    elif args.action == "approve":
        req = q.get(args.id)
        print(f"\n--- message to {req.venue} via {req.channel} ---\n{req.message}\n---")
        if not args.yes and input("Approve exactly this text? [y/N] ").strip().lower() != "y":
            raise SystemExit("Not approved.")
        q.approve(args.id, args.by, req.message_hash)
        print("Approved. Run: fairfare book link", args.id)
    elif args.action == "link":
        print(q.hand_off(args.id))
        print("Open the link and press send yourself. Then log the reply: fairfare book reply", args.id, "--text ...")
    elif args.action == "reply":
        r = q.record_reply(args.id, args.text)
        print(json.dumps(r.reply_summary, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(prog="fairfare", description="Audit quotes, plan verified family trips.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="audit a quote text file")
    a.add_argument("quote", type=Path)
    a.add_argument("--destination", default="Kazakhstan")
    a.add_argument("--start", type=date.fromisoformat, required=True)
    a.add_argument("--end", type=date.fromisoformat, required=True)
    a.add_argument("--live", action="store_true", help="also research closures and prices on the web")
    a.set_defaults(func=cmd_audit)

    pl = sub.add_parser("plan", help="plan a trip from a brief JSON file (needs web access)")
    pl.add_argument("brief", type=Path)
    pl.add_argument("--out", default="outputs")
    pl.add_argument("--pdf", action="store_true")
    pl.set_defaults(func=cmd_plan)

    sv = sub.add_parser("serve", help="run the API")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--demo", action="store_true", help="offline synthetic demo, no keys or network")
    sv.set_defaults(func=cmd_serve)

    t = sub.add_parser("traces", help="inspect and review run traces")
    t.add_argument("action", choices=["list", "show", "lint", "review", "feedback"])
    t.add_argument("run", nargs="?", help="run id (prefix ok)")
    t.add_argument("--rating", choices=["good", "bad"], default="good")
    t.add_argument("--note")
    t.set_defaults(func=cmd_traces)

    b = sub.add_parser("book", help="draft, approve and hand off booking enquiries (never auto-sends)")
    b.add_argument("action", choices=["draft", "list", "approve", "link", "reply"])
    b.add_argument("id", nargs="?")
    b.add_argument("--venue"); b.add_argument("--kind", default="hotel"); b.add_argument("--city")
    b.add_argument("--start", type=date.fromisoformat); b.add_argument("--end", type=date.fromisoformat)
    b.add_argument("--party", type=int, default=4); b.add_argument("--channel", default="whatsapp")
    b.add_argument("--contact"); b.add_argument("--language", default="en")
    b.add_argument("--by"); b.add_argument("--text"); b.add_argument("--yes", action="store_true")
    b.set_defaults(func=cmd_book)

    args = p.parse_args()
    if args.cmd == "traces" and args.action != "list" and not args.run:
        p.error("traces " + args.action + " needs a run id")
    if args.cmd == "book":
        need = {"draft": ("venue", "start"), "approve": ("id", "by"), "link": ("id",), "reply": ("id", "text")}
        for f in need.get(args.action, ()):
            if not getattr(args, f):
                p.error(f"book {args.action} needs --{f}" if f not in ("id",) else f"book {args.action} needs an id")
    try:
        args.func(args)
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:  # readable failure instead of a LangGraph/LiteLLM traceback
        if os.getenv("FAIRFARE_DEBUG"):
            raise
        name = type(exc).__name__
        hint = ""
        if "Connection" in name or "Connection refused" in str(exc):
            hint = ("\nHint: no LLM reachable. Start Ollama (`ollama serve`; `ollama pull qwen2.5:7b`) "
                    "or set FAIRFARE_MODEL and the provider's API key.")
        raise SystemExit(f"fairfare {args.cmd} failed: {name}: {str(exc).splitlines()[0][:300]}{hint}"
                         "\n(set FAIRFARE_DEBUG=1 for the full traceback)")


if __name__ == "__main__":
    main()
