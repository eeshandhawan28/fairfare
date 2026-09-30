"""Deterministic price audit: compare each quote line to independently sourced bands."""
from __future__ import annotations

from fairfare.models import Finding, QuoteLine, ReferencePrice

TOLERANCE = 0.15  # allow 15% above the top of the band before warning


def _match(line: QuoteLine, refs: list[ReferencePrice]) -> ReferencePrice | None:
    text = line.item.lower()
    for ref in refs:
        if ref.category == line.category and any(k.lower() in text for k in ref.keywords):
            return ref
    return None


def check_prices(lines: list[QuoteLine], refs: list[ReferencePrice], travellers: int = 1,
                 nights: int = 1) -> list[Finding]:
    findings: list[Finding] = []
    for line in lines:
        if line.amount is None:
            findings.append(
                Finding(severity="warn", kind="price", line=line.item,
                        message="Quote gives no readable amount for this line; ask the agent for it.",
                        confidence="high")
            )
            continue
        ref = _match(line, refs)
        if ref is None:
            findings.append(
                Finding(
                    severity="info",
                    kind="price",
                    line=line.item,
                    message="No independent price reference for this line yet; cannot audit.",
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
                        f"Reference dated {ref.as_of}."
                    ),
                    source=ref.source,
                )
            )
    return findings
