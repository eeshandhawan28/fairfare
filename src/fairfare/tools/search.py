"""Web search behind one interface. DuckDuckGo needs no key; Tavily is optional."""
from __future__ import annotations

import os
from typing import Protocol

from pydantic import BaseModel

from fairfare import tracing


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""
    digest: str = ""  # model-written summary of the whole search (claude-cli provider only)


class SearchProvider(Protocol):
    def search(self, query: str, n: int = 5) -> list[SearchResult]: ...


class DDGSearch:
    def search(self, query: str, n: int = 5) -> list[SearchResult]:
        try:
            from ddgs import DDGS
        except ImportError:  # older package name
            from duckduckgo_search import DDGS  # type: ignore
        with tracing.span("search", kind="tool", provider="ddg", query=query) as rec:
            hits = DDGS().text(query, max_results=n) or []
            rec["results"] = len(hits)
            return [SearchResult(title=h.get("title", ""), url=h.get("href", ""), snippet=h.get("body", ""))
                    for h in hits if h.get("href")]


class TavilySearch:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ["TAVILY_API_KEY"]

    def search(self, query: str, n: int = 5) -> list[SearchResult]:
        import httpx

        with tracing.span("search", kind="tool", provider="tavily", query=query) as rec:
            r = httpx.post("https://api.tavily.com/search",
                           json={"api_key": self.api_key, "query": query, "max_results": n}, timeout=30)
            r.raise_for_status()
            hits = r.json().get("results", [])
            rec["results"] = len(hits)
            return [SearchResult(title=h.get("title", ""), url=h["url"], snippet=h.get("content", ""))
                    for h in hits if h.get("url")]


class FixtureSearch:
    """Deterministic search for tests and offline demos: first key contained in the query wins."""

    def __init__(self, table: dict[str, list[SearchResult]]) -> None:
        self.table = table
        self.queries: list[str] = []

    def search(self, query: str, n: int = 5) -> list[SearchResult]:
        self.queries.append(query)
        with tracing.span("search", kind="tool", provider="fixture", query=query) as rec:
            for key, results in self.table.items():
                if key.lower() in query.lower():
                    rec["results"] = len(results[:n])
                    return results[:n]
            rec["results"] = 0
            return []


def get_search() -> SearchProvider:
    provider = os.getenv("FAIRFARE_SEARCH", "").lower()
    if provider == "tavily" or (not provider and os.getenv("TAVILY_API_KEY")):
        return TavilySearch()
    if provider in ("claude-cli", "claude"):
        from fairfare.claude_cli import ClaudeCLISearch
        return ClaudeCLISearch()
    return DDGSearch()
