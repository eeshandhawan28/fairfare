"""Turn a free-text travel-agent quote into structured line items (the only LLM step)."""
from __future__ import annotations

from pydantic import ValidationError

from fairfare import tracing
from fairfare.llm import LLM, extract_json
from fairfare.models import QuoteLine

SYSTEM = """You extract line items from a travel agent's quote.
Return ONLY a JSON array. Each element has:
  item (string), category (one of: flight, stay, transfer, activity, other),
  amount (number, total for that line), currency (ISO code, default INR), note (string).
Do not invent lines. If an amount is missing, skip the line."""


def parse_quote(llm: LLM, quote_text: str) -> list[QuoteLine]:
    raw = llm.complete("quote_parser", SYSTEM, quote_text)
    try:
        data = extract_json(raw)
    except ValueError:
        tracing.event("parse_failed", reason="no JSON in model output", output=raw)
        return []
    if isinstance(data, dict):
        data = data.get("lines", [])
    lines: list[QuoteLine] = []
    dropped: list = []
    for entry in data:
        try:
            lines.append(QuoteLine(**entry))
        except (ValidationError, TypeError):
            dropped.append(entry)  # drop rows with no usable item rather than guess
    tracing.event("parse_result", parsed=len(lines), dropped=len(dropped), dropped_rows=str(dropped))
    return lines
