import time

import pytest
from fastapi.testclient import TestClient

from conftest import FakeExtractorLLM
from fairfare.api.app import Deps, create_app
from fairfare.booking.queue import BookingQueue

BRIEF = {"destination": "Kazakhstan", "start": "2026-10-19", "end": "2026-10-24", "passport": "Indian",
         "travellers": [{"name": "A", "age": 58}, {"name": "B", "age": 24}], "interests": ["nature"]}


@pytest.fixture
def client(tmp_path, web, monkeypatch):
    monkeypatch.setenv("FAIRFARE_TRACE_DIR", str(tmp_path / "traces"))
    deps = Deps(llm=FakeExtractorLLM(other='[{"item": "Shymbulak day", "category": "activity", "amount": 6500}]'),
                search=web[0], fetcher=web[1], queue=BookingQueue(tmp_path / "b"), outputs=tmp_path / "out")
    return TestClient(create_app(deps))


def wait(client, jid):
    for _ in range(100):
        j = client.get(f"/jobs/{jid}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job timed out")


def test_health_and_unknown_job(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/jobs/nope").status_code == 404


def test_plan_job_produces_plan_packs_and_trace(client):
    job = wait(client, client.post("/plan", json={"brief": BRIEF}).json()["job_id"])
    assert job["status"] == "done", job
    run_id = job["result"]["run_id"]
    days = job["result"]["plan"]["days"]
    assert len(days) == 6
    assert "Day by day" in client.get(f"/packs/{run_id}/md").text
    assert "<h1>" in client.get(f"/packs/{run_id}/html").text
    assert client.get(f"/packs/{run_id}/nope").status_code == 404
    assert client.get("/packs/..%2Fetc/md").status_code in (400, 404)
    runs = client.get("/runs").json()
    assert any(r["run_id"] == run_id and r["meta"]["kind"] == "plan" for r in runs)
    assert client.get(f"/runs/{run_id}/lint").status_code == 200
    assert client.post(f"/runs/{run_id}/feedback", json={"rating": "good", "note": "ok"}).status_code == 200
    assert client.post(f"/runs/{run_id}/feedback", json={"rating": "meh"}).status_code == 422


def test_audit_job_flags_closure(client):
    body = {"quote_text": "Shymbulak day 6500", "brief": BRIEF, "live": False}
    job = wait(client, client.post("/audit", json=body).json()["job_id"])
    assert job["status"] == "done", job
    assert any(f["kind"] == "closure" and f["severity"] == "high" for f in job["result"]["findings"])


def test_booking_flow_enforces_approval(client):
    body = {"venue": "Hotel Test", "kind": "hotel", "start": "2026-10-19", "end": "2026-10-24", "party_size": 4,
            "channel": "whatsapp", "contact": "+7 701 000 0000"}
    rid = client.post("/bookings", json=body).json()["id"]
    assert client.post(f"/bookings/{rid}/handoff").status_code == 409
    h = client.get(f"/bookings").json()
    h = next(b for b in h if b["id"] == rid)["message_hash"]
    assert client.post(f"/bookings/{rid}/approve", json={"by": "", "message_hash": h}).status_code == 409
    assert client.post(f"/bookings/{rid}/approve", json={"by": "Eeshan", "message_hash": "bad"}).status_code == 409
    assert client.post(f"/bookings/{rid}/approve", json={"by": "Eeshan", "message_hash": h}).json()["status"] == "approved"
    link = client.post(f"/bookings/{rid}/handoff").json()["link"]
    assert link.startswith("https://wa.me/")
    assert client.post("/bookings/zzz/approve", json={"by": "x", "message_hash": "h"}).status_code == 404
    assert client.post(f"/bookings/{rid}/reply", json={"text": "Deposit needed"}).json()["reply_summary"]["asks_for_payment"]


def test_path_traversal_ids_are_rejected(client):
    for bad in ("..%2F..%2Fetc%2Fpasswd", "x.jsonl", "a%00b"):
        assert client.get(f"/runs/{bad}").status_code in (400, 404, 422)
    assert client.post("/bookings/..%2Fx/approve", json={"by": "x", "message_hash": "h"}).status_code in (404, 405, 422)
