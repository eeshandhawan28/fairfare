"""Claude Code CLI backend: LLM, web search and a fetch fallback, for machines with no Ollama.

Enable with FAIRFARE_MODEL=claude-cli/haiku (or claude-cli/sonnet) and FAIRFARE_SEARCH=claude-cli.

Design-rule caveat: where direct page fetches are blocked (WebFetch goes through the same proxy),
the only evidence is the WebSearch digest, which is model-written. "Verbatim quote verified
against the page" is then weaker: quotes are checked against the digest, not the source page.
Such evidence is tagged method="search_digest" in traces and only used when direct HTTP fails.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

from fairfare import tracing
from fairfare.tools.search import SearchResult

CLI_TIMEOUT = int(os.getenv("FAIRFARE_CLI_TIMEOUT", "180"))


def run_claude(prompt: str, model: str = "haiku", system: str | None = None, tools: str = "",
               timeout: int = CLI_TIMEOUT) -> str:
    cmd = ["claude", "-p", "--model", model, "--output-format", "text", "--no-session-persistence",
           "--disable-slash-commands", "--setting-sources", ""]
    if tools:
        cmd += ["--allowed-tools", tools, "--permission-mode", "bypassPermissions"]
    else:
        cmd += ["--tools", ""]
    if system:
        cmd += ["--system-prompt", system]
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd="/tmp")
    if p.returncode != 0:
        raise RuntimeError(f"claude CLI failed ({p.returncode}): {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout.strip()


def _json_from(text: str):
    from fairfare.llm import extract_json
    return extract_json(text)


# url -> text gathered by WebSearch. Direct page fetches are blocked in some sandboxes, so the
# search digest is the only evidence available there. Traces tag it method="search_digest".
DIGESTS: dict[str, list[str]] = {}


class ClaudeCLISearch:
    digest_mode = True  # Researcher extracts from the digest itself, once per query

    def __init__(self, model: str = "haiku") -> None:
        self.model = model
        self.last_digest = ""

    def search(self, query: str, n: int = 5) -> list[SearchResult]:
        with tracing.span("search", kind="tool", provider="claude-cli", query=query) as rec:
            out = run_claude(
                f"Use WebSearch for: {query}\n"
                f'Return ONLY JSON: {{"results":[{{"title":"...","url":"..."}}] (up to {n}, real result URLs), '
                '"digest":"plain sentences stating the concrete facts the sources give (place names, '
                'durations, opening dates, closures with dates, prices, visa rules, transport, scams), '
                'each sentence self-contained, naming the place and the source site. No opinions of your own."}}',
                model=self.model, tools="WebSearch")
            try:
                data = _json_from(out) or {}
            except Exception:
                data = {}
            hits = data.get("results", []) if isinstance(data, dict) else []
            digest = (data.get("digest") or "") if isinstance(data, dict) else ""
            res = [SearchResult(title=h.get("title", ""), url=h["url"], snippet=digest[:200])
                   for h in hits if isinstance(h, dict) and str(h.get("url", "")).startswith("http")][:n]
            self.last_digest = digest
            for r in res:
                DIGESTS.setdefault(r.url, [])
                if digest and digest not in DIGESTS[r.url]:
                    DIGESTS[r.url].append(digest)
            rec["results"] = len(res)
            rec["digest_chars"] = len(digest)
            return res


class SearchDigestFetcher:
    """Serves the WebSearch digest gathered for a URL as its 'page' (weaker evidence; labelled)."""

    def fetch(self, url: str):
        from fairfare.tools.fetch import Page
        with tracing.span("fetch", kind="tool", url=url, method="search_digest") as rec:
            text = "\n".join(DIGESTS.get(url, []))
            rec["chars"] = len(text)
            if not text:
                raise RuntimeError(f"no search digest for {url}")
            return Page(url=url, text=text, method="search_digest",
                        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


class FallbackFetcher:
    """Direct HTTP first (strongest evidence); model-mediated fetch only if that fails."""

    def __init__(self, primary, fallback) -> None:
        self.primary, self.fallback = primary, fallback

    def fetch(self, url: str):
        try:
            return self.primary.fetch(url)
        except Exception as exc:
            tracing.event("fetch_fallback", url=url, error=str(exc)[:200])
            return self.fallback.fetch(url)
