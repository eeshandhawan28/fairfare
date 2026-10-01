"""Render the trip pack: markdown, printable large-font HTML, WhatsApp text, optional PDF."""
from __future__ import annotations

import html
from typing import Optional

from fairfare import tracing
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
        contact = (f" Contact: {c.data['contact']} (from {domain(c.evidence.url)}, not independently verified)."
                   if c.data.get("contact") else "")
        out.append(f"- {c.text}{extra}{contact} [{domain(c.evidence.url)}]({c.evidence.url})")
    return out


COST_CATEGORIES = {"airport_transfer": "airport transfer", "taxi": "taxi", "public_transport": "public transport",
                   "entry_ticket": "entry tickets", "meal": "a meal", "sim_card": "SIM card", "hotel": "hotel"}


def load_label(load: int, cap: int) -> str:
    if cap <= 0 or load <= 0:
        return "light"
    r = load / cap
    return "light" if r <= 0.5 else "comfortable" if r <= 0.85 else "at your group's comfortable limit"


def _cost_line(c: Claim) -> str:
    d = c.data
    lo, hi = d.get("amount_low"), d.get("amount_high")
    amt = ""
    if lo not in (None, ""):
        amt = f"{lo}" if hi in (None, "", lo) else f"{lo}-{hi}"
        amt = f" ({amt} {d.get('currency', '')}{' per ' + str(d['per']) if d.get('per') else ''})"
    return f"{c.subject}: {c.text}{amt}"


def missing_costs(plan: TripPlan) -> list[str]:
    found = {str(c.data.get("item", "")) for c in plan.costs}
    return [label for key, label in COST_CATEGORIES.items() if key not in found and key != "hotel"]


def evidence_note(plan: TripPlan) -> str:
    claims = plan.visa + plan.transport + plan.avoid + plan.costs + plan.stay + plan.food + plan.contacts + plan.practical
    ev = [e for p in plan.places for e in p.evidence] + [c.evidence for c in claims]
    if ev and all(e.retrieved_at == "search_digest" for e in ev):
        return ("Evidence quality: page fetching was unavailable, so facts come from web-search summaries rather "
                "than the pages themselves. Treat every line as a lead and confirm it at the linked source.")
    return ""


def _lines(claims: list[Claim], fmt) -> list[str]:
    return [f"- {fmt(c)} [{domain(c.evidence.url)}]({c.evidence.url})" for c in claims]


def _extra_sections(plan: TripPlan) -> list[str]:
    out: list[str] = []
    if plan.stay:
        out += ["## Where to stay", *_lines(plan.stay, lambda c: f"**{c.subject}**"
                + (f" ({c.data['area']})" if c.data.get("area") else "") + f". {c.text}"), ""]
    out += ["## What things cost"]
    if plan.costs:
        out += _lines(plan.costs, _cost_line)
    miss = missing_costs(plan)
    if miss:
        out.append(f"- No sourced price found for: {', '.join(miss)}. Ask for these in writing before you pay.")
    out.append("")
    venues = [c for c in plan.food if str(c.data.get("kind", "")).lower() in ("restaurant", "market")]
    dishes = [c for c in plan.food if c not in venues]
    veg = lambda c: " (vegetarian-friendly)" if str(c.data.get("vegetarian", "")).lower() == "yes" else ""
    if venues:
        out += ["## Where to eat", *_lines(venues, lambda c: f"**{c.subject}**. {c.text}{veg(c)}"), ""]
    if dishes:
        out += ["## Dishes to try", *_lines(dishes, lambda c: f"**{c.subject}**. {c.text}{veg(c)}"), ""]
    gems = [p for p in plan.places if p.hidden_gem]
    if gems:
        out += ["## Hidden gems (mentioned by several independent travellers)",
                *[f"- **{p.name}**. {p.notes} " + " ".join(f"[{domain(u)}]({u})" for u in p.sources[:3]) for p in gems], ""]
    if plan.avoid:
        out += ["## Scams and things to avoid", *_lines(plan.avoid, lambda c: c.text), ""]
    if plan.practical:
        out += ["## Money, SIM and practical tips", *_lines(plan.practical, lambda c: c.text), ""]
    if plan.contacts:
        out += ["## Emergency and local contacts", *_lines(plan.contacts, lambda c: f"{c.text}"
                + (f" Phone: {c.data['phone']}." if c.data.get("phone") else "")), ""]
    return out


