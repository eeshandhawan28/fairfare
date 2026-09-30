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


FX_INSTR = "Extract the current exchange rate: how many {to} equal 1 {frm}. data.rate is that number."
FX_SCHEMA = '"rate": number'


def fx_rate(researcher: Researcher, frm: str, to: str) -> tuple[float, str] | None:
    """(units of `to` per 1 `frm`, source url) from the web; the number must appear in the quote."""
    if frm.upper() == to.upper():
        return 1.0, ""
    claims = researcher.run([f"1 {frm} to {to} exchange rate today"], "fx",
                            FX_INSTR.format(frm=frm.upper(), to=to.upper()), FX_SCHEMA)
    for c in claims:
        try:
            r = float(str(get(c.data, "rate", "")).replace(",", ""))
        except ValueError:
            continue
        if r > 0 and number_in_quote(r, c.evidence.quote):
            return r, c.evidence.url
    return None


def scout_prices(researcher: Researcher, lines: list[QuoteLine], brief: TripBrief) -> list[ReferencePrice]:
    from fairfare.planning.graph import parallel

    digest = getattr(researcher.search, "digest_mode", False)
    fx_cache: dict[str, tuple[float, str] | None] = {}

    def rate_for(cur: str, to: str):
        if cur not in fx_cache:
            fx_cache[cur] = fx_rate(researcher, cur, to)
        return fx_cache[cur]

    def one(line: QuoteLine) -> list[ReferencePrice]:
        refs: list[ReferencePrice] = []
        queries = [f"{line.item} price {brief.destination} {line.currency}",
                   f"{line.item} cost {brief.destination} {brief.start.year}"]
        if digest:
            queries = [f"{line.item} typical price {brief.destination} {brief.start.year} local currency"]
        claims = researcher.run(queries, "price", INSTRUCTIONS.format(item=line.item, destination=brief.destination),
                                SCHEMA)
        for unit in ("total", "per_person"):
            usable, note = [], ""
            for c in claims:
                try:
                    amount = float(str(get(c.data, "amount", "")).replace(",", ""))
                except ValueError:
                    continue
                cur = str(get(c.data, "currency", "")).upper()
                if get(c.data, "unit") != unit or amount <= 0 or not number_in_quote(amount, c.evidence.quote):
                    continue  # the number must be in the quote
                if cur != line.currency.upper():
                    fx = rate_for(cur, line.currency)
                    if not fx:
                        continue
                    amount *= fx[0]
                    note = f"converted from {cur} at about {fx[0]:.4g}"
                usable.append((amount, c))
            if usable:  # drop outliers so one inflated or bogus price cannot widen the band
                m = median(a for a, _ in usable)
                usable = [(a, c) for a, c in usable if m / 2.5 <= a <= m * 2.5]
            enough = len(usable) >= 2 if digest else len({registrable(c.evidence.url) for _, c in usable}) >= 2
            if not enough:
                continue
            amounts = [a for a, _ in usable]
            basis = "; ".join(x for x in (note, "built from a web-search summary, treat as a rough guide" if digest else "") if x)
            refs.append(ReferencePrice(
                category=line.category, keywords=[line.item.lower()], low=min(amounts), high=max(amounts),
                currency=line.currency, unit=unit, basis=basis,
                source="; ".join(sorted({c.evidence.url for _, c in usable})),
                as_of=date.today().isoformat()))
        return refs

    todo = [l for l in lines if l.amount is not None]
    groups = parallel(*[(lambda l=l: one(l)) for l in todo], workers=4) if todo else []
    return [r for g in groups for r in g]
