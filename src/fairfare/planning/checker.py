"""Independent plan checker. It does not trust the scheduler: it re-derives every rule."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fairfare.models import ClosureNotice, Place, TripBrief, TripPlan
from fairfare.planning import rules as R
from fairfare.planning.scheduler import Planner


@dataclass
class Violation:
    rule: str
    day: str
    message: str
    place: Optional[str] = None


# rules whose fix is "drop this place and re-plan"
EXCLUDING = {"closed_place", "unverified_place", "min_age", "altitude_early"}


def check_plan(plan: TripPlan, places: list[Place], notices: list[ClosureNotice]) -> list[Violation]:
    brief = plan.brief
    by_name = {p.name: p for p in places}
    youngest = min((t.age for t in brief.travellers), default=99)
    out: list[Violation] = []
    n_days = len(plan.days)
    alt_days: list[int] = []
    for i, d in enumerate(plan.days):
        day = d.day.isoformat()
        acts = [b for b in d.blocks if b.kind == "activity"]
        load = 0
        for b in acts:
            p = by_name.get(b.place or "")
            if p is None or not p.evidence:
                out.append(Violation("unverified_place", day, f"{b.title} has no verified source.", b.place))
                continue
            load += R.place_cost(p)
            if R.closed_on(p, d.day, notices):
                out.append(Violation("closed_place", day, f"{p.name} is reported closed on {day}.", p.name))
            if p.min_age is not None and youngest < p.min_age:
                out.append(Violation("min_age", day, f"{p.name} requires age {p.min_age}+.", p.name))
            if (p.altitude_m or 0) >= R.ALTITUDE_M:
                alt_days.append(i)
                if not R.altitude_allowed(i, n_days):
                    out.append(Violation("altitude_early", day, f"{p.name} is at altitude too early in the trip.", p.name))
        role_cap = d.cap
        if load > role_cap:
            out.append(Violation("over_cap", day, f"Day load {load} exceeds cap {role_cap}."))
        if d.role == "full":
            if not any(b.kind == "meal" for b in d.blocks):
                out.append(Violation("no_meal", day, "No meal block."))
            if R.needs_long_rest(brief) and not any(
                    b.kind == "rest" and R.mins(b.end) - R.mins(b.start) >= 90 for b in d.blocks):
                out.append(Violation("no_rest", day, "Travellers 50+ or with limited mobility need a 90+ minute rest."))
        if d.role in ("arrival", "departure") and len(acts) > 1:
            out.append(Violation("half_day_overloaded", day, "More than one activity on an arrival/departure day."))
        ordered = sorted(d.blocks, key=lambda b: R.mins(b.start))
        for a, b in zip(ordered, ordered[1:]):
            if R.mins(b.start) < R.mins(a.end):
                out.append(Violation("overlap", day, f"'{a.title}' overlaps '{b.title}'."))
            if a.kind == "activity" and b.kind == "activity":
                out.append(Violation("no_transfer", day, f"No transfer between '{a.title}' and '{b.title}'."))
        if len(acts) > R.PACE_MAX_ACTIVITIES[brief.pace] and d.role == "full":
            out.append(Violation("too_many_activities", day, f"{len(acts)} activities exceeds the {brief.pace} pace."))
    if R.altitude_sensitive(brief):
        for a, b in zip(alt_days, alt_days[1:]):
            if b - a < 2:
                out.append(Violation("altitude_back_to_back", plan.days[b].day.isoformat(),
                                     "Altitude days too close together for older or limited-mobility travellers."))
    return out


def plan_with_repair(brief: TripBrief, places: list[Place], notices: list[ClosureNotice],
                     max_iter: int = 3) -> tuple[TripPlan, list[Violation], int]:
    """Plan, check, drop offending places, re-plan. Returns (plan, remaining violations, iterations)."""
    exclude: set[str] = set()
    for it in range(1, max_iter + 1):
        planner = Planner(brief, places, notices, exclude)
        plan = planner.plan()
        violations = check_plan(plan, places, notices)
        offenders = {v.place for v in violations if v.rule in EXCLUDING and v.place}
        if not offenders or offenders <= exclude:
            break
        exclude |= {o for o in offenders if o}
    for v in violations:
        plan.warnings.append(f"Plan check ({v.rule}, {v.day}): {v.message}")
    return plan, violations, it
