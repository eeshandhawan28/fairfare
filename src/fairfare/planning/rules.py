"""Shared family-pacing rules. Defaults are assumptions to tune, not sourced figures."""
from __future__ import annotations

import math
from datetime import date, timedelta

from fairfare.models import ClosureNotice, Place, TripBrief

DAY_START = 9 * 60 + 30
LUNCH = (13 * 60, 14 * 60)
DINNER = (19 * 60 + 30, 20 * 60 + 30)
DAY_END = 18 * 60 + 30
ALTITUDE_M = 2000
PACE_MULT = {"relaxed": 0.7, "balanced": 1.0, "packed": 1.25}
PACE_MAX_ACTIVITIES = {"relaxed": 2, "balanced": 3, "packed": 4}
ARRIVAL_DEPARTURE_FACTOR = 0.3


def max_one_way_min(brief: TripBrief) -> int:
    """Longest one-way day-trip drive this group should be sent on."""
    if needs_long_rest(brief):
        return 120
    return 150 if brief.pace == "relaxed" else 240


def wants_early_evening(brief: TripBrief) -> bool:
    return any(t.age < 8 for t in brief.travellers) or any("late" in a.lower() for a in brief.avoid)


def dinner_window(brief: TripBrief) -> tuple[int, int]:
    return (18 * 60, 19 * 60 + 15) if wants_early_evening(brief) else DINNER


def day_end(brief: TripBrief) -> int:
    """Last minute an activity may end: an hour before dinner."""
    return dinner_window(brief)[0] - 45


def hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def traveller_cap(age: int, limited: bool) -> int:
    base = 6 if age < 12 else 10 if age < 18 else 12 if age < 50 else 9 if age < 65 else 6
    return min(base, 5) if limited else base


def group_cap(brief: TripBrief) -> int:
    """Schedule to the slowest traveller, scaled by requested pace."""
    if not brief.travellers:
        return round(12 * PACE_MULT[brief.pace])
    base = min(traveller_cap(t.age, t.mobility == "limited") for t in brief.travellers)
    return max(2, round(base * PACE_MULT[brief.pace]))


FULL_DAY_MIN = 210  # longer than the morning window: the outing takes the whole day


def is_excursion(p: Place) -> bool:
    return p.travel_min is not None and p.travel_min >= 60


def is_full_day(p: Place) -> bool:
    return p.duration_min > FULL_DAY_MIN or is_excursion(p)


def excursion_hours(p: Place) -> float:
    """Door-to-door hours: travel both ways plus the visit."""
    return (2 * (p.travel_min or 0) + p.duration_min) / 60


def max_excursion_hours(brief: TripBrief) -> float:
    """Longest door-to-door day trip this group should be asked to do."""
    if min((t.age for t in brief.travellers), default=99) < 6 or needs_long_rest(brief):
        return 6.5
    return 10.0


def place_cost(p: Place) -> int:
    """Effort units. A full-day outing counts as four hours at its effort level."""
    hours = (max(4, math.ceil(excursion_hours(p) * 0.9)) if is_excursion(p) else 4) if is_full_day(p) \
        else math.ceil(p.duration_min / 60)
    return p.effort * hours


def needs_long_rest(brief: TripBrief) -> bool:
    """Older travellers, limited mobility, or a small child who needs a nap."""
    return any(t.age >= 50 or t.age < 6 or t.mobility == "limited" for t in brief.travellers)


def altitude_sensitive(brief: TripBrief) -> bool:
    return any(t.age >= 60 or t.mobility == "limited" for t in brief.travellers)


def rest_window(brief: TripBrief) -> tuple[int, int]:
    older = any(t.age >= 50 or t.mobility == "limited" for t in brief.travellers)
    if older:
        return (14 * 60, 16 * 60)
    if needs_long_rest(brief):  # a small child's nap: shorter, so the afternoon is still usable
        return (13 * 60 + 30, 15 * 60)
    return (14 * 60, 14 * 60 + 30)


def trip_days(brief: TripBrief) -> list[date]:
    return [brief.start + timedelta(days=i) for i in range((brief.end - brief.start).days + 1)]


def role_of(index: int, n_days: int) -> str:
    if index == 0:
        return "arrival"
    if index == n_days - 1 and n_days > 1:
        return "departure"
    return "full"


def altitude_allowed(index: int, n_days: int) -> bool:
    """No altitude on arrival day, and on short trips not before day 2."""
    return index >= (1 if n_days <= 3 else 2)


def closed_on(place: Place, day: date, notices: list[ClosureNotice]) -> ClosureNotice | None:
    name = place.name.lower()
    if day.strftime("%A").lower() in {d.lower() for d in place.closed_days}:
        return ClosureNotice(venue=place.name, keywords=[name], closed_from=day, closed_to=day,
                             reason="closed on this weekday", source="opening hours in the place's sources")
    for n in notices:
        if any(k in name or name in k for k in n.keywords if k) and n.covers(day):
            return n
    return None


def transfer_minutes(prev_city: str, city: str) -> int:
    return 60 if prev_city and city and prev_city.lower() != city.lower() else 30
