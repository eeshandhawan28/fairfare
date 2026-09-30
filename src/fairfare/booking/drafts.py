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
SENSITIVE = re.compile(r"passport|паспорт|card number|cvv|iban|\b\d{12,19}\b|otp", re.I)


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
    return (f"Hello! We would like to ask whether you have {KIND_EN[req.kind]} available for {dates} "
            f"for {req.party_size} people{ages}.{extra} Could you tell us availability, the total price "
            f"including all taxes and fees, and your cancellation terms? Thank you.")


def required_facts(req: BookingRequest) -> list[str]:
    facts = [str(req.party_size)]
    facts.append(req.start.strftime("%d.%m.%Y") if req.language == "ru" else req.start.isoformat())
    return facts


def draft_message(req: BookingRequest, llm: Optional[LLM] = None) -> str:
    base = template(req)
    if llm is None:
        return base
    system = ("Rewrite this booking enquiry to sound natural and polite in the same language. Keep every date, "
              "number and question. Do not add anything else. Return only the message.")
    polished = llm.complete("booking_writer", system, base).strip()
    ok = all(f in polished for f in required_facts(req)) and not SENSITIVE.search(polished) and len(polished) < 900
    tracing.event("booking_polish", accepted=ok)
    return polished if ok else base
