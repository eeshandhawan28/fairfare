"""Opening hours and weekly closing days for the venues we might schedule (grounded in the quote)."""
from __future__ import annotations

import re

from fairfare.agents.destination import _time_in_quote
from fairfare.models import Place, TripBrief
from fairfare.research import Researcher, get

INSTRUCTIONS = (
    "Extract the regular opening hours of {venue}: opens and closes as HH:MM 24h, and any weekdays it is closed "
    "(closed_days, e.g. 'Monday, Friday'). Also from_center_min: the one-way travel time in minutes from the centre "
    "of the nearest main city to {venue}, only if the text states a time (or a distance you can convert). "
    "Only what the text states. Give travel time as its own claim with its own quote."
)
SCHEMA = '"opens": "HH:MM", "closes": "HH:MM", "closed_days": "weekday names", "from_center_min": "number"'
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def scout_hours(researcher: Researcher, places: list[Place], brief: TripBrief) -> dict[str, dict]:
    from fairfare.planning.graph import parallel

    def one(p: Place) -> tuple[str, dict]:
        claims = researcher.run([f"{p.name} {brief.destination} opening hours closed days how far from the city centre travel time"], "hours",
                                INSTRUCTIONS.format(venue=p.name), SCHEMA)
        words = {w for w in re.findall(r"[a-z]{4,}", p.name.lower())}
        merged: dict = {}
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
            tm = _travel(get(c.data, "from_center_min", None), c.evidence.quote)
            if tm:
                out["travel_min"], out["travel_estimated"] = tm, False
            for k, v in out.items():
                if v and not merged.get(k):
                    merged[k] = v
        return p.name, merged

    results = parallel(*[(lambda p=p: one(p)) for p in places], workers=4) if places else []
    return {n: r for n, r in results if r}


def _travel(raw, quote: str) -> int | None:
    """Travel minutes only when the quote itself talks about a time or distance."""
    try:
        v = int(float(str(raw).split()[0]))
    except (ValueError, IndexError, TypeError):
        return None
    q = quote.lower()
    if not (0 < v <= 600) or not re.search(r"\d", q) or not re.search(r"\b(min|mins|minutes?|hours?|hrs?|km|kilomet\w+|miles?)\b", q):
        return None
    return v
