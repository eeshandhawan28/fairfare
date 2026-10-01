from datetime import date

from fairfare.models import Evidence, Place, Traveller, TripBrief, ReferencePrice, QuoteLine
from fairfare.planning.places import annotate_travel, canon, is_food_item, merge_similar
from fairfare.planning.scheduler import Planner, eligible
from fairfare.agents.price_check import check_gaps, check_prices
from fairfare.agents.destination import tidy, _time_in_quote
from fairfare.models import Claim

EV = [Evidence(url="https://x.test/a", quote="q" * 30, retrieved_at="2026-09-30")]


def P(name, **kw):
    return Place(name=name, evidence=list(EV), **kw)


def brief(**kw):
    base = dict(destination="Kyoto, Japan", start=date(2026, 11, 20), end=date(2026, 11, 26),
                travellers=[Traveller(name="a", age=33), Traveller(name="b", age=35)], pace="packed")
    base.update(kw)
    return TripBrief(**base)


def test_alias_names_merge():
    merged = merge_similar([P("Kinkaku-ji"), P("Kinkaku-ji Temple"), P("Fushimi Inari Taisha"),
                            P("Fushimi Inari Shrine"), P("Ginkaku-ji (Silver Pavilion)"), P("Ginkaku-ji Temple")])
    assert len(merged) == 3
    assert canon("Kiyomizu-Dera Temple") == canon("Kiyomizu-dera")


def test_food_items_never_scheduled():
    assert is_food_item(P("Kaiseki cuisine", kind="food"))
    assert is_food_item(P("Street food in Osaka"))
    assert not is_food_item(P("Nishiki Market", kind="food"))
    assert eligible(P("Baursak", kind="food"), brief()) is not None


def test_day_trips_get_own_day_and_travel_time():
    places = [P("Ha Long Bay", city="Ha Long", travel_min=150, duration_min=240),
              P("Old Quarter", city="Hanoi"), P("Temple of Literature", city="Hanoi"),
              P("Hoan Kiem Lake", city="Hanoi"), P("Water Puppet Theatre", city="Hanoi")]
    b = brief(destination="Hanoi, Vietnam", must_do=["Ha Long Bay"], end=date(2026, 11, 25))
    plan = Planner(b, places, []).plan()
    for d in plan.days:
        titles = [x.title for x in d.blocks if x.kind == "activity"]
        if "Ha Long Bay" in titles:
            assert d.role == "full" and titles == ["Ha Long Bay"]
            assert any("Travel to Ha Long Bay" in x.title for x in d.blocks)
    assert any(x.title == "Ha Long Bay" for d in plan.days for x in d.blocks)


def test_other_city_becomes_excursion_and_too_far_is_dropped():
    places = annotate_travel([P("Hanoi Museum", city="Hanoi"), P("Mai Chau", city="Mai Chau")], brief(destination="Hanoi"))
    assert places[1].travel_min == 120 and places[1].travel_estimated
    assert "too far" in (eligible(P("Far", travel_min=600), brief()) or "")


def test_sunrise_place_goes_early_and_not_afternoon():
    balloon = P("Balloon flight", best_time="sunrise", duration_min=120, city="Marrakech")
    plan = Planner(brief(destination="Marrakech"), [balloon, P("Bahia Palace", city="Marrakech")], []).plan()
    for d in plan.days:
        for b in d.blocks:
            if b.title == "Balloon flight":
                assert b.start < "09:30"


def test_steep_places_excluded_for_limited_mobility():
    steep = Place(name="Castle", evidence=[Evidence(url="https://x.test", quote="a steep uphill walk to the top",
                                                     retrieved_at="x")])
    b = brief(travellers=[Traveller(name="m", age=70, mobility="limited")])
    assert "steep" in eligible(steep, b)


def test_opening_time_must_be_in_quote():
    assert _time_in_quote("10:30", "The mausoleum closes at 10.30 am") is True
    assert _time_in_quote("17:00", "open daily 9am to 5pm") is True
    assert _time_in_quote("18:00", "open daily 9am to 5pm") is False


def test_tidy_drops_near_duplicates():
    def c(t):
        return Claim(kind="visa", subject="v", text=t, evidence=EV[0])
    out = tidy([c("Indian citizens can enter visa-free for 14 days per visit."),
                c("Indian passport holders can enter visa-free for up to 14 days per visit."),
                c("Passports must be valid for six months.")], limit=5)
    assert len(out) == 2


def test_bands_do_not_leak_across_destinations():
    ref = ReferencePrice(category="transfer", destination="Kazakhstan", keywords=["airport transfer"], low=1000,
                         high=2000, source="s", as_of="2026-09-30")
    line = QuoteLine(item="Airport transfer", category="transfer", amount=9000)
    assert check_prices([line], [ref], destination="Tokyo, Japan")[0].confidence == "low"
    assert check_prices([line], [ref], destination="Almaty, Kazakhstan")[0].severity == "warn"


