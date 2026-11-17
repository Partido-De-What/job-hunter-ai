"""Configure the search from the profile and a few answers: one AI call drafts the field-specific
settings (search terms, location filter, CV strategies), Python checks them and writes the files.
The user reviews the result in the wizard and can change it later on the Settings page.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..llm.client import LLM
from ..llm.prompts import render_profile
from ..models.profile import MasterProfile
from ..models.settings import Settings
from .files import update_yaml

_S = {"type": "string"}
_LIST = {"type": "array", "items": _S}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SEARCH_SCHEMA = _obj({
    "career_focus": _S,
    "cv_strategies": {"type": "array", "items": _obj({"letter": _S, "description": _S})},
    "experience_strategies": {"type": "array", "items": _obj({"experience_id": _S, "letters": _LIST})},
    "home_city_names": _obj({"en": _S, "de": _S, "it": _S}),
    "local_place_patterns": _LIST,
    "foreign_place_patterns": _LIST,
    "accept_language": _S,
    "language_aliases": {"type": "array", "items": _obj({"language": _S, "words": _LIST})},
    "term_lists": {"type": "array", "items": _obj({"label": _S, "terms": _LIST})},
    "core_queries": {"type": "array", "items": _obj({"lang": _S, "queries": _LIST})},
    "title_patterns": _LIST,
    "topic_patterns": _LIST,
    "exclude_title_patterns": _LIST,
    "cv_conventions": _obj({k: {"type": "boolean"} for k in
                            ("photo", "date_of_birth", "nationality", "work_permit", "driving_licence")}),
    "jobspy_location": _S,
    "indeed_country": _S,
})

SEARCH_INSTRUCTIONS = """\
You configure a personal job-search tool for the candidate below. Use the profile and the
candidate's answers; do not invent experience. Output:
- career_focus: the candidate's field in one line.
- cv_strategies: 2-4 CV emphases (letters A, B, C, D), each one line naming the experience and
  skills it highlights. experience_strategies: for each experience_id, the letters it serves.
- home_city_names: the home city as written in English, German and Italian.
- local_place_patterns / foreign_place_patterns: lowercase regular expressions (Python) that
  match job locations inside the target area (country, regions, the main cities, the country's
  short codes such as '\\bat\\b') and in neighbouring or common foreign countries. Plain words
  are fine; use \\b for short codes.
- accept_language: an HTTP Accept-Language value for the job market's languages.
- language_aliases: for every language in the profile, the words that name it in English,
  German, Italian, French, Spanish and the job market's language(s), lowercase, word stems ok.
- term_lists: search words for job titles and skills of this field, one list per job-market
  language (label = the language name in lowercase, e.g. "german", "english").
- core_queries: 4-6 short job-board searches per job-market language (lang = 2-letter code).
- title_patterns: 15-30 lowercase regex stems that appear in relevant job TITLES (both languages).
- topic_patterns: 15-30 lowercase regex stems of methods/skills that appear in relevant ad TEXT.
- exclude_title_patterns: titles needing a degree or status the candidate lacks (e.g. postdoc
  without a PhD, professor, apprenticeship, medical doctor), as lowercase regexes.
- cv_conventions: what CVs in the target country usually show (photo and date of birth are usual
  in German-speaking countries, not in the UK, Ireland, USA or Canada).
- jobspy_location: "<main city>, <country>" in English; indeed_country: the country in English.
"""


@dataclass
class SearchAnswers:
    country: str
    primary_locations: list[str]
    secondary_locations: list[str] = field(default_factory=list)
    wanted_jobs: str = ""                 # in the user's words: what kind of job they want
    min_salary: float = 0
    currency: str = "EUR"
    document_languages: list[str] = field(default_factory=lambda: ["en"])
    keep_example_sources: bool = False


def _user_message(a: SearchAnswers) -> str:
    return (f"CANDIDATE ANSWERS\n- country to work in: {a.country}\n"
            f"- cities / region: {', '.join(a.primary_locations)}"
            + (f" (also: {', '.join(a.secondary_locations)})" if a.secondary_locations else "")
            + f"\n- jobs wanted: {a.wanted_jobs or '(not stated: infer from the profile)'}\n"
            f"- document languages: {', '.join(a.document_languages)}")


def draft_search(llm: LLM, profile: MasterProfile, settings: Settings, answers: SearchAnswers) -> dict:
    res = llm.complete_json(system=SEARCH_INSTRUCTIONS + "\n" + render_profile(profile, settings),
                            user=_user_message(answers), schema=SEARCH_SCHEMA, model=settings.llm.strong_model)
    if res.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"the search setup stopped: {res.stop_reason}")
    return json.loads(res.text)


def _valid_patterns(items: list[str]) -> list[str]:
    out = []
    for p in items or []:
        p = (p or "").strip().lower()
        if not p or p in out:
            continue
        try:
            re.compile(p)
        except re.error:
            continue                      # a broken pattern is dropped, never written
        out.append(p)
    return out


def search_terms_doc(d: dict) -> dict:
    doc: dict = {}
    for tl in d.get("term_lists") or []:
        label = re.sub(r"[^a-z_]", "", (tl.get("label") or "").lower()) or "terms"
        terms = [t.strip() for t in tl.get("terms") or [] if t.strip()]
        if terms:
            doc[label] = terms
    doc["relevance"] = {"min_topic_matches": 3,
                        "exclude_title_patterns": _valid_patterns(d.get("exclude_title_patterns")),
                        "title_patterns": _valid_patterns(d.get("title_patterns")),
                        "topic_patterns": _valid_patterns(d.get("topic_patterns"))}
    doc["core_queries"] = {q["lang"].lower()[:2]: [x for x in q.get("queries") or [] if x.strip()]
                           for q in d.get("core_queries") or [] if q.get("lang")}
    if not doc["relevance"]["title_patterns"]:
        raise ValueError("the AI returned no usable title patterns - try again")
    return doc


SEARCH_TERMS_HEADER = """\
# Search terms for your field, drafted by the setup wizard from your profile.
# Change them on the dashboard's Settings page (Search tab) or here.
#
# - core_queries: short searches used by query-based sources (JobSpy)
# - relevance: the free Stage 1 filter. A job passes if its TITLE matches a title pattern or its
#   TEXT contains at least `min_topic_matches` topic patterns. exclude_title_patterns sets aside
#   titles that need a degree or status you do not have.

