from datetime import date

import pytest

from fairfare.booking.drafts import draft_message
from fairfare.booking.models import BookingRequest
from fairfare.booking.queue import ApprovalError, BookingQueue
from fairfare.booking.senders import handoff_link


def req(**kw):
    base = dict(venue="Hotel Test", kind="hotel", start=date(2026, 10, 19), end=date(2026, 10, 24),
                party_size=4, party_ages=[58, 55, 24, 17], channel="whatsapp", contact="+7 701 000 0000")
    return BookingRequest(**{**base, **kw})


class Polisher:
    def __init__(self, out):
        self.out = out

    def complete(self, agent, system, user):
        return self.out


def test_template_has_facts_in_english_and_russian():
    en = draft_message(req())
    assert "2026-10-19" in en and "4 people" in en and "cancellation" in en
    ru = draft_message(req(language="ru"))
    assert "19.10.2026" in ru and "Здравствуйте" in ru and "4" in ru


def test_polish_rejected_if_it_drops_facts_or_asks_for_payment_details():
    good = "Hi! Do you have a room for 4 people on 2026-10-19 to 2026-10-24? Total price and cancellation terms please."
    assert draft_message(req(), Polisher(good)) == good
    dropped = "Hi, any rooms free?"
    assert "2026-10-19" in draft_message(req(), Polisher(dropped))  # fell back to template
    leaky = good + " Our passport numbers are attached."
    assert "passport" not in draft_message(req(), Polisher(leaky)).lower()


def test_nothing_can_be_handed_off_without_approval(tmp_path):
    q = BookingQueue(tmp_path)
    r = q.draft(req())
    with pytest.raises(ApprovalError):
        q.hand_off(r.id)
    with pytest.raises(ApprovalError):
        q.approve(r.id, "  ", q.get(r.id).message_hash)
    q.approve(r.id, "Eeshan", q.get(r.id).message_hash)
    link = q.hand_off(r.id)
    assert link.startswith("https://wa.me/77010000000?text=")
    assert q.get(r.id).status == "handed_off"
    with pytest.raises(ApprovalError):
        q.hand_off(r.id)  # cannot hand off twice


def test_editing_voids_approval(tmp_path):
    q = BookingQueue(tmp_path)
    r = q.draft(req())
    q.approve(r.id, "Eeshan", q.get(r.id).message_hash)
    q.edit(r.id, "Changed text")
    assert q.get(r.id).status == "drafted" and not q.get(r.id).approved_by
    with pytest.raises(ApprovalError):
        q.hand_off(r.id)


def test_reply_summary_flags_payment_requests(tmp_path):
    q = BookingQueue(tmp_path)
    r = q.draft(req())
    with pytest.raises(ApprovalError):
        q.record_reply(r.id, "hello")  # nothing sent yet
    q.approve(r.id, "E", q.get(r.id).message_hash)
    q.hand_off(r.id)
    out = q.record_reply(r.id, "Available. Please send a deposit of 50% to confirm.")
    assert out.status == "replied" and out.reply_summary["asks_for_payment"] is True


def test_phone_channel_is_not_automated_and_email_needs_address():
    with pytest.raises(ValueError):
        handoff_link(req(channel="phone").model_copy(update={"message": "x"}))
    with pytest.raises(ValueError):
        handoff_link(req(channel="email", contact="nope").model_copy(update={"message": "x"}))
    assert handoff_link(req(channel="email", contact="a@b.kz").model_copy(update={"message": "hi there"})).startswith("mailto:a@b.kz")


def test_approval_is_bound_to_the_message(tmp_path):
    q = BookingQueue(tmp_path)
    r = q.draft(req())
    with pytest.raises(ApprovalError):
        q.approve(r.id, "E", "not-the-hash")
    q.approve(r.id, "E", q.get(r.id).message_hash)
    q.edit(r.id, "Different text")
    with pytest.raises(ApprovalError):
        q.hand_off(r.id)
