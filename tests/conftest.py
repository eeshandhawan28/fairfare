"""Shared fixtures. The fake web and fake extractor live in fairfare.demo so the demo server reuses them."""
from __future__ import annotations

from datetime import date

import pytest

from fairfare.demo import CLAIMS, PAGES, SEARCH_TABLE, FakeExtractorLLM  # noqa: F401  (re-exported for tests)
from fairfare.models import Traveller, TripBrief
from fairfare.tools.fetch import FixtureFetcher
from fairfare.tools.search import FixtureSearch


@pytest.fixture
def web():
    return FixtureSearch(SEARCH_TABLE), FixtureFetcher(PAGES)


@pytest.fixture
def family() -> TripBrief:
    """Synthetic example family, not real people."""
    return TripBrief(
        destination="Kazakhstan", start=date(2026, 10, 19), end=date(2026, 10, 24), passport="Indian",
        travellers=[Traveller(name="Parent A", age=58), Traveller(name="Parent B", age=55),
                    Traveller(name="Adult", age=24), Traveller(name="Teen", age=17)],
        interests=["nature"], pace="balanced")
