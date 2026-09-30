"""Run eval cases against the configured (real) model. Usage: python evals/run.py

Each case checks that a known problem is caught. Run it per model to decide
which model each agent should use.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from fairfare.data import load_closures, load_reference_prices
from fairfare.graph import run_audit
from fairfare.llm import LiteLLMClient
from fairfare.models import TripBrief

ROOT = Path(__file__).resolve().parents[1]


def run_plan_case(case: dict) -> bool:
    """Live plan eval: needs web access (search + fetch) and a real model."""
    from fairfare.planning.graph import run_plan
    from fairfare.tools.fetch import CachedFetcher, HttpFetcher
    from fairfare.tools.search import get_search

    brief = TripBrief(**json.loads((ROOT / case["brief_file"]).read_text()))
    r = run_plan(LiteLLMClient(), get_search(), CachedFetcher(HttpFetcher()), brief,
                 static_notices=load_closures(), eval_case=case["name"])
    print(f"  trace: {r['run_id']}")
    plan, exp, ok = r["plan"], case["expect"], True
    acts = [(d.day, b) for d in plan.days for b in d.blocks if b.kind == "activity"]

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        ok &= cond
        print(f"  {label}: {'PASS' if cond else 'FAIL'}")

    for key, iso in exp.get("never_scheduled_from", {}).items():
        check(f"'{key}' not scheduled from {iso}",
              not any(key in (b.place or "").lower() and day.isoformat() >= iso for day, b in acts))
    if "min_activities" in exp:
        check(f"at least {exp['min_activities']} activities", len(acts) >= exp["min_activities"])
    if exp.get("all_activities_cited"):
        check("every activity has a source", all(b.sources for _, b in acts))
    if exp.get("visa_has_official_source"):
        check("visa answer from an official source", any(c.data.get("official") for c in plan.visa))
    if exp.get("no_high_severity_plan_violations"):
        check("no closed/unverified places in plan", not any(
            v.rule in ("closed_place", "unverified_place") for v in r["violations"]))
    return ok


def run_case(path: Path) -> bool:
    case = json.loads(path.read_text())
    if case.get("type") == "plan":
        return run_plan_case(case)
    brief = TripBrief(
        destination="Kazakhstan",
        start=date.fromisoformat(case["start"]),
        end=date.fromisoformat(case["end"]),
    )
    result = run_audit(LiteLLMClient(), brief, (ROOT / case["quote_file"]).read_text(),
                       load_reference_prices(), load_closures(), eval_case=case["name"])
    print(f"  trace: {result['run_id']}")
    findings = result["findings"]
    ok = True
    want_closure = case["expect"].get("closure_flagged_for")
    if want_closure:
        hit = any(f.kind == "closure" and want_closure in f.line.lower() for f in findings)
        ok &= hit
        print(f"  closure flagged for '{want_closure}': {'PASS' if hit else 'FAIL'}")
    want_price = case["expect"].get("price_flagged_for")
    if want_price:
        hit = any(f.kind == "price" and f.severity == "warn" and want_price in f.line.lower()
                  for f in findings)
        ok &= hit
        print(f"  price flagged for '{want_price}': {'PASS' if hit else 'FAIL'}")
    return ok


def main() -> int:
    failed = 0
    only = sys.argv[1] if len(sys.argv) > 1 else ""  # e.g. `python evals/run.py plan` to pick cases
    for path in sorted((ROOT / "evals" / "cases").glob("*.json")):
        if only not in path.stem:
            continue
        print(path.stem)
        if not run_case(path):
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
