from __future__ import annotations

import json
from pathlib import Path

from fairfare.models import ClosureNotice, ReferencePrice

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_reference_prices(path: Path | None = None) -> list[ReferencePrice]:
    raw = json.loads((path or DATA_DIR / "reference_prices.json").read_text())
    return [ReferencePrice(**r) for r in raw]


def load_closures(path: Path | None = None) -> list[ClosureNotice]:
    raw = json.loads((path or DATA_DIR / "closures.json").read_text())
    return [ClosureNotice(**r) for r in raw]


def notices_for(destination: str, notices: list[ClosureNotice]) -> list[ClosureNotice]:
    """A closure notice for another destination says nothing about this trip."""
    return [n for n in notices if not n.destination or n.destination.lower() in destination.lower()]
