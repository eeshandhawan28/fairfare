# Plan summary

Full plan with research and sources lives in the project's Trip Copilot planning document. Key decisions:

- Quote auditor is the headline feature; cost transparency is the core promise.
- Verification (closures, seasons, hours) and family-aware pacing come next.
- Stack: Python, LangGraph, FastAPI, Next.js, Playwright for JS-heavy operator sites.
- Models: one provider-agnostic layer (LiteLLM); Ollama for free dev and tests; cheap hosted models (DeepSeek, Moonshot) for high-volume steps and a stronger model for planning and checking, chosen by evals.
- Portfolio and personal tool first: light auth, no payments, no scale work.
- Booking agent last: WhatsApp and email before calls, never books or pays without approval.

## Agent pattern

Constraint manager, specialists, planner and checker loop (after ATLAS, arXiv 2509.25586), plus a dedicated verification agent and a price-audit agent.

## Known data caveats

- Shymbulak closure from 19 Oct 2026 is reported by Kursiv Media via a Google AI summary; unconfirmed.
- Reference prices are synthetic placeholders.
