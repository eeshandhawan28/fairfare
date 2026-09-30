from __future__ import annotations

from fairfare.models import Finding, QuoteLine, TripBrief

ORDER = {"high": 0, "warn": 1, "info": 2}
LABEL = {"high": "ACT NOW", "warn": "CHECK", "info": "NOTE"}


def render_report(brief: TripBrief, lines: list[QuoteLine], findings: list[Finding]) -> str:
    total = sum(line.amount for line in lines)
    currency = lines[0].currency if lines else "INR"
    out = [
        f"# Quote audit: {brief.destination}",
        f"{brief.start.isoformat()} to {brief.end.isoformat()} "
        f"({brief.nights} nights, {len(brief.travellers)} travellers)",
        "",
        f"Quote total: {total:,.0f} {currency} across {len(lines)} lines.",
        "",
    ]
    actionable = [f for f in findings if f.severity != "info"]
    if not actionable:
        out.append("No price or closure issues found. Lines without a reference are listed as notes.")
    else:
        out.append(f"{len(actionable)} issue(s) need your attention:")
    out.append("")
    for f in sorted(findings, key=lambda x: ORDER[x.severity]):
        src = f" [source: {f.source}]" if f.source else ""
        out.append(f"- **{LABEL[f.severity]}** ({f.kind}, {f.confidence} confidence) "
                   f"{f.line}: {f.message}{src}")
    return "\n".join(out) + "\n"
