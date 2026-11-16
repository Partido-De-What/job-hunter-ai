# Job Hunter AI — architecture

How the system is built and why. The requirements are in [SPECIFICATION.md](SPECIFICATION.md).

## 1. Principles

- **Python is deterministic, Claude is linguistic.** Python collects, filters, deduplicates, stores, computes scores and renders documents. Claude only reads job ads, classifies requirements and writes text.
- **The AI never computes the final score.** It classifies requirements and proposes sub-scores with reasons and evidence; Python applies weights, categories and the eligibility rule.
- **The master profile is the single source of truth.** Every CV or letter claim must cite profile evidence; claims without valid evidence are removed.
- **Human approval** before anything leaves the computer. The program never applies or contacts employers.
- **Honest monitoring:** every source has a status and last success; failures are shown.

## 2. Folders

```
config/       settings.yaml (candidate, scoring, thresholds, AI, automation), search_terms.yaml, sources.yaml
examples/     example profile and home location (fictional)
personal/     master_profile.yaml, home_location.yaml          (private, git-ignored)
cv/           original CV and photo, read-only                 (private, git-ignored)
data/         documents, reports, logs, imports                (private, git-ignored)
src/vjh/      models, db, sources, pipeline, scoring, llm, documents, tracker, dashboard, automation, cli*
tests/        pytest suite (fake internet, fake Claude, fake Word, temporary databases)
```

The SQLite database is at `%LOCALAPPDATA%/job-hunter/vjh.db` by default (outside synced folders such as OneDrive, which can corrupt an open SQLite file).

## 3. Data and storage

- SQLite + SQLAlchemy + Alembic migrations. Migrations only add (tables, columns, indexes); a backup is made before every upgrade; downgrades are disabled.
- `application_events` is append-only (database triggers forbid UPDATE and DELETE).
- Evaluation cache key: (job content hash, profile version, model). Daily runs evaluate only new jobs; older ones are re-evaluated only on request (`vjh evaluate --refresh`).

## 4. Collection

- Plugin collectors: `jsonld_page` (schema.org JobPosting, optional page-text fallback), `pdf_links`, `personio`, `smartrecruiters`, `rss`, `email_alert` (.eml files; links are never opened), `jobspy` (LinkedIn/Indeed/Glassdoor; **on** by default via `collection.allow_jobspy`, although it scrapes against those sites' terms - each user is told at setup and can switch it off).
- Polite HTTP client: identifies itself, checks robots.txt before every automated request (unreadable robots.txt = do not fetch), per-host delay, limited retries honouring Retry-After.
- One failing source never stops a run; runs, errors and per-source status are recorded.
- Manual import from URL, text or file (.txt/.md/.html/.pdf).

## 5. Matching

- **Stage 1 (free):** title/topic patterns, excluded titles, deadline, age since discovery, location (configured local/foreign place patterns), salary only when the advertised maximum is known and too low. Cross-source deduplication within employer groups (same first letters of the normalised employer name), with matching title numbers and similar text; re-checked every run. Nothing is deleted.
- **Stage 2 (AI):** structured JSON output validated by Pydantic; evidence IDs not in the profile are dropped and met/partially-met requirements left without evidence become `unknown`. Private data (address, phone, email, date of birth, private notes) is never in prompts. Job text is wrapped as untrusted data.
- **AI backends:** `claude_code` (the user's subscription through `claude -p`, no tools, empty temp folder, API key removed from its environment, daily job limit) or `api` (Anthropic SDK with structured outputs, prompt caching, refusal fallback, daily cost cap).

## 6. Documents

- The AI returns a plan (CV: summary, focus roles, bullets per role/degree, skill groups, keywords, flags; letter: typed paragraphs). Python renders facts from the profile and enforces: evidence on every claim, focus roles detailed (≤6 bullets) and others condensed (≤2), reverse-chronological order.
- CV header follows `candidate.cv_conventions` (photo, date of birth, nationality, work permit, driving licence). Labels in EN/DE/IT. DOCX via python-docx, PDF via Microsoft Word (docx2pdf); letters are re-laid out to fit one page.
- QA: deterministic checks (language levels against the profile, pronouns, employers and dates, employer named, page limits, placeholders, emphasis) plus an independent AI reviewer. `qa_passed` only without errors. Every run creates a new version; approval is manual.

## 7. Tracking, dashboard, automation

- Tracker: automatic forward-only steps up to `approved`; `applied` and later are always manual. Due list for follow-ups, interviews and deadlines. Feedback report suggests configuration changes but never applies them.
- Dashboard: Streamlit on 127.0.0.1, usage statistics off, no Deploy button; actions reuse the same services as the CLI. Quick actions on ticked rows (native row selection); a Progress page (funnel from the append-only history, weekly goal, response/interview rates; Altair charts); an Applications board built from native columns (drag-and-drop kanban components were not used: they would bypass the tracker's rules). `vjh shortcut` creates a Windows Desktop icon; a second start only reopens the browser.
- Daily routine (`vjh run-daily`): collect → filter → evaluate → auto documents (new, eligible jobs ≥ threshold, limited per day, drafts only) → housekeeping (old logs, surplus backups) → HTML report (autoescaped, only http(s) links) → email to the user only (TLS required, no attachments, skipped on quiet days).
- Windows Task Scheduler via task XML: runs as the user while logged on, least privilege, catches up missed runs, no overlap, 2-hour limit, no console window.

## 8. Testing

- An autouse fixture removes mail/API variables, disables `.env` loading and blocks real Claude calls in every test.
- Unit tests per module, a headless dashboard test per page, and an end-to-end test that drives the whole chain through the real `vjh` commands.

## 9. Known limitations

- Commute times are shown as unavailable until a routing service is chosen.
- Scheduler and PDF conversion are Windows-specific (Task Scheduler, Microsoft Word).
- Websites change: collectors may need updates; `vjh sources list` shows failing or stale sources.