"""


def apply_search(root: Path, profile_path: Path, d: dict, a: SearchAnswers) -> list[str]:
    """Write settings.yaml, search_terms.yaml, sources.yaml and the profile's CV strategies.
    Returns short notes for the user."""
    from ..models.settings import Settings
    notes: list[str] = []
    strategies = {s["letter"].strip().upper()[:1]: s["description"].strip()
                  for s in d.get("cv_strategies") or [] if s.get("letter") and s.get("description")}
    aliases = {x["language"].strip(): [w.lower() for w in x.get("words") or [] if w.strip()]
               for x in d.get("language_aliases") or [] if x.get("language")}
    city = a.primary_locations[0] if a.primary_locations else ""
    names = {k: (v or city) for k, v in (d.get("home_city_names") or {}).items()}
    update_yaml(root / "config" / "settings.yaml", {
        "candidate.country": a.country,
        "candidate.career_focus": d.get("career_focus") or a.wanted_jobs,
        "candidate.home_city_names": names or {"en": city, "de": city, "it": city},
        "candidate.local_place_patterns": _valid_patterns(d.get("local_place_patterns")),
        "candidate.foreign_place_patterns": _valid_patterns(d.get("foreign_place_patterns")),
        "candidate.cv_strategies": strategies or {"A": d.get("career_focus") or a.wanted_jobs},
        "candidate.cv_conventions": d.get("cv_conventions") or {},
        "candidate.language_aliases": aliases,
        "preferences.primary_locations": a.primary_locations,
        "preferences.secondary_locations": a.secondary_locations,
        "preferences.min_gross_annual_salary": float(a.min_salary or 0),
        "preferences.salary_currency": a.currency,
        "preferences.document_languages": a.document_languages,
        "collection.accept_language": d.get("accept_language") or "en;q=0.9",
    }, validate=Settings.model_validate)
    notes.append("settings saved (country, locations, CV strategies, location filter)")

    terms = search_terms_doc(d)
    (root / "config" / "search_terms.yaml").write_text(
        SEARCH_TERMS_HEADER + yaml.safe_dump(terms, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
    notes.append(f"search terms written ({len(terms['relevance']['title_patterns'])} title patterns)")

    notes += _update_sources(root / "config" / "sources.yaml", d, a)

    if profile_path.exists():
        prof = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        by_id = {x["experience_id"]: [s.upper()[:1] for s in x.get("letters") or [] if s.strip().upper()[:1] in strategies]
                 for x in d.get("experience_strategies") or []}
        for x in prof.get("experiences") or []:
            if x["id"] in by_id:
                x["priority_profiles"] = by_id[x["id"]]
        MasterProfile.model_validate(prof)
        text = profile_path.read_text(encoding="utf-8")
        head = "".join(line + "\n" for line in text.splitlines() if line.startswith("#"))
        profile_path.write_text(head + "\n" + yaml.safe_dump(prof, allow_unicode=True, sort_keys=False, width=110),
                                encoding="utf-8")
    return notes


def _update_sources(path: Path, d: dict, a: SearchAnswers) -> list[str]:
    from ..models.source import SourceRegistry
    from .files import _ryaml
    y = _ryaml()
    doc = y.load(path.read_text(encoding="utf-8"))
    notes = []
    loc = d.get("jobspy_location") or ", ".join(x for x in (a.primary_locations[:1] + [a.country]) if x)
    city = a.primary_locations[0] if a.primary_locations else loc
    for s in doc.get("sources") or []:
        params = s.get("params") or {}
        if s.get("collector") == "jobspy":
            if params.get("site") == "indeed":
                params["location"] = city
                params["country_indeed"] = d.get("indeed_country") or a.country
            else:
                params["location"] = loc
        elif str(s.get("access_restrictions", "")).startswith("EXAMPLE") and not a.keep_example_sources:
            s["active"] = False                    # example sources belong to the example region
    import io
    buf = io.StringIO()
    y.dump(doc, buf)
    SourceRegistry.model_validate(yaml.safe_load(buf.getvalue()))
    path.write_text(buf.getvalue(), encoding="utf-8")
    notes.append(f"LinkedIn/Indeed search set to {loc}; example sources switched off")
    return notes