def render_markdown(plan: TripPlan, audit_report: Optional[str] = None) -> str:
    b = plan.brief
    ages = ", ".join(f"{t.name} ({t.age})" for t in b.travellers) or "not given"
    out = [f"# {b.destination}: {b.start.isoformat()} to {b.end.isoformat()}",
           f"{b.nights} nights. Travellers: {ages}. Pace: {b.pace}. Budget: {b.budget_tier}.", ""]
    note = evidence_note(plan)
    if note:
        out += [f"> {note}", ""]
    if plan.warnings:
        out += ["## Check before you go", *[f"- {w}" for w in plan.warnings], ""]
    out.append("## Day by day")
    ideas = [c.subject for c in plan.food if str(c.data.get("kind", "")).lower() in ("restaurant", "market")] or \
        [f"try {c.subject}" for c in plan.food]
    stay = plan.stay[0].subject if plan.stay else ""
    used_ideas: set[str] = set()
    for i, d in enumerate(plan.days, 1):
        out.append(f"### Day {i}: {d.day.strftime('%A %d %B')} ({d.role} day, {load_label(d.load, d.cap)} load)")
        for blk in d.blocks:
            src = f" {_srcs(blk.sources)}" if blk.sources else ""
            extra = ""
            if blk.kind == "meal" and ideas:
                today = " ".join(b.title.lower() for b in d.blocks if b.kind == "activity")
                fresh = [x for x in ideas if x not in used_ideas and x.lower() not in today] or \
                    [x for x in ideas if x.lower() not in today] or ideas
                pick = fresh[:2]
                used_ideas.update(pick)
                extra = " Ideas: " + " or ".join(pick) + " (see Where to eat / Dishes to try)."
            if blk.kind == "arrival" and stay:
                extra = f" Suggested area or stay: {stay} (see Where to stay)."
            note = f" {blk.notes}{extra}" if blk.notes else extra
            out.append(f"- {blk.start}-{blk.end} **{blk.title}**.{note}{src}")
        if d.plan_b:
            out.append(f"- {d.plan_b}")
        out.append("")
    out += _extra_sections(plan)
    out += ["## Entry and visa", *_claims_md(plan.visa, "No entry rules found yet. Check the official embassy site."),
            "- If a visa or e-visa is needed, apply early and only through the official portal or embassy.", ""]
    out += ["## Getting around", *_claims_md(plan.transport, "No verified transport options found yet."), ""]
    if audit_report:
        out += ["## Quote audit", audit_report, ""]
    urls = sorted({e.url for p in plan.places for e in p.evidence} | {
        c.evidence.url for c in plan.visa + plan.transport + plan.avoid + plan.costs + plan.stay + plan.food + plan.contacts + plan.practical})
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
    if evidence_note(plan):
        parts.insert(1, f"<p class='b'>{e(evidence_note(plan))}</p>")
    for title, claims in (("Where to stay", plan.stay), ("What things cost", plan.costs), ("Where to eat and what to try", plan.food),
                          ("Scams and things to avoid", plan.avoid), ("Emergency and local contacts", plan.contacts), ("Money, SIM and practical tips", plan.practical),
                          ("Entry and visa", plan.visa), ("Getting around", plan.transport)):
        if claims:
            def item(c: Claim) -> str:
                tag = ""
                if c.kind == "visa":
                    tag = " (official source)" if c.data.get("official") else " (unofficial source, verify)"
                contact = f" Contact: {c.data['contact']} (from {domain(c.evidence.url)}, not independently verified)." \
                    if c.data.get("contact") else ""
                text = _cost_line(c) if c.kind == "cost" else c.text
                return f"<li>{e(text)}{e(tag)}{e(contact)} <small>{e(domain(c.evidence.url))}</small></li>"
            parts.append(f"<section><h2>{title}</h2><ul>" + "".join(item(c) for c in claims) + "</ul></section>")
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
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html_text)
                page.pdf(path=path, format="A4", print_background=True)
            finally:
                browser.close()
    except Exception as exc:  # browser binary missing etc.: caller falls back to HTML
        tracing.event("pdf_failed", error=f"{type(exc).__name__}: {exc}")
        return False
    return True
