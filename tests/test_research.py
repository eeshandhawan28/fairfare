from datetime import date

from conftest import FakeExtractorLLM
from fairfare import tracing
from fairfare.agents.closure_scout import scout_closures
from fairfare.agents.destination import research_local_intel, research_places, research_transport, research_visa
from fairfare.research import Researcher, quote_in_text


def _r(web):
    return Researcher(FakeExtractorLLM(), *web)


def test_quote_must_be_on_the_page():
    assert quote_in_text("cable cars go into staged maintenance", "…the cable cars go into staged maintenance ahead…")
    assert not quote_in_text("cable cars reopen tomorrow morning", "the cable cars go into staged maintenance")
    assert not quote_in_text("too short", "too short")


def test_hallucinated_claim_is_rejected_and_traced(web, family, tmp_path):
    tracer = tracing.Tracer(directory=tmp_path)
    with tracing.use(tracer):
        places = research_places(_r(web), family)
    names = {p.name for p in places}
    assert "Invented Palace" not in names and "Medeu" in names
    events = tracing.load(tracer.run_id, tmp_path)
    assert any(e.get("name") == "claim_rejected" for e in events)


def test_place_fields_are_parsed_from_claims(web, family):
    p = {x.name: x for x in research_places(_r(web), family)}
    assert p["Charyn Canyon"].duration_min == 480 and p["Charyn Canyon"].effort == 2
    assert p["Shymbulak"].altitude_m == 2260 and p["Shymbulak"].effort == 3
    assert p["Medeu"].evidence[0].url.startswith("https://example-guide")


def test_closure_scout_keeps_best_sourced_notice(web, family):
    notices = scout_closures(_r(web), ["Shymbulak"], family)
    assert len(notices) == 1  # third-party notice with the same start is merged into the operator's
    n = notices[0]
    assert n.confidence == "high" and "shymbulak.com" in n.source
    assert n.closed_from == date(2026, 10, 19) and n.closed_to == date(2026, 11, 30)


def test_closure_scout_rejects_pages_about_other_venues(web, family, tmp_path):
    tracer = tracing.Tracer(directory=tmp_path)
    with tracing.use(tracer):
        notices = scout_closures(_r(web), ["Medeu"], family)  # search returns Shymbulak pages
    assert notices == []
    assert any(e.get("name") == "closure_claim_offtopic" for e in tracing.load(tracer.run_id, tmp_path))


def test_hidden_gem_needs_two_independent_domains(web, family):
    gems, avoid = research_local_intel(_r(web), family)
    assert [g.name for g in gems] == ["Kolsai Lakes"] and gems[0].hidden_gem
    assert len(gems[0].evidence) == 2
    assert len(avoid) == 1 and "overcharge" in avoid[0].text


def test_single_mention_is_not_a_hidden_gem(web, family):
    from conftest import SEARCH_TABLE
    from fairfare.tools.search import FixtureSearch

    only_one = {k: v for k, v in SEARCH_TABLE.items() if k not in ("hidden gems", "underrated")}
    only_one["hidden gems"] = SEARCH_TABLE["hidden gems"][:1]
    gems, _ = research_local_intel(Researcher(FakeExtractorLLM(), FixtureSearch(only_one), web[1]), family)
    assert gems == []


def test_visa_prefers_official_source_and_transport_contact_is_verified(web, family):
    visa = research_visa(_r(web), family)
    assert visa[0].data["official"] is True
    transport = research_transport(_r(web), family)
    contacts = {c.subject: c.data.get("contact") for c in transport}
    assert contacts["Airport transfer"] == "+7 727 000 0000"
    assert not contacts["Taxi apps"]
