"""Job source registry entry (Part 4)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import CollectionMethod


class SourceKind(StrEnum):
    JOB_BOARD = "job_board"
    ACADEMIC = "academic"
    EMPLOYER = "employer"
    ALERTS = "alerts"


class CollectorName(StrEnum):
    NONE = "none"                    # registered, not collected (yet)
    EMAIL_ALERT = "email_alert"      # saved alert emails (.eml) dropped in data/imports/alerts
    MANUAL = "manual"                # user-provided URL / text / file only
    PERSONIO = "personio"            # official public Personio XML feed
    SMARTRECRUITERS = "smartrecruiters"  # official public Posting API
    RSS = "rss"                      # public RSS/Atom feed
    JSONLD_PAGE = "jsonld_page"      # schema.org JobPosting on permitted pages
    JOBSPY = "jobspy"                # python-jobspy: LinkedIn/Indeed/Glassdoor (user decision 2026-11-15)
    PDF_LINKS = "pdf_links"          # one PDF per vacancy on a permitted page


AUTOMATIC_COLLECTORS = {CollectorName.PERSONIO, CollectorName.SMARTRECRUITERS, CollectorName.RSS,
                        CollectorName.JSONLD_PAGE, CollectorName.JOBSPY, CollectorName.PDF_LINKS}
# Pages on these domains are never fetched by our own client; automatic access only via JobSpy.
NEVER_AUTOMATIC_DOMAINS = ("linkedin.com",)


class SourceConfig(BaseModel):
    """One entry of config/sources.yaml."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    url: str
    kind: SourceKind
    collector: CollectorName = CollectorName.NONE
    params: dict[str, Any] = {}
    automatic_supported: bool = False
    access_restrictions: str = "not yet investigated"
    update_frequency: str | None = None
    active: bool = True
    notes: str = ""

    @model_validator(mode="after")
    def rules(self) -> SourceConfig:
        if (any(d in self.url for d in NEVER_AUTOMATIC_DOMAINS)
                and self.collector in AUTOMATIC_COLLECTORS and self.collector != CollectorName.JOBSPY):
            raise ValueError(f"{self.name}: LinkedIn may only use JobSpy, email alerts or manual import")
        if self.collector == CollectorName.JOBSPY and self.params.get("site") not in ("linkedin", "indeed", "glassdoor"):
            raise ValueError(f"{self.name}: jobspy needs params.site = linkedin | indeed | glassdoor")
        if self.collector in AUTOMATIC_COLLECTORS and not self.automatic_supported:
            raise ValueError(f"{self.name}: collector {self.collector} needs automatic_supported: true")
        if self.automatic_supported and self.collector not in AUTOMATIC_COLLECTORS:
            raise ValueError(f"{self.name}: automatic_supported but collector is {self.collector}")
        return self

    @property
    def collection_method(self) -> CollectionMethod:
        return {
            CollectorName.PERSONIO: CollectionMethod.API,
            CollectorName.SMARTRECRUITERS: CollectionMethod.API,
            CollectorName.RSS: CollectionMethod.RSS,
            CollectorName.JSONLD_PAGE: CollectionMethod.HTML,
            CollectorName.JOBSPY: CollectionMethod.HTML,
            CollectorName.PDF_LINKS: CollectionMethod.HTML,
            CollectorName.EMAIL_ALERT: CollectionMethod.EMAIL_ALERT,
        }.get(self.collector, CollectionMethod.MANUAL)


class SourceRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[SourceConfig]

    @model_validator(mode="after")
    def unique_names(self) -> SourceRegistry:
        names = [s.name for s in self.sources]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"Duplicate source names: {sorted(dupes)}")
        return self

    def get(self, name: str) -> SourceConfig:
        for s in self.sources:
            if s.name.lower() == name.lower():
                return s
        raise KeyError(name)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    collection_method: CollectionMethod
    access_restrictions: str = "not yet investigated"
    update_frequency: str | None = None
    automatic_supported: bool = False     # False until verified in Phase 2
    active: bool = True
    last_checked: datetime | None = None
    last_success: datetime | None = None
    error_status: str | None = None
