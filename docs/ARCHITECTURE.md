# Architecture and security model

## Pipelines
- **Audit**: parse quote -> reference price bands (curated file, or live scouts needing 2+ independent domains) -> deterministic price check -> closure scouting -> deterministic overlap check -> findings.
- **Plan**: research places, local intel, visa, transport -> closure scouting on eligible places -> window-aware scheduler -> independent checker -> repair loop (drop offenders, re-plan, up to 5 rounds).

## Core rule
The LLM only extracts. Each claim needs a verbatim quote that is checked against the fetched page text. Dates and numbers must appear in the quote. Prices, closures, scheduling, checking and booking gates are plain code.

## Security model
- Web pages are untrusted data (wrapped in `<page>` tags, never treated as instructions).
- Fetcher refuses non-public addresses on every redirect hop, and in the headless browser.
- "Official" visa sources need a strict government-domain match; independence is counted by registrable domain.
- Booking never sends anything. Flow: draft -> approved (bound to the message hash) -> handed off as a wa.me / mailto link you open yourself. Editing voids approval. Sensitive data (passport, card numbers) is rejected in drafts.
- Set `FAIRFARE_API_KEY` for any non-local use; `serve` refuses a non-local host without it.

## Known limits
- Live web retrieval was not exercised from the build sandbox (search and fetch were blocked); tests and the demo use fixtures.
- `data/reference_prices.json` is synthetic; the Shymbulak closure is an unconfirmed report. Verify before relying on either.
- Phone calling is an interface only.
