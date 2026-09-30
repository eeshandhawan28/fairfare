"""Hand-off channels. These produce links the *user* opens; the system never sends messages."""
from __future__ import annotations

import re
from urllib.parse import quote

from fairfare.booking.models import BookingRequest


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def handoff_link(req: BookingRequest) -> str:
    if req.channel == "whatsapp":
        d = digits(req.contact)
        if len(d) < 8:
            raise ValueError("WhatsApp needs a phone number with country code")
        return f"https://wa.me/{d}?text={quote(req.message)}"
    if req.channel == "email":
        if "@" not in req.contact:
            raise ValueError("email channel needs an email address")
        return f"mailto:{req.contact}?subject={quote('Booking enquiry: ' + req.venue)}&body={quote(req.message)}"
    raise ValueError("phone bookings are not automated; call the number yourself using the drafted script")
