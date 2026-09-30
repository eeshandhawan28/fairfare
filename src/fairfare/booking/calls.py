"""Voice-call agent: interface only, deliberately not implemented.

Before a real implementation ships it needs: an explicit AI disclosure at the start of every call,
the user's approval of the script and the callee, compliance with local call-recording and
telemarketing rules, Russian/Kazakh speech quality checks, and the same rule as text bookings:
no payment or ID details are ever spoken. Until then the drafted script is read by a human.
"""
from __future__ import annotations

from typing import Protocol

from fairfare.booking.models import BookingRequest


class CallAgent(Protocol):
    def call(self, req: BookingRequest) -> str: ...


class NotImplementedCallAgent:
    def call(self, req: BookingRequest) -> str:
        raise NotImplementedError("Voice calls are not enabled. Use the drafted message or call the venue yourself.")
