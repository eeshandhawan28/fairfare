"""Summarise a venue's reply into availability / price / deadline. Read-only: never commits money."""
from __future__ import annotations

import re
from typing import Optional

from fairfare.llm import LLM, extract_json

SYSTEM = ('Extract from a venue reply. Return ONLY JSON: {"available": true|false|null, "total_price": number|null, '
          '"currency": string|null, "deadline": string|null, "asks_for_payment": true|false, "notes": string}')


def summarize_reply(text: str, llm: Optional[LLM] = None) -> dict:
    out: dict = {"asks_for_payment": bool(re.search(r"prepay|deposit|pay now|предоплат|оплат", text, re.I))}
    if llm is not None:
        try:
            data = extract_json(llm.complete("booking_reader", SYSTEM, text))
            if isinstance(data, dict):
                out.update({k: v for k, v in data.items() if k != "asks_for_payment"})
        except ValueError:
            out["notes"] = "could not parse reply automatically"
    return out
