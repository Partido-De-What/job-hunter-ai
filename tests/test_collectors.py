"""Collectors against saved sample data (no internet)."""

import json
from datetime import datetime
from pathlib import Path

from fakeweb import FakeWeb

from vjh.models.settings import CollectionSettings
from vjh.models.source import SourceConfig
from vjh.sources.base import CollectContext, get_collector
from vjh.sources.collectors.email_alert import parse_alert
from vjh.sources.collectors.jsonld_page import clean_title
from vjh.sources.html_utils import extract_jobpostings, jobposting_to_job

NOW = datetime(2026, 11, 15, 8, 0)
LONG = "Wir suchen eine engagierte Person für unser Labor. " * 20

PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>111</id><office>Wien</office><name>Labortechniker:in Pflanzenzüchtung</name>
    <employmentType>permanent</employmentType><schedule>full-time</schedule>
    <createdAt>2026-11-10T09:00:00+00:00</createdAt>
    <jobDescriptions><jobDescription><name>Aufgaben</name>
      <value><![CDATA[<p>Feldversuche &amp; <b>Bonituren</b></p>]]></value></jobDescription></jobDescriptions>
  </position>
  <position>
    <id>222</id><office>Berlin</office><name>Sales Manager</name>
  </position>
</workzag-jobs>"""


def ctx(web: FakeWeb, tmp_path: Path, **kw) -> CollectContext:
    client, _ = web.client()
    return CollectContext(client=client, settings=CollectionSettings(max_pages_per_source=10),
                          now=NOW, data_dir=tmp_path, **kw)


def src(**kw) -> SourceConfig:
    base = dict(name="Test", url="https://ex.at/jobs", kind="employer")
    return SourceConfig(**{**base, **kw})


def test_personio_feed(tmp_path):
    web = FakeWeb({"https://acme.jobs.personio.de/xml?language=de": PERSONIO_XML})
    s = src(collector="personio", automatic_supported=True,
            params={"company": "acme", "employer": "Acme GmbH", "only_locations": ["wien"]})
    jobs = get_collector("personio")(s, ctx(web, tmp_path))
    assert len(jobs) == 1                                  # Berlin filtered out
    j = jobs[0].job
    assert j.title == "Labortechniker:in Pflanzenzüchtung" and j.employer == "Acme GmbH"
    assert j.url == "https://acme.jobs.personio.de/job/111" and jobs[0].external_id == "111"
    assert "Feldversuche & Bonituren" in j.description and "<b>" not in j.description
    assert j.published_at.isoformat() == "2026-11-10"


def test_smartrecruiters_api(tmp_path):
    api = "https://api.smartrecruiters.com/v1/companies/Acme"
    web = FakeWeb({
        f"{api}/postings?limit=100&country=at&offset=0": json.dumps({"totalFound": 1, "content": [
            {"id": "9", "name": "QC Analyst", "releasedDate": "2026-11-01T10:00:00.000Z",
             "location": {"city": "Wien", "country": "at"}, "company": {"name": "Acme"},
             "ref": f"{api}/postings/9"}]}),
        f"{api}/postings/9": json.dumps({"postingUrl": "https://jobs.smartrecruiters.com/Acme/9",
            "jobAd": {"sections": {"jobDescription": {"title": "Job", "text": "<p>HPLC</p>"}}}}),
    })
    s = src(collector="smartrecruiters", automatic_supported=True, params={"company": "Acme", "country": "at"})
    jobs = get_collector("smartrecruiters")(s, ctx(web, tmp_path))
    assert jobs[0].job.title == "QC Analyst" and "HPLC" in jobs[0].job.description
    assert jobs[0].job.url == "https://jobs.smartrecruiters.com/Acme/9"


def test_html_to_text_keeps_inline_text_together():
    from vjh.sources.html_utils import html_to_text
    out = html_to_text("<p>Wir <b>suchen</b> dich</p><ul><li>PCR</li><li>GUS</li></ul>line<br>two")
    assert "Wir suchen dich" in out and "- PCR" in out and "- GUS" in out and "line\ntwo" in out


def test_rss_feed(tmp_path):
    rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>Jobs</title>
      <item><title>Research Technician</title><link>https://ex.at/job/1</link>
      <description>&lt;p&gt;PCR work&lt;/p&gt;</description><pubDate>Mon, 10 Nov 2026 08:00:00 GMT</pubDate></item>
    </channel></rss>"""
    web = FakeWeb({"https://ex.at/feed": rss})
    s = src(collector="rss", automatic_supported=True, params={"feed_url": "https://ex.at/feed", "employer": "Ex"})
    jobs = get_collector("rss")(s, ctx(web, tmp_path))
    assert jobs[0].job.title == "Research Technician" and jobs[0].job.description == "PCR work"


def _jsonld_page(posting: dict) -> str:
    return f'<html><head><script type="application/ld+json">{json.dumps(posting)}</script></head><body><h1>x</h1></body></html>'


