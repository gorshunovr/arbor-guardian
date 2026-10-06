# arbor-guardian

Read the Arbor guardian/parent portal as JSON — **school messages** (unread /
date period / all), the **children** on the account, **attendance**,
**timetable**, per-session **attendance by date**, **homework/assignments**,
account **balances**, meal-account **spend by week**, **invoices / top-ups /
credit notes**, **report cards**, **behaviour**, **exams** (experimental),
**clubs**, **trips**, the **school-shop catalogue**, the **attendance
certificate** PDF and (experimental) **meal choices**. Python 3 standard library only — no
`pip` install, no `curl`/`jq`. Works as a plain CLI or an agent skill (`SKILL.md`).

Nothing about any account is hard-coded: you supply email + password, the school
subdomain(s) are discovered from those credentials, and the children (with their
student ids) are discovered from each school's dashboard. A guardian with
children across **several schools** is handled — all schools are processed by
default and every record carries its `school`.

## Files
- `arbor_guardian.py` — thin CLI entry (run this; same UX as before).
- `arbor_guardian/` — package split by concern (auth, cache, CLI, one module per
  command group). Also: `python3 -m arbor_guardian …`.
- `SKILL.md` — instructions the agent reads (when/how to use, output shape).
- `.env.example` — copy to `.env` and fill in (gitignored).
- `pyproject.toml` — project metadata + optional `ruff` config (dev-only).
- `tests/` — tiny stdlib unit tests for pure helpers (no network).
- `README.md` — this file (human setup).

## How it works
1. `POST https://<school>/auth/login?lang=en` with `{"items":[{"username","password"}]}`
   → establishes a **session cookie** (no Bearer token required). The cookie jar
   is cached per school and reused until it expires (~24h); re-login is automatic.
2. Schools come from `login.arbor.sc/applications/search-by-email`; children come
   from each school's dashboard (the student switcher, or the info panel for a
   single-child school).
3. Each subcommand hits the relevant portal JSON endpoint and the response is
   parsed inside the script, so only the distilled data reaches the agent.

## Setup
Configuration comes from environment variables. A `.env` file (in the current
directory or next to the script) is loaded automatically; real env vars win.
Copy `.env.example` to `.env`:

```sh
ARBOR_EMAIL=you@example.com
ARBOR_PW=your-password
# optional — leave unset to process ALL schools on the account:
# ARBOR_SCHOOL=schoolone.uk.arbor.sc,schooltwo.uk.arbor.sc   # comma-separated subset
# ARBOR_COOKIE_DIR=~/.cache/arbor                            # per-school cookie jars
# ARBOR_CACHE_DB=~/.cache/arbor/cache.sqlite3                # SQLite cache
```

Only email + password are required. School subdomains are discovered from the
credentials, so by default **every school on the account is fetched**. Set
`ARBOR_SCHOOL` (or `--school`) to restrict to specific ones. To see your schools:

```sh
python3 arbor_guardian.py --list-schools
# {"schools":[{"name":"…","subdomain":"yourschool.uk.arbor.sc"}, …]}
```

Usage (subcommands; `messages` is the default if omitted):
```sh
python3 arbor_guardian.py                       # unread messages, all schools, with bodies
python3 arbor_guardian.py messages --no-bodies  # preview, marks nothing read
python3 arbor_guardian.py messages --mode period --since 2026-06-10
python3 arbor_guardian.py children              # children + student ids per school
python3 arbor_guardian.py attendance            # attendance % (etc.) per child
python3 arbor_guardian.py timetable --date 2026-07-02
python3 arbor_guardian.py balances              # meals balance + outstanding
python3 arbor_guardian.py attendance --student-id 123   # one child
python3 arbor_guardian.py attendance-by-date --non-present   # lates/absences this year
python3 arbor_guardian.py attendance-by-date --since 2026-09-01 --until 2026-09-30
python3 arbor_guardian.py assignments                    # due/overdue/submitted homework
python3 arbor_guardian.py assignments --segment overdue --details
python3 arbor_guardian.py meals --since 2026-10-12      # (experimental) meal choices
python3 arbor_guardian.py meals --options --date 2026-10-19   # that day's options (ids like 100_01)
python3 arbor_guardian.py meals --school S --student-id 123 --set 2026-10-19=100_01   # dry run
python3 arbor_guardian.py meals --school S --student-id 123 --set 2026-10-19=100_01 --add-to-basket
python3 arbor_guardian.py invoices                       # invoices + top-ups + credit notes (current term)
python3 arbor_guardian.py invoices --student-id 456 --kind invoices
python3 arbor_guardian.py report-cards
python3 arbor_guardian.py report-cards --student-id 123 --download /tmp/rc   # PDFs (experimental)
python3 arbor_guardian.py clubs --details
python3 arbor_guardian.py trips --details
python3 arbor_guardian.py shop                           # catalogue only, never buys
python3 arbor_guardian.py account-spend                  # meals spend by week/day (current term)
python3 arbor_guardian.py account-spend --since 2026-09-28 --details   # + items bought per day
python3 arbor_guardian.py account-spend --school S --student-id 123 --account-id 456   # another account
python3 arbor_guardian.py behaviour                      # points, incidents, detentions
python3 arbor_guardian.py exams                          # exam timetable (exam candidates only)
python3 arbor_guardian.py attendance-by-date --student-id 123 --certificate /tmp/att   # certificate PDF
python3 arbor_guardian.py trips --files                  # + attachment names
python3 arbor_guardian.py --school a.uk.arbor.sc --all-schools --list-schools
```
Each subcommand prints a JSON object with a `cmd` field; see `SKILL.md` for the
exact output shape of each.

