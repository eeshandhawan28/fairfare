"""Grounded research: search -> fetch -> LLM extraction -> quote verification.

The LLM may only *extract*. Every claim it returns must carry a verbatim quote, and the
quote is checked against the page text. Claims whose quote is not on the page are
rejected and counted in the trace. That is how the system stays honest about sources.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from fairfare import tracing
from fairfare.llm import LLM, extract_json
from fairfare.models import Claim, Evidence
from fairfare.tools.fetch import Fetcher
from fairfare.tools.search import SearchProvider

MAX_PAGE_CHARS = 6000
MIN_QUOTE_CHARS = 20

SYSTEM = """You extract facts from a web page for a travel planner.
Return ONLY a JSON array. Each element:
  {{"subject": "<what the fact is about>", "text": "<the fact in one sentence>",
    "quote": "<an EXACT verbatim excerpt from the page that supports it, 20-300 chars>",
    "data": {{ {schema} }} }}
Rules: {instructions}
Never use knowledge from outside the page. If the page has nothing relevant return []."""


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", " ", s.lower()).strip()


def quote_in_text(quote: str, text: str) -> bool:
    q = _norm(quote)
    return len(q) >= MIN_QUOTE_CHARS and q in _norm(text)


def domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


class Researcher:
    def __init__(self, llm: LLM, search: SearchProvider, fetcher: Fetcher,
                 agent: str = "extractor", pages_per_query: int = 3) -> None:
        self.llm, self.search, self.fetcher = llm, search, fetcher
        self.agent, self.pages_per_query = agent, pages_per_query

    def run(self, queries: list[str], kind: str, instructions: str, schema: str = "") -> list[Claim]:
        system = SYSTEM.format(instructions=instructions, schema=schema)
        claims: list[Claim] = []
        seen_urls: set[str] = set()
        stats = {"pages": 0, "fetch_failed": 0, "claims_raw": 0, "claims_rejected": 0}
        with tracing.span(f"research:{kind}", kind="agent", queries=queries) as rec:
            for q in queries:
                try:
                    results = self.search.search(q, n=self.pages_per_query + 2)
                except Exception as exc:
                    tracing.event("search_failed", query=q, error=f"{type(exc).__name__}: {exc}")
                    continue
                for r in results[: self.pages_per_query]:
                    if r.url in seen_urls:
                        continue
                    seen_urls.add(r.url)
                    text, retrieved = r.snippet, ""
                    try:
                        page = self.fetcher.fetch(r.url)
                        text, retrieved = page.text, page.retrieved_at
                    except Exception as exc:
                        stats["fetch_failed"] += 1
                        tracing.event("fetch_failed", url=r.url, error=f"{type(exc).__name__}: {exc}")
                        if not text:
                            continue
                    stats["pages"] += 1
                    claims.extend(self._extract(system, r.url, text, retrieved, kind, stats))
            deduped = self._dedupe(claims)
            rec["output"] = {**stats, "claims": len(deduped)}
        return deduped

    def _extract(self, system: str, url: str, text: str, retrieved: str, kind: str,
                 stats: dict[str, int]) -> list[Claim]:
        user = f"PAGE URL: {url}\nPAGE TEXT:\n{text[:MAX_PAGE_CHARS]}"
        try:
            data = extract_json(self.llm.complete(self.agent, system, user))
        except ValueError:
            tracing.event("extract_unparseable", url=url)
            return []
        out: list[Claim] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            stats["claims_raw"] += 1
            quote = str(item.get("quote", ""))
            if not quote_in_text(quote, text):
                stats["claims_rejected"] += 1
                tracing.event("claim_rejected", url=url, subject=item.get("subject", ""), quote=quote)
                continue
            out.append(Claim(kind=kind, subject=str(item.get("subject", "")), text=str(item.get("text", "")),
                             evidence=Evidence(url=url, quote=quote, retrieved_at=retrieved or "snippet"),
                             data=item.get("data") if isinstance(item.get("data"), dict) else {}))
        return out

    @staticmethod
    def _dedupe(claims: list[Claim]) -> list[Claim]:
        seen, out = set(), []
        for c in claims:
            key = (c.evidence.url, _norm(c.subject), _norm(c.text))
            if key not in seen:
                seen.add(key)
                out.append(c)
        return out


def independent_domains(claims: list[Claim]) -> int:
    return len({domain(c.evidence.url) for c in claims})


def get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    v = data.get(key, default)
    return default if v in ("", "null", "None") else v
