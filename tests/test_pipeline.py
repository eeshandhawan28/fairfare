"""Offline tests: a fake LLM returns canned parses, so no model or network is needed."""
import json
from datetime import date

from fairfare.data import load_closures, load_reference_prices
from fairfare.graph import build_graph
from fairfare.llm import extract_json
from fairfare.models import TripBrief


class FakeLLM:
    def complete(self, agent: str, system: str, user: str) -> str:
        lines = [
            {"item": "Airport transfer Almaty, both ways", "category": "transfer", "amount": 4500},
            {"item": "Almaty city tour, half day", "category": "activity", "amount": 3000},
            {"item": "Shymbulak ski resort day with cable car", "category": "activity", "amount": 6500},
        ]
        return "Here you go:\n" + json.dumps(lines)


def _run(start: date, end: date):
    brief = TripBrief(destination="Kazakhstan", start=start, end=end)
    graph = build_graph(FakeLLM(), load_reference_prices(), load_closures())
    return graph.invoke({"quote_text": "ignored", "brief": brief})


def test_closure_flagged_when_dates_overlap():
    result = _run(date(2026, 10, 19), date(2026, 10, 24))
    closures = [f for f in result["findings"] if f.kind == "closure"]
    assert len(closures) == 1
    assert "shymbulak" in closures[0].line.lower()
    assert closures[0].confidence == "low"  # unconfirmed source stays visibly low confidence


def test_closure_not_flagged_before_closure_starts():
    result = _run(date(2026, 10, 10), date(2026, 10, 15))
    assert not [f for f in result["findings"] if f.kind == "closure"]


def test_overpriced_transfer_flagged_and_fair_price_not():
    result = _run(date(2026, 10, 10), date(2026, 10, 15))
    warned = [f.line for f in result["findings"] if f.kind == "price" and f.severity == "warn"]
    assert any("airport transfer" in w.lower() for w in warned)
    assert not any("city tour" in w.lower() for w in warned)


def test_report_lists_high_severity_first():
    result = _run(date(2026, 10, 19), date(2026, 10, 24))
    body = result["report"]
    assert body.index("ACT NOW") < body.index("CHECK")


def test_extract_json_ignores_chatter():
    assert extract_json('sure! [{"a": 1}] done') == [{"a": 1}]


def test_unreadable_amount_still_gets_closure_check():
    class BadAmountLLM:
        def complete(self, agent, system, user):
            return json.dumps([{"item": "Shymbulak ski day", "category": "activity", "amount": "six thousand"}])

    brief = TripBrief(destination="Kazakhstan", start=date(2026, 10, 19), end=date(2026, 10, 24))
    result = build_graph(BadAmountLLM(), load_reference_prices(), load_closures()).invoke(
        {"quote_text": "q", "brief": brief})
    kinds = {(f.kind, f.severity) for f in result["findings"]}
    assert ("closure", "high") in kinds  # closure caught despite the unreadable amount
    assert ("price", "warn") in kinds  # and the missing amount is called out
