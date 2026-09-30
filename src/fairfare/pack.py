"""Render the trip pack: markdown, printable large-font HTML, WhatsApp text, optional PDF."""
from __future__ import annotations

import html
from typing import Optional

from fairfare.models import Claim, TripPlan
from fairfare.research import domain


def _srcs(urls: list[str]) -> str:
    return " ".join(f"[{domain(u)}]({u})" for u in urls)


def _claims_md(claims: list[Claim], empty: str) -> list[str]:
    if not claims:
        return [empty]
    out = []
    for c in claims:
        extra = ""
        if c.kind == "visa":
            extra = " (official source)" if c.data.get("official") else " (unofficial source, verify)"
        contact = f" Contact: {c.data['contact']}." if c.data.get("contact") else ""
        out.append(f"- {c.text}{extra}{contact} [{domain(c.evidence.url)}]({c.evidence.url})")
    return out


def render_markdown(plan: TripPlan, audit_report: Optional[str] = None) -> str:
    b = plan.brief
    ages = ", ".join(f"{t.name} ({t.age})" for t in b.travellers) or "not given"
    out = [f"# {b.destination}: {b.start.isoformat()} to {b.end.isoformat()}",
           f"{b.nights} nights. Travellers: {ages}. Pace: {b.pace}. Budget: {b.budget_tier}.", ""]
    if plan.warnings:
        out += ["## Check before you go", *[f"- {w}" for w in plan.warnings], ""]
    out.append("## Day by day")
    for i, d in enumerate(plan.days, 1):
        out.append(f"### Day {i}: {d.day.strftime('%A %d %B')} ({d.role} day, effort {d.load} of {d.cap})")
        for blk in d.blocks:
            src = f" {_srcs(blk.sources)}" if blk.sources else ""
            note = f" {blk.notes}" if blk.notes else ""
            out.append(f"- {blk.start}-{blk.end} **{blk.title}**.{note}{src}")
        if d.plan_b:
            out.append(f"- {d.plan_b}")
        out.append("")
    out += ["## Entry and visa", *_claims_md(plan.visa, "No entry rules found yet. Check the official embassy site."), ""]
    out += ["## Getting around", *_claims_md(plan.transport, "No verified transport options found yet."), ""]
    if plan.avoid:
        out += ["## Reports to be careful about", *_claims_md(plan.avoid, ""), ""]
    if audit_report:
        out += ["## Quote audit", audit_report, ""]
    urls = sorted({e.url for p in plan.places for e in p.evidence} | {c.evidence.url for c in plan.visa + plan.transport + plan.avoid})
    out += ["## Sources", *[f"- {u}" for u in urls], "", f"Run: {plan.run_id}"]
    return "\n".join(out) + "\n"


def render_whatsapp(plan: TripPlan) -> str:
    b = plan.brief
    lines = [f"*{b.destination}* {b.start.strftime('%d %b')} to {b.end.strftime('%d %b %Y')}", ""]
    for i, d in enumerate(plan.days, 1):
        lines.append(f"*Day {i} - {d.day.strftime('%a %d %b')}*")
        for blk in d.blocks:
            if blk.kind in ("activity", "meal", "arrival", "departure", "rest"):
                lines.append(f"{blk.start} {blk.title}")
        lines.append("")
    if plan.warnings:
        lines.append("*Check before you go*")
        lines += [f"- {w}" for w in plan.warnings[:5]]
    return "\n".join(lines).strip() + "\n"


def render_html(plan: TripPlan, audit_report: Optional[str] = None) -> str:
    """Large-font, print-friendly page for parents. Escapes everything it embeds."""
    e = html.escape
    b = plan.brief
    parts = [f"<h1>{e(b.destination)}: {b.start.isoformat()} to {b.end.isoformat()}</h1>"]
    if plan.warnings:
        parts.append("<section class='warn'><h2>Check before you go</h2><ul>" +
                     "".join(f"<li>{e(w)}</li>" for w in plan.warnings) + "</ul></section>")
    for i, d in enumerate(plan.days, 1):
        rows = "".join(
            f"<li><b>{e(blk.start)}-{e(blk.end)}</b> {e(blk.title)}"
            + (f" <small>{e(blk.notes)}</small>" if blk.notes else "") + "</li>" for blk in d.blocks)
        pb = f"<p class='b'>{e(d.plan_b)}</p>" if d.plan_b else ""
        parts.append(f"<section><h2>Day {i}: {e(d.day.strftime('%A %d %B'))}</h2><ul>{rows}</ul>{pb}</section>")
    for title, claims in (("Entry and visa", plan.visa), ("Getting around", plan.transport)):
        if claims:
            parts.append(f"<section><h2>{title}</h2><ul>" + "".join(
                f"<li>{e(c.text)} <small>{e(domain(c.evidence.url))}</small></li>" for c in claims) + "</ul></section>")
    if audit_report:
        parts.append(f"<section><h2>Quote audit</h2><pre>{e(audit_report)}</pre></section>")
    css = ("body{font-family:system-ui,sans-serif;font-size:20px;line-height:1.5;max-width:820px;margin:24px auto;"
           "padding:0 16px}h1{font-size:30px}h2{font-size:24px;border-bottom:2px solid #999}"
           ".warn{background:#fff3cd;padding:8px 16px}.b{color:#555}small{color:#555}pre{white-space:pre-wrap}"
           "@media print{section{break-inside:avoid}}")
    return f"<!doctype html><html><head><meta charset='utf-8'><title>Trip pack</title><style>{css}</style></head><body>{''.join(parts)}</body></html>"


def html_to_pdf(html_text: str, path: str) -> bool:
    """PDF via headless Chromium if Playwright is installed. Returns False when unavailable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html_text)
            page.pdf(path=path, format="A4", print_background=True)
        finally:
            browser.close()
    return True
