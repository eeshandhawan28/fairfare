"""Turn a free-text travel-agent quote into structured line items (the only LLM step)."""
from __future__ import annotations

from pydantic import ValidationError

from fairfare.llm import LLM, extract_json
from fairfare.models import QuoteLine

SYSTEM = """You extract line items from a travel agent's quote.
Return ONLY a JSON array. Each element has:
  item (string), category (one of: flight, stay, transfer, activity, other),
  amount (number, total for that line), currency (ISO code, default INR), note (string).
Do not invent lines. If an amount is missing, skip the line."""


def parse_quote(llm: LLM, quote_text: str) -> list[QuoteLine]:
    raw = llm.complete("quote_parser", SYSTEM, quote_text)
    data = extract_json(raw)
    if isinstance(data, dict):
        data = data.get("lines", [])
    lines: list[QuoteLine] = []
    for entry in data:
        try:
            lines.append(QuoteLine(**entry))
        except (ValidationError, TypeError):
            continue  # drop malformed rows rather than guess
    return lines
