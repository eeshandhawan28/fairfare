"""Page fetching: plain HTTP first, headless browser for JavaScript-rendered sites, sqlite cache."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

from pydantic import BaseModel

from fairfare import tracing

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
UA = "Mozilla/5.0 (compatible; fairfare/0.1; personal travel research)"
MIN_USEFUL_CHARS = 300


class Page(BaseModel):
    url: str
    text: str
    retrieved_at: str
    method: str = "http"


class Fetcher(Protocol):
    def fetch(self, url: str) -> Page: ...


class UnsafeURL(ValueError):
    pass


def check_public_url(url: str) -> None:
    """Refuse non-http(s) schemes and hosts that resolve to private, loopback or link-local addresses."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise UnsafeURL(f"only http(s) URLs are fetched: {url}")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise UnsafeURL(f"cannot resolve {u.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise UnsafeURL(f"{u.hostname} resolves to a non-public address")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def html_to_text(html: str) -> str:
    try:
        import trafilatura

        extracted = trafilatura.extract(html, include_comments=False, include_tables=True)
        if extracted:
            return extracted
    except Exception:
        pass
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


class HttpFetcher:
    def __init__(self, browser: bool | None = None, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self.browser = os.getenv("FAIRFARE_BROWSER", "1") != "0" if browser is None else browser

    def _http(self, url: str) -> str:
        import httpx
        from urllib.parse import urljoin

        current = url
        with httpx.Client(headers={"User-Agent": UA}, timeout=self.timeout, follow_redirects=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                check_public_url(current)  # every hop, so a redirect cannot reach an internal address
                with client.stream("GET", current) as r:
                    if r.is_redirect and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    r.raise_for_status()
                    body = b""
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            break
                    return html_to_text(body[:MAX_BYTES].decode(r.encoding or "utf-8", errors="replace"))
        raise RuntimeError("too many redirects")

    def _browser(self, url: str) -> str:
        from playwright.sync_api import sync_playwright  # optional dependency

        check_public_url(url)
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            try:
                page = b.new_page(user_agent=UA)

                def guard(route):  # the browser follows redirects and sub-requests itself: vet each one
                    try:
                        check_public_url(route.request.url)
                        route.continue_()
                    except UnsafeURL:
                        route.abort()

                page.route("**/*", guard)
                page.goto(url, wait_until="networkidle", timeout=int(self.timeout * 1000))
                return re.sub(r"\s+", " ", page.inner_text("body")).strip()
            finally:
                b.close()

    def fetch(self, url: str) -> Page:
        with tracing.span("fetch", kind="tool", url=url) as rec:
            text, method = "", "http"
            try:
                text = self._http(url)
            except Exception as exc:
                rec["http_error"] = f"{type(exc).__name__}: {exc}"
            if len(text) < MIN_USEFUL_CHARS and self.browser:
                try:
                    text, method = self._browser(url), "browser"
                except Exception as exc:
                    rec["browser_error"] = f"{type(exc).__name__}: {exc}"
            rec["chars"] = len(text)
            rec["method"] = method
            if not text:
                cause = rec.get("browser_error") or rec.get("http_error") or "page had no readable text"
                raise RuntimeError(f"could not fetch readable text from {url}: {cause}")
            return Page(url=url, text=text, retrieved_at=_now(), method=method)


class CachedFetcher:
    """Facts are dated. Cache pages with their retrieval time and expire them."""

    def __init__(self, inner: Fetcher, path: Optional[Path] = None, ttl_hours: float = 24.0) -> None:
        self.inner = inner
        self.ttl = ttl_hours * 3600
        self.path = path or Path(os.getenv("FAIRFARE_CACHE", "cache/pages.sqlite"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS pages (key TEXT PRIMARY KEY, url TEXT, text TEXT, "
                       "retrieved_at TEXT, method TEXT, ts REAL)")

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def fetch(self, url: str) -> Page:
        key = hashlib.sha1(url.encode()).hexdigest()
        with self._db() as db:
            row = db.execute("SELECT url, text, retrieved_at, method, ts FROM pages WHERE key=?", (key,)).fetchone()
        if row and time.time() - row[4] < self.ttl:
            tracing.event("cache_hit", url=url)
            return Page(url=row[0], text=row[1], retrieved_at=row[2], method=row[3])
        page = self.inner.fetch(url)
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?)",
                       (key, page.url, page.text, page.retrieved_at, page.method, time.time()))
        return page


class FixtureFetcher:
    """url -> text, for tests. Unknown URLs raise like a failed fetch."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    def fetch(self, url: str) -> Page:
        with tracing.span("fetch", kind="tool", url=url, method="fixture") as rec:
            if url not in self.pages:
                raise RuntimeError(f"fixture has no page for {url}")
            rec["chars"] = len(self.pages[url])
            return Page(url=url, text=self.pages[url], retrieved_at="2026-09-30T00:00:00+00:00", method="fixture")


def default_fetcher() -> "Fetcher":
    """Cached HTTP fetcher; with FAIRFARE_SEARCH=claude-cli, falls back to Claude WebFetch when HTTP is blocked."""
    inner: Fetcher = HttpFetcher()
    if os.getenv("FAIRFARE_SEARCH", "").lower() in ("claude-cli", "claude"):
        from fairfare.claude_cli import FallbackFetcher, SearchDigestFetcher
        inner = FallbackFetcher(inner, SearchDigestFetcher())
    return CachedFetcher(inner)
