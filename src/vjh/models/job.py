"""Job advertisements and advertised salary."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import JobStatus, Language, SalaryPeriod


class Salary(BaseModel):
    """Salary exactly as advertised. Nothing is inferred: in particular the number
    of payments per year stays None unless the ad (or applicable terms) states it."""

    model_config = ConfigDict(extra="forbid")

    amount_min: float | None = Field(default=None, ge=0)
    amount_max: float | None = Field(default=None, ge=0)
    period: SalaryPeriod | None = None
    currency: str = "EUR"
    payments_per_year: int | None = Field(default=None, ge=1, le=16)
    is_advertised_minimum: bool = False   # e.g. Austrian "KV-Mindestgehalt"
    negotiable: bool | None = None
    source_text: str = ""                 # quoted salary text from the ad

    @model_validator(mode="after")
    def range_is_ordered(self) -> Salary:
        if self.amount_min is not None and self.amount_max is not None:
            if self.amount_max < self.amount_min:
                raise ValueError("amount_max is smaller than amount_min")
        if (self.amount_min is not None or self.amount_max is not None) and self.period is None:
            raise ValueError("A salary amount needs a period (monthly/annual/hourly)")
        return self

    def annual_gross_min(self) -> float | None:
        """Annual minimum, only when it can be computed without assumptions."""
        if self.amount_min is None:
            return None
        if self.period == SalaryPeriod.ANNUAL:
            return self.amount_min
        if self.period == SalaryPeriod.MONTHLY and self.payments_per_year:
            return self.amount_min * self.payments_per_year
        return None  # unknown: never assume 14 payments


def content_hash(title: str, employer: str, description: str) -> str:
    """Stable hash of the job content, insensitive to whitespace and case."""
    norm = "\n".join(re.sub(r"\s+", " ", s).strip().lower() for s in (title, employer, description))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: int | None = None
    title: str = Field(min_length=1)
    employer: str = Field(min_length=1)
    location: str | None = None
    url: str | None = None
    source: str
    published_at: date | None = None
    discovered_at: datetime
    retrieved_at: datetime
    deadline: date | None = None
    salary: Salary | None = None
    employment_type: str | None = None
    description: str = ""
    language: Language | None = None
    status: JobStatus = JobStatus.NEW
    content_hash: str = ""

    @model_validator(mode="after")
    def fill_hash(self) -> Job:
        if not self.content_hash:
            self.content_hash = content_hash(self.title, self.employer, self.description)
        return self
