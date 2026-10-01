"""Deterministic price audit: compare each quote line to independently sourced bands."""
from __future__ import annotations

import re

from fairfare.models import Finding, QuoteLine, ReferencePrice

TOLERANCE = 0.15  # allow 15% above the top of the band before warning


def _match(line: QuoteLine, refs: list[ReferencePrice], destination: str = "") -> ReferencePrice | None:
    text = line.item.lower()
    for ref in refs:
        if ref.destination and ref.destination.lower() not in destination.lower():
            continue  # a band for another destination says nothing about this one
        if ref.category == line.category and any(k.lower() in text for k in ref.keywords):
            return ref
    return None


AGGREGATE_RE = re.compile(r"\b(meals?|food|dining|restaurants?|breakfasts?|lunch(es)?|dinners?|street food)\b|"
                          r"\b\d+\s*(nights?|days?)\b|\b(all|daily|every|per day)\b|\+|&|\bwith\b", re.I)


MULTIDAY_RE = re.compile(r"\b(\d+\s*days?|daily|per day|each day|city tours?)\b", re.I)


def _is_aggregate(line: QuoteLine) -> bool:
    if MULTIDAY_RE.search(f"{line.item} {line.note}"):
        return True
    return line.category in ("other", "activity") and bool(AGGREGATE_RE.search(f"{line.item} {line.note}"))


def check_prices(lines: list[QuoteLine], refs: list[ReferencePrice], travellers: int = 1,
                 nights: int = 1, destination: str = "") -> list[Finding]:
    findings: list[Finding] = []
    for line in lines:
        if line.amount is None:
            findings.append(
                Finding(severity="warn", kind="price", line=line.item,
                        message="Quote gives no readable amount for this line; ask the agent for it.",
                        confidence="high")
            )
            continue
        if _is_aggregate(line):
            findings.append(Finding(
                severity="info", kind="price", line=line.item, confidence="low",
                message="This line bundles many items (several meals, days or nights), so it cannot be compared "
                        "with a single-item price. Ask the agent to itemise it, then compare per meal or per day."))
            continue
        ref = _match(line, refs, destination)
        if ref is None:
            findings.append(
                Finding(
                    severity="info",
                    kind="price",
                    line=line.item,
                    message="No independent price band for this line, so it is neither confirmed nor flagged. "
                            "Ask the agent to itemise it and compare two other quotes.",
                    confidence="low",
                )
            )
            continue
        if line.currency != ref.currency:
            findings.append(
                Finding(
                    severity="info",
                    kind="price",
                    line=line.item,
                    message=f"Currency {line.currency} differs from reference {ref.currency}; skipped.",
                    source=ref.source,
                    confidence="low",
                )
            )
            continue
        scale = travellers if ref.unit == "per_person" else 1
        low, high = ref.low * scale, ref.high * scale
        ceiling = high * (1 + TOLERANCE)
        if line.amount > ceiling:
            over = (line.amount - high) / high * 100
            findings.append(
                Finding(
                    severity="warn",
                    kind="price",
                    line=line.item,
                    message=(
                        f"Quoted {line.amount:,.0f} {line.currency}; independent range is "
                        f"{low:,.0f} to {high:,.0f} ({over:.0f}% above the top of the range"
                        f"{', scaled to ' + str(travellers) + ' travellers' if scale > 1 else ''}). "
                        f"Reference dated {ref.as_of}{'; ' + ref.basis if ref.basis else ''}."
                    ),
                    source=ref.source,
                )
            )
    return findings


GAP_TERMS = {
    "travel insurance": ("insurance",),
    "visa or e-visa fees": ("visa", "evisa", "e-visa"),
    "meals": ("meal", "breakfast", "lunch", "dinner", "food"),
    "local transport": ("taxi", "transfer", "metro", "train", "car", "transport"),
    "entry tickets": ("ticket", "entry", "admission", "tour", "excursion"),
    "taxes, service charges and tips": ("tax", "gst", "service charge", "tip"),
}


def check_gaps(lines: list[QuoteLine]) -> list[Finding]:
    """What a quote silently leaves out is where surprise costs come from."""
    if not lines:
        return []
    text = " ".join(f"{l.item} {l.note}" for l in lines).lower()
    missing = [label for label, terms in GAP_TERMS.items() if not any(t in text for t in terms)]
    out = []
    if missing:
        out.append(Finding(severity="warn", kind="gap", line="Whole quote", confidence="medium",
                           message="Not mentioned anywhere in this quote: " + ", ".join(missing) +
                                   ". Ask whether each is included or extra, and get it in writing."))
    if not any(l.category == "flight" for l in lines):
        out.append(Finding(severity="info", kind="gap", line="Whole quote", confidence="high",
                           message="No flight line: confirm whether flights are included or booked separately."))
    return out