def test_quote_gaps_flag_missing_items():
    lines = [QuoteLine(item="Hotel", category="stay", amount=1000)]
    f = check_gaps(lines)
    assert f and "insurance" in f[0].message


def test_static_closures_do_not_leak_across_destinations():
    from fairfare.data import load_closures, notices_for
    n = load_closures()
    assert n and notices_for("Almaty, Kazakhstan", n)
    assert notices_for("Lisbon, Portugal", n) == []


def test_usage_limit_notice_is_fatal_not_data(monkeypatch):
    import subprocess
    import pytest
    from fairfare import claude_cli

    class R:
        returncode, stdout, stderr = 0, "You've hit your session limit · resets 7:50pm (Asia/Kolkata)", ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    with pytest.raises(claude_cli.LLMUnavailable):
        claude_cli.run_claude("hi")


def test_hours_inferred_from_evidence_text():
    from fairfare.planning.places import infer_hours
    h = infer_hours("Open 9:30 a.m., closing at 4:30 p.m. October to March and 5:30 p.m. April to September. Closed on Mondays.")
    assert h["closes"] == "16:30" and h["opens"] == "09:30" and h["closed_days"] == ["Monday"]


def test_place_closing_before_slot_is_not_scheduled_late():
    p = Place(name="Museum", city="Tokyo", closes="16:30", evidence=list(EV))
    plan = Planner(brief(destination="Tokyo", pace="relaxed"), [p], []).plan()
    for d in plan.days:
        for b in d.blocks:
            if b.title == "Museum":
                assert b.end <= "16:30"


def test_seasonal_food_filtered():
    from fairfare.agents.destination import seasonal_ok
    assert seasonal_ok("Sardines are a summer staple during the June festival", 10) is False
    assert seasonal_ok("Chestnuts are sold in autumn", 10) is True
    assert seasonal_ok("Pastel de nata is eaten all year", 10) is True


def test_aggregate_quote_lines_not_compared_to_single_item_band():
    ref = ReferencePrice(category="other", keywords=["meals"], low=160, high=399, source="s", as_of="x")
    line = QuoteLine(item="Meals for 4 nights, 4 people", category="other", amount=42000)
    f = check_prices([line], [ref])
    assert f[0].severity == "info" and "itemise" in f[0].message


def test_departure_day_has_no_fixed_activity_and_early_dinner_for_kids():
    b = brief(destination="Lisbon", travellers=[Traveller(name="k", age=3), Traveller(name="p", age=35)])
    plan = Planner(b, [P(f"Sight{i}", city="Lisbon") for i in range(6)], []).plan()
    assert not [x for x in plan.days[-1].blocks if x.kind == "activity"]
    dinners = [x for d in plan.days for x in d.blocks if x.title == "Dinner"]
    assert dinners and all(x.start == "18:00" for x in dinners)


def test_round3_hikes_crowds_and_access():
    from fairfare.planning.places import apply_access
    from fairfare.agents.closure_scout import _keywords
    old = brief(travellers=[Traveller(name="m", age=70, mobility="limited")], avoid=["crowds"])
    hike = Place(name="Mt. Nokogiri", evidence=[Evidence(url="https://x.test/a", quote="A hiking destination with a jagged cliff lookout", retrieved_at="2026-09-30")])
    assert "hard walking" in (eligible(hike, old) or "")
    busy = Place(name="Senso-ji", evidence=[Evidence(url="https://x.test/a", quote="Attracts about 30 million annual visitors each year", retrieved_at="2026-09-30")])
    assert "crowded" in (eligible(busy, old) or "")
    chiado = P("Chiado")
    claim = Claim(subject="Chiado", text="Chiado is steep with cobbled hills", evidence=EV[0], kind="access")
    marked = apply_access([chiado, P("Belem Tower")], [claim])
    assert "steep" in marked[0].notes and not marked[1].notes
    assert eligible(marked[0], old) is not None
    assert "medeu" in _keywords("Medeu and Shymbulak")


def test_round3_desert_is_an_excursion_not_a_sunrise_slot():
    p = annotate_travel([P("Agafay Desert", city="Marrakech", best_time="sunrise", travel_min=40), P("Bahia Palace", city="Marrakech")],
                        brief(destination="Marrakech"))
    assert p[0].travel_min == 60 and p[0].best_time == "" and p[0].duration_min >= 270
    assert p[1].travel_min is None


def test_round3_multiday_transport_is_not_compared_to_a_single_ride():
    from fairfare.agents.price_check import _is_aggregate
    assert _is_aggregate(QuoteLine(item="Local daily transport (Yandex Go, city tours)", category="transfer", amount=2500, currency="INR"))
    assert _is_aggregate(QuoteLine(item="Majorelle entry + transfer", category="activity", amount=1, currency="INR"))
