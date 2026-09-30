"""Destination research agents. All are grounded: every output carries a verified quote."""
from __future__ import annotations

import re

from fairfare.models import Claim, Evidence, Place, TripBrief
from fairfare.research import Researcher, domain, get, independent_domains

OFFICIAL_HINTS = (".gov", "gov.", "mfa.", "embassy", "consulate", "evisa", "e-visa", "visa.kz", "immigration")


def is_official(url: str) -> bool:
    return any(h in domain(url) for h in OFFICIAL_HINTS)


# ---------- places ----------

PLACES_INSTR = (
    "Extract specific visitable places or activities in {destination} (not generic advice). "
    "For each, fill data with what the page states; omit fields the page does not state."
)
PLACES_SCHEMA = ('"city": "string", "kind": "sight|nature|food|market|culture|adventure|wellness", '
                 '"duration_min": number, "effort": "1 easy|2 moderate|3 strenuous", '
                 '"altitude_m": number, "min_age": number')


def _int(v, default=None):
    """First number in the value ("2 moderate" -> 2, "3,200 m" -> 3200), else default."""
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v)) if v is not None else None
    if not m:
        return default
    try:
        return int(float(m.group().replace(",", "")))
    except ValueError:
        return default


def claim_to_place(c: Claim, hidden_gem: bool = False) -> Place:
    d = c.data
    return Place(
        name=c.subject.strip(), city=str(get(d, "city", "")), kind=str(get(d, "kind", "sight")),
        duration_min=max(30, min(_int(get(d, "duration_min"), 90), 480)),
        effort=max(1, min(_int(get(d, "effort"), 1), 3)), altitude_m=_int(get(d, "altitude_m")),
        min_age=_int(get(d, "min_age")), hidden_gem=hidden_gem, evidence=[c.evidence], notes=c.text)


def research_places(r: Researcher, brief: TripBrief) -> list[Place]:
    dest = brief.destination
    queries = [f"top things to do in {dest}", f"{dest} attractions families with older parents",
               f"{dest} day trips from the city"] + [f"{dest} {i}" for i in brief.interests[:3]]
    claims = r.run(queries, "place", PLACES_INSTR.format(destination=dest), PLACES_SCHEMA)
    merged: dict[str, Place] = {}
    for c in claims:
        p = claim_to_place(c)
        key = p.name.lower()
        if key in merged:
            merged[key].evidence.extend(p.evidence)
        else:
            merged[key] = p
    return list(merged.values())


# ---------- local intel (forums, reddit) ----------

INTEL_INSTR = (
    "Extract first-hand traveller reports about {destination}: (a) lesser-known places praised by visitors "
    "(sentiment 'positive'), (b) scams, unsafe or unfriendly experiences (sentiment 'negative'). "
    "Ignore generic listicles and adverts."
)
INTEL_SCHEMA = ('"sentiment": "positive|negative", "place": "string", "city": "string", '
                '"kind": "sight|nature|food|market|culture|adventure|wellness", "duration_min": number')


def research_local_intel(r: Researcher, brief: TripBrief) -> tuple[list[Place], list[Claim]]:
    """Returns (hidden gems confirmed by >=2 independent domains, avoid claims needing review)."""
    dest = brief.destination
    queries = [f"site:reddit.com {dest} hidden gems worth visiting", f"site:reddit.com {dest} tourist scams avoid",
               f"site:reddit.com {dest} trip report itinerary", f"{dest} travel forum underrated places"]
    claims = r.run(queries, "intel", INTEL_INSTR.format(destination=dest), INTEL_SCHEMA)
    positives: dict[str, list[Claim]] = {}
    avoid: list[Claim] = []
    for c in claims:
        if str(get(c.data, "sentiment", "")).lower() == "negative":
            avoid.append(c)
        elif str(get(c.data, "sentiment", "")).lower() == "positive":
            positives.setdefault(re.sub(r"\W+", " ", str(get(c.data, "place", c.subject)).lower()).strip(), []).append(c)
    gems = []
    for name, group in positives.items():
        if independent_domains(group) >= 2:  # one mention is a rumour, two independent ones is a signal
            first = group[0]
            place = claim_to_place(Claim(kind="place", subject=str(get(first.data, "place", first.subject)),
                                         text=first.text, evidence=first.evidence, data=first.data), hidden_gem=True)
            place.evidence = [g.evidence for g in group]
            gems.append(place)
    return gems, avoid


# ---------- visa ----------

VISA_INSTR = (
    "Extract entry rules for {passport} passport holders visiting {destination}: visa-free days, e-visa, "
    "required documents, fees, validity. Quote the exact sentence."
)
VISA_SCHEMA = '"requirement": "visa_free|evisa|visa_required|unclear", "days": number'


def research_visa(r: Researcher, brief: TripBrief) -> list[Claim]:
    q = [f"{brief.destination} visa requirements for {brief.passport} citizens official",
         f"{brief.destination} entry rules {brief.passport} passport {brief.start.year}"]
    claims = r.run(q, "visa", VISA_INSTR.format(passport=brief.passport, destination=brief.destination), VISA_SCHEMA)
    for c in claims:
        c.data["official"] = is_official(c.evidence.url)
    return sorted(claims, key=lambda c: not c.data["official"])


# ---------- ground transport ----------

TRANSPORT_INSTR = (
    "Extract ground transport options in {destination}: ride-hailing apps, airport transfers, car rental, "
    "trains, private drivers/guides. Include a phone/URL in data.contact ONLY if it appears verbatim in the page."
)
TRANSPORT_SCHEMA = '"type": "ride_hailing|rental|transfer|train|driver|guide", "name": "string", "contact": "string"'


def research_transport(r: Researcher, brief: TripBrief) -> list[Claim]:
    d = brief.destination
    q = [f"{d} taxi apps tourists ride hailing", f"{d} car rental with driver tourists", f"{d} airport transfer"]
    claims = r.run(q, "transport", TRANSPORT_INSTR.format(destination=d), TRANSPORT_SCHEMA)
    for c in claims:
        contact = str(get(c.data, "contact", ""))
        if contact and _norm_digits(contact) not in _norm_digits(c.evidence.quote):
            c.data["contact"] = ""  # a contact not present in the quote is not verified
    return claims


def _norm_digits(s: str) -> str:
    return re.sub(r"[^\w@.]", "", s.lower())
