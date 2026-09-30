"""FastAPI backend. Dependencies are injected so tests run with fakes and no network."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel

from fairfare import tracing
from fairfare.agents.trace_reviewer import lint_trace, review_trace
from fairfare.api.jobs import JobStore
from fairfare.booking.models import BookingRequest
from fairfare.booking.queue import ApprovalError, BookingQueue
from fairfare.data import load_closures, load_reference_prices
from fairfare.graph import run_audit
from fairfare.llm import LLM, LiteLLMClient, TracedLLM
from fairfare.models import TripBrief
from fairfare.pack import html_to_pdf
from fairfare.planning.graph import run_plan
from fairfare.tools.fetch import CachedFetcher, Fetcher, HttpFetcher
from fairfare.tools.search import SearchProvider, get_search


@dataclass
class Deps:
    llm: LLM = field(default_factory=LiteLLMClient)
    search: Optional[SearchProvider] = None
    fetcher: Optional[Fetcher] = None
    queue: BookingQueue = field(default_factory=BookingQueue)
    outputs: Path = field(default_factory=lambda: Path(os.getenv("FAIRFARE_OUTPUTS", "outputs")))

    def live(self) -> tuple[SearchProvider, Fetcher]:
        self.search = self.search or get_search()
        self.fetcher = self.fetcher or CachedFetcher(HttpFetcher())
        return self.search, self.fetcher


class AuditIn(BaseModel):
    quote_text: str
    brief: TripBrief
    live: bool = False


class PlanIn(BaseModel):
    brief: TripBrief


class FeedbackIn(BaseModel):
    rating: str
    note: str = ""


class ApproveIn(BaseModel):
    by: str


class EditIn(BaseModel):
    message: str


class ReplyIn(BaseModel):
    text: str


def create_app(deps: Optional[Deps] = None) -> FastAPI:
    d = deps or Deps()
    jobs = JobStore()
    app = FastAPI(title="fairfare", version="0.2.0")
    app.add_middleware(CORSMiddleware, allow_origins=os.getenv("FAIRFARE_CORS", "http://localhost:3000").split(","),
                       allow_methods=["*"], allow_headers=["*"])

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # ---- pipelines ----

    @app.post("/audit")
    def audit(body: AuditIn) -> dict[str, str]:
        def job() -> dict[str, Any]:
            search, fetcher = d.live() if body.live else (None, None)
            r = run_audit(d.llm, body.brief, body.quote_text, load_reference_prices(), load_closures(),
                          search=search, fetcher=fetcher, source="api")
            return {"run_id": r["run_id"], "report": r["report"],
                    "lines": [x.model_dump(mode="json") for x in r["lines"]],
                    "findings": [x.model_dump(mode="json") for x in r["findings"]]}
        return {"job_id": jobs.submit(job)}

    @app.post("/plan")
    def plan(body: PlanIn) -> dict[str, str]:
        def job() -> dict[str, Any]:
            search, fetcher = d.live()
            r = run_plan(d.llm, search, fetcher, body.brief, static_notices=load_closures(), source="api")
            out = d.outputs / r["run_id"]
            out.mkdir(parents=True, exist_ok=True)
            (out / "pack.md").write_text(r["markdown"])
            (out / "pack.html").write_text(r["html"])
            (out / "pack.txt").write_text(r["whatsapp"])
            return {"run_id": r["run_id"], "plan": r["plan"].model_dump(mode="json"),
                    "violations": [v.__dict__ for v in r["violations"]]}
        return {"job_id": jobs.submit(job)}

    @app.get("/jobs/{jid}")
    def job_status(jid: str) -> dict[str, Any]:
        j = jobs.get(jid)
        if j is None:
            raise HTTPException(404, "unknown job")
        return j

    @app.get("/packs/{run_id}/{fmt}")
    def pack(run_id: str, fmt: str) -> Response:
        if "/" in run_id or ".." in run_id:
            raise HTTPException(400, "bad run id")
        base = d.outputs / run_id
        if fmt == "pdf":
            html_path = base / "pack.html"
            if not html_path.exists():
                raise HTTPException(404, "no such pack")
            pdf = base / "pack.pdf"
            if not pdf.exists() and not html_to_pdf(html_path.read_text(), str(pdf)):
                raise HTTPException(501, "PDF export needs Playwright (pip install playwright && playwright install chromium)")
            return Response(pdf.read_bytes(), media_type="application/pdf")
        names = {"html": ("pack.html", HTMLResponse), "md": ("pack.md", PlainTextResponse),
                 "txt": ("pack.txt", PlainTextResponse)}
        if fmt not in names or not (base / names[fmt][0]).exists():
            raise HTTPException(404, "no such pack")
        return names[fmt][1]((base / names[fmt][0]).read_text())

    # ---- traces ----

    @app.get("/runs")
    def runs() -> list[dict[str, Any]]:
        out = []
        for p in reversed(tracing.list_runs()):
            ev = tracing.load(str(p))
            out.append({**tracing.summarize(ev), "meta": {k: v for k, v in ev[0].items() if k not in ("ts", "type", "run_id")}})
        return out

    @app.get("/runs/{run_id}")
    def run_events(run_id: str) -> list[dict[str, Any]]:
        if run_id.endswith(".jsonl"):
            raise HTTPException(400, "bad run id")  # file paths are a CLI convenience, never an API input
        try:
            return tracing.load(run_id)
        except FileNotFoundError:
            raise HTTPException(404, "unknown run")

    @app.get("/runs/{run_id}/lint")
    def run_lint(run_id: str) -> list[dict[str, str]]:
        return lint_trace(run_events(run_id))

    @app.get("/runs/{run_id}/review")
    def run_review(run_id: str) -> list[dict[str, str]]:
        ev = run_events(run_id)
        return lint_trace(ev) + review_trace(TracedLLM(d.llm), ev)

    @app.post("/runs/{run_id}/feedback")
    def run_feedback(run_id: str, body: FeedbackIn) -> dict[str, str]:
        ev = run_events(run_id)
        if body.rating not in ("good", "bad"):
            raise HTTPException(422, "rating must be good or bad")
        tracing.Tracer(run_id=ev[0]["run_id"]).feedback(body.rating, body.note)
        return {"status": "recorded"}

    # ---- bookings (drafts and approvals only; nothing is ever sent by the system) ----

    def booking_or_404(rid: str) -> BookingRequest:
        try:
            return d.queue.get(rid)
        except KeyError:
            raise HTTPException(404, "unknown booking")

    @app.post("/bookings")
    def booking_create(req: BookingRequest) -> BookingRequest:
        return d.queue.draft(req, d.llm if os.getenv("FAIRFARE_POLISH", "0") == "1" else None)

    @app.get("/bookings")
    def booking_list() -> list[BookingRequest]:
        return d.queue.list()

    @app.put("/bookings/{rid}")
    def booking_edit(rid: str, body: EditIn) -> BookingRequest:
        booking_or_404(rid)
        try:
            return d.queue.edit(rid, body.message)
        except ApprovalError as e:
            raise HTTPException(409, str(e))

    @app.post("/bookings/{rid}/approve")
    def booking_approve(rid: str, body: ApproveIn) -> BookingRequest:
        booking_or_404(rid)
        try:
            return d.queue.approve(rid, body.by)
        except ApprovalError as e:
            raise HTTPException(409, str(e))

    @app.post("/bookings/{rid}/handoff")
    def booking_handoff(rid: str) -> dict[str, str]:
        booking_or_404(rid)
        try:
            return {"link": d.queue.hand_off(rid)}
        except (ApprovalError, ValueError) as e:
            raise HTTPException(409, str(e))

    @app.post("/bookings/{rid}/reply")
    def booking_reply(rid: str, body: ReplyIn) -> BookingRequest:
        booking_or_404(rid)
        try:
            return d.queue.record_reply(rid, body.text, TracedLLM(d.llm))
        except ApprovalError as e:
            raise HTTPException(409, str(e))

    return app

