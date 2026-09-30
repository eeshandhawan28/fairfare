from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal, Optional

import hashlib

from pydantic import BaseModel, Field, computed_field

Status = Literal["drafted", "approved", "handed_off", "replied", "cancelled"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BookingRequest(BaseModel):
    id: str = ""
    venue: str
    kind: Literal["hotel", "tour", "ticket", "restaurant", "transport"] = "hotel"
    city: str = ""
    start: date
    end: Optional[date] = None
    party_size: int = Field(ge=1)
    party_ages: list[int] = Field(default_factory=list)
    notes: str = ""
    channel: Literal["whatsapp", "email", "phone"] = "whatsapp"
    contact: str = ""  # phone (with country code) or email
    language: Literal["en", "ru"] = "en"
    status: Status = "drafted"
    message: str = ""
    approved_by: str = ""
    approved_hash: str = ""
    approved_at: str = ""
    reply: str = ""
    reply_summary: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=now)
    log: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[misc]
    @property
    def message_hash(self) -> str:
        """Approval is bound to exactly this text: change one character and the hash changes."""
        return hashlib.sha256(self.message.encode()).hexdigest()[:16]
