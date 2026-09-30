from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from fairfare.data import load_closures, load_reference_prices
from fairfare.graph import build_graph
from fairfare.llm import LiteLLMClient
from fairfare.models import TripBrief


def main() -> None:
    p = argparse.ArgumentParser(prog="fairfare", description="Audit a travel-agent quote.")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="audit a quote text file")
    a.add_argument("quote", type=Path)
    a.add_argument("--destination", default="Kazakhstan")
    a.add_argument("--start", type=date.fromisoformat, required=True)
    a.add_argument("--end", type=date.fromisoformat, required=True)
    args = p.parse_args()

    brief = TripBrief(destination=args.destination, start=args.start, end=args.end)
    graph = build_graph(LiteLLMClient(), load_reference_prices(), load_closures())
    result = graph.invoke({"quote_text": args.quote.read_text(), "brief": brief})
    print(result["report"])


if __name__ == "__main__":
    main()
