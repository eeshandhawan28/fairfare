import json
from datetime import date

from fairfare import tracing
from fairfare.agents.trace_reviewer import lint_trace, review_trace
from fairfare.data import load_closures, load_reference_prices
from fairfare.graph import run_audit
from fairfare.models import TripBrief

GOOD = json.dumps([
    {"item": "Airport transfer Almaty", "category": "transfer", "amount": 4500},
    {"item": "Shymbulak day", "category": "activity", "amount": 6500},
])


class CannedLLM:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, agent, system, user):
        return self.reply


def _run(tmp_path, reply):
    tracer = tracing.Tracer(directory=tmp_path)
    brief = TripBrief(destination="Kazakhstan", start=date(2026, 10, 19), end=date(2026, 10, 24))
    result = run_audit(CannedLLM(reply), brief, "q", load_reference_prices(), load_closures(), tracer=tracer)
    return result, tracing.load(tracer.run_id, tmp_path)


def test_run_writes_spans_for_llm_and_every_node(tmp_path):
    _, events = _run(tmp_path, GOOD)
    names = [e["name"] for e in events if e["type"] == "span"]
    assert "llm:quote_parser" in names
    for node in ("parse", "prices", "closures", "report"):
        assert node in names
    assert events[0]["type"] == "run_start" and events[-1]["type"] == "run_end"
    assert events[-1]["high"] == 1  # the Shymbulak closure


def test_lint_flags_zero_line_parse(tmp_path):
    _, events = _run(tmp_path, "sorry, I cannot help")
    issues = lint_trace(events)
    assert any(i["severity"] == "high" and "JSON" in i["problem"] for i in issues)


def test_lint_flags_dropped_rows_and_low_coverage(tmp_path):
    reply = json.dumps([{"item": "Mystery fee", "amount": 100}, {"amount": 5}])
    _, events = _run(tmp_path, reply)
    problems = " ".join(i["problem"] for i in lint_trace(events))
    assert "dropped" in problems
    assert "no price reference" in problems


def test_feedback_is_appended_without_second_run_start(tmp_path):
    result, _ = _run(tmp_path, GOOD)
    tracing.Tracer(run_id=result["run_id"], directory=tmp_path).feedback("bad", "missed the closure")
    events = tracing.load(result["run_id"], tmp_path)
    assert sum(e["type"] == "run_start" for e in events) == 1
    assert any(i["problem"].startswith("Human marked") for i in lint_trace(events))


def test_model_reviewer_output_is_labelled_and_parsed(tmp_path):
    _, events = _run(tmp_path, GOOD)
    reply = '[{"severity": "medium", "span": "prices", "problem": "x", "suggestion": "y"}]'
    issues = review_trace(CannedLLM(reply), events)
    assert issues[0]["source"] == "model"
    assert review_trace(CannedLLM("nonsense"), events)[0]["source"] == "model"
