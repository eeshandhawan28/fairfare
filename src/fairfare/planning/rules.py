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


def is_full_day(p: Place) -> bool:
    return p.duration_min > FULL_DAY_MIN


def place_cost(p: Place) -> int:
    """Effort units. A full-day outing counts as four hours at its effort level."""
    hours = 4 if is_full_day(p) else math.ceil(p.duration_min / 60)
    return p.effort * hours


def needs_long_rest(brief: TripBrief) -> bool:
    return any(t.age >= 50 or t.mobility == "limited" for t in brief.travellers)


def altitude_sensitive(brief: TripBrief) -> bool:
    return any(t.age >= 60 or t.mobility == "limited" for t in brief.travellers)


def rest_window(brief: TripBrief) -> tuple[int, int]:
    return (14 * 60, 16 * 60) if needs_long_rest(brief) else (14 * 60, 14 * 60 + 30)


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
    for n in notices:
        if any(k in name or name in k for k in n.keywords if k) and n.covers(day):
            return n
    return None


def transfer_minutes(prev_city: str, city: str) -> int:
    return 45 if prev_city and city and prev_city.lower() != city.lower() else 30
