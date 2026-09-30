"""Review a run's trace. Two layers:

1. lint_trace: deterministic rules that catch known failure patterns for free.
2. review_trace: a small, cheap model reads the compacted trace and flags anything
   else it can see. It is told to report only what the trace shows, and its output is
   labelled as model-generated so a human still decides what to change.
"""
from __future__ import annotations

from typing import Any

from fairfare.llm import LLM, extract_json

SLOW_LLM_MS = 30_000

SYSTEM = """You review execution traces of a travel-quote auditing pipeline.
Report problems you can SEE in the trace: wrong or empty parses, model output that
ignores the instructions, missing data, suspicious findings, slow or failing steps.
Do not speculate beyond the trace. Return ONLY a JSON array of objects:
  {"severity": "high|medium|low", "span": "<span name>", "problem": "...", "suggestion": "..."}
Return [] if nothing is wrong."""


def lint_trace(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    def add(sev: str, span: str, problem: str, suggestion: str) -> None:
        issues.append({"source": "lint", "severity": sev, "span": span,
                       "problem": problem, "suggestion": suggestion})

    for e in events:
        if e["type"] == "span" and e.get("error"):
            add("high", e["name"], f"Span failed: {e['error']}", "Fix the error or handle it explicitly.")
        if e["type"] == "span" and e.get("kind") == "llm" and e["latency_ms"] > SLOW_LLM_MS:
            add("low", e["name"], f"Slow LLM call ({e['latency_ms'] / 1000:.0f}s) on {e.get('model')}",
                "Try a smaller or hosted model for this agent.")
        if e["type"] == "event" and e["name"] == "parse_failed":
            add("high", "parse", "Model returned no parseable JSON for the quote.",
                "Tighten the prompt, try a stronger model, or add a JSON-mode retry.")
        if e["type"] == "event" and e["name"] == "parse_result":
            if e.get("parsed", 0) == 0:
                add("high", "parse", "Parser produced zero quote lines.", "Check the quote text and model output in the llm span.")
            if e.get("dropped", 0) > 0:
                add("medium", "parse", f"{e['dropped']} malformed line(s) were dropped.",
                    "Inspect the llm output; the model may be breaking the schema.")
        if e["type"] == "event" and e["name"] == "price_coverage":
            lines, unref = e.get("lines", 0), e.get("unreferenced", 0)
            if lines and unref / lines > 0.5:
                add("medium", "prices", f"{unref}/{lines} lines had no price reference, so they were not audited.",
                    "Add reference prices for these categories; this is the biggest limit on audit value.")
        if e["type"] == "feedback" and e.get("rating") == "bad":
            add("high", "run", f"Human marked this run bad: {e.get('note') or 'no note'}",
                "Turn this run into an eval case.")
    return issues


def compact(events: list[dict[str, Any]], limit: int = 6000) -> str:
    rows = []
    for e in events:
        if e["type"] == "span":
            extra = {k: v for k, v in e.items() if k in ("model", "prompt", "output", "error")}
            rows.append(f"SPAN {e['name']} ({e['latency_ms']}ms) {extra}")
        elif e["type"] == "event":
            rows.append(f"EVENT {e['name']} { {k: v for k, v in e.items() if k not in ('ts', 'run_id', 'type', 'name')} }")
        elif e["type"] in ("run_start", "run_end"):
            rows.append(f"{e['type'].upper()} { {k: v for k, v in e.items() if k not in ('ts', 'run_id', 'type')} }")
    text = "\n".join(rows)
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


def review_trace(llm: LLM, events: list[dict[str, Any]]) -> list[dict[str, str]]:
    raw = llm.complete("trace_reviewer", SYSTEM, compact(events))
    try:
        data = extract_json(raw)
    except ValueError:
        return [{"source": "model", "severity": "low", "span": "review",
                 "problem": "Reviewer model returned no parseable output.", "suggestion": "Retry or use a stronger model."}]
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict):
            out.append({"source": "model", "severity": str(item.get("severity", "low")),
                        "span": str(item.get("span", "")), "problem": str(item.get("problem", "")),
                        "suggestion": str(item.get("suggestion", ""))})
    return out
