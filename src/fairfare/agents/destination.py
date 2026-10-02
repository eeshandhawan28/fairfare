"""Destination research agents. All are grounded: every output carries a verified quote."""
from __future__ import annotations

import re

from fairfare.models import Claim, Evidence, Place, TripBrief
from fairfare.planning.places import llm_merge_aliases, merge_similar
from fairfare.research import Researcher, domain, get, independent_domains

GOV_LABELS = {"gov", "govt", "gob", "gouv", "go", "mil"}


def is_official(url: str) -> bool:
    """Government site: TLD is gov/mil, or a gov-style second-level label under a country TLD.

    Deliberately strict. A look-alike such as embassy-evisa-fast.com is NOT official.
    """
    labels = domain(url).split(".")
    return labels[-1] in {"gov", "mil"} or (len(labels) >= 2 and labels[-2] in GOV_LABELS and len(labels[-1]) <= 3)


# ---------- places ----------

PLACES_INSTR = (
    "Extract specific, named, visitable places in {destination}: attractions, temples, parks, museums, viewpoints, "
    "markets, districts, day-trip destinations. subject = the place's own proper name. Do NOT output dishes, cuisines, "
    "food categories, tour operators or hotels as places (a dish must have kind 'food'). "
    "For each, fill data only with what the page states; omit fields it does not state. "
    "city = the city, town or village the place is physically located in (NOT the nearest big city: Kaindy Lake "
    "is in the Kolsai/Saty area, not Almaty). from_center_min = one-way travel minutes by road from the main city "
    "centre; REQUIRED for any place outside the main city, only if the page states a time or distance. best_time = 'sunrise' only if the page says it must be done at sunrise/dawn. "
    "opens/closes = daily opening and closing time as HH:MM 24h, only if stated. Quote the sentence that supports it."
)
PLACES_SCHEMA = ('"city": "string", "kind": "sight|nature|food|market|culture|adventure|wellness", '
                 '"duration_min": number, "effort": "1 easy|2 moderate|3 strenuous", '
                 '"altitude_m": number, "min_age": number, "from_center_min": number, '
                 '"best_time": "sunrise|any", "opens": "HH:MM", "closes": "HH:MM"')


