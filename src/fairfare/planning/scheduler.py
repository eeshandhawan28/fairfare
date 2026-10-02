"""Deterministic family-aware scheduler. Picks from *verified* places only."""
from __future__ import annotations

import math
import re
from datetime import date

from fairfare.models import Block, ClosureNotice, DayPlan, Place, TripBrief, TripPlan
from fairfare.planning import rules as R
from fairfare.planning.places import MAX_ONE_WAY_MIN, annotate_travel, is_food_item

STEEP_WORDS = ("steep", "stairs", "steps", "uphill", "climb", "strenuous", "demanding", "scramble", "hilltop",
               "highest point", "cobbled hill")
WILD_RE = re.compile(r"\b(mountains?|gorge|canyon|waterfalls?|national park|nature (park|reserve)|trek\w*|volcano)\b", re.I)
HIKE_RE = re.compile(r"\b(hike|hikes|hiking|trek|treks|trekking|trail|trails|summit|ridge|peak|cliff)\b", re.I)
MASS_VISITORS_RE = re.compile(r"\b\d+(\.\d+)?\s*million\s+(annual\s+|yearly\s+)?(visitors|tourists|people)\b|\bmillions of visitors\b", re.I)
CROWD_WORDS = ("crowd", "busiest", "most visited", "most popular", "long queue", "packed with", "tourist hub")


def _rank(p: Place, brief: TripBrief) -> tuple:
    name = p.name.lower()
    must = any(m.lower() in name for m in brief.must_do)
    interest = any(i.lower() in p.kind.lower() or i.lower() in name or i.lower() in p.notes.lower()
                   for i in brief.interests)
    return (0 if must else 1 if p.hidden_gem else 2 if interest else 3, -len(p.evidence), p.effort, name)


def eligible(p: Place, brief: TripBrief) -> str | None:
    """Reason a place can never be scheduled for this family, else None."""
    if not p.evidence:
        return "no verified source"
    if any(a.lower() in p.name.lower() or a.lower() == p.kind.lower() for a in brief.avoid):
        return "on your avoid list"
    youngest = min((t.age for t in brief.travellers), default=99)
    if p.min_age is not None and youngest < p.min_age:
        return f"minimum age {p.min_age}"
    if is_food_item(p):
        return "food item (listed under What to eat)"
    if p.travel_min is not None and p.travel_min > min(MAX_ONE_WAY_MIN, R.max_one_way_min(brief)):
        return f"too far for this group's day trip (about {p.travel_min / 60:.1f} h each way)"
    if p.travel_min is not None and R.excursion_hours(p) > min(R.dinner_window(brief)[0] / 60 - 8.5, R.max_excursion_hours(brief)):
        return f"day trip would take {R.excursion_hours(p):.0f} h door to door"
    if any("crowd" in a.lower() for a in brief.avoid):
        text = " ".join(e.quote for e in p.evidence).lower() + " " + p.notes.lower()
        if any(w in text for w in CROWD_WORDS) or MASS_VISITORS_RE.search(text):
            return "busy and crowded (on your avoid list)"
    limited = any(t.mobility == "limited" for t in brief.travellers)
    avoids_steep = any(k in a.lower() for a in brief.avoid for k in ("steep", "stairs", "hike", "hiking", "climb", "walking"))
    if limited or avoids_steep:
        text = " ".join(e.quote for e in p.evidence).lower() + " " + p.notes.lower()
        if any(w in text for w in STEEP_WORDS) or HIKE_RE.search(f"{p.name} {p.kind} {text}"):
            return "steep, stairs or hard walking (mobility or avoid list)"
        if p.effort >= 3:
            return "strenuous (mobility or avoid list)"
        if WILD_RE.search(f"{p.name} {p.kind}"):
            return "wilderness or mountain outing (mobility or avoid list)"
    return None


def windows_for(role: str, brief: TripBrief, early: bool = False) -> list[tuple[int, int]]:
    if role == "arrival":
        return [(16 * 60, R.day_end(brief))]
    if role == "departure":
        return [(R.DAY_START, 11 * 60 + 30)]
    wins = [(R.DAY_START, R.LUNCH[0]), (R.rest_window(brief)[1], R.day_end(brief))]
    return [(5 * 60 + 30, 9 * 60 + 30)] + wins if early else wins


def _hhmm(v: str) -> int | None:
    try:
        return R.mins(v) if v else None
    except ValueError:
        return None


