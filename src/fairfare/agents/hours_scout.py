"""Opening hours and weekly closing days for the venues we might schedule (grounded in the quote)."""
from __future__ import annotations

import re

from fairfare.agents.destination import _time_in_quote
from fairfare.models import Place, TripBrief
from fairfare.research import Researcher, get

INSTRUCTIONS = (
    "Extract the regular opening hours of {venue}: opens and closes as HH:MM 24h, and any weekdays it is closed "
    "(closed_days, e.g. 'Monday, Friday'). Only what the text states."
)
SCHEMA = '"opens": "HH:MM", "closes": "HH:MM", "closed_days": "weekday names"'
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def scout_hours(researcher: Researcher, places: list[Place], brief: TripBrief) -> dict[str, dict]:
    from fairfare.planning.graph import parallel

    def one(p: Place) -> tuple[str, dict]:
        claims = researcher.run([f"{p.name} {brief.destination} opening hours closed days"], "hours",
                                INSTRUCTIONS.format(venue=p.name), SCHEMA)
        words = {w for w in re.findall(r"[a-z]{4,}", p.name.lower())}
        for c in claims:
            if words and not words & set(re.findall(r"[a-z]{4,}", c.subject.lower())):
                continue
            q = c.evidence.quote.lower()
            opens = str(get(c.data, "opens", "") or "")
            closes = str(get(c.data, "closes", "") or "")
            days = [d for d in DAYS if d in str(get(c.data, "closed_days", "")).lower() and d in q]
            out = {"opens": opens if _time_in_quote(opens, c.evidence.quote) else "",
                   "closes": closes if _time_in_quote(closes, c.evidence.quote) else "",
                   "closed_days": [d.capitalize() for d in days]}
            if out["opens"] or out["closes"] or out["closed_days"]:
                return p.name, out
        return p.name, {}

    results = parallel(*[(lambda p=p: one(p)) for p in places], workers=4) if places else []
    return {n: r for n, r in results if r}