def _time_in_quote(t: str, quote: str) -> bool:
    """Opening times are kept only if the clock time is readable in the quote itself."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t.strip())
    if not m:
        return False
    h, mi = int(m.group(1)), int(m.group(2))
    q = quote.lower().replace(" ", "").replace(".", ":")
    h12 = h % 12 or 12
    forms = {f"{h}:{mi:02d}", f"{h:02d}:{mi:02d}", f"{h12}:{mi:02d}"}
    if mi == 0:
        forms |= {f"{h12}{'am' if h < 12 else 'pm'}", f"{h}h"}
    return any(f in q for f in forms)


def _altitude(v):
    """Metres from '2,260 m' or '3.2 km'."""
    if v is None:
        return None
    n = _int(v)
    if n is None:
        return None
    return int(float(re.search(r"\d[\d,]*(?:\.\d+)?", str(v)).group().replace(",", "")) * 1000) \
        if re.search(r"km", str(v), re.I) else n


def _minutes(v, default=90):
    """Minutes from '90', '2 hours', '1.5h'."""
    if v is None:
        return default
    m = re.search(r"\d+(?:\.\d+)?", str(v))
    if not m:
        return default
    x = float(m.group())
    return int(x * 60) if re.search(r"h(our|r)?s?\b", str(v), re.I) and not re.search(r"min", str(v), re.I) else int(x)


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
    opens, closes = str(get(d, "opens", "") or ""), str(get(d, "closes", "") or "")
    opens = opens if _time_in_quote(opens, c.evidence.quote) else ""
    closes = closes if _time_in_quote(closes, c.evidence.quote) else ""
    ft = _int(get(d, "from_center_min"))
    best = "sunrise" if str(get(d, "best_time", "")).lower() == "sunrise" and \
        re.search(r"sunrise|dawn|early morning", c.evidence.quote, re.I) else "any"
    return Place(
        travel_min=ft if ft and ft >= 60 else None, best_time=best, opens=opens, closes=closes,
        name=c.subject.strip(), city=str(get(d, "city", "")), kind=str(get(d, "kind", "sight")),
        duration_min=max(30, min(_minutes(get(d, "duration_min")), 480)),
        effort=max(1, min(_int(get(d, "effort"), 1), 3)), altitude_m=_altitude(get(d, "altitude_m")),
        min_age=_int(get(d, "min_age")), hidden_gem=hidden_gem, evidence=[c.evidence], notes=c.text)


def research_places(r: Researcher, brief: TripBrief) -> list[Place]:
    dest = brief.destination
    who = "families with older parents" if any(t.age >= 60 for t in brief.travellers) else \
        "families with young children" if any(t.age < 12 for t in brief.travellers) else "visitors"
    interests = [i for i in brief.interests if i.lower() not in ("food", "street food", "eating")]
    general = [f"top things to do in {dest}", f"{dest} attractions {who}", f"{dest} day trips from the city"]
    instr = PLACES_INSTR.format(destination=dest)
    from fairfare.planning.graph import parallel
    jobs = [lambda: ("", r.run(general, "place", instr, PLACES_SCHEMA))] + [
        (lambda i=i: (i, r.run([f"best {i} places in {dest}"], "place", instr, PLACES_SCHEMA))) for i in interests[:4]]
    places = []
    for tag, claims in parallel(*jobs, workers=5):
        for c in claims:
            p = claim_to_place(c)
            p.tags = [tag] if tag else []
            places.append(p)
    places = merge_similar(places)
    return llm_merge_aliases(r.llm, places)


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
    if getattr(r.search, "digest_mode", False):  # site: operators do not survive summarised search
        queries = [f"reddit {dest} hidden gems locals recommend", f"reddit {dest} tourist scams to avoid",
                   f"{dest} travel forum underrated places"]
    else:
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
    "required documents, fees, validity, how early to apply and where (official portal or embassy). "
    "Quote the exact sentence."
)
VISA_SCHEMA = '"requirement": "visa_free|evisa|visa_required|unclear", "days": number'


def research_visa(r: Researcher, brief: TripBrief) -> list[Claim]:
    q = [f"{brief.destination} visa requirements for {brief.passport} citizens official government e-visa portal",
         f"{brief.destination} entry rules {brief.passport} passport {brief.start.year}"]
    claims = r.run(q, "visa", VISA_INSTR.format(passport=brief.passport, destination=brief.destination), VISA_SCHEMA)
    for c in claims:
        c.data["official"] = is_official(c.evidence.url)
    return tidy(sorted(claims, key=lambda c: not c.data["official"]), limit=5)


# ---------- ground transport ----------

TRANSPORT_INSTR = (
    "Extract practical ground transport a visitor can actually use in {destination}: ride-hailing apps, metro/train/"
    "airport express, taxi rules, how to get from the airport to the centre. Skip vendor adverts and booking-site "
    "listings. Include a phone/URL in data.contact ONLY if it appears verbatim in the page."
)
TRANSPORT_SCHEMA = '"type": "ride_hailing|rental|transfer|train|driver|guide", "name": "string", "contact": "string"'


def research_transport(r: Researcher, brief: TripBrief) -> list[Claim]:
    d = brief.destination
    q = [f"{d} taxi apps tourists ride hailing", f"{d} airport to city centre transport options",
         f"{d} car rental with driver tourists"]
    if " and " in d.split(",")[0].lower():  # multi-city trip: how to get between the cities
        q.insert(1, f"{d.split(',')[0]} train between cities travel time")
    if getattr(r.search, "digest_mode", False):
        q = q[:2]
    claims = r.run(q, "transport", TRANSPORT_INSTR.format(destination=d), TRANSPORT_SCHEMA)
    for c in claims:
        contact = str(get(c.data, "contact", ""))
        if contact and _norm_digits(contact) not in _norm_digits(c.evidence.quote):
            c.data["contact"] = ""  # a contact not present in the quote is not verified
    return tidy(claims, limit=6)


def _norm_digits(s: str) -> str:
    return re.sub(r"[^\w@.]", "", s.lower())


# ---------- near-duplicate tidying ----------


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def tidy(claims: list[Claim], limit: int = 8, similarity: float = 0.72) -> list[Claim]:
    """Drop near-duplicate statements (same fact from several pages) and cap the list. Order is kept."""
    from difflib import SequenceMatcher
    kept: list[Claim] = []
    for c in claims:
        k = _key(c.text)
        if any(SequenceMatcher(None, k, _key(o.text)).ratio() >= similarity or
               (len(k.split()) >= 4 and set(k.split()) <= set(_key(o.text).split())) for o in kept):
            continue
        kept.append(c)
    return kept[:limit]


# ---------- costs ----------

COST_INSTR = (
    "Extract concrete prices a visitor to {destination} would pay: airport-to-city transfer or taxi fare, metro/train "
    "ticket, entry fees for major sights, a typical restaurant meal, street food, a SIM card, hotel per night. "
    "The quote MUST contain the number. subject = what it costs (e.g. 'Airport taxi to centre')."
)
COST_SCHEMA = ('"item": "airport_transfer|taxi|public_transport|entry_ticket|meal|sim_card|hotel|other", '
               '"amount_low": number, "amount_high": number, "currency": "string", "per": "person|group|night|ride"')


def research_costs(r: Researcher, brief: TripBrief) -> list[Claim]:
    d = brief.destination
    q = [f"{d} typical prices tourists taxi airport transfer meal cost", f"{d} entry fees attractions prices SIM card cost"]
    claims = r.run(q, "cost", COST_INSTR.format(destination=d), COST_SCHEMA)
    good = [c for c in claims if re.search(r"\d", c.evidence.quote) and get(c.data, "currency")]
    return tidy(good, limit=10)


# ---------- where to stay ----------

STAY_INSTR = (
    "Extract named hotels, riads, hostels or neighbourhoods that the page recommends for {who} visiting "
    "{destination} on a {tier} budget. subject = the hotel or neighbourhood name."
)
STAY_SCHEMA = '"type": "hotel|hostel|neighbourhood", "area": "string", "price_note": "string"'


def research_stay(r: Researcher, brief: TripBrief) -> list[Claim]:
    who = "families" if len(brief.travellers) > 2 else "couples" if len(brief.travellers) == 2 else "solo travellers"
    extra = " ".join(i for i in brief.interests if i.lower() in ("riad", "riads", "ryokan", "hostel", "boutique"))
    q = [f"best area and hotels to stay in {brief.destination} for {who} {brief.budget_tier} {extra} price per night".replace("  ", " ")]
    claims = r.run(q, "stay", STAY_INSTR.format(who=who, destination=brief.destination, tier=brief.budget_tier), STAY_SCHEMA)
    return tidy(claims, limit=6)


# ---------- food ----------

FOOD_INSTR = (
    "Extract local dishes worth trying and named restaurants, street-food areas or food markets in {destination}. "
    "subject = dish or venue name; data.kind says which. {diet}"
)
FOOD_SCHEMA = '"kind": "dish|restaurant|market", "vegetarian": "yes|no|unknown", "area": "string"'


def research_food(r: Researcher, brief: TripBrief) -> list[Claim]:
    diet = brief.dietary or [i for i in brief.interests if "vegetarian" in i.lower() or "vegan" in i.lower()]
    extra = f"Note which are suitable for: {', '.join(diet)}." if diet else ""
    d = brief.destination
    q = [f"best local food to try in {d} restaurants street food", f"{d} best restaurants for dinner locals recommend"]
    if any(t.age < 12 for t in brief.travellers):
        q.append(f"{d} family friendly restaurants with kids")
    elif any(t.age >= 65 or t.mobility == "limited" for t in brief.travellers):
        q.append(f"{d} quiet comfortable restaurants easy access step-free")
    if any("trap" in a.lower() for a in brief.avoid):
        q.append(f"{d} where locals eat not touristy restaurants")
    if diet:
        q.append(f"{brief.destination} {diet[0]} friendly restaurants")
    claims = r.run(q, "food", FOOD_INSTR.format(destination=brief.destination, diet=extra), FOOD_SCHEMA)
    claims = [c for c in claims if seasonal_ok(c.evidence.quote + " " + c.text, brief.start.month)]
    return tidy(claims, limit=16)


# ---------- emergency and local contacts ----------

CONTACT_INSTR = (
    "Extract emergency and consular contacts for a {passport} traveller in {destination}: police, ambulance, tourist "
    "police, the {passport} embassy or consulate. Put the phone number in data.phone ONLY if it is in the quote."
)
CONTACT_SCHEMA = '"type": "emergency|embassy|tourist_police|hospital", "phone": "string"'


def research_contacts(r: Researcher, brief: TripBrief) -> list[Claim]:
    d = brief.destination
    q = [f"{d} emergency numbers police ambulance tourists", f"{brief.passport} embassy in {d} contact"]
    claims = r.run(q, "contact", CONTACT_INSTR.format(passport=brief.passport, destination=d), CONTACT_SCHEMA)
    for c in claims:
        phone = str(get(c.data, "phone", ""))
        if phone and _norm_digits(phone) not in _norm_digits(c.evidence.quote):
            c.data["phone"] = ""
    return tidy(claims, limit=6)


# ---------- seasonality ----------

MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")
SEASON_MONTHS = {"spring": (3, 4, 5), "summer": (6, 7, 8), "autumn": (9, 10, 11), "fall": (9, 10, 11),
                 "winter": (12, 1, 2)}


def seasonal_ok(text: str, month: int) -> bool:
    """False when the text ties something to months or seasons that do not include the trip month."""
    t = text.lower()
    named = [i + 1 for i, m in enumerate(MONTHS) if re.search(rf"\b{m}\b", t) and m != "may"] + \
        ([5] if re.search(r"\bin may\b|\bmay (to|through|-)|\bfrom may\b", t) else [])
    seasons = [s for s in SEASON_MONTHS if re.search(rf"\b{s}\b", t)]
    if named and month not in named and not any(abs(month - n) <= 1 or abs(month - n) == 11 for n in named):
        return False
    if seasons and not named and not any(month in SEASON_MONTHS[s] for s in seasons):
        return False
    return True


# ---------- practical (money, SIM, tipping) ----------

PRACTICAL_INSTR = (
    "Extract practical facts for a visitor to {destination}: mobile SIM/eSIM options and where to buy them, "
    "currency and how to get cash (ATMs, cards accepted), tipping customs, plug type and voltage, and whether tap "
    "water is safe. subject = the topic (e.g. 'SIM card', 'Tipping')."
)
PRACTICAL_SCHEMA = '"topic": "sim|money|tipping|plug|water|language|other"'


def research_practical(r: Researcher, brief: TripBrief) -> list[Claim]:
    d = brief.destination
    q = [f"{d} tourist practical tips SIM card currency ATM tipping plug"]
    claims = r.run(q, "practical", PRACTICAL_INSTR.format(destination=d), PRACTICAL_SCHEMA)
    return tidy(claims, limit=8)


# ---------- access (steep, stairs, hard walking) ----------

ACCESS_INSTR = (
    "Extract places, neighbourhoods or attractions in {destination} that are steep, need many stairs or steps, involve "
    "hiking or long uphill walking, or are hard for wheelchair users and people with limited mobility. subject = the "
    "exact place or neighbourhood name as written; the quote must name it and the difficulty."
)
ACCESS_SCHEMA = '"difficulty": "steep|stairs|hiking|uneven|other"'


def research_access(r: Researcher, brief: TripBrief) -> list[Claim]:
    """Only needed when someone has limited mobility or the group avoids steep walks."""
    avoids = any(k in a.lower() for a in brief.avoid for k in ("steep", "stairs", "hike", "hiking", "climb", "walking"))
    if not (avoids or any(t.mobility == "limited" for t in brief.travellers)):
        return []
    d = brief.destination
    q = [f"{d} steep hills stairs hard for elderly wheelchair accessible which attractions neighbourhoods to avoid"]
    return tidy(r.run(q, "access", ACCESS_INSTR.format(destination=d), ACCESS_SCHEMA), limit=12)
