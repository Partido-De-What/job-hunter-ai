# Job Hunter AI

A local, private job-search assistant that you set up for **your** field and **your** region.

Every day it can:

- **collect** new vacancies from job boards, employers and research institutes;
- **filter out** the irrelevant ones, for free;
- **evaluate** the rest against your profile with Claude;
- **write** a tailored CV and cover letter for very good matches;
- **email** you a short report.

You decide everything that matters. **Nothing is ever sent to an employer**: you review the documents, apply yourself, and record it.

Everything runs on your own computer. Your CV, profile, documents and database never leave it, apart from the job texts and profile facts sent to Claude for evaluation and writing.

More detail: [docs/SPECIFICATION.md](docs/SPECIFICATION.md) (requirements) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (design).

---

## Contents

1. [What you need](#1-what-you-need)
2. [Getting started](#2-getting-started)
3. [Daily use in 5 minutes](#3-daily-use-in-5-minutes)
4. [Where jobs come from](#4-where-jobs-come-from)
5. [How jobs are matched to you](#5-how-jobs-are-matched-to-you)
6. [CVs and cover letters](#6-cvs-and-cover-letters)
7. [Tracking applications and giving feedback](#7-tracking-applications-and-giving-feedback)
8. [The dashboard](#8-the-dashboard)
9. [The automatic daily run and email](#9-the-automatic-daily-run-and-email)
10. [What controls what (configuration)](#10-what-controls-what-configuration)
11. [Privacy and safety](#11-privacy-and-safety)
12. [Troubleshooting](#12-troubleshooting)
13. [Tests, structure, license](#13-tests-structure-license)

---

## 1. What you need

- **Windows 10 or 11** with **Python 3.12 or newer** and **Git**.
  - On macOS or Linux everything works except the daily scheduler and the PDF conversion. Ask Claude Code to adapt them (cron instead of Task Scheduler, LibreOffice instead of Word).
- **Microsoft Word**, to turn the documents into PDFs. Without it you still get Word files.
- **A Claude subscription** (Pro or Max) with [Claude Code](https://claude.com/claude-code) installed. Alternatively, an Anthropic API key, which is billed per use.
- Optional: a Gmail (or other) account for the morning email.

---

## 2. Getting started

### The easy way: let Claude Code guide you (recommended)

1. Put this folder somewhere on your computer (for example on your Desktop).
2. Put your **CV** (PDF or Word) and, if you want, a **photo** into a new folder named `cv` inside it.
3. Open the folder in Claude Code and say: **"Set me up"**.

Claude Code follows the *New user setup* in `CLAUDE.md`, step by step:

- installs everything;
- builds your profile from your CV and **asks you** about every gap. It never invents facts or language levels;
- sets your region, field, languages and CV style;
- researches and checks job sources for your area;
- runs a first small test with you.

### By hand

```powershell
cd "<the folder where you put Job Hunter>"
```

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\Activate.ps1
```

> If PowerShell says *"running scripts is disabled"*, run this once and activate again:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

```powershell
python -m pip install -e ".[dev]"
```

Then:

1. Copy `examples\master_profile.example.yaml` to `personal\master_profile.yaml` and replace the example person with **your** facts. Copy `examples\home_location.example.yaml` to `personal\home_location.yaml`.
2. Edit `config\settings.yaml`: the `candidate:` and `preferences:` sections (country, cities, salary, career focus, CV strategies, CV conventions).
3. Edit `config\search_terms.yaml` (words for your field) and `config\sources.yaml` (sources for your region).
4. Log Claude Code in:

   ```powershell
   claude auth login
   ```

5. Create the database and check everything. `vjh status` should end with `Overall: OK`:

   ```powershell
   vjh init
   ```

   ```powershell
   vjh status
   ```

6. Optionally install the daily task (section 9):

   ```powershell
   vjh schedule install
   ```

---

## 3. Daily use in 5 minutes

**Every new PowerShell window first needs these two commands**: go to the folder, then activate the environment. The prompt then starts with `(.venv)`.

```powershell
cd "<the folder where you put Job Hunter>"
```

```powershell
.\.venv\Scripts\Activate.ps1
```

Then:

1. **Read the morning email** (or `data\reports\daily-<date>.html`).
2. **Open the dashboard** to look at the good matches:

   ```powershell
   vjh dashboard
   ```

3. For a job you like:
   - read its evaluation;
   - create (or read) the CV and cover letter;
   - approve them.
4. **Apply yourself**, then record it. This also sets a follow-up reminder 10 days later:

   ```powershell
   vjh track set 44 applied --note "sent via the careers portal"
   ```

5. For jobs that are **not** for you, say why. This teaches the search what you want:

   ```powershell
   vjh track dismiss 41 "Requires a driving licence"
   ```

All commands: `vjh --help`.

---

## 4. Where jobs come from

| How | Examples |
|---|---|
| **Automatic** | Career pages with structured job data, PDF job notices, Personio/SmartRecruiters feeds, RSS |
| **Your job-alert emails** | Any job board that can email you new jobs |
| **Manual import** | Any other site: a web address, pasted text, or a file |
| **JobSpy (on)** | LinkedIn, Indeed, Glassdoor (see below) |

`config\sources.yaml` holds example entries. Replace them with sources for your area. Then check them; this only reads:

```powershell
vjh sources check
```

Only sources whose robots.txt allows it, and that offer usable job data, should be collected automatically. Claude Code can do this research with you.

Collect now (the daily run does this for you):

```powershell
vjh collect
```

**Job-alert emails.** On each job board, create a saved search / job alert. Save the alert emails as `.eml` files into `data\imports\alerts\`:

- **Gmail:** ⋮ menu → *Download message*.
- **Outlook:** drag the email into the folder.

The next collection turns each job link into a job, without opening the links.

**Manual import** (also in the dashboard):

```powershell
vjh import-url "https://example.org/jobs/123"
```

```powershell
vjh import-file "C:\Users\me\Downloads\job.pdf" --title "Lab Technician" --employer "Example Ltd"
```

**JobSpy (LinkedIn, Indeed, Glassdoor) is switched on.** It is the quickest way to get many jobs, but you should know that:

- these sites **forbid automated collection in their terms of use**;
- they may **temporarily block your internet connection** if they notice it;
- JobSpy **never logs in**, so your accounts are not involved.

Before the first run, set the `location` of the JobSpy entries in `config\sources.yaml` to **your** city. Their search words are the `core_queries` in `config\search_terms.yaml`.

To stop using it, set `collection.allow_jobspy: false` in `config\settings.yaml`.

Source monitoring:

```powershell
vjh sources list
```

---

## 5. How jobs are matched to you

**Stage 1: free filter (no AI)**

```powershell
vjh filter
```

Each new job becomes one of these:

- **relevant**: goes to the AI.
- **filtered_out**: set aside, with a reason. Possible reasons:
  - not related to your field;
  - the title needs a degree you don't have;
  - deadline passed or found too long ago;
  - outside your region;
  - the advertised **maximum** salary is below your minimum. An unknown salary is never rejected.
- **duplicate**: the same job on another site.
- **needs_text**: only a title is known.

Nothing is deleted: `vjh jobs --status filtered_out` shows the reasons.

**Stage 2: AI evaluation**

```powershell
vjh evaluate
```

- Claude labels **every requirement** of the ad (met, partially met, not met, unknown), with the quote from the ad and evidence from your profile.
- **Python computes the score.** An unmet **mandatory** requirement marks the job *potentially ineligible*. *Unknown* never counts as met.
- **Your language levels are the limit.** A requirement above your level counts as not met.
- **Only newly found jobs use the AI.** After changing your profile, re-check older jobs with `vjh evaluate --refresh`.

**How the AI is reached** (`llm.backend`):

- **`claude_code` (default):** your subscription through Claude Code. No extra bill; at most 40 jobs a day (`subscription_daily_max_jobs`).
- **`api`:** your own Anthropic key in `.env`, billed per call, with a daily cost cap.

Results, best first:

```powershell
vjh jobs
```

One job in detail:

```powershell
vjh job 44
```

---

## 6. CVs and cover letters

Created automatically for very good new matches (score ≥ 80, at most 2 a day), or on request. For a German version, add `--lang de`; for only the CV, add `--type cv`:

```powershell
vjh docs generate 44
```

**Kept truthful**

- **Python** writes every fact from your profile: name, contact details, photo, employers, titles, dates, degrees, language levels. Which personal details appear follows `candidate.cv_conventions`.
- **The AI** only chooses and rephrases evidence-backed bullets and drafts the letter. Anything without evidence is removed before the document is created.
- **Your most relevant experience for each job gets the detail.** Other roles are kept short but listed, and everything stays newest first.
- Your private notes are never used.

**Quality check.** A document is marked `qa_passed` only if both checks find no errors:

- **Python** checks language levels against your profile, pronouns, employers and dates, and page limits.
- **An independent AI reviewer** looks for unsupported claims, grammar, ATS problems, CV/letter consistency, and wording that is hard to defend.

Files go into `data\documents\<job>-<employer>\` as `.docx`, `.pdf` and `_qa.json`, named after you, the document and the job, e.g. `Anna_Muster_CV_Lab_Technician.docx` (German: `…_Lebenslauf_…`; letters: `…_Cover_Letter_…` / `…_Anschreiben_…`). New runs add `_v2`, `_v3`, …; nothing is overwritten.

List documents and read a report:

```powershell
vjh docs list
```

```powershell
vjh docs show 3
```

After reading a document yourself, approve it:

```powershell
vjh docs approve 3
```

---

## 7. Tracking applications and giving feedback

Statuses: `new → recommended → under_review → documents_generated → approved → applied → interview → offer`, plus `rejected` and `archived`.

- Automatic steps only move forward, up to `approved`.
- **You** set `applied` and everything after it.
- The history can never be edited or deleted.

```powershell
vjh track set 44 interview --interview "2026-11-25 10:00"
```

```powershell
vjh track list
```

```powershell
vjh track due
```

**Feedback:**

```powershell
vjh feedback add 44 --good "exactly the kind of lab work I want"
```

```powershell
vjh feedback report
```

The report suggests changes, for example titles to exclude or a different threshold. **It never changes anything by itself.**

---

## 8. The dashboard

```powershell
vjh dashboard
```

It opens at http://127.0.0.1:8501 and works **only on this computer** (usage statistics off). Keep the window open while you use it; **Ctrl+C** stops it. If it is already running, `vjh dashboard` just opens the browser again.

**Desktop icon (Windows):** `vjh shortcut` puts a **Job Hunter AI** icon on your Desktop. Double-click it to open the dashboard (it runs in a minimised window; close that window to stop it). Remove it with `vjh shortcut --remove`.

**Look and layout:** the colours, corners and fonts are set in `.streamlit/config.toml` (a calm green theme, no external fonts, so the dashboard stays fully local). The **Overview** opens with **Next steps**: overdue dates, documents waiting for approval or with a failed check, recommended jobs to rate, how far you are from the weekly goal, and failing sources, each with an **Open** button.

**ATS keyword match:** on a job's page, above its documents, a box shows how many of the ad's skills that you really have are actually written in the newest CV (for example "5 of 6"), with the missing ones in grey. Applicant tracking systems rank CVs by such keywords. The same check adds a *warning* (never an error) to the CV's quality report. It works without the AI, so it costs nothing.

**Job titles are links:** click a title in any table or card to open the job ad (where you apply). A 🔎 in front means no link is stored, so it opens a web search for that job instead.

**Two people on one computer:** use two separate copies of this folder. Each copy needs its own `dashboard: port` (e.g. 8501 and 8502) and `dashboard: title` (this also names the Desktop icon), its own `database: path` and its own `automation: task_name` in `config/settings.yaml`.

**Quick actions:** in the job and application tables, tick one or more rows at the left edge. Buttons appear to open the job, rate it (👍 good match / 👎 not for me, with a reason), mark it as applied (only after you really sent it; a follow-up reminder is set) or change its status.

Pages:

- **Overview**, with this week's application goal
- **Progress**: applications this week vs. your goal (`progress: weekly_application_goal` in `config/settings.yaml`), weeks in a row on goal, response and interview rates, found → recommended → applied → interview → offer, the last 12 weeks
- **Jobs**, with filters
- **Job details**: eligibility, score, skills, salary, documents (open PDF or Word, download, open folder, approve; a "documents ready" badge jumps there), status, feedback
- **Applications**: a board (To review → Documents ready → Applied → Interview → Offer) or a table with quick actions
- **Documents**: QA reports, downloads, approve or reject
- **Sources**
- **Logs & errors**
- **Feedback**
- **Import a job**

---

## 9. The automatic daily run and email

```powershell
vjh run-daily
```

Steps, each isolated so that one failure doesn't stop the rest:

1. collect;
2. filter;
3. evaluate (new jobs only);
4. documents for top matches;
5. housekeeping (old logs and backups);
6. report;
7. email.

**Daily task:**

```powershell
vjh schedule install
```

- Runs every day at 07:30. Change it with `--time 08:15`.
- Runs as you, only while you are logged in, without administrator rights.
- Missed runs start when the PC is on again.
- No window opens. The log is in `data\logs\`.

```powershell
vjh schedule status
```

```powershell
vjh schedule remove
```

**Email** goes only to you. It is encrypted, has no attachments, and is skipped on quiet days. Copy `.env.example` to `.env` and fill in your mail settings. For Gmail, use an **app password** (https://myaccount.google.com/apppasswords), written **without spaces**. Then test:

```powershell
vjh notify test
```

---

## 10. What controls what (configuration)

| File | Controls |
|---|---|
| `config\settings.yaml` → `candidate` | Country, career focus, city names, location filter, CV strategies, CV conventions, language names |
| `config\settings.yaml` → `preferences`, `thresholds`, `scoring` | Cities, salary floor and currency, commute limit, thresholds, scoring weights |
| `config\settings.yaml` → `llm`, `collection`, `automation` | AI route and limits, JobSpy switch, daily time, auto documents, email, housekeeping |
| `config\search_terms.yaml` | Search queries; relevance words; excluded titles |
| `config\sources.yaml` | Your sources and how each is collected |
| `personal\master_profile.yaml` | Your facts and evidence (private) |
| `personal\home_location.yaml` | Your address (private) |
| `.env` | Mail settings, optional API key (private) |

---

## 11. Privacy and safety

- **Nothing is ever sent to an employer.** Documents need your approval, and `applied` is your own action.
- **Private and never committed to Git**: `cv/`, `personal/`, `data/`, `.env`.
- **What the AI never receives**: your address, phone number, email, date of birth or private notes.
- **Job ads are untrusted.**
  - Instructions inside them are ignored.
  - Their text is escaped in reports and emails.
  - Only normal web links become clickable.
- **Your stated language levels are the maximum**, and every claim needs evidence.
- **The database is protected**: it is backed up before every upgrade, and application history is append-only.
- **Tests can never** read your `.env`, send email or use your Claude account.

---

## 12. Troubleshooting

| You see | What to do |
|---|---|
| `vjh` is not recognised | Run the two commands at the top of [section 3](#3-daily-use-in-5-minutes), or use the full path: `& ".\.venv\Scripts\vjh.exe" status` |
| `running scripts is disabled` | Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `Database missing or out of date` | Run `vjh init` (makes a backup first). If you use Claude Code: run database commands in your own terminal, not in Claude's sandbox (see `CLAUDE.md`). |
| `Claude Code is not logged in` | Run `claude auth login`, then `claude auth status`. |
| Evaluation stops with a usage limit | Your Claude plan's limit was reached; the rest waits for the next run. Lower `subscription_daily_max_jobs` if it happens often. |
| Email: `almost ready…` or `Username and Password not accepted` | Put a Gmail **app password**, without spaces, after `SMTP_PASSWORD=` in `.env`, and save the file. |
| A source is *failing* or *stale* | `vjh sources list` shows the error; re-check with `vjh sources check`. Websites change. |
| No PDF was created | Microsoft Word must be installed and not blocked by a dialog. The `.docx` is always there. |
| The daily task did not run | `vjh schedule status`; read the newest `data\logs\daily-*.log`. It only runs while you are logged in. |
| `database is locked` | Close the dashboard and try again. |

---

## 13. Tests, structure, license

```powershell
python -m pytest
```

All tests should pass. They use a fake profile, temporary databases, a fake internet, a fake Claude and a fake Word.

Check the code:

```powershell
ruff check src tests
```

```
config/      settings, search terms, sources (examples - make them yours)
examples/    example profile and address (fictional)
docs/        specification and architecture
personal/    your profile and address (private, created by you)
cv/          your CV and photo (private, read-only)
data/        documents, reports, logs, imports (private)
src/vjh/     the program
tests/       automated tests
```

License: MIT (see `LICENSE`).
