"""Find dated closures and maintenance notices for venues, from the live web.

Confidence rule (deterministic): the operator's own domain, or two independent
domains, raise confidence; a single third-party mention stays low.
"""
from __future__ import annotations

import re
from datetime import date

from fairfare import tracing
from fairfare.models import ClosureNotice, TripBrief
from fairfare.research import Researcher, date_in_quote, get, independent_domains, registrable

INSTRUCTIONS = (
    "Extract ONLY statements that {venue} (or its lifts, cable cars, entrance, or a named part of it) "
    "is closed, under maintenance, out of season, or reopening, WITH dates. "
    "Resolve dates to ISO YYYY-MM-DD using the year context {year} if the page omits the year. "
    "Ignore other venues and undated statements."
)
SCHEMA = '"closed_from": "YYYY-MM-DD", "closed_to": "YYYY-MM-DD or null", "reason": "string"'


def _slug(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]{4,}", name.lower())]


def _is_operator_domain(venue: str, url: str) -> bool:
    """Operator site = the registrable name is one of the venue's words (or all of them joined), exactly."""
    name = registrable(url).split(".")[0]
    words = _slug(venue)
    return name in words or name.replace("-", "") == "".join(words) or name in {"".join(words[:2])}


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def scout_closures(researcher: Researcher, venues: list[str], brief: TripBrief) -> list[ClosureNotice]:
    notices: list[ClosureNotice] = []
    month = brief.start.strftime("%B")
    year = brief.start.year
    for venue in venues:
        queries = [f"{venue} closed maintenance {month} {year}",
                   f"{venue} closure dates {year}",
                   f"{venue} official site opening hours season"]
        if getattr(researcher.search, "digest_mode", False):
            queries = queries[:1]  # each search is slow; one focused query per venue
        claims = researcher.run(queries, "closure", INSTRUCTIONS.format(venue=venue, year=year), SCHEMA)
        parsed = []
        venue_words = set(_slug(venue))
        for c in claims:
            if venue_words and not venue_words & set(_slug(c.subject)):
                # search returned a page about something else: never pin its closure on this venue
                tracing.event("closure_claim_offtopic", venue=venue, subject=c.subject)
                continue
            start = _parse_date(get(c.data, "closed_from"))
            if start is None or not date_in_quote(start, c.evidence.quote):
                # the date must be readable in the quote itself, not just asserted by the model
                tracing.event("closure_claim_ungrounded_date", venue=venue, quote=c.evidence.quote)
                continue
            end = _parse_date(get(c.data, "closed_to"))
            if end is not None and (end < start or not date_in_quote(end, c.evidence.quote)):
                end = None  # unreadable or inconsistent end date: treat as "reopening not stated"
            parsed.append((c, start, end))
        for c, start, end in parsed:
            peers = [p[0] for p in parsed if p[1] == start]
            official = _is_operator_domain(venue, c.evidence.url)
            n = independent_domains(peers)
            confidence = "high" if official else ("medium" if n >= 2 else "low")
            notices.append(ClosureNotice(
                venue=venue, keywords=[venue.lower()] + _slug(venue)[:1], closed_from=start, closed_to=end,
                reason=str(get(c.data, "reason", "")), confidence=confidence,
                source=f'{c.evidence.url} ("{c.evidence.quote[:120]}") retrieved {c.evidence.retrieved_at}'))
    return _dedupe(notices)


def _dedupe(notices: list[ClosureNotice]) -> list[ClosureNotice]:
    best: dict[tuple, ClosureNotice] = {}
    rank = {"low": 0, "medium": 1, "high": 2}

    def score(n: ClosureNotice) -> tuple:
        return (rank[n.confidence], n.closed_to is not None)

    for n in notices:
        key = (n.venue.lower(), n.closed_from)  # same venue, same start: keep the best-sourced notice
        if key not in best or score(n) > score(best[key]):
            best[key] = n
    return list(best.values())
