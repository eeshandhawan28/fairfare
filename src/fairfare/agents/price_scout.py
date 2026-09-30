"""Build independent price bands for quote lines from live sources.

A band needs at least two claims from two different domains in the quote's currency,
so a single blog's number never becomes a benchmark.
"""
from __future__ import annotations

from datetime import date

from fairfare.models import QuoteLine, ReferencePrice, TripBrief
from statistics import median

from fairfare.research import Researcher, get, number_in_quote, registrable

INSTRUCTIONS = (
    "Extract prices for '{item}' in {destination}. Only include a price if the page states the number and "
    "the currency. unit must be 'total' (whole price for the group/booking) or 'per_person'; skip prices per "
    "night, per hour or with unclear unit. Do not convert currencies."
)
SCHEMA = '"amount": number, "currency": "ISO code", "unit": "total|per_person"'


def scout_prices(researcher: Researcher, lines: list[QuoteLine], brief: TripBrief) -> list[ReferencePrice]:
    refs: list[ReferencePrice] = []
    for line in lines:
        if line.amount is None:
            continue
        queries = [f"{line.item} price {brief.destination} {line.currency}",
                   f"{line.item} cost {brief.destination} {brief.start.year}"]
        claims = researcher.run(queries, "price", INSTRUCTIONS.format(item=line.item, destination=brief.destination),
                                SCHEMA)
        for unit in ("total", "per_person"):
            usable = []
            for c in claims:
                try:
                    amount = float(str(get(c.data, "amount", "")).replace(",", ""))
                except ValueError:
                    continue
                if str(get(c.data, "currency", "")).upper() == line.currency.upper() \
                        and get(c.data, "unit") == unit and amount > 0 \
                        and number_in_quote(amount, c.evidence.quote):  # the number must be in the quote
                    usable.append((amount, c))
            if usable:  # drop outliers so one inflated or bogus price cannot widen the band
                m = median(a for a, _ in usable)
                usable = [(a, c) for a, c in usable if m / 2.5 <= a <= m * 2.5]
            if len({registrable(c.evidence.url) for _, c in usable}) < 2:
                continue
            amounts = [a for a, _ in usable]
            refs.append(ReferencePrice(
                category=line.category, keywords=[line.item.lower()], low=min(amounts), high=max(amounts),
                currency=line.currency, unit=unit,
                source="; ".join(sorted({c.evidence.url for _, c in usable})),
                as_of=date.today().isoformat()))
    return refs
