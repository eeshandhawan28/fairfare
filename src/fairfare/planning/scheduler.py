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


def windows_for(role: str, brief: TripBrief) -> list[tuple[int, int]]:
    if role == "arrival":
        return [(16 * 60, R.DAY_END)]
    if role == "departure":
        return [(R.DAY_START, 11 * 60 + 30)]
    return [(R.DAY_START, R.LUNCH[0]), (R.rest_window(brief)[1], R.DAY_END)]


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
            begin, end = cursor + t, cursor + t + p.duration_min
            if end <= wend:
                placed.append((p, cursor, begin, end))
                cursor, prev_city, first = end, p.city, False
                break
            wi += 1
    return placed, True


class Planner:
    def __init__(self, brief: TripBrief, places: list[Place], notices: list[ClosureNotice],
                 exclude: set[str] | None = None, checked: set[str] | None = None) -> None:
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
        days = R.trip_days(self.brief)
        used: set[str] = set()
        last_alt_index = -10
        last_full_index = -10
        out: list[DayPlan] = []
        for i, day in enumerate(days):
            role = R.role_of(i, len(days))
            picks, cap = self._pick(day, i, len(days), role, used, last_alt_index, last_full_index)
            if picks and R.is_full_day(picks[0]):
                last_full_index = i
            if any((p.altitude_m or 0) >= R.ALTITUDE_M for p in picks):
                last_alt_index = i
            used.update(p.name for p in picks)
            dp = self._layout(day, role, picks, cap)
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
            for name in sorted(scheduled):
                if name.lower() not in self.checked:
                    self.warnings.append(f"{name}: closures and season were not checked online. Confirm it is open on your dates.")
        empty = [d.day.strftime("%d %b") for d in out if d.role == "full" and not any(
            b.kind == "activity" for b in d.blocks)]
        if empty:
            self.warnings.append(
                f"No verified activity fits on {', '.join(empty)}. Research found {len(self.candidates)} usable "
                "place(s); add must-dos, loosen the pace, or re-run with more search coverage.")
        return TripPlan(brief=self.brief, days=out, warnings=sorted(set(self.warnings)), closures=self.notices,
                        places=[p for p in self.candidates])

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
            if R.is_full_day(p) and R.needs_long_rest(self.brief) and i - last_full < 2:
                continue  # no back-to-back full-day outings for older or limited-mobility travellers
            if picks and R.is_full_day(picks[0]):
                continue
            if not R.is_full_day(p):
                tentative = sorted(picks + [p], key=lambda x: -x.effort)  # strenuous in the morning
                if not place_all(tentative, windows_for(role, self.brief))[1]:
                    continue  # would not fit the day's time windows
            picks.append(p)
            spent += cost
            city = city or p.city
        picks.sort(key=lambda p: -p.effort)
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
        if role != "full":
            if role == "arrival":
                blocks.append(Block(start="12:00", end="15:00", kind="arrival",
                                    title="Arrive, transfer to hotel, check in", notes="Adjust to your flight times."))
            self._emit(blocks, picks, windows_for(role, self.brief))
            if role == "departure":
                blocks.append(Block(start="12:00", end="13:00", kind="departure",
                                    title="Transfer to airport", notes="Leave at least 3 hours before departure."))
        elif picks and R.is_full_day(picks[0]):
            p = picks[0]
            start = 9 * 60
            end = min(start + p.duration_min, R.DINNER[0] - 30)
            note = "Full-day outing: start early, allow extra time for the drive, take lunch on the way. "
            if (p.altitude_m or 0) >= R.ALTITUDE_M:
                note += f"Altitude about {p.altitude_m} m. "
            blocks.append(Block(start=R.hm(start), end=R.hm(end), kind="activity", title=p.name, place=p.name,
                                notes=(note + p.notes).strip(), sources=p.sources))
        else:
            self._emit(blocks, picks, windows_for(role, self.brief))
            blocks.append(Block(start=R.hm(R.LUNCH[0]), end=R.hm(R.LUNCH[1]), kind="meal", title="Lunch"))
            blocks.append(Block(start=R.hm(rest_a), end=R.hm(rest_b), kind="rest", title="Rest at the hotel"))
        if role != "departure":
            blocks.append(Block(start=R.hm(R.DINNER[0]), end=R.hm(R.DINNER[1]), kind="meal", title="Dinner"))
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
            elif p.altitude_m is None and p.kind in ("nature", "adventure") and R.altitude_sensitive(self.brief):
                note += "Altitude not verified: check before going. "
            blocks.append(Block(start=R.hm(begin), end=R.hm(end), kind="activity", title=p.name, place=p.name,
                                notes=(note + p.notes).strip(), sources=p.sources))
