"""Booking requests with a hard human-approval gate. Nothing leaves without `approve`."""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Optional

from fairfare.booking.drafts import assert_no_sensitive, draft_message
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
        self.lock = threading.RLock()  # read-modify-write cycles must not interleave

    def _path(self, rid: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", rid):
            raise KeyError(rid)
        return self.dir / f"{rid}.json"

    def save(self, req: BookingRequest) -> BookingRequest:
        tmp = self._path(req.id).with_suffix(".tmp")
        tmp.write_text(req.model_dump_json(indent=2))
        os.replace(tmp, self._path(req.id))  # atomic: readers never see half a file
        return req

    def get(self, rid: str) -> BookingRequest:
        p = self._path(rid)
        if not p.exists():
            raise KeyError(rid)
        return BookingRequest(**json.loads(p.read_text()))

    def list(self) -> list[BookingRequest]:
        return [BookingRequest(**json.loads(p.read_text())) for p in sorted(self.dir.glob("*.json"))]

    def draft(self, req: BookingRequest, llm: Optional[LLM] = None) -> BookingRequest:
        req.id = uuid.uuid4().hex[:8]  # ids are always server-chosen: a client can never overwrite a booking
        req.approved_by = req.approved_at = req.approved_hash = req.reply = ""
        req.message = draft_message(req, llm)
        req.status = "drafted"
        req.log.append(f"{now()} drafted")
        return self.save(req)

    def edit(self, rid: str, message: str) -> BookingRequest:
        assert_no_sensitive(message)
        with self.lock:
            req = self.get(rid)
            if req.status not in ("drafted", "approved"):
                raise ApprovalError(f"cannot edit a request that is {req.status}")
            req.message, req.status = message, "drafted"  # any edit voids a previous approval
            req.approved_by = req.approved_at = req.approved_hash = ""
            req.log.append(f"{now()} edited; approval reset")
            return self.save(req)

    def approve(self, rid: str, by: str, message_hash: str) -> BookingRequest:
        """Approval names the exact text the human saw. If the text changed since, it is refused."""
        if not by.strip():
            raise ApprovalError("approval needs a name")
        with self.lock:
            req = self.get(rid)
            if req.status != "drafted":
                raise ApprovalError(f"only drafted requests can be approved (this one is {req.status})")
            if message_hash != req.message_hash:
                raise ApprovalError("the message changed since you read it; review the current text and approve again")
            req.status, req.approved_by, req.approved_at = "approved", by.strip(), now()
            req.approved_hash = req.message_hash
            req.log.append(f"{now()} approved by {by.strip()} (text {req.message_hash})")
            return self.save(req)

    def hand_off(self, rid: str) -> str:
        """Returns a wa.me or mailto link for the user to open and send. We never send for them."""
        with self.lock:
            req = self.get(rid)
            if req.status != "approved" or req.approved_hash != req.message_hash:
                raise ApprovalError("request is not approved for its current text")
            link = handoff_link(req)
            req.status = "handed_off"
            req.log.append(f"{now()} link generated for {req.channel}")
            self.save(req)
            return link

    def record_reply(self, rid: str, text: str, llm: Optional[LLM] = None) -> BookingRequest:
        with self.lock:
            req = self.get(rid)
            if req.status not in ("handed_off", "replied"):
                raise ApprovalError("nothing has been sent yet")
            req.reply, req.status = text[:5000], "replied"
            req.reply_summary = summarize_reply(req.reply, llm)
            req.log.append(f"{now()} reply recorded")
            return self.save(req)

    def cancel(self, rid: str) -> BookingRequest:
        with self.lock:
            req = self.get(rid)
            req.status = "cancelled"
            req.log.append(f"{now()} cancelled")
            return self.save(req)
