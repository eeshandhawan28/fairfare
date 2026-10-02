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
from fairfare.agents.hours_scout import scout_hours
from fairfare.agents.destination import (research_contacts, research_costs, research_food, research_local_intel,
                                         research_places, research_access, research_practical, research_stay, research_transport, research_visa)
from fairfare.graph import _traced
from fairfare.llm import LLM, TracedLLM
from fairfare.models import Claim, ClosureNotice, Place, TripBrief, TripPlan
from fairfare.pack import render_html, render_markdown, render_whatsapp
from fairfare.planning.checker import Violation, plan_with_repair
from fairfare.planning.places import apply_access, llm_merge_aliases, merge_similar
from fairfare.planning.scheduler import _rank, eligible
from fairfare.research import Researcher
from fairfare.tools.fetch import Fetcher
from fairfare.tools.search import SearchProvider

import os

MAX_VENUES_TO_VERIFY = int(os.getenv("FAIRFARE_MAX_VENUES", "14"))


class PlanState(TypedDict, total=False):
    brief: TripBrief
    places: list[Place]
    avoid: list[Claim]
    notices: list[ClosureNotice]
    checked: list[str]
    visa: list[Claim]
    transport: list[Claim]
    costs: list[Claim]
    stay: list[Claim]
    food: list[Claim]
    contacts: list[Claim]
    practical: list[Claim]
    plan: TripPlan
    violations: list[Violation]
    markdown: str
    html: str
    whatsapp: str


def _merge_places(*groups: list[Place]) -> list[Place]:
    return merge_similar([p for g in groups for p in g])


def parallel(*fns, workers: int = 4) -> list[Any]:
    """Run independent research steps concurrently. Each thread keeps the caller's tracer."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(contextvars.copy_context().run, fn) for fn in fns]
        return [f.result() for f in futures]


def build_plan_graph(researcher: Researcher, static_notices: list[ClosureNotice]):
    def places_node(s: PlanState) -> PlanState:
        brief = s["brief"]

        def places_and_intel():
            base = research_places(researcher, brief)
            gems, avoid = research_local_intel(researcher, brief)
            return base, gems, avoid

        (base, gems, avoid), visa, transport, costs, stay, food, contacts, practical, access = parallel(
            places_and_intel, lambda: research_visa(researcher, brief), lambda: research_transport(researcher, brief),
            lambda: research_costs(researcher, brief), lambda: research_stay(researcher, brief),
            lambda: research_food(researcher, brief), lambda: research_contacts(researcher, brief),
            lambda: research_practical(researcher, brief), lambda: research_access(researcher, brief), workers=9)
        places = llm_merge_aliases(researcher.llm, _merge_places(base, gems))
        places = apply_access(places, access)
        return {"places": places, "avoid": avoid, "visa": visa, "transport": transport, "costs": costs,
                "stay": stay, "food": food, "contacts": contacts, "practical": practical}

    def closures_node(s: PlanState) -> PlanState:
        brief = s["brief"]
        # only places that could actually be scheduled are worth a closure search
        ranked = sorted((p for p in s["places"] if eligible(p, brief) is None), key=lambda p: _rank(p, brief))
        venues = [p.name for p in ranked[:MAX_VENUES_TO_VERIFY]]
        venues += [m for m in brief.must_do if m not in venues]
        by_name = {p.name: p for p in ranked}
        live, hours = parallel(lambda: scout_closures(researcher, venues, brief),
                               lambda: scout_hours(researcher, [by_name[v] for v in venues if v in by_name], brief),
                               workers=2)
        places = []
        for p in s["places"]:
            h = hours.get(p.name)
            if h:
                p = p.model_copy(update={k: v for k, v in h.items() if v or k == "travel_estimated"})
            places.append(p)
        return {"notices": list(static_notices) + live, "checked": venues, "places": places}

    def schedule_node(s: PlanState) -> PlanState:
        plan, violations, iterations = plan_with_repair(s["brief"], s["places"], s["notices"], checked=set(s.get("checked", [])))
        tracing.event("plan_check", iterations=iterations, violations=len(violations),
                      rules=",".join(sorted({v.rule for v in violations})))
        if not plan.places:
            plan.warnings.append("No verified places were found, so no activities were scheduled. "
                                 "Check search access and the model in the trace.")
        plan.visa, plan.transport, plan.avoid = s["visa"], s["transport"], s["avoid"]
        plan.costs, plan.stay, plan.food, plan.contacts = s["costs"], s["stay"], s["food"], s["contacts"]
        plan.practical = s.get("practical", [])
        tr = tracing.current()
        plan.run_id = tr.run_id if tr else ""
        return {"plan": plan, "violations": violations}

    def pack_node(s: PlanState) -> PlanState:
        plan = s["plan"]
        return {"markdown": render_markdown(plan), "html": render_html(plan), "whatsapp": render_whatsapp(plan)}

    g = StateGraph(PlanState)
    for name, fn in (("places", places_node), ("closures", closures_node),
                     ("schedule", schedule_node), ("pack", pack_node)):
        g.add_node(f"{name}_step", _traced(name, fn))  # node ids must not collide with state keys
    g.set_entry_point("places_step")
    g.add_edge("places_step", "closures_step")
    g.add_edge("closures_step", "schedule_step")
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
        from fairfare.data import notices_for
        graph = build_plan_graph(researcher, notices_for(brief.destination, static_notices or []))
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
