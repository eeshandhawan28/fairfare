"""Deterministic place hygiene: dedupe aliases, classify food vs sights, work out day-trip travel."""
from __future__ import annotations

import re
import unicodedata

from fairfare import tracing
from fairfare.llm import LLM, extract_json
from fairfare.models import Place, TripBrief

GENERIC = {"temple", "shrine", "area", "complex", "site", "the", "of", "and", "de", "la", "el", "old"}
MARKET_WORDS = ("market", "bazaar", "souk", "souq", "street", "district", "quarter", "night market", "food hall")
FOOD_NAME = re.compile(r"\b(cuisine|street food|food tour|dish|dishes|specialit(y|ies)|cooking class|tasting)\b", re.I)
DEFAULT_EXCURSION_MIN = 120
MIN_EXCURSION_VISIT_MIN = 270
MAX_ONE_WAY_MIN = 240  # hard cap; stricter caps apply for relaxed or older groups (see rules)


def canon(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    toks = [t for t in re.sub(r"[^a-z0-9]+", " ", s).split() if t not in GENERIC]
    return " ".join(toks)


def _same(a: str, b: str) -> bool:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if not small <= big:
        return False
    return len(small) >= 2 or (len(next(iter(small))) >= 5 and len(big) <= 3)


def _merge_into(keep: Place, other: Place) -> None:
    keep.evidence.extend(e for e in other.evidence if e not in keep.evidence)
    keep.hidden_gem = keep.hidden_gem or other.hidden_gem
    if other.travel_min is not None:
        keep.travel_min = max(keep.travel_min or 0, other.travel_min)
    keep.city = keep.city or other.city
    keep.opens, keep.closes = keep.opens or other.opens, keep.closes or other.closes
    keep.closed_days = keep.closed_days or other.closed_days
    if other.best_time == "sunrise":
        keep.best_time = "sunrise"
    if len(other.name) < len(keep.name) and canon(other.name) == canon(keep.name):
        keep.name = other.name


def merge_similar(places: list[Place]) -> list[Place]:
    out: list[Place] = []
    keys: list[str] = []
    for p in sorted(places, key=lambda x: -len(x.evidence)):
        k = canon(p.name)
        for i, existing in enumerate(keys):
            if _same(k, existing):
                _merge_into(out[i], p)
                break
        else:
            out.append(p.model_copy(deep=True))
            keys.append(k)
    return out


ALIAS_SYSTEM = ("You group place names that refer to the SAME real-world place (English/local/alternative names, "
                "e.g. 'Kinkaku-ji' and 'Golden Pavilion'). Output ONLY a JSON array of arrays of names copied "
                "exactly from the input; include only groups with 2 or more names. Different places in the same "
                "area are NOT the same place.")


def llm_merge_aliases(llm: LLM, places: list[Place]) -> list[Place]:
    """Alias groups from the model are accepted only if every name is one we already hold."""
    if len(places) < 2:
        return places
    names = [p.name for p in places]
    try:
        groups = extract_json(llm.complete("extractor", ALIAS_SYSTEM, "\n".join(names)))
    except Exception:
        return places
    by_name = {p.name: p for p in places}
    dropped: set[str] = set()
    for g in groups if isinstance(groups, list) else []:
        if not isinstance(g, list):
            continue
        members = [by_name[n] for n in g if isinstance(n, str) and n in by_name and n not in dropped]
        if len(members) < 2:
            continue
        members.sort(key=lambda p: -len(p.evidence))
        keep = members[0]
        for other in members[1:]:
            _merge_into(keep, other)
            dropped.add(other.name)
            tracing.event("place_alias_merged", kept=keep.name, merged=other.name)
    return [p for p in places if p.name not in dropped]


_T = r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?"
_CLOSES = re.compile(r"clos\w*\s+(?:at\s+|by\s+)?" + _T, re.I)
_OPENS = re.compile(r"open\w*\s+(?:at\s+|from\s+|daily\s+from\s+)?" + _T, re.I)
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _to_hhmm(h: str, m: str | None, ap: str) -> str:
    hour = int(h) % 12 + (12 if ap.lower() == "p" else 0)
    return f"{hour:02d}:{int(m or 0):02d}"


def infer_hours(text: str) -> dict:
    """Opening/closing times and closed weekdays stated in a place's own evidence.

    Seasonal statements list several times; the earliest closing and latest opening are kept, so the
    schedule errs towards not arriving after closing.
    """
    closes = sorted(_to_hhmm(*m.groups()) for m in _CLOSES.finditer(text))
    opens = sorted(_to_hhmm(*m.groups()) for m in _OPENS.finditer(text))
    days = [d.capitalize() for d in _DAYS if re.search(rf"closed (?:on )?(?:[a-z, ]*and )?{d}s?\b|{d}s? (?:are )?closed", text, re.I)]
    return {"opens": opens[-1] if opens else "", "closes": closes[0] if closes else "", "closed_days": days}


def is_food_item(p: Place) -> bool:
    """Dishes, cuisines and food categories are 'things to eat', never scheduled as places."""
    name = p.name.lower()
    if FOOD_NAME.search(p.name) and not any(w in name for w in ("market", "bazaar", "souk", "souq", "food hall")):
        return True
    if any(w in name for w in MARKET_WORDS):
        return False
    return p.kind.lower() == "food"


def base_cities(brief: TripBrief, places: list[Place]) -> set[str]:
    dest = brief.destination.lower()
    cities = [p.city.strip().lower() for p in places if p.city.strip()]
    named = {c for c in set(cities) if c in dest or any(part and part in dest for part in c.split(",")[:1])}
    if named:
        return named
    if cities:
        return {max(set(cities), key=cities.count)}
    return set()


def annotate_travel(places: list[Place], brief: TripBrief) -> list[Place]:
    """Places outside the base city become day-trip excursions with an explicit one-way travel time."""
    bases = base_cities(brief, places)
    out = []
    for p in places:
        p = p.model_copy(deep=True)
        if not (p.opens and p.closes):
            h = infer_hours(" ".join(e.quote for e in p.evidence) + " " + p.notes)
            p.opens, p.closes = p.opens or h["opens"], p.closes or h["closes"]
            p.closed_days = p.closed_days or h["closed_days"]
        city = p.city.strip().lower()
        if p.travel_min is None and bases and city and city not in bases:
            p.travel_min, p.travel_estimated = DEFAULT_EXCURSION_MIN, True
        if p.travel_min is not None and p.travel_min < 60:
            p.travel_min = None  # near enough to treat as part of the base city
        if p.travel_min is not None:
            p.duration_min = max(p.duration_min, MIN_EXCURSION_VISIT_MIN)  # a day trip is worth a real visit
        out.append(p)
    return out
