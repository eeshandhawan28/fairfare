from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

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
    origin: str = ""
    passport: str = "Indian"
    budget_tier: Literal["budget", "comfort", "luxury"] = "comfort"
    pace: Literal["relaxed", "balanced", "packed"] = "balanced"
    interests: list[str] = Field(default_factory=list)
    must_do: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)

    @property
    def nights(self) -> int:
        return (self.end - self.start).days


# ---------- evidence ----------


class Evidence(BaseModel):
    """A verbatim quote from a fetched page. Claims without one are never kept."""

    url: str
    quote: str
    retrieved_at: str


class Claim(BaseModel):
    kind: str
    subject: str
    text: str
    evidence: Evidence
    data: dict[str, Any] = Field(default_factory=dict)


# ---------- quote audit ----------


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
    unit: Literal["total", "per_person"] = "total"
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

    def covers(self, day: date) -> bool:
        return self.closed_from <= day and (self.closed_to is None or day <= self.closed_to)

    def overlaps(self, start: date, end: date) -> bool:
        return self.closed_from <= end and (self.closed_to is None or self.closed_to >= start)


# ---------- planning ----------


class Place(BaseModel):
    name: str
    city: str = ""
    kind: str = "sight"  # sight, nature, food, market, culture, adventure, wellness
    duration_min: int = 90
    effort: int = 1  # 1 easy, 2 moderate, 3 strenuous
    altitude_m: Optional[int] = None
    min_age: Optional[int] = None
    hidden_gem: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    notes: str = ""

    @property
    def sources(self) -> list[str]:
        return [e.url for e in self.evidence]


BlockKind = Literal["activity", "meal", "rest", "transfer", "arrival", "departure"]


class Block(BaseModel):
    start: str  # HH:MM
    end: str
    title: str
    kind: BlockKind = "activity"
    place: Optional[str] = None
    notes: str = ""
    sources: list[str] = Field(default_factory=list)


class DayPlan(BaseModel):
    day: date
    role: Literal["arrival", "full", "departure"] = "full"
    blocks: list[Block] = Field(default_factory=list)
    plan_b: str = ""
    load: int = 0
    cap: int = 0


class TripPlan(BaseModel):
    brief: TripBrief
    days: list[DayPlan] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    visa: list[Claim] = Field(default_factory=list)
    transport: list[Claim] = Field(default_factory=list)
    avoid: list[Claim] = Field(default_factory=list)
    closures: list[ClosureNotice] = Field(default_factory=list)
    places: list[Place] = Field(default_factory=list)
    run_id: str = ""
