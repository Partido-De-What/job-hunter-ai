"""LinkedIn / Indeed / Glassdoor via python-jobspy (user decision 2026-11-15).

No login. One scrape_jobs call per query, with a pause between calls.
params:
  site:              linkedin | indeed | glassdoor
  location:          e.g. "Vienna, Austria" (required)
  queries:           list of search terms (default: core_queries de + en from search_terms.yaml)
  results_per_query: default 15
  hours_old:         only jobs posted in the last N hours (default 168)
  distance:          radius in miles (JobSpy unit), default 30
  country_indeed:    default "Austria" (Indeed / Glassdoor)
  fetch_description: LinkedIn full text, one extra request per job (default true)
  pause_seconds:     pause between queries (default 10)
"""

from __future__ import annotations

import math
import time
from datetime import date, datetime
from typing import Any

from ...models.common import SalaryPeriod
from ...models.job import Job, Salary
from ...models.source import SourceConfig
from ..base import CollectContext, CollectedJob, register


def _scrape(**kwargs):
    from jobspy import scrape_jobs  # imported lazily: heavy (pandas)
    return scrape_jobs(**kwargs)


scrape = _scrape        # replaced in tests
pause = time.sleep      # replaced in tests

_PERIOD = {"yearly": SalaryPeriod.ANNUAL, "monthly": SalaryPeriod.MONTHLY, "hourly": SalaryPeriod.HOURLY}
_TRUSTED_SALARY = {"direct_data", "description"}   # never estimates


def _v(row: dict, key: str) -> Any:
    """Value or None (pandas NaN / NaT / None / empty string -> None)."""
    val = row.get(key)
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    try:
        import pandas as pd
        if pd.isna(val):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    if isinstance(val, str) and not val.strip():
        return None
    return val


def _date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except ValueError:
        return None


def _salary(row: dict) -> Salary | None:
    source = _v(row, "salary_source")
    lo, hi = _v(row, "min_amount"), _v(row, "max_amount")
    period = _PERIOD.get(str(_v(row, "interval") or "").lower())
    if lo is None and hi is None:
        return None
    text = f"{lo}-{hi} {_v(row, 'currency') or ''} {_v(row, 'interval') or ''} (source: {source})".strip()
    if source not in _TRUSTED_SALARY or period is None:
        return Salary(source_text=text)                    # keep note, no numbers
    lo = float(lo) if lo is not None else float(hi)
    hi = float(hi) if hi is not None and float(hi) >= lo else None
    return Salary(amount_min=lo, amount_max=hi, period=period,
                  currency=_v(row, "currency") or "EUR", source_text=text)


def row_to_job(row: dict, *, src: SourceConfig, now: datetime) -> CollectedJob:
    url = _v(row, "job_url") or _v(row, "job_url_direct")
    return CollectedJob(
        job=Job(
            title=str(_v(row, "title") or "(untitled)"),
            employer=str(_v(row, "company") or "Unknown"),
            location=_v(row, "location"),
            url=url,
            source=src.name,
            published_at=_date(_v(row, "date_posted")),
            discovered_at=now, retrieved_at=now,
            salary=_salary(row),
            employment_type=_v(row, "job_type"),
            description=str(_v(row, "description") or ""),
        ),
        external_id=str(_v(row, "id") or url),
    )


def default_queries(search_terms: dict) -> list[str]:
    core = search_terms.get("core_queries", {})
    return list(dict.fromkeys([*core.get("de", []), *core.get("en", [])]))


@register("jobspy")
def collect(src: SourceConfig, ctx: CollectContext) -> list[CollectedJob]:
    if not ctx.settings.allow_jobspy:
        raise RuntimeError("JobSpy is switched off (collection.allow_jobspy: false in config/settings.yaml). "
                           "Set it to true to collect from LinkedIn/Indeed/Glassdoor again (see README).")
    p = src.params
    site = p["site"]
    queries = p.get("queries") or default_queries(ctx.search_terms)
    if not queries:
        raise RuntimeError("no queries: set params.queries or core_queries in search_terms.yaml")
    out: dict[str, CollectedJob] = {}
    errors: list[str] = []
    for i, query in enumerate(queries):
        if i:
            pause(float(p.get("pause_seconds", 10)))
        kwargs = dict(
            site_name=[site], search_term=query, location=p["location"],
            distance=int(p.get("distance", 30)), results_wanted=int(p.get("results_per_query", 15)),
            hours_old=int(p.get("hours_old", 168)), country_indeed=p.get("country_indeed", "Austria"),
            description_format="markdown", enforce_annual_salary=False, verbose=0,
        )
        if site == "linkedin":
            kwargs["linkedin_fetch_description"] = bool(p.get("fetch_description", True))
        elif p.get("fetch_description"):
            kwargs["fetch_description"] = True     # e.g. Glassdoor: otherwise no job text
        try:
            frame = scrape(**kwargs)
        except Exception as exc:  # noqa: BLE001 - keep going with the other queries
            errors.append(f"{query}: {type(exc).__name__}: {exc}")
            ctx.log.warning("%s: query '%s' failed: %s", src.name, query, exc)
            continue
        rows = frame.to_dict("records") if hasattr(frame, "to_dict") else list(frame)
        ctx.log.info("%s: query '%s' -> %d rows", src.name, query, len(rows))
        for row in rows:
            cj = row_to_job(row, src=src, now=ctx.now)
            out.setdefault(cj.external_id or cj.job.url or cj.job.content_hash, cj)
    if errors and len(errors) == len(queries):
        raise RuntimeError(f"all {len(queries)} queries failed; first: {errors[0]}")
    return list(out.values())
