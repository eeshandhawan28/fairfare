from datetime import date

from fairfare.models import Block, ClosureNotice, Evidence, Place, Traveller, TripBrief
from fairfare.planning import rules as R
from fairfare.planning.checker import check_plan, plan_with_repair
from fairfare.planning.scheduler import Planner

EV = [Evidence(url="https://x.test/a", quote="q" * 30, retrieved_at="2026-09-30")]


def P(name, effort=1, minutes=90, city="Almaty", alt=None, kind="sight", **kw):
    return Place(name=name, effort=effort, duration_min=minutes, city=city, altitude_m=alt, kind=kind,
                 evidence=kw.pop("evidence", EV), **kw)


def brief(ages=(58, 55, 24, 17), pace="balanced", limited=False, **kw):
    ts = [Traveller(name=f"T{i}", age=a, mobility="limited" if limited and i == 0 else "full") for i, a in enumerate(ages)]
    return TripBrief(destination="X", start=date(2026, 10, 19), end=date(2026, 10, 24), travellers=ts, pace=pace, **kw)


def test_cap_follows_slowest_traveller_and_pace():
    assert R.group_cap(brief(ages=(30, 30))) == 12
    assert R.group_cap(brief(ages=(30, 70))) == 6
    assert R.group_cap(brief(ages=(30, 30), limited=True)) == 5
    assert R.group_cap(brief(ages=(30, 30), pace="relaxed")) == round(12 * 0.7)


def test_day_shape_arrival_full_departure():
    places = [P(f"Place{i}") for i in range(8)]
    plan = Planner(brief(), places, []).plan()
    assert [d.role for d in plan.days] == ["arrival"] + ["full"] * 4 + ["departure"]
    full = plan.days[1]
    kinds = [b.kind for b in full.blocks]
    assert "meal" in kinds and "rest" in kinds
    rest = next(b for b in full.blocks if b.kind == "rest")
    assert R.mins(rest.end) - R.mins(rest.start) >= 90  # travellers 50+
    assert sum(b.kind == "activity" for b in plan.days[0].blocks) <= 1
    assert not any(b.kind == "activity" for b in plan.days[-1].blocks) or \
        sum(b.kind == "activity" for b in plan.days[-1].blocks) <= 1


def test_closed_place_is_never_scheduled_on_closed_days():
    notice = ClosureNotice(venue="Shymbulak", keywords=["shymbulak"], closed_from=date(2026, 10, 19),
                           closed_to=None, source="s", confidence="low")
    plan = Planner(brief(ages=(30, 30)), [P("Shymbulak", effort=2, alt=2260), P("Medeu")], [notice]).plan()
    scheduled = {b.place for d in plan.days for b in d.blocks if b.kind == "activity"}
    assert "Shymbulak" not in scheduled and "Medeu" in scheduled
    assert any("Shymbulak" in w and "closed" in w for w in plan.warnings)


def test_altitude_not_on_arrival_or_day_one_and_not_back_to_back_for_older():
    places = [P("High A", effort=2, alt=2500), P("High B", effort=2, alt=2600), P("Low", effort=1)]
    plan = Planner(brief(), places, []).plan()
    alt_days = [i for i, d in enumerate(plan.days) if any(b.place and b.place.startswith("High") for b in d.blocks)]
    assert alt_days and min(alt_days) >= 2
    assert all(b - a >= 2 for a, b in zip(alt_days, alt_days[1:]))


def test_unverified_and_underage_places_are_excluded():
    places = [P("Unsourced", evidence=[]), P("Adults Only", min_age=18), P("Fine")]
    plan = Planner(brief(must_do=["Unsourced"]), places, []).plan()
    scheduled = {b.place for d in plan.days for b in d.blocks if b.kind == "activity"}
    assert scheduled == {"Fine"}
    assert any("Unsourced" in w and "no verified source" in w for w in plan.warnings)


def test_must_do_and_hidden_gem_rank_first():
    places = [P("Boring"), P("Gem", hidden_gem=True), P("Wanted")]
    plan = Planner(brief(must_do=["Wanted"]), places, []).plan()
    order = [b.place for d in plan.days for b in d.blocks if b.kind == "activity"]
    assert order.index("Wanted") < order.index("Gem") < order.index("Boring")


def test_checker_flags_hand_broken_plans():
    places = [P("A", effort=3, minutes=240), P("B", effort=3, minutes=240)]
    plan = Planner(brief(ages=(70, 30)), places, []).plan()
    d = plan.days[1]
    d.blocks = [
        Block(start="09:30", end="12:00", title="A", kind="activity", place="A"),
        Block(start="11:00", end="13:00", title="B", kind="activity", place="B"),
        Block(start="15:00", end="16:00", title="Ghost", kind="activity", place="Ghost"),
    ]
    d.cap = 6
    rules = {v.rule for v in check_plan(plan, places, [])}
    assert {"no_meal", "no_rest", "overlap", "unverified_place", "over_cap", "no_transfer"} <= rules


def test_repair_loop_removes_offender_and_replans():
    places = [P("Good"), P("Later Closed")]
    n = ClosureNotice(venue="Later Closed", keywords=["later closed"], closed_from=date(2026, 10, 21),
                      closed_to=date(2026, 10, 22), source="s")
    plan, violations, iters = plan_with_repair(brief(), places, [n])
    assert not [v for v in violations if v.rule == "closed_place"]
    days = {d.day.day: {b.place for b in d.blocks} for d in plan.days}
    assert "Later Closed" not in days[21] and "Later Closed" not in days[22]