def test_jobposting_extraction_with_graph_and_monthly_salary():
    posting = {"@context": "https://schema.org", "@graph": [{"@type": "Organization", "name": "x"}, {
        "@type": "JobPosting", "title": "Pflanzenzüchter/in", "datePosted": "2026-11-02",
        "validThrough": "2026-12-01T23:59", "hiringOrganization": {"@type": "Organization", "name": "Saat AG"},
        "jobLocation": {"@type": "Place", "address": {"addressLocality": "Wien", "addressCountry": "AT"}},
        "baseSalary": {"@type": "MonetaryAmount", "currency": "EUR",
                       "value": {"@type": "QuantitativeValue", "minValue": 3100, "unitText": "MONTH"}},
        "description": "<p>Feldversuche</p>", "identifier": {"value": "ABC-1"}}]}
    jps = extract_jobpostings(_jsonld_page(posting))
    job, ext = jobposting_to_job(jps[0], page_url="https://ex.at/j/1", source="T", now=NOW)
    assert job.employer == "Saat AG" and job.location == "Wien, AT" and ext == "ABC-1"
    assert job.deadline.isoformat() == "2026-12-01"
    assert job.salary.amount_min == 3100 and job.salary.period == "monthly"
    assert job.salary.payments_per_year is None and job.salary.annual_gross_min() is None


def test_salary_without_unit_keeps_text_only():
    posting = {"@type": "JobPosting", "title": "T", "baseSalary": {"currency": "EUR", "value": 45000}}
    job, _ = jobposting_to_job(extract_jobpostings(_jsonld_page(posting))[0], page_url="u", source="T", now=NOW)
    assert job.salary.amount_min is None and "45000" in job.salary.source_text


def test_jsonld_page_follows_links_and_skips_known(tmp_path):
    listing = '<a href="/job/1">A</a><a href="/job/2">B</a><a href="/about">x</a>'
    short = {"@type": "JobPosting", "title": "Lab Technician", "description": "Short teaser."}
    web = FakeWeb({
        "https://ex.at/jobs": listing,
        "https://ex.at/job/2": _jsonld_page(short).replace("<h1>x</h1>", f"<article><p>{LONG}</p></article>"),
    })
    c = ctx(web, tmp_path, known_urls={"https://ex.at/job/1"})
    s = src(collector="jsonld_page", automatic_supported=True, params={"link_pattern": r"/job/\d+"})
    jobs = get_collector("jsonld_page")(s, c)
    assert "https://ex.at/job/1" not in web.requests          # known: not fetched again
    assert c.skipped_known == 1
    assert jobs[0].job.title == "Lab Technician"
    assert len(jobs[0].job.description) > 500                  # teaser replaced by page text


def test_text_fallback_and_title_cleanup(tmp_path):
    page = f"<html><head><title>Technical Assistant | Some Institute</title></head><body><article><h1>Technical Assistant | Some Institute</h1><p>{LONG}</p></article></body></html>"
    web = FakeWeb({"https://ex.at/jobs": '<a href="/open/l/7">x</a>', "https://ex.at/open/l/7": page})
    s = src(collector="jsonld_page", automatic_supported=True,
            params={"link_pattern": "/open/l/", "text_fallback": True, "employer": "Inst"})
    jobs = get_collector("jsonld_page")(s, ctx(web, tmp_path))
    assert jobs[0].job.title == "Technical Assistant" and jobs[0].job.employer == "Inst"
    assert clean_title("A | B | C") == "A" and clean_title("Plain") == "Plain"


EML = b"""From: LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>
To: someone@example.com
Subject: 3 new jobs for plant breeding
Date: Sat, 15 Nov 2026 07:00:00 +0100
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8

<html><body>
<a href="https://www.linkedin.com/comm/jobs/view/123">Plant Breeding Technician - Saat AG</a>
<a href="https://www.linkedin.com/comm/jobs/view/456">Research Assistant Molecular Biology</a>
<a href="https://www.linkedin.com/comm/jobs/view/456">Research Assistant Molecular Biology</a>
<a href="https://www.linkedin.com/comm/jobs/search">View all jobs</a>
<a href="https://www.linkedin.com/comm/unsubscribe?x=1">Unsubscribe from these alerts</a>
</body></html>
"""


def test_email_alert_extracts_jobs_without_opening_links():
    jobs = parse_alert(EML, fallback_source="Email alerts", now=NOW)
    assert [j.job.title for j in jobs] == ["Plant Breeding Technician - Saat AG",
                                           "Research Assistant Molecular Biology"]
    assert all(j.job.source == "LinkedIn Jobs" for j in jobs)
    assert jobs[0].job.published_at.isoformat() == "2026-11-15"


def test_email_alert_collector_moves_processed_files(tmp_path):
    alerts = tmp_path / "imports" / "alerts"
    alerts.mkdir(parents=True)
    (alerts / "a.eml").write_bytes(EML)
    web = FakeWeb()
    jobs = get_collector("email_alert")(src(name="Email alerts", url="file://x", kind="alerts",
                                            collector="email_alert"), ctx(web, tmp_path))
    assert len(jobs) == 2 and web.requests == []              # nothing fetched
    assert not (alerts / "a.eml").exists() and (alerts / "processed" / "a.eml").exists()
