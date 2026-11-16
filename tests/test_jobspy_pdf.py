"""JobSpy collector (with a fake JobSpy) and the PDF-links collector."""

import math
from datetime import date, datetime

import pytest
from fakeweb import FakeWeb

from vjh.models.settings import CollectionSettings
from vjh.models.source import SourceConfig
from vjh.sources.base import CollectContext, get_collector
from vjh.sources.collectors import jobspy_boards, pdf_links

NOW = datetime(2026, 11, 15, 8, 0)
NAN = math.nan


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        assert orient == "records"
        return self.rows


ROW = {"id": "li-1", "site": "linkedin", "job_url": "https://www.linkedin.com/jobs/view/1",
       "title": "Plant Breeding Technician", "company": "Saat AG", "location": "Wien, Austria",
       "date_posted": date(2026, 11, 12), "job_type": "fulltime", "description": "Field trials.",
       "min_amount": 3000.0, "max_amount": 3500.0, "interval": "monthly", "currency": "EUR",
       "salary_source": "direct_data"}


def _ctx(tmp_path, terms=None):
    client, _ = FakeWeb().client()
    return CollectContext(client=client, settings=CollectionSettings(max_pages_per_source=10, allow_jobspy=True), now=NOW,
                          data_dir=tmp_path, search_terms=terms or {})


def _src(**params):
    return SourceConfig(name="LinkedIn Jobs", url="https://www.linkedin.com/jobs", kind="job_board",
                        collector="jobspy", automatic_supported=True, params={"site": "linkedin", "location": "Testtown", **params})


@pytest.fixture
def fake_jobspy(monkeypatch):
    calls, pauses = [], []

    def scrape(**kw):
        calls.append(kw)
        if kw["search_term"] == "broken":
            raise RuntimeError("429 rate limited")
        return FakeFrame([dict(ROW), dict(ROW)])          # same job twice -> deduplicated

    monkeypatch.setattr(jobspy_boards, "scrape", scrape)
    monkeypatch.setattr(jobspy_boards, "pause", pauses.append)
    return calls, pauses


def test_jobspy_maps_rows_and_dedupes(tmp_path, fake_jobspy):
    calls, pauses = fake_jobspy
    jobs = get_collector("jobspy")(_src(queries=["a", "b"], pause_seconds=7), _ctx(tmp_path))
    assert len(jobs) == 1 and len(calls) == 2 and pauses == [7.0]
    j = jobs[0].job
    assert (j.title, j.employer, j.location) == ("Plant Breeding Technician", "Saat AG", "Wien, Austria")
    assert j.published_at == date(2026, 11, 12) and jobs[0].external_id == "li-1"
    assert j.salary.period == "monthly" and j.salary.payments_per_year is None
    assert calls[0]["enforce_annual_salary"] is False and calls[0]["linkedin_fetch_description"] is True


def test_jobspy_uses_core_queries_by_default(tmp_path, fake_jobspy):
    calls, _ = fake_jobspy
    terms = {"core_queries": {"de": ["Labortechniker"], "en": ["plant breeding", "Labortechniker"]}}
    get_collector("jobspy")(_src(), _ctx(tmp_path, terms))
    assert [c["search_term"] for c in calls] == ["Labortechniker", "plant breeding"]


def test_glassdoor_fetch_description_is_passed(tmp_path, fake_jobspy):
    calls, _ = fake_jobspy
    src = SourceConfig(name="G", url="https://www.glassdoor.at", kind="job_board", collector="jobspy",
                       automatic_supported=True, params={"site": "glassdoor", "location": "Testtown", "queries": ["x"],
                                                         "fetch_description": True})
    get_collector("jobspy")(src, _ctx(tmp_path))
    assert calls[0]["fetch_description"] is True and "linkedin_fetch_description" not in calls[0]


def test_jobspy_ignores_estimated_salaries_and_nan(tmp_path):
    row = {**ROW, "salary_source": "glassdoor estimate", "company": NAN, "date_posted": NAN}
    cj = jobspy_boards.row_to_job(row, src=_src(), now=NOW)
    assert cj.job.salary.amount_min is None and "estimate" in cj.job.salary.source_text
    assert cj.job.employer == "Unknown" and cj.job.published_at is None


def test_jobspy_one_failed_query_is_tolerated_all_failed_raises(tmp_path, fake_jobspy):
    jobs = get_collector("jobspy")(_src(queries=["broken", "ok"]), _ctx(tmp_path))
    assert len(jobs) == 1
    with pytest.raises(RuntimeError, match="all 1 queries failed"):
        get_collector("jobspy")(_src(queries=["broken"]), _ctx(tmp_path))


def test_pdf_links_collector(tmp_path, monkeypatch):
    listing = ('<a href="/fileadmin/jobs/2026/RN1.pdf">Research Technician (40 h/week)</a>'
               '<a href="/fileadmin/jobs/2026/RN2.pdf">Gardener</a><a href="/about">About</a>')
    web = FakeWeb({"https://uni.at/jobs": listing,
                   "https://uni.at/fileadmin/jobs/2026/RN2.pdf": b"%PDF-fake"})
    monkeypatch.setattr(pdf_links, "pdf_text", lambda data: "Full notice text")
    client, _ = web.client()
    ctx = CollectContext(client=client, settings=CollectionSettings(), now=NOW, data_dir=tmp_path,
                         known_urls={"https://uni.at/fileadmin/jobs/2026/RN1.pdf"})
    src = SourceConfig(name="Uni", url="https://uni.at/jobs", kind="academic", collector="pdf_links",
                       automatic_supported=True, params={"link_pattern": r"jobs/.+\.pdf$", "employer": "Uni"})
    jobs = get_collector("pdf_links")(src, ctx)
    assert [j.job.title for j in jobs] == ["Gardener"] and jobs[0].job.description == "Full notice text"
    assert ctx.skipped_known == 1
    assert "https://uni.at/fileadmin/jobs/2026/RN1.pdf" not in web.requests


def test_jobspy_is_off_unless_allowed(tmp_path, fake_jobspy):
    calls, _ = fake_jobspy
    client, _ = FakeWeb().client()
    ctx = CollectContext(client=client, settings=CollectionSettings(allow_jobspy=False), now=NOW, data_dir=tmp_path)
    with pytest.raises(RuntimeError, match="switched off"):
        get_collector("jobspy")(_src(queries=["a"]), ctx)
    assert calls == []
