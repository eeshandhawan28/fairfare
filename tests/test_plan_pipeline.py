from conftest import FakeExtractorLLM
from fairfare import tracing
from fairfare.agents.trace_reviewer import lint_trace
from fairfare.data import load_closures
from fairfare.pack import render_html, render_markdown, render_whatsapp
from fairfare.planning.graph import run_plan


def _run(web, family, tmp_path, static=None):
    tracer = tracing.Tracer(directory=tmp_path)
    r = run_plan(FakeExtractorLLM(), web[0], web[1], family, static_notices=static or [], tracer=tracer)
    return r, tracing.load(tracer.run_id, tmp_path)


def test_end_to_end_plan_respects_live_closure_and_cites_sources(web, family, tmp_path):
    r, events = _run(web, family, tmp_path)
    plan = r["plan"]
    scheduled = {b.place for d in plan.days for b in d.blocks if b.kind == "activity"}
    assert "Shymbulak" not in scheduled  # closed 19 Oct onwards per the operator's page
    assert scheduled  # something real was planned
    assert "Invented Palace" not in {p.name for p in plan.places}
    assert any(p.hidden_gem for p in r["places"])  # found by research; may be too far for this older group to schedule
    for d in plan.days:
        for b in d.blocks:
            if b.kind == "activity":
                assert b.sources, f"{b.title} has no citation"
    assert any("Shymbulak" in w and "closed" in w for w in plan.warnings)
    assert plan.visa and plan.visa[0].data["official"]
    assert plan.avoid and plan.transport
    assert not [v for v in r["violations"] if v.rule in ("closed_place", "unverified_place")]


def test_pack_formats_include_citations_and_escape_html(web, family, tmp_path):
    r, _ = _run(web, family, tmp_path)
    md, html, wa = r["markdown"], r["html"], r["whatsapp"]
    assert "## Day by day" in md and "## Sources" in md and "example-guide.test" in md
    assert "Check before you go" in html and "<script" not in html
    plan = r["plan"]
    plan.warnings.append("<script>alert(1)</script>")
    assert "<script>alert(1)" not in render_html(plan) and "&lt;script&gt;" in render_html(plan)
    assert "Day 1" in wa and "Sources" not in wa
    assert render_markdown(plan) and render_whatsapp(plan)


def test_static_notice_and_trace_health(web, family, tmp_path):
    r, events = _run(web, family, tmp_path, static=load_closures())
    names = [e["name"] for e in events if e["type"] == "span"]
    for n in ("places", "closures", "research:visa", "schedule", "pack", "research:place", "research:closure"):
        assert n in names
    assert events[-1]["type"] == "run_end" and events[-1]["status"] == "ok"
    assert not [i for i in lint_trace(events) if i["severity"] == "high"]


def test_no_search_results_gives_visible_warning_not_silent_empty_plan(family, tmp_path):
    from fairfare.tools.fetch import FixtureFetcher
    from fairfare.tools.search import FixtureSearch

    r = run_plan(FakeExtractorLLM(), FixtureSearch({}), FixtureFetcher({}), family,
                 tracer=tracing.Tracer(directory=tmp_path))
    assert any("No verified places" in w for w in r["plan"].warnings)


def test_example_brief_file_is_valid_and_pdf_degrades_gracefully(tmp_path):
    import json
    from pathlib import Path

    from fairfare.models import TripBrief
    from fairfare.pack import html_to_pdf

    brief = TripBrief(**json.loads((Path(__file__).parents[1] / "examples" / "kazakhstan_family.json").read_text()))
    assert len(brief.travellers) == 4 and brief.nights == 5
    assert html_to_pdf("<h1>x</h1>", str(tmp_path / "x.pdf")) in (True, False)  # never raises
