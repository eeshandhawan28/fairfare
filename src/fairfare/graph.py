"""LangGraph pipeline: parse quote -> audit prices -> check closures -> report.

Only the parse step calls an LLM. Price and closure checks are deterministic so a
finding is always traceable to a dated source, never to a model's recollection.
Every node is traced as a span.
"""
from __future__ import annotations

import functools
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, StateGraph

from fairfare import tracing
from fairfare.agents.closures import check_closures
from fairfare.agents.closure_scout import scout_closures
from fairfare.agents.price_check import check_gaps, check_prices
from fairfare.agents.price_scout import scout_prices
from fairfare.agents.quote_parser import parse_quote
from fairfare.llm import LLM, TracedLLM
from fairfare.models import ClosureNotice, Finding, QuoteLine, ReferencePrice, TripBrief
from fairfare.report import render_report
from fairfare.research import Researcher
from fairfare.tools.fetch import Fetcher
from fairfare.tools.search import SearchProvider


class AuditState(TypedDict, total=False):
    quote_text: str
    brief: TripBrief
    lines: list[QuoteLine]
    findings: list[Finding]
    report: str


def _traced(name: str, fn: Callable[[AuditState], AuditState]) -> Callable[[AuditState], AuditState]:
    @functools.wraps(fn)  # keep fn's annotations: LangGraph infers each node's input schema from them
    def wrapper(state):
        with tracing.span(name, kind="node") as rec:
            result = fn(state)
            rec["output"] = {k: (len(v) if isinstance(v, list) else "set") for k, v in result.items()}
            return result
    return wrapper


def build_graph(llm: LLM, refs: list[ReferencePrice], notices: list[ClosureNotice],
                researcher: Optional[Researcher] = None):
    def parse_node(state: AuditState) -> AuditState:
        return {"lines": parse_quote(llm, state["quote_text"])}

    def price_node(state: AuditState) -> AuditState:
        all_refs = list(refs)
        if researcher:  # live mode: build extra bands from the web; static bands stay first
            all_refs += scout_prices(researcher, state["lines"], state["brief"])
        found = check_prices(state["lines"], all_refs, max(1, len(state["brief"].travellers)),
                             state["brief"].nights, state["brief"].destination) + check_gaps(state["lines"])
        unreferenced = sum(1 for f in found if "No independent price band" in f.message)
        tracing.event("price_coverage", lines=len(state["lines"]), unreferenced=unreferenced)
        return {"findings": state.get("findings", []) + found}

    def closure_node(state: AuditState) -> AuditState:
        all_notices = list(notices)
        if researcher:
            venues = [l.item for l in state["lines"] if l.category in ("activity", "other")]
            all_notices += scout_closures(researcher, venues, state["brief"])
        tracing.event("closure_sources", static=len(notices), total=len(all_notices))
        found = check_closures(state["lines"], state["brief"], all_notices)
        return {"findings": state.get("findings", []) + found}

    def report_node(state: AuditState) -> AuditState:
        return {"report": render_report(state["brief"], state["lines"], state["findings"])}

    graph = StateGraph(AuditState)
    graph.add_node("parse", _traced("parse", parse_node))
    graph.add_node("prices", _traced("prices", price_node))
    graph.add_node("closures", _traced("closures", closure_node))
    graph.add_node("report", _traced("report", report_node))
    graph.set_entry_point("parse")
    graph.add_edge("parse", "prices")
    graph.add_edge("prices", "closures")
    graph.add_edge("closures", "report")
    graph.add_edge("report", END)
    return graph.compile()


def run_audit(
    llm: LLM,
    brief: TripBrief,
    quote_text: str,
    refs: list[ReferencePrice],
    notices: list[ClosureNotice],
    tracer: Optional[tracing.Tracer] = None,
    search: Optional[SearchProvider] = None,
    fetcher: Optional[Fetcher] = None,
    **meta: Any,
) -> dict[str, Any]:
    """Run one traced audit. Returns the final graph state plus the run id."""
    tracer = tracer or tracing.Tracer(destination=brief.destination, start=str(brief.start),
                                      end=str(brief.end), **meta)
    with tracing.use(tracer):
        traced = TracedLLM(llm)
        researcher = Researcher(traced, search, fetcher) if search and fetcher else None
        graph = build_graph(traced, refs, notices, researcher)
        try:
            result = graph.invoke({"quote_text": quote_text, "brief": brief})
        except Exception as exc:
            tracer.end(status="error", error=f"{type(exc).__name__}: {exc}")
            raise
        findings = result.get("findings", [])
        tracer.end(status="ok", lines=len(result.get("lines", [])),
                   findings=len(findings),
                   high=sum(1 for f in findings if f.severity == "high"),
                   price_findings=sum(1 for f in findings if f.kind == "price"),
                   closure_findings=sum(1 for f in findings if f.kind == "closure"))
    result["run_id"] = tracer.run_id
    return result
