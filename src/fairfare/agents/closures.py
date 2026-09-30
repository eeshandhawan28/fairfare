"""Deterministic closure check: does a dated notice overlap the trip dates?"""
from __future__ import annotations

from fairfare.models import ClosureNotice, Finding, QuoteLine, TripBrief


def _overlaps(notice: ClosureNotice, brief: TripBrief) -> bool:
    starts_before_trip_ends = notice.closed_from <= brief.end
    ends_after_trip_starts = notice.closed_to is None or notice.closed_to >= brief.start
    return starts_before_trip_ends and ends_after_trip_starts


def check_closures(
    lines: list[QuoteLine], brief: TripBrief, notices: list[ClosureNotice]
) -> list[Finding]:
    findings: list[Finding] = []
    for line in lines:
        text = f"{line.item} {line.note}".lower()
        for notice in notices:
            if not any(k.lower() in text for k in notice.keywords):
                continue
            if not _overlaps(notice, brief):
                continue
            reopening = (
                f"until {notice.closed_to.isoformat()}"
                if notice.closed_to
                else "(reopening date not stated)"
            )
            findings.append(
                Finding(
                    severity="high",
                    kind="closure",
                    line=line.item,
                    message=(
                        f"{notice.venue} is reported closed from {notice.closed_from.isoformat()} "
                        f"{reopening}, which overlaps your trip "
                        f"({brief.start.isoformat()} to {brief.end.isoformat()}). "
                        f"Reason: {notice.reason or 'not stated'}. "
                        "Confirm with the operator before paying."
                    ),
                    source=notice.source,
                    confidence=notice.confidence,
                )
            )
    return findings
