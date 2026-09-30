"""Trip planning pipeline (LangGraph):

places -> closures -> entry & transport -> schedule + check/repair loop -> pack

Research nodes call the LLM only to extract quoted facts; scheduling and checking are
deterministic code. Every node is a traced span.
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from fairfare import tracing
from fairfare.agents.closure_scout import scout_closures
from fairfare.agents.destination import (research_local_intel, research_places, research_transport,
                                         research_visa)
from fairfare.graph import _traced
from fairfare.llm import LLM, TracedLLM
from fairfare.models import Claim, ClosureNotice, Place, TripBrief, TripPlan
from fairfare.pack import render_html, render_markdown, render_whatsapp
from fairfare.planning.checker import Violation, plan_with_repair
from fairfare.planning.scheduler import _rank, eligible
from fairfare.research import Researcher
from fairfare.tools.fetch import Fetcher
from fairfare.tools.search import SearchProvider

MAX_VENUES_TO_VERIFY = 14


class PlanState(TypedDict, total=False):
    brief: TripBrief
    places: list[Place]
    avoid: list[Claim]
    notices: list[ClosureNotice]
    checked: list[str]
    visa: list[Claim]
    transport: list[Claim]
    plan: TripPlan
    violations: list[Violation]
    markdown: str
    html: str
    whatsapp: str


def _merge_places(*groups: list[Place]) -> list[Place]:
    merged: dict[str, Place] = {}
    for g in groups:
        for p in g:
            key = p.name.lower()
            if key in merged:
                merged[key].evidence.extend(e for e in p.evidence if e not in merged[key].evidence)
                merged[key].hidden_gem = merged[key].hidden_gem or p.hidden_gem
            else:
                merged[key] = p.model_copy(deep=True)
    return list(merged.values())


def build_plan_graph(researcher: Researcher, static_notices: list[ClosureNotice]):
    def places_node(s: PlanState) -> PlanState:
        base = research_places(researcher, s["brief"])
        gems, avoid = research_local_intel(researcher, s["brief"])
        return {"places": _merge_places(base, gems), "avoid": avoid}

    def closures_node(s: PlanState) -> PlanState:
        brief = s["brief"]
        # only places that could actually be scheduled are worth a closure search
        ranked = sorted((p for p in s["places"] if eligible(p, brief) is None), key=lambda p: _rank(p, brief))
        venues = [p.name for p in ranked[:MAX_VENUES_TO_VERIFY]]
        venues += [m for m in brief.must_do if m not in venues]
        live = scout_closures(researcher, venues, brief)
        return {"notices": list(static_notices) + live, "checked": venues}

    def entry_node(s: PlanState) -> PlanState:
        return {"visa": research_visa(researcher, s["brief"]), "transport": research_transport(researcher, s["brief"])}

    def schedule_node(s: PlanState) -> PlanState:
        plan, violations, iterations = plan_with_repair(s["brief"], s["places"], s["notices"], checked=set(s.get("checked", [])))
        tracing.event("plan_check", iterations=iterations, violations=len(violations),
                      rules=",".join(sorted({v.rule for v in violations})))
        if not plan.places:
            plan.warnings.append("No verified places were found, so no activities were scheduled. "
                                 "Check search access and the model in the trace.")
        plan.visa, plan.transport, plan.avoid = s["visa"], s["transport"], s["avoid"]
        tr = tracing.current()
        plan.run_id = tr.run_id if tr else ""
        return {"plan": plan, "violations": violations}

    def pack_node(s: PlanState) -> PlanState:
        plan = s["plan"]
        return {"markdown": render_markdown(plan), "html": render_html(plan), "whatsapp": render_whatsapp(plan)}

    g = StateGraph(PlanState)
    for name, fn in (("places", places_node), ("closures", closures_node), ("entry", entry_node),
                     ("schedule", schedule_node), ("pack", pack_node)):
        g.add_node(f"{name}_step", _traced(name, fn))  # node ids must not collide with state keys
    g.set_entry_point("places_step")
    g.add_edge("places_step", "closures_step")
    g.add_edge("closures_step", "entry_step")
    g.add_edge("entry_step", "schedule_step")
    g.add_edge("schedule_step", "pack_step")
    g.add_edge("pack_step", END)
    return g.compile()


def run_plan(llm: LLM, search: SearchProvider, fetcher: Fetcher, brief: TripBrief,
             static_notices: Optional[list[ClosureNotice]] = None,
             tracer: Optional[tracing.Tracer] = None, **meta: Any) -> dict[str, Any]:
    tracer = tracer or tracing.Tracer(kind="plan", destination=brief.destination, start=str(brief.start),
                                      end=str(brief.end), travellers=len(brief.travellers), **meta)
    with tracing.use(tracer):
        researcher = Researcher(TracedLLM(llm), search, fetcher)
        graph = build_plan_graph(researcher, static_notices or [])
        try:
            state = graph.invoke({"brief": brief})
        except Exception as exc:
            tracer.end(status="error", error=f"{type(exc).__name__}: {exc}")
            raise
        plan = state["plan"]
        tracer.end(status="ok", days=len(plan.days), places=len(plan.places), warnings=len(plan.warnings),
                   violations=len(state["violations"]), closures=len(state["notices"]))
    state["run_id"] = tracer.run_id
    return state
