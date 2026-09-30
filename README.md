# fairfare

Cost-transparent, verified, family-aware trip planning. Built after a family trip to Kazakhstan where the travel agent's quote was opaque and included a ski-resort day that was closed for our dates.

**The headline feature is the quote auditor**: paste an agent's quote and see which lines are priced above independent ranges and which activities are closed for your dates. Every finding carries a source and a confidence level.

## Status

Early spike. What works today:

- Parse a free-text quote into line items (LLM, provider-agnostic).
- Audit prices against reference bands (deterministic).
- Flag closures that overlap the trip dates (deterministic).
- Markdown report, worst issues first.

What is still stubbed: `data/reference_prices.json` is **synthetic** and `data/closures.json` holds one **unconfirmed** entry. Live price and closure retrieval is the next milestone. See [docs/PLAN.md](docs/PLAN.md).

## Design rule

Only quote parsing calls an LLM. Price and closure checks are deterministic code over dated, sourced data, so a finding is never a model's recollection.

## Quickstart (free, local models)

```bash
ollama serve &
ollama pull qwen2.5:7b
pip install -e ".[dev]"
cp .env.example .env

pytest                                   # offline, no model needed
fairfare audit evals/cases/shymbulak_quote.txt --start 2026-10-19 --end 2026-10-24
python evals/run.py                      # scores the configured model on known cases
```

## Tracing and improvement loop

Every run writes `traces/<run_id>.jsonl` (gitignored): each LLM call (model, prompt, output, latency, tokens) and each graph node is a span, plus events like parse results and price coverage.

```bash
fairfare traces list
fairfare traces show <run>        # span tree with timings and errors
fairfare traces lint <run>        # deterministic checks, free
fairfare traces review <run>      # lint + a small cheap model reads the trace
fairfare traces feedback <run> --rating bad --note "missed the closure"
```

Loop: run, review, turn every `bad` run into a case in `evals/cases/`, change a prompt or model, re-run `python evals/run.py`. Model reviewer output is labelled `model` and is advice, not truth. Traces are plain JSON, so exporting to Langfuse or OpenTelemetry later needs no changes to the agents.

## Models

All agents call one layer (LiteLLM). Edit `config/models.yaml` to route any agent to Ollama, DeepSeek, Moonshot or any OpenRouter model. Use `evals/` to compare models per agent before switching. Small local models are weaker at structured output, so expect to check parse quality.

## Layout

```
src/fairfare/   agents, graph, models, llm layer, CLI
config/         model routing
data/           reference prices and closure notices
evals/          known-problem cases and runner
tests/          offline tests with a fake LLM
traces/         local run traces (gitignored)
docs/           plan
```

## Usage
```
pip install -e ".[dev]"
fairfare serve --demo          # offline demo API on :8000; then: cd web && npm run dev
fairfare plan examples/kazakhstan_family.json
fairfare audit --quote quote.txt --brief examples/kazakhstan_family.json [--live]
fairfare book draft|list|approve|link|reply
fairfare traces list|show|lint|review|feedback
```
See docs/ARCHITECTURE.md for the security model and known limits.

## Roadmap

1. Spike: quote auditor on one destination (this repo now).
2. Live retrieval: price sources, operator-site and news closure checks (headless browser).
3. Full multi-agent trip planner with family-aware pacing and cited itineraries.
4. Booking agent (WhatsApp and email first, then calls), always with user approval.