def place_all(picks: list[Place], windows: list[tuple[int, int]]) -> tuple[list[tuple[Place, int, int, int]], bool]:
    """Assign picks to time windows in order. Returns ([(place, transfer_start, begin, end)], all_fit)."""
    placed: list[tuple[Place, int, int, int]] = []
    wi, cursor, prev_city, first = 0, windows[0][0], "", True
    for p in picks:
        while True:
            if wi >= len(windows):
                return placed, False
            wstart, wend = windows[wi]
            cursor = max(cursor, wstart)
            t = 0 if first else R.transfer_minutes(prev_city, p.city)
            begin = cursor + t
            if p.best_time == "sunrise" and wstart >= 9 * 60 + 30:
                wi += 1  # sunrise-only: never after the early window
                continue
            opens, closes = _hhmm(p.opens), _hhmm(p.closes)
            if opens is not None and begin < opens:
                begin = opens
            end = begin + p.duration_min
            if closes is not None and end > closes:
                wi += 1
                continue
            if end <= wend:
                placed.append((p, cursor, begin, end))
                cursor, prev_city, first = end, p.city, False
                break
            wi += 1
    return placed, True


class Planner:
    def __init__(self, brief: TripBrief, places: list[Place], notices: list[ClosureNotice],
                 exclude: set[str] | None = None, checked: set[str] | None = None) -> None:
        places = annotate_travel(places, brief)
        self.brief, self.notices = brief, notices
        self.checked = {c.lower() for c in checked} if checked is not None else None
        self.exclude = {e.lower() for e in (exclude or set())}
        self.warnings: list[str] = []
        self.candidates: list[Place] = []
        for p in sorted(places, key=lambda x: _rank(x, brief)):
            if p.name.lower() in self.exclude:
                continue
            reason = eligible(p, brief)
            if reason:
                if any(m.lower() in p.name.lower() for m in brief.must_do):
                    self.warnings.append(f"Must-do '{p.name}' not scheduled: {reason}.")
                continue
            self.candidates.append(p)

    def plan(self) -> TripPlan:
        self.full_days = 0
        cities = [p.city.strip().lower() for p in self.candidates if p.city.strip() and p.travel_min is None]
        self.arrival_city = max(set(cities), key=cities.count) if cities else ""
        days = R.trip_days(self.brief)
        used: set[str] = set()
        last_alt_index = -10
        last_full_index = -10
        prev_city = ""
        out: list[DayPlan] = []
        for i, day in enumerate(days):
            role = R.role_of(i, len(days))
            picks, cap = self._pick(day, i, len(days), role, used, last_alt_index, last_full_index)
            if picks and R.is_full_day(picks[0]):
                last_full_index = i
                self.full_days += 1
            if any((p.altitude_m or 0) >= R.ALTITUDE_M for p in picks):
                last_alt_index = i
            used.update(p.name for p in picks)
            dp = self._layout(day, role, picks, cap)
            prev_city = self._city_hop(dp, picks, prev_city)
            dp.plan_b = self._plan_b(day, used)
            out.append(dp)
        rank = {"low": 0, "medium": 1, "high": 2}
        best: dict[tuple, ClosureNotice] = {}
        for n in self.notices:
            for p in self.candidates:
                if any(k in p.name.lower() or p.name.lower() in k for k in n.keywords if k) and \
                        n.overlaps(self.brief.start, self.brief.end):
                    key = (p.name, n.closed_from)
                    if key not in best or (rank[n.confidence], n.closed_to is not None) > \
                            (rank[best[key].confidence], best[key].closed_to is not None):
                        best[key] = n
        for (name, _), n in best.items():
            end = f"until {n.closed_to.isoformat()}" if n.closed_to else "reopening date not stated"
            self.warnings.append(
                f"{name} is reported closed from {n.closed_from.isoformat()} ({end}); kept off those days "
                f"[{n.confidence} confidence]. Confirm with the operator.")
        if self.checked is not None:
            scheduled = {b.place for d in out for b in d.blocks if b.kind == "activity" and b.place}
            open_air = ("district", "quarter", "street", "area", "old town", "neighbourhood", "neighborhood", "square", "walk")
            unchecked = [n for n in sorted(scheduled) if n.lower() not in self.checked
                         and not any(w in n.lower() for w in open_air)]
            if unchecked:
                self.warnings.append("Closures and season not checked online for: " + ", ".join(unchecked) +
                                     ". Confirm each is open on your dates.")
        self.warnings.extend(self._uncovered_interests(out))
        empty = [d.day.strftime("%d %b") for d in out if d.role == "full" and not any(
            b.kind == "activity" for b in d.blocks)]
        if empty:
            self.warnings.append(
                f"No verified activity fits on {', '.join(empty)}. Research found {len(self.candidates)} usable "
                "place(s); add must-dos, loosen the pace, or re-run with more search coverage.")
        return TripPlan(brief=self.brief, days=out, warnings=sorted(set(self.warnings)), closures=self.notices,
                        places=[p for p in self.candidates])

    def _uncovered_interests(self, days: list[DayPlan]) -> list[str]:
        """Interests the schedule does not serve, said plainly instead of silently dropped."""
        skip = {"food", "street food", "cuisine", "eating", "sights", "sightseeing", "culture", "easy sights"}
        scheduled = {b.place for d in days for b in d.blocks if b.kind == "activity" and b.place}
        text = " ".join(f"{p.name} {p.kind} {p.notes}" for p in self.candidates if p.name in scheduled).lower()
        out = []
        for i in self.brief.interests:
            key = i.lower().strip()
            if key in skip:
                continue
            toks = [t for t in re.findall(r"[a-z]{4,}", key) if t not in {"day", "trip", "trips", "sights", "local"}]
            stem = lambda t: t[:-1] if t.endswith("s") else t
            if key in text or any(stem(t) in text for t in toks):
                continue
            out.append(i)
        if not out:
            return []
        return ["No verified place for your interest in " + ", ".join(out) +
                " made it into the schedule. Ask your hotel or a local operator to arrange it, or add it as a must-do."]

    # ---- selection ----

    def _pick(self, day: date, i: int, n: int, role: str, used: set[str], last_alt: int,
              last_full: int = -10) -> tuple[list[Place], int]:
        cap = R.group_cap(self.brief)
        if role != "full":
            cap = max(1, round(cap * R.ARRIVAL_DEPARTURE_FACTOR))
        max_n = 1 if role != "full" else R.PACE_MAX_ACTIVITIES[self.brief.pace]
        sensitive = R.altitude_sensitive(self.brief)
        picks: list[Place] = []
        spent, city = 0, ""
        for p in self.candidates:
            if p.name in used or len(picks) >= max_n:
                continue
            if role == "departure":
                continue  # flight times are unknown: no fixed activity on the way out (see the optional suggestion)
            if role == "arrival" and any(m.lower() in p.name.lower() for m in self.brief.must_do):
                continue  # never spend a must-do on the tired arrival evening
            if role == "arrival" and p.city and self.arrival_city and p.city.lower() != self.arrival_city:
                continue  # no cross-city hop on the tired arrival evening
            if R.closed_on(p, day, self.notices):
                continue
            high = (p.altitude_m or 0) >= R.ALTITUDE_M
            if high and (not R.altitude_allowed(i, n) or (sensitive and i - last_alt < 2)):
                continue
            if picks and p.city and city and p.city.lower() != city.lower():
                continue
            cost = R.place_cost(p)
            if spent + cost > cap:
                continue
            if R.is_full_day(p) and (picks or role != "full"):
                continue  # a full-day outing needs a whole ordinary day to itself
            if R.is_full_day(p) and (R.needs_long_rest(self.brief) or self.brief.pace != "packed") and i - last_full < 2:
                continue  # no back-to-back full-day outings unless the pace is packed and the group is fit
            if R.is_full_day(p) and self.brief.pace != "packed" and self.full_days >= math.ceil(
                    n / (4 if self.brief.pace == "relaxed" else 3)):
                continue  # keep day trips a minority of the trip
            if picks and R.is_full_day(picks[0]):
                continue
            if not R.is_full_day(p):
                tentative = sorted(picks + [p], key=lambda x: (x.best_time != "sunrise", -x.effort))
                early = any(x.best_time == "sunrise" for x in tentative)
                if early and role != "full":
                    continue
                if not place_all(tentative, windows_for(role, self.brief, early))[1]:
                    continue  # would not fit the day's time windows
                if role != "full" and (p.effort > 1 or p.duration_min > 120):
                    continue  # arrival and departure days stay light
            picks.append(p)
            spent += cost
            city = city or p.city
        picks.sort(key=lambda p: (p.best_time != "sunrise", -p.effort))
        return picks, cap

    def _city_hop(self, dp: DayPlan, picks: list[Place], prev_city: str) -> str:
        """Moving between base cities (Kyoto to Osaka) needs an explicit travel block."""
        acts = [b for b in dp.blocks if b.kind == "activity"]
        cities = [p.city for p in picks if p.city and not R.is_excursion(p)]
        if not cities:
            return prev_city
        first = cities[0]
        if prev_city and first.lower() != prev_city.lower() and acts and R.mins(acts[0].start) < 12 * 60 \
                and dp.role == "full":
            begin = R.mins(acts[0].start)
            start = max(8 * 60, begin - 60)
            if start < begin:
                dp.blocks.append(Block(start=R.hm(start), end=R.hm(begin), kind="transfer",
                                       title=f"Travel from {prev_city} to {first} (about 1 h by train or car)"))
                dp.blocks.sort(key=lambda b: R.mins(b.start))
        return cities[-1]

    def _plan_b(self, day: date, used: set[str]) -> str:
        for p in self.candidates:
            if p.name not in used and p.effort == 1 and not R.is_full_day(p) and not R.closed_on(p, day, self.notices) \
                    and not (p.travel_min and p.travel_min >= 60) and p.city:
                where = f" ({p.city})" if p.city else ""
                return f"Plan B for weather or a closure: {p.name}{where}."
        return ""

    # ---- layout ----

    def _layout(self, day: date, role: str, picks: list[Place], cap: int) -> DayPlan:
        blocks: list[Block] = []
        rest_a, rest_b = R.rest_window(self.brief)
        load = sum(R.place_cost(p) for p in picks)
        if role != "full":
            if role == "arrival":
                blocks.append(Block(start="12:00", end="15:00", kind="arrival",
                                    title="Arrive, transfer to hotel, check in", notes="Adjust to your flight times."))
            self._emit(blocks, picks, windows_for(role, self.brief))
            if role == "departure":
                opt = self._plan_b(day, set())
                hint = opt.replace("Plan B for weather or a closure:", "Optional if your flight is after 15:00:")
                blocks.append(Block(start="12:00", end="13:00", kind="departure",
                                    title="Transfer to airport",
                                    notes="Leave at least 3 hours before departure; adjust to your flight. " + hint))
        elif picks and R.is_full_day(picks[0]):
            p = picks[0]
            note = "Full-day outing: start early and take lunch on the way. "
            if (p.altitude_m or 0) >= R.ALTITUDE_M:
                note += f"Altitude about {p.altitude_m} m. "
            if R.is_excursion(p):
                start = 8 * 60
                tm = p.travel_min or 0
                est = " (estimated, confirm the route)" if p.travel_estimated else ""
                blocks.append(Block(start=R.hm(start), end=R.hm(start + tm), kind="transfer",
                                    title=f"Travel to {p.name} (about {tm / 60:.1f} h each way{est})"))
                vs = start + tm
                ve = vs + p.duration_min
                blocks.append(Block(start=R.hm(vs), end=R.hm(ve), kind="activity", title=p.name, place=p.name,
                                    notes=(note + p.notes).strip(), sources=p.sources))
                blocks.append(Block(start=R.hm(ve), end=R.hm(ve + tm), kind="transfer",
                                    title="Return to the hotel"))
            else:
                start = 9 * 60
                end = min(start + p.duration_min, R.dinner_window(self.brief)[0] - 30)
                blocks.append(Block(start=R.hm(start), end=R.hm(end), kind="activity", title=p.name, place=p.name,
                                    notes=(note + p.notes).strip(), sources=p.sources))
        else:
            early = any(p.best_time == "sunrise" for p in picks)
            self._emit(blocks, picks, windows_for(role, self.brief, early))
            blocks.append(Block(start=R.hm(R.LUNCH[0]), end=R.hm(R.LUNCH[1]), kind="meal", title="Lunch"))
            blocks.append(Block(start=R.hm(rest_a), end=R.hm(rest_b), kind="rest", title="Rest at the hotel"))
        if role != "departure":
            d0, d1 = R.dinner_window(self.brief)
            blocks.append(Block(start=R.hm(d0), end=R.hm(d1), kind="meal", title="Dinner"))
        blocks.sort(key=lambda b: R.mins(b.start))
        return DayPlan(day=day, role=role, blocks=blocks, load=load, cap=cap)  # type: ignore[arg-type]

    def _emit(self, blocks: list[Block], picks: list[Place], windows: list[tuple[int, int]]) -> None:
        placed, ok = place_all(picks, windows)
        if not ok:
            raise RuntimeError("scheduler invariant broken: picks do not fit their windows")
        for i, (p, cursor, begin, end) in enumerate(placed):
            if i > 0:
                blocks.append(Block(start=R.hm(cursor), end=R.hm(begin), kind="transfer", title=f"Transfer to {p.name}"))
            note = f"{p.city}. " if p.city else ""
            if (p.altitude_m or 0) >= R.ALTITUDE_M:
                note += f"Altitude about {p.altitude_m} m: go slowly, carry water. "
            blocks.append(Block(start=R.hm(begin), end=R.hm(end), kind="activity", title=p.name, place=p.name,
                                notes=(note + p.notes).strip(), sources=p.sources))
