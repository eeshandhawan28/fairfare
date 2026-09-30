"""Offline demo world: fixture web pages, a search index and a stand-in extractor "LLM".

Used by tests and by `fairfare serve --demo`, so the whole product can be tried with no
network, no API keys and no model. The data is synthetic and clearly not real research.
"""
from __future__ import annotations

import json
import re

from fairfare.tools.fetch import FixtureFetcher
from fairfare.tools.search import FixtureSearch, SearchResult

PAGES = {
    "https://example-guide.test/almaty": (
        "Almaty travel guide. Medeu skating rink and dam sits in a mountain gorge and takes about two hours "
        "to visit at 1,691 m. Kok Tobe hill has a cable car, views over the city and a small zoo, an easy "
        "outing of about 90 minutes. The Green Bazaar is a covered market where you can eat and shop for "
        "about an hour. Charyn Canyon is a full day trip of eight hours, moderate walking."
    ),
    "https://ski.test/shymbulak": (
        "Shymbulak Mountain Resort is located at 2,260 m to 3,200 m above sea level. Shymbulak is a mountain "
        "resort and visitors ride the cable car for views and hiking, about three hours, strenuous at altitude."
    ),
    "https://news.test/shymbulak-maintenance": (
        "Kursiv Media reports that the summer season at Shymbulak runs through October 18, 2026, and from "
        "October 19, 2026 the cable cars go into staged maintenance ahead of the winter opening."
    ),
    "https://shymbulak.com/en/news": (
        "Important: the cable cars at Shymbulak will be closed for scheduled maintenance from October 19, 2026 "
        "to November 30, 2026. The resort reopens for the winter season on December 1."
    ),
    "https://gov.test/visa": (
        "Citizens of India may stay in Kazakhstan visa-free for up to 14 days per entry. Passports must be valid "
        "for the whole stay."
    ),
    "https://taxi.test/apps": (
        "Visitors to Almaty commonly use the inDrive and Yandex Go apps for taxis. Call +7 727 000 0000 for a "
        "pre-booked airport transfer."
    ),
    "https://reddit.test/hidden1": (
        "Everyone goes to Charyn but Kolsai Lakes are the underrated gem near Almaty, we spent a quiet day there "
        "hiking between the three lakes and it was worth the long drive."
    ),
    "https://forum.test/hidden2": (
        "If you want something unheard of, Kolsai Lakes are wonderful and far quieter than the big sights around "
        "Almaty, a long drive but so rewarding for a hike."
    ),
    "https://reddit.test/scam": (
        "Warning: unlicensed taxi drivers at Almaty airport quoted us five times the normal fare, avoid taxis "
        "outside the official stand."
    ),
}

