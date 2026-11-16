# Job Hunter AI — specification

A local, private AI job-search assistant for one person. Region, field, languages and document conventions are configurable (`config/settings.yaml`); the candidate's facts live in a private master profile (`personal/master_profile.yaml`).

## 1. Objective

Automate as much of a personal job search as reasonably possible, while the user keeps every decision that matters:

1. Collect new vacancies from job boards, employers' career pages and research institutes.
2. Search in the languages of the target job market.
3. Identify relevant vacancies, including adjacent roles with transferable skills.
4. Evaluate eligibility and suitability for each vacancy.
5. Rank vacancies by suitability.
6. Generate tailored CVs and cover letters.
7. Track applications, deadlines, follow-ups and interviews.
8. Show salary information as advertised and commute information where available.
9. Produce a daily report and run automatically every day.
10. Provide a local dashboard.

**Never** submit applications, send emails to employers or upload documents without explicit approval by the user.

## 2. Candidate profile

- A master profile is the single source of truth: every claim is an evidence item with an ID, a source, a confidence and a verification status.
- Never invent experience, skills, qualifications, certificates, dates or language levels. Gaps and conflicts become open questions for the user.
- Language levels in the profile are the maximum that may be claimed anywhere.
- Private notes (e.g. past salary) are never used in documents or sent to the AI.

## 3. Sources

- A configurable source registry records, per source: name, website, collection method, access restrictions, update frequency, last successful collection, and whether automatic collection is supported.
- Use official APIs, feeds and structured public data; respect robots.txt, terms and rate limits; never bypass CAPTCHAs, logins, paywalls or anti-bot measures.
- Where automatic access is not possible: job-alert emails and manual import (URL, text, file).
- Never pretend a source was monitored when it was not: failing and stale sources are shown.

## 4. Matching

- Stage 1 (Python, free): relevance by keywords, location, date, duplicates, basic salary filter.
- Stage 2 (AI): only for relevant, unseen jobs. Every requirement classified as met / partially met / not met / unknown, mandatory vs desirable, with quotes from the ad and evidence from the profile. Unknown never counts as met.
- Weighted score computed by Python (default: experience 25, education 20, technical skills 20, responsibilities 15, languages 10, location 5, career development 5). Categories: 90+ excellent, 80+ strong, 70+ good potential, 60+ possible, below 60 low priority. These are prioritisation scores, not hiring probabilities.
- An explicitly unmet mandatory requirement flags the vacancy as potentially ineligible regardless of score; unusual opportunities are not rejected without human review.
- Cache evaluations; avoid repeated evaluation of unchanged jobs; track API usage and cost.

## 5. Documents

- Tailored CV (two pages) and one-page cover letter as editable DOCX and PDF, in the configured languages.
- Several CV strategies emphasising different experience; the most relevant experience for each job is emphasised; roles stay in reverse-chronological order.
- Every claim traceable to profile evidence; no invented content; no generic clichés.
- Independent quality check before a document is marked ready: facts, dates, employer and position, language levels, grammar, ATS compatibility, CV/letter consistency, wording that is hard to defend.
- The original CV is never modified.

## 6. Salary and commute

- Store salary exactly as advertised; never assume extra payments (e.g. 13th/14th salary) unless stated; distinguish advertised minimums.
- Commute times only from a reliable routing service; otherwise shown as unavailable. The home address is private configuration, never in code.

## 7. Tracking and feedback

- Statuses: new, recommended, under review, documents generated, approved, applied, interview, rejected, offer, archived. History is append-only.
- Track discovery date, deadline, application date, follow-up date, interview date, employer response, documents submitted, notes.
- Feedback on recommendations improves future priorities without silently changing the scoring model.

## 8. Dashboard and automation

- Local dashboard: overview, vacancies with filters, job details, eligibility, skills, salary, commute, documents, tracker, source monitoring, logs, feedback, manual import.
- Daily scheduled run (independent of the dashboard), clear logs, configurable thresholds, optional email notification to the user only.

## 9. Security and privacy

- Job ads and scraped content are untrusted data; instructions inside them are ignored.
- Credentials only in environment variables / `.env`; personal data never committed; no unrestricted permission bypasses; ask before actions involving external accounts or submissions.
