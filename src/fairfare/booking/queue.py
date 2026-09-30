"""Booking requests with a hard human-approval gate. Nothing leaves without `approve`."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Optional

from fairfare.booking.drafts import draft_message
from fairfare.booking.models import BookingRequest, now
from fairfare.booking.replies import summarize_reply
from fairfare.booking.senders import handoff_link
from fairfare.llm import LLM


class ApprovalError(Exception):
    pass


class BookingQueue:
    def __init__(self, directory: Optional[Path] = None) -> None:
        self.dir = Path(directory or os.getenv("FAIRFARE_BOOKINGS", "bookings"))
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, rid: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", rid):
            raise KeyError(rid)
        return self.dir / f"{rid}.json"

    def save(self, req: BookingRequest) -> BookingRequest:
        self._path(req.id).write_text(req.model_dump_json(indent=2))
        return req

    def get(self, rid: str) -> BookingRequest:
        p = self._path(rid)
        if not p.exists():
            raise KeyError(rid)
        return BookingRequest(**json.loads(p.read_text()))

    def list(self) -> list[BookingRequest]:
        return [BookingRequest(**json.loads(p.read_text())) for p in sorted(self.dir.glob("*.json"))]

    def draft(self, req: BookingRequest, llm: Optional[LLM] = None) -> BookingRequest:
        req.id = req.id or uuid.uuid4().hex[:8]
        req.message = draft_message(req, llm)
        req.status = "drafted"
        req.log.append(f"{now()} drafted")
        return self.save(req)

    def edit(self, rid: str, message: str) -> BookingRequest:
        req = self.get(rid)
        if req.status not in ("drafted", "approved"):
            raise ApprovalError(f"cannot edit a request that is {req.status}")
        req.message, req.status = message, "drafted"  # any edit voids a previous approval
        req.approved_by = req.approved_at = ""
        req.log.append(f"{now()} edited; approval reset")
        return self.save(req)

    def approve(self, rid: str, by: str) -> BookingRequest:
        if not by.strip():
            raise ApprovalError("approval needs a name")
        req = self.get(rid)
        if req.status != "drafted":
            raise ApprovalError(f"only drafted requests can be approved (this one is {req.status})")
        req.status, req.approved_by, req.approved_at = "approved", by.strip(), now()
        req.log.append(f"{now()} approved by {by.strip()}")
        return self.save(req)

    def hand_off(self, rid: str) -> str:
        """Returns a wa.me or mailto link for the user to open and send. We never send for them."""
        req = self.get(rid)
        if req.status != "approved":
            raise ApprovalError("request is not approved")
        link = handoff_link(req)
        req.status = "handed_off"
        req.log.append(f"{now()} link generated for {req.channel}")
        self.save(req)
        return link

    def record_reply(self, rid: str, text: str, llm: Optional[LLM] = None) -> BookingRequest:
        req = self.get(rid)
        if req.status not in ("handed_off", "replied"):
            raise ApprovalError("nothing has been sent yet")
        req.reply, req.status = text, "replied"
        req.reply_summary = summarize_reply(text, llm)
        req.log.append(f"{now()} reply recorded")
        return self.save(req)

    def cancel(self, rid: str) -> BookingRequest:
        req = self.get(rid)
        req.status = "cancelled"
        req.log.append(f"{now()} cancelled")
        return self.save(req)