# claims returned by the fake extractor per URL; quotes are verbatim substrings of PAGES (or deliberately not)
CLAIMS = {
    "https://example-guide.test/almaty": [
        {"subject": "Medeu", "text": "Skating rink and dam in a gorge.", "quote":
            "Medeu skating rink and dam sits in a mountain gorge and takes about two hours to visit",
         "data": {"city": "Almaty", "kind": "sight", "duration_min": 120, "effort": 1, "altitude_m": 1691}},
        {"subject": "Kok Tobe", "text": "Hill with cable car and views.", "quote":
            "Kok Tobe hill has a cable car, views over the city and a small zoo",
         "data": {"city": "Almaty", "kind": "sight", "duration_min": 90, "effort": 1}},
        {"subject": "Green Bazaar", "text": "Covered market.", "quote":
            "The Green Bazaar is a covered market where you can eat and shop for about an hour",
         "data": {"city": "Almaty", "kind": "market", "duration_min": 60, "effort": 1}},
        {"subject": "Charyn Canyon", "text": "Full day trip.", "quote":
            "Charyn Canyon is a full day trip of eight hours, moderate walking",
         "data": {"city": "Charyn", "kind": "nature", "duration_min": 480, "effort": 2}},
        # hallucinated: this quote is NOT on the page and must be rejected
        {"subject": "Invented Palace", "text": "Made up.", "quote":
            "The Invented Palace welcomes visitors every day from nine in the morning", "data": {}},
    ],
    "https://ski.test/shymbulak": [
        {"subject": "Shymbulak", "text": "Mountain resort with cable car.", "quote":
            "visitors ride the cable car for views and hiking, about three hours, strenuous at altitude",
         "data": {"city": "Almaty", "kind": "adventure", "duration_min": 180, "effort": 3, "altitude_m": 2260}},
    ],
    "https://news.test/shymbulak-maintenance": [
        {"subject": "Shymbulak", "text": "Cable cars closed from Oct 19.", "quote":
            "from October 19, 2026 the cable cars go into staged maintenance ahead of the winter opening",
         "data": {"closed_from": "2026-10-19", "closed_to": None, "reason": "staged cable car maintenance"}},
    ],
    "https://shymbulak.com/en/news": [
        {"subject": "Shymbulak", "text": "Closed for maintenance.", "quote":
            "the cable cars at Shymbulak will be closed for scheduled maintenance from October 19, 2026 to November 30, 2026",
         "data": {"closed_from": "2026-10-19", "closed_to": "2026-11-30", "reason": "scheduled maintenance"}},
    ],
    "https://gov.test/visa": [
        {"subject": "Entry", "text": "Indian citizens can stay 14 days visa-free.", "quote":
            "Citizens of India may stay in Kazakhstan visa-free for up to 14 days per entry",
         "data": {"requirement": "visa_free", "days": 14}},
    ],
    "https://taxi.test/apps": [
        {"subject": "Taxi apps", "text": "inDrive and Yandex Go are commonly used.", "quote":
            "commonly use the inDrive and Yandex Go apps for taxis", "data": {"type": "ride_hailing", "name": "inDrive"}},
        {"subject": "Airport transfer", "text": "Pre-book airport transfer.", "quote":
            "Call +7 727 000 0000 for a pre-booked airport transfer",
         "data": {"type": "transfer", "name": "Airport transfer", "contact": "+7 727 000 0000"}},
    ],
    "https://reddit.test/hidden1": [
        {"subject": "Kolsai Lakes", "text": "Underrated gem.", "quote":
            "Kolsai Lakes are the underrated gem near Almaty", "data": {"sentiment": "positive",
            "place": "Kolsai Lakes", "city": "Kolsai", "kind": "nature", "duration_min": 300}},
    ],
    "https://forum.test/hidden2": [
        {"subject": "Kolsai Lakes", "text": "Quiet and rewarding.", "quote":
            "Kolsai Lakes are wonderful and far quieter than the big sights around Almaty", "data": {
                "sentiment": "positive", "place": "Kolsai Lakes", "city": "Kolsai", "kind": "nature",
                "duration_min": 300}},
    ],
    "https://reddit.test/scam": [
        {"subject": "Airport taxis", "text": "Unlicensed drivers overcharge.", "quote":
            "unlicensed taxi drivers at Almaty airport quoted us five times the normal fare",
         "data": {"sentiment": "negative", "place": "Airport taxis"}},
    ],
}

SEARCH_TABLE = {
    "closure": [SearchResult(title="t", url="https://shymbulak.com/en/news", snippet="s"),
                SearchResult(title="t", url="https://news.test/shymbulak-maintenance", snippet="s")],
    "opening hours": [SearchResult(title="t", url="https://shymbulak.com/en/news", snippet="s")],
    "visa": [SearchResult(title="t", url="https://gov.test/visa", snippet="s")],
    "entry rules": [SearchResult(title="t", url="https://gov.test/visa", snippet="s")],
    "taxi": [SearchResult(title="t", url="https://taxi.test/apps", snippet="s")],
    "rental": [SearchResult(title="t", url="https://taxi.test/apps", snippet="s")],
    "airport transfer": [SearchResult(title="t", url="https://taxi.test/apps", snippet="s")],
    "hidden gems": [SearchResult(title="t", url="https://reddit.test/hidden1", snippet="s"),
                    SearchResult(title="t", url="https://forum.test/hidden2", snippet="s")],
    "scams": [SearchResult(title="t", url="https://reddit.test/scam", snippet="s")],
    "underrated": [SearchResult(title="t", url="https://forum.test/hidden2", snippet="s")],
    "top things to do": [SearchResult(title="t", url="https://example-guide.test/almaty", snippet="s"),
                         SearchResult(title="t", url="https://ski.test/shymbulak", snippet="s")],
}


class FakeExtractorLLM:
    """Returns the canned claims for whichever page URL appears in the prompt.

    For quote parsing it reads lines like "Almaty city tour: INR 3,000" so the demo works on pasted quotes.
    """

    def __init__(self, other: str = "[]") -> None:
        self.other = other
        self.calls: list[str] = []

    def complete(self, agent: str, system: str, user: str) -> str:
        self.calls.append(agent)
        if agent == "quote_parser" and self.other == "[]":
            return json.dumps(parse_quote_lines(user))
        for url, claims in CLAIMS.items():
            if f"PAGE URL: {url}" in user:
                return json.dumps(claims)
        return self.other


def parse_quote_lines(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = re.search(r"^(.*?)[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([\d,]{3,})\s*$", line.strip())
        if m and m.group(1).strip():
            name = m.group(1).strip(" :-")
            low = name.lower()
            cat = "transfer" if "transfer" in low else "stay" if "hotel" in low else "flight" if "flight" in low else "activity"
            out.append({"item": name, "category": cat, "amount": m.group(2)})
    return out


def demo_world() -> tuple[FixtureSearch, FixtureFetcher]:
    return FixtureSearch(SEARCH_TABLE), FixtureFetcher(PAGES)


