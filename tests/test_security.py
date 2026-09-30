import pytest

from fairfare.agents.destination import is_official
from fairfare.research import domain, independent_domains, quote_in_text, registrable
from fairfare.tools.fetch import UnsafeURL, check_public_url


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "http://127.0.0.1/admin",
                                 "http://localhost:8000/", "http://169.254.169.254/latest/meta-data/",
                                 "http://10.0.0.5/", "http://[::1]/"])
def test_fetch_refuses_non_public_targets(url):
    with pytest.raises(UnsafeURL):
        check_public_url(url)


@pytest.mark.parametrize("url,expected", [
    ("https://travel.state.gov/visa", True), ("https://www.gov.kz/memleket", True), ("https://evisa.gov.in/", True),
    ("https://embassy-evisa-fast.com/kazakhstan", False), ("https://gov.evil.com/", False),
    ("https://visa-gov.net/", False), ("https://blog.example.com/gov", False)])
def test_official_domain_is_strict(url, expected):
    assert is_official(url) is expected


def test_subdomains_of_one_site_are_one_source():
    assert registrable("https://old.reddit.com/r/x") == registrable("https://www.reddit.com/r/y") == "reddit.com"
    assert registrable("https://news.bbc.co.uk/a") == "bbc.co.uk"
    from fairfare.models import Claim, Evidence
    c = lambda u: Claim(kind="k", subject="s", text="t", evidence=Evidence(url=u, quote="q" * 30, retrieved_at="x"))
    assert independent_domains([c("https://a.evil.com/1"), c("https://b.evil.com/2")]) == 1
    assert independent_domains([c("https://evil.com/1"), c("https://other.org/2")]) == 2


def test_ungrounded_dates_and_numbers():
    from datetime import date
    from fairfare.research import date_in_quote, number_in_quote
    q = "the cable cars will be closed from October 19, 2026 for maintenance"
    assert date_in_quote(date(2026, 10, 19), q)
    assert not date_in_quote(date(2026, 11, 3), q)
    assert number_in_quote(4500, "a transfer costs INR 4,500 per car") and not number_in_quote(3000, "costs INR 4,500")


def test_units_are_parsed_not_defaulted_to_unsafe_values():
    from fairfare.agents.destination import _altitude, _minutes
    assert _altitude("2,260 m") == 2260 and _altitude("3.2 km") == 3200 and _altitude(None) is None
    assert _minutes("2 hours") == 120 and _minutes("90") == 90 and _minutes("45 min") == 45 and _minutes(None) == 90


def test_brief_validation_blocks_absurd_inputs():
    from datetime import date
    from pydantic import ValidationError
    from fairfare.models import TripBrief
    with pytest.raises(ValidationError):
        TripBrief(destination="x", start=date(2026, 10, 24), end=date(2026, 10, 19))
    with pytest.raises(ValidationError):
        TripBrief(destination="x", start=date(2026, 1, 1), end=date(2030, 1, 1))


def test_swapped_closure_range_is_rejected():
    from datetime import date
    from pydantic import ValidationError
    from fairfare.models import ClosureNotice
    with pytest.raises(ValidationError):
        ClosureNotice(venue="v", keywords=["v"], closed_from=date(2026, 11, 1), closed_to=date(2026, 10, 1), source="s")


def test_corrupt_trace_lines_are_skipped(tmp_path):
    from fairfare import tracing
    (tmp_path / "abc.jsonl").write_text('{"type": "event", "name": "x"}\nnot json\n{"type": "event", "name": "y"}\n')
    assert len(tracing.load("abc", tmp_path)) == 2


def test_job_store_evicts_old_jobs():
    import time
    from fairfare.api.jobs import JobStore
    s = JobStore(workers=1, keep=3)
    ids = [s.submit(lambda: {"ok": 1}) for _ in range(8)]
    time.sleep(0.5)
    ids.append(s.submit(lambda: {"ok": 1}))
    time.sleep(0.3)
    assert s.get(ids[0]) is None and s.get(ids[-1]) is not None


def test_booking_rejects_sensitive_edit_and_mailto_injection(tmp_path):
    from datetime import date
    from fairfare.booking.models import BookingRequest
    from fairfare.booking.queue import ApprovalError, BookingQueue
    from fairfare.booking.senders import handoff_link
    q = BookingQueue(tmp_path)
    r = q.draft(BookingRequest(venue="H", kind="hotel", start=date(2026, 10, 19), end=date(2026, 10, 24),
                               party_size=2, channel="whatsapp", contact="+7 701 000 0000"))
    with pytest.raises(Exception):
        q.edit(r.id, "my passport number is 12345678")
    with pytest.raises(Exception):
        handoff_link("email", "a@b.com,evil@x.com", "s", "b")


def test_api_key_required_when_set(tmp_path, web, monkeypatch):
    from fastapi.testclient import TestClient
    from conftest import FakeExtractorLLM
    from fairfare.api.app import Deps, create_app
    from fairfare.booking.queue import BookingQueue
    monkeypatch.setenv("FAIRFARE_API_KEY", "secret")
    deps = Deps(llm=FakeExtractorLLM(), search=web[0], fetcher=web[1], queue=BookingQueue(tmp_path / "b"),
                outputs=tmp_path / "o")
    c = TestClient(create_app(deps))
    assert c.get("/health").status_code == 200
    assert c.get("/runs").status_code == 401
    assert c.get("/runs", headers={"X-API-Key": "secret"}).status_code == 200
