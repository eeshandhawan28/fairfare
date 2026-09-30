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


def run_case(path: Path) -> bool:
    case = json.loads(path.read_text())
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
    for path in sorted((ROOT / "evals" / "cases").glob("*.json")):
        print(path.stem)
        if not run_case(path):
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
