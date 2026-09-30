"""Message drafting. Templates come first because they are predictable and never leak
sensitive data; the LLM may only polish wording, and a guard rejects any polish that
drops a required fact or adds anything that looks like payment or ID details."""
from __future__ import annotations

import re
from typing import Optional

from fairfare import tracing
from fairfare.booking.models import BookingRequest
from fairfare.llm import LLM

KIND_EN = {"hotel": "a room", "tour": "a tour", "ticket": "tickets", "restaurant": "a table",
           "transport": "a transfer"}
KIND_RU = {"hotel": "номер", "tour": "экскурсию", "ticket": "билеты", "restaurant": "столик",
           "transport": "трансфер"}
SENSITIVE = re.compile(r"passport|паспорт|card number|cvv|iban|otp|aadhaar|\b(?:\d[ -]?){12,19}\b", re.I)


def assert_no_sensitive(text: str) -> None:
    if SENSITIVE.search(text):
        raise ValueError("message looks like it contains passport, ID or payment details; remove them")


def template(req: BookingRequest) -> str:
    dates = req.start.isoformat() + (f" to {req.end.isoformat()}" if req.end else "")
    ages = f" (ages: {', '.join(map(str, req.party_ages))})" if req.party_ages else ""
    if req.language == "ru":
        d = req.start.strftime("%d.%m.%Y") + (f" - {req.end.strftime('%d.%m.%Y')}" if req.end else "")
        ages_ru = f" (возраст: {', '.join(map(str, req.party_ages))})" if req.party_ages else ""
        extra = f" {req.notes}" if req.notes else ""
        return (f"Здравствуйте! Мы хотели бы узнать, есть ли у вас {KIND_RU[req.kind]} на {d} "
                f"для {req.party_size} человек{ages_ru}.{extra} Подскажите, пожалуйста, наличие, полную стоимость "
                f"в тенге или рупиях и условия отмены. Спасибо!")
    extra = f" {req.notes}" if req.notes else ""
    needs = []
    if req.kind == "hotel":
        if any(a <= 4 for a in req.party_ages):
            needs.append("Could you provide a cot for a small child?")
        if any(a >= 65 for a in req.party_ages):
            needs.append("If possible we would like a lift-accessible or ground-floor room without stairs.")
        needs.append("Is breakfast included, and what is the check-in time?")
    ask = (" " + " ".join(needs)) if needs else ""
    return (f"Hello! We would like to ask whether you have {KIND_EN[req.kind]} available for {dates} "
            f"for {req.party_size} people{ages}.{extra}{ask} Could you tell us availability, the price per night "
            f"and the total including all taxes and fees, and your cancellation terms? Thank you.")


def required_facts(req: BookingRequest) -> list[str]:
    return [rf"(?<!\d){req.party_size}(?!\d)",
            re.escape(req.start.strftime("%d.%m.%Y") if req.language == "ru" else req.start.isoformat())]


def draft_message(req: BookingRequest, llm: Optional[LLM] = None) -> str:
    assert_no_sensitive(req.notes)  # notes flow into the template, so vet them before anything else
    base = template(req)
    if llm is None:
        return base
    system = ("Rewrite this booking enquiry to sound natural and polite in the same language. Keep every date, "
              "number and question. Do not add anything else. Return only the message.")
    polished = llm.complete("booking_writer", system, base).strip()
    ok = all(re.search(f, polished) for f in required_facts(req)) and not SENSITIVE.search(polished) and len(polished) < 900
    tracing.event("booking_polish", accepted=ok)
    return polished if ok else base
