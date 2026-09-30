"""LangGraph pipeline: parse quote -> audit prices -> check closures -> report.

Only the parse step calls an LLM. Price and closure checks are deterministic so a
finding is always traceable to a dated source, never to a model's recollection.
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, StateGraph

from fairfare.agents.closures import check_closures
from fairfare.agents.price_check import check_prices
from fairfare.agents.quote_parser import parse_quote
from fairfare.llm import LLM
from fairfare.models import ClosureNotice, Finding, QuoteLine, ReferencePrice, TripBrief
from fairfare.report import render_report


class AuditState(TypedDict, total=False):
    quote_text: str
    brief: TripBrief
    lines: list[QuoteLine]
    findings: list[Finding]
    report: str


def build_graph(llm: LLM, refs: list[ReferencePrice], notices: list[ClosureNotice]):
    def parse_node(state: AuditState) -> AuditState:
        return {"lines": parse_quote(llm, state["quote_text"])}

    def price_node(state: AuditState) -> AuditState:
        found = check_prices(state["lines"], refs)
        return {"findings": state.get("findings", []) + found}

    def closure_node(state: AuditState) -> AuditState:
        found = check_closures(state["lines"], state["brief"], notices)
        return {"findings": state.get("findings", []) + found}

    def report_node(state: AuditState) -> AuditState:
        return {"report": render_report(state["brief"], state["lines"], state["findings"])}

    graph = StateGraph(AuditState)
    graph.add_node("parse", parse_node)
    graph.add_node("prices", price_node)
    graph.add_node("closures", closure_node)
    graph.add_node("report", report_node)
    graph.set_entry_point("parse")
    graph.add_edge("parse", "prices")
    graph.add_edge("prices", "closures")
    graph.add_edge("closures", "report")
    graph.add_edge("report", END)
    return graph.compile()
