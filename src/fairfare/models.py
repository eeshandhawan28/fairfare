from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Traveller(BaseModel):
    name: str
    age: int
    mobility: Literal["full", "limited"] = "full"


class TripBrief(BaseModel):
    destination: str
    start: date
    end: date
    travellers: list[Traveller] = Field(default_factory=list)

    @property
    def nights(self) -> int:
        return (self.end - self.start).days


class QuoteLine(BaseModel):
    item: str
    category: Literal["flight", "stay", "transfer", "activity", "other"] = "other"
    amount: Optional[float] = None  # None when the quote gives no usable number
    currency: str = "INR"
    note: str = ""

    @field_validator("amount", mode="before")
    @classmethod
    def _numeric_or_none(cls, v):
        """An unreadable amount must not delete the line: closure checks still need its name."""
        if v is None or isinstance(v, bool):
            return None
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            return None


class Finding(BaseModel):
    severity: Literal["info", "warn", "high"]
    kind: Literal["price", "closure"]
    line: str
    message: str
    source: Optional[str] = None
    confidence: Literal["low", "medium", "high"] = "medium"


class ReferencePrice(BaseModel):
    """An independently sourced price band for one kind of line item."""

    category: str
    keywords: list[str]
    low: float
    high: float
    currency: str = "INR"
    source: str
    as_of: str


class ClosureNotice(BaseModel):
    """A dated closure or maintenance notice for a venue."""

    venue: str
    keywords: list[str]
    closed_from: date
    closed_to: Optional[date] = None  # None = reopening date not stated
    reason: str = ""
    source: str
    confidence: Literal["low", "medium", "high"] = "medium"
