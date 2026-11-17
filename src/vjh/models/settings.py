"""Schema for config/settings.yaml. Contains no secrets."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evaluation import DIMENSIONS


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CategoryBand(_Strict):
    name: str
    min_score: float = Field(ge=0, le=100)


class ScoringSettings(_Strict):
    weights: dict[str, float]
    categories: list[CategoryBand]

    @model_validator(mode="after")
    def check(self) -> ScoringSettings:
        if set(self.weights) != set(DIMENSIONS):
            raise ValueError(f"weights must have exactly these keys: {DIMENSIONS}")
        total = sum(self.weights.values())
        if abs(total - 100) > 1e-9:
            raise ValueError(f"weights must sum to 100, got {total}")
        mins = [c.min_score for c in self.categories]
        if mins != sorted(mins, reverse=True) or mins[-1] != 0:
            raise ValueError("categories must be ordered high→low and end at 0")
        return self

    def category_for(self, score: float) -> str:
        for band in self.categories:
            if score >= band.min_score:
                return band.name
        return self.categories[-1].name


class Thresholds(_Strict):
    recommend_min_score: float = Field(ge=0, le=100)
    generate_documents_min_score: float = Field(ge=0, le=100)
    max_job_age_days: int = Field(ge=1)


class Preferences(_Strict):
    primary_locations: list[str]
    secondary_locations: list[str]
    min_gross_annual_salary: float = Field(ge=0)       # 0 = no salary filter
    salary_currency: str = "EUR"
    max_commute_minutes_one_way: int = Field(ge=1)
    document_languages: list[str]


class CVConventions(_Strict):
    """What the CV header shows. Austria/Germany: photo and birth date are usual;
    UK/US/Ireland/Canada: usually leave both out."""

    photo: bool = True
    date_of_birth: bool = True
    nationality: bool = True
    work_permit: bool = True
    driving_licence: bool = True


class CandidateSettings(_Strict):
    """Who and where the search is for. Everything region- or field-specific lives here."""

    country: str
    career_focus: str                                   # e.g. "life sciences: plant science, ..."
    home_city_names: dict[str, str] = {}                # document language -> city name, e.g. {de: Wien}
    local_place_patterns: list[str] = []                # regexes: location is inside the target area
    foreign_place_patterns: list[str] = []              # regexes: clearly outside -> filtered out
    cv_strategies: dict[str, str]                       # letter -> what that CV version emphasises
    cv_conventions: CVConventions = CVConventions()
    language_aliases: dict[str, list[str]] = {}         # language -> words that name it (any language)

    @model_validator(mode="after")
    def strategies_are_letters(self) -> CandidateSettings:
        bad = [k for k in self.cv_strategies if not (len(k) == 1 and k.isupper())]
        if bad or not self.cv_strategies:
            raise ValueError("cv_strategies needs at least one entry, keyed by single capital letters (A, B, ...)")
        return self


class ModelPrice(_Strict):
    """USD per 1M tokens. Cache writes are billed at 1.25x input (5-minute cache)."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float = Field(ge=0)


class LLMSettings(_Strict):
    # "claude_code": your Claude subscription via the Claude Code CLI (no API key, no per-call bill)
    # "api": the Anthropic API with ANTHROPIC_API_KEY (billed per call)
    backend: Literal["claude_code", "api"] = "claude_code"
    claude_cli: str = "claude"
    cli_timeout_seconds: int = Field(default=600, ge=30)
    subscription_daily_max_jobs: int = Field(default=40, ge=1)
    screening_model: str                 # used for job evaluation (Phase 3)
    strong_model: str                    # used for document writing (Phase 4)
    daily_cost_cap_usd: float = Field(ge=0)
    max_retries: int = Field(ge=0)
    effort: str = "medium"               # low | medium | high | xhigh | max
    max_output_tokens: int = Field(default=16000, ge=1024)
    refusal_fallback: bool = True        # server-side fallback on policy refusals
    max_failures_per_job: int = Field(default=2, ge=1)
    letter_revision: bool = True         # second AI pass that edits cover letters for natural wording
    prices: dict[str, ModelPrice] = {}

    def price_for(self, model: str) -> ModelPrice | None:
        return self.prices.get(model)


class DatabaseSettings(_Strict):
    path: str


class PathSettings(_Strict):
    data_dir: str
    personal_dir: str
    cv_dir: str


class CollectionSettings(_Strict):
    # Generic, non-identifying client name (no personal contact details).
    user_agent: str = "JobHunter-personal-job-search/1.0 (non-commercial; respects robots.txt)"
    accept_language: str = "en;q=0.9"         # languages you prefer pages in, e.g. "de-AT,de;q=0.9,en;q=0.8"
    # JobSpy scrapes LinkedIn/Indeed/Glassdoor against their terms of use. On by default; set false to stop.
    allow_jobspy: bool = True
    min_delay_seconds: float = Field(default=3.0, ge=1.0)
    timeout_seconds: float = Field(default=20.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_pages_per_source: int = Field(default=30, ge=1)


class AutomationSettings(_Strict):
    daily_time: str = Field(default="07:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    task_name: str = "Job Hunter daily"
    auto_documents: bool = True               # CV + letter for very good new matches
    auto_documents_max_per_day: int = Field(default=2, ge=0, le=10)
    email_enabled: bool = True                # needs SMTP_* and NOTIFY_TO in .env
    email_only_when_news: bool = True         # skip the email on quiet days
    report_days_back: int = Field(default=1, ge=1, le=14)
    log_retention_days: int = Field(default=90, ge=7)    # run logs older than this are deleted
    db_backups_keep: int = Field(default=10, ge=1)       # newest database backups kept


class DashboardSettings(_Strict):
    title: str = "Job Hunter AI"      # browser tab, sidebar and Desktop icon name
    port: int = Field(default=8501, ge=1024, le=65535)   # each copy on this computer needs its own port


class ProgressSettings(_Strict):
    weekly_application_goal: int = Field(default=5, ge=0, le=100)   # dashboard Progress page; 0 = no goal


class Settings(_Strict):
    candidate: CandidateSettings
    scoring: ScoringSettings
    thresholds: Thresholds
    preferences: Preferences
    llm: LLMSettings
    database: DatabaseSettings
    paths: PathSettings
    collection: CollectionSettings = CollectionSettings()
    automation: AutomationSettings = AutomationSettings()
    progress: ProgressSettings = ProgressSettings()
    dashboard: DashboardSettings = DashboardSettings()
