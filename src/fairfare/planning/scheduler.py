"""Deterministic family-aware scheduler. Picks from *verified* places only."""
from __future__ import annotations

from datetime import date

from fairfare.models import Block, ClosureNotice, DayPlan, Place, TripBrief, TripPlan
from fairfare.planning import rules as R


def _rank(p: Place, brief: TripBrief) -> tuple:
    name = p.name.lower()
    must = any(m.lower() in name for m in brief.must_do)
    interest = any(i.lower() in p.kind.lower() or i.lower() in name for i in brief.interests)
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
    return None


class Planner:
    def __init__(self, brief: TripBrief, places: list[Place], notices: list[ClosureNotice],
                 exclude: set[str] | None = None) -> None:
        self.brief, self.notices = brief, notices
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
        days = R.trip_days(self.brief)
        used: set[str] = set()
        last_alt_index = -10
        out: list[DayPlan] = []
        for i, day in enumerate(days):
            role = R.role_of(i, len(days))
            picks, cap = self._pick(day, i, len(days), role, used, last_alt_index)
            if any((p.altitude_m or 0) >= R.ALTITUDE_M for p in picks):
                last_alt_index = i
            used.update(p.name for p in picks)
            dp = self._layout(day, role, picks, cap)
            dp.plan_b = self._plan_b(day, used)
            out.append(dp)
        for n in self.notices:
            for p in self.candidates:
                if any(k in p.name.lower() or p.name.lower() in k for k in n.keywords if k) and \
                        n.overlaps(self.brief.start, self.brief.end):
                    end = n.closed_to.isoformat() if n.closed_to else "reopening date not stated"
                    self.warnings.append(
                        f"{p.name} is reported closed from {n.closed_from.isoformat()} ({end}); kept off those days "
                        f"[{n.confidence} confidence]. Confirm with the operator.")
        return TripPlan(brief=self.brief, days=out, warnings=sorted(set(self.warnings)), closures=self.notices,
                        places=[p for p in self.candidates])

    # ---- selection ----

    def _pick(self, day: date, i: int, n: int, role: str, used: set[str], last_alt: int) -> tuple[list[Place], int]:
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
            picks.append(p)
            spent += cost
            city = city or p.city
        picks.sort(key=lambda p: -p.effort)  # strenuous in the morning
        return picks, cap

    def _plan_b(self, day: date, used: set[str]) -> str:
        for p in self.candidates:
            if p.name not in used and p.effort == 1 and not R.closed_on(p, day, self.notices):
                where = f" ({p.city})" if p.city else ""
                return f"Plan B for weather or a closure: {p.name}{where}."
        return ""

    # ---- layout ----

    def _layout(self, day: date, role: str, picks: list[Place], cap: int) -> DayPlan:
        blocks: list[Block] = []
        rest_a, rest_b = R.rest_window(self.brief)
        load = sum(R.place_cost(p) for p in picks)
        if role == "arrival":
            blocks.append(Block(start="12:00", end="15:00", kind="arrival",
                                title="Arrive, transfer to hotel, check in", notes="Adjust to your flight times."))
            self._activities(blocks, picks, 16 * 60, R.DAY_END, "")
        elif role == "departure":
            self._activities(blocks, picks, R.DAY_START, 11 * 60 + 30, "")
            blocks.append(Block(start="12:00", end="13:00", kind="departure",
                                title="Transfer to airport", notes="Leave at least 3 hours before departure."))
        else:
            self._activities(blocks, picks, R.DAY_START, R.LUNCH[0], "")
            blocks.append(Block(start=R.hm(R.LUNCH[0]), end=R.hm(R.LUNCH[1]), kind="meal", title="Lunch"))
            blocks.append(Block(start=R.hm(rest_a), end=R.hm(rest_b), kind="rest", title="Rest at the hotel"))
            afternoon_start = rest_b
            # anything that did not fit in the morning window goes after the rest
            placed = {b.place for b in blocks if b.kind == "activity"}
            rest = [p for p in picks if p.name not in placed]
            self._activities(blocks, rest, afternoon_start, R.DAY_END, "")
        if role != "departure":
            blocks.append(Block(start=R.hm(R.DINNER[0]), end=R.hm(R.DINNER[1]), kind="meal", title="Dinner"))
        blocks.sort(key=lambda b: R.mins(b.start))
        return DayPlan(day=day, role=role, blocks=blocks, load=load, cap=cap)  # type: ignore[arg-type]

    def _activities(self, blocks: list[Block], picks: list[Place], start: int, limit: int, _: str) -> None:
        cursor, prev_city = start, ""
        for p in picks:
            t = R.transfer_minutes(prev_city, p.city) if prev_city or blocks else 0
            has_prior_activity = any(b.kind == "activity" for b in blocks)
            begin = cursor + (t if has_prior_activity else 0)
            end = begin + p.duration_min
            if end > limit:
                continue
            if has_prior_activity:
                blocks.append(Block(start=R.hm(cursor), end=R.hm(begin), kind="transfer",
                                    title=f"Transfer to {p.name}"))
            note = f"{p.city}. " if p.city else ""
            if (p.altitude_m or 0) >= R.ALTITUDE_M:
                note += f"Altitude about {p.altitude_m} m: go slowly, carry water. "
            blocks.append(Block(start=R.hm(begin), end=R.hm(end), kind="activity", title=p.name, place=p.name,
                                notes=(note + p.notes).strip(), sources=p.sources))
            cursor, prev_city = end, p.city