## Notes
- `messages` is **guardian-level** (shared across a school's children). The
  other subcommands are **per child**; children (and their student ids) are
  discovered automatically, so nothing is hard-coded. All schools are processed
  by default; every record carries its `school` (and `student_id`).
- **Meal basket writes are opt-in.** `meals --set DATE=VALUE` is a dry run;
  only `--add-to-basket` POSTs, and only to the portal's
  `/guardians/basket/process-meal-provisions/...` form (any checkout / pay /
  top-up URL is refused). That puts choices **in the basket only** — the
  portal still needs checkout to confirm them (at one school the basket page
  is currently broken and redirects to the dashboard). The tool never checks
  out or pays.
- **Not yet covered**: exam results and the exam-timetable PDF, attachment
  downloads. Payment / top-up / sign-up / consent / checkout are deliberately
  out of scope — no command can book, sign up or pay for anything.
- Local research notes live under `notes/` and `har/`, which are gitignored
  (they can contain personal data) — never commit them.

## Security
- `ARBOR_PW` is a secret: store it in the environment only, never in the repo.
- The cookie file holds a live session — keep it `0600` (the script sets this)
  and out of git. Cache/cookie directories are created as `0700` when possible.
- `--school` / discovered hosts must be `*.arbor.sc` hostnames (no path
  segments): this blocks cookie-jar path traversal and posting credentials to a
  typo / phishing host.
- `--offline` serves the SQLite cache and does not require credentials.

## Caching (SQLite)
Results are cached in `$ARBOR_CACHE_DB` (default `~/.cache/arbor/cache.sqlite3`)
so re-reads don't hammer Arbor:
- **Message bodies are immutable and cached forever.** Re-reading a message is
  served from cache — no request, and it is **not re-marked read** on the server.
- `--offline` serves everything from cache and makes **zero requests** to Arbor
  (re-reads, or when the portal is unavailable / rate-limiting you).
- `--max-age SEC` reuses a cached result of any read-only subcommand (except
  `messages`) if it is younger than `SEC` seconds; otherwise it refetches.
- `--no-cache` bypasses the cache entirely.

```sh
python3 arbor_guardian.py messages --mode all            # fetch + cache bodies
python3 arbor_guardian.py messages --mode all --offline  # re-read, no requests
python3 arbor_guardian.py attendance --max-age 3600      # reuse if < 1h old
```

## Being a good citizen
- Requests are throttled to at least `ARBOR_MIN_INTERVAL` seconds apart
  (default 0.5s) and back off on HTTP 429/503 (honouring `Retry-After`), so the
  script never bursts the portal — even `messages --mode all --bodies`, which
  fetches many bodies, runs at a steady, polite pace.
- Sessions are cached per school and reused (~24h), so a typical run makes only
  a handful of requests.

## Caveats
- Fetching a body marks the message **read** on the server (for all children).
  Use `--no-bodies` to avoid side effects.
- `meals --add-to-basket` changes the basket (see above); `--set` alone and
  `--options` don't.
- HTML in message bodies is flattened to plain text.


## Development
Runtime stays **stdlib-only**. For contributors, optional lint/format:

```sh
pip install 'ruff>=0.6'    # or: pip install -e '.[dev]'
ruff check arbor_guardian arbor_guardian.py
ruff format arbor_guardian arbor_guardian.py
python -m unittest discover -s tests -v
```

CI runs on GitHub Actions (`.github/workflows/ci.yml`; mirrored in
`ci/github-actions.yml`) — ruff, `compileall`, CLI `--help` smoke, and unit
tests on push/PR to `main`. See [CONTRIBUTING.md](CONTRIBUTING.md) for layout
notes and the no-secrets / no-PI rules.

## License

MIT — see [LICENSE](LICENSE).
