---
name: arbor-guardian
description: Read data from the Arbor guardian/parent portal as JSON — school messages (unread / date period / all), the children on the account, attendance (KPIs, per-session marks, certificate PDF), timetable/lessons, homework, account balances, meal-account spend by week, invoices/top-ups, report cards, behaviour, exams, clubs, trips, the school-shop catalogue and meal choices. Use when the user asks to check Arbor school messages, a child's attendance, timetable, homework, behaviour, meals spend or balance, or on a schedule to pull new messages. Returns structured JSON for the agent to parse and act on.
---

# Arbor guardian portal → JSON

Reads the Arbor guardian portal and prints a JSON object to stdout. The agent
decides what to do with the result (summarise, remind, notify). This skill only
fetches data. Implementation lives in the `arbor_guardian/` package;
`python3 arbor_guardian.py …` remains the public entry point.

## Config (env, or a `.env` file loaded automatically)
Required: `ARBOR_EMAIL`, `ARBOR_PW`. Optional: `ARBOR_SCHOOL` (comma-separated
subdomains — if unset, ALL schools on the account are used; auto-discovered),
`ARBOR_COOKIE_DIR`, `ARBOR_CACHE_DB`. Nothing account-specific is hard-coded:
schools are discovered from the credentials and children from each school's
dashboard.

## Subcommands
```bash
python3 arbor_guardian.py <subcommand> [--school HOST]... [--all-schools]
```
- `messages` (**default** if omitted) — School Messages (guardian-level).
  `--mode unread|all|period`, `--since/--until YYYY-MM-DD`, `--limit N`,
  `--no-bodies`. **Fetching bodies marks messages read** (for all children);
  use `--no-bodies` to only list metadata.
- `children` — the children on the account: `student_id` + `name` per school.
- `attendance [--student-id ID]...` — attendance KPI(s) per child (percentage;
  for secondary schools also merits/demerits/behaviour).
- `timetable [--student-id ID]... [--date D | --since D --until D]` — calendar
  events / lessons per child.
- `balances [--student-id ID]...` — meals balance per child + outstanding total.
- `attendance-by-date [--student-id ID]... [--academic-year-id ID]
  [--date D | --since D --until D] [--non-present]` — per-session (AM/PM)
  register marks for an academic year (default: current year).
  `--non-present` lists only sessions that aren't plain present (late,
  absences, closures, trips…); the `summary` still counts every session.
  `--certificate DIR` also saves that year's attendance-certificate PDF as
  `DIR/attendance-certificate-<student>-<year>.pdf` (one extra GET; not cached).
- `assignments [--student-id ID]... [--academic-year-id ID]
  [--segment due|overdue|submitted]... [--details]` — homework lists + counts
  (default: all three lists, current year). `--details` fetches each
  assignment's page (course, instructions, attachment names) — 1 request per
  item, so prefer it with `--segment`/`--student-id`.
- `meals [--student-id ID]... [--date D | --since D --until D] [--menu-id ID]` —
  **experimental**: per-day meal choices from the school's meal-menu grid
  (only schools that offer pre-ordering; others return
  `meal_choices_available:false`). Read-only by default.
  - `--options` — for each **editable** day in range, the options the portal
    offers: `value` id (e.g. `100_01`), label, leading `code`, `price`,
    `selected`. One GET per slideover (a slideover covers several days).
  - `--set DATE=VALUE` (repeatable; VALUE an option id or `none`) — **dry
    run**: shows before/after and the exact form body; makes no POST.
    Requires exactly one `--school` and one `--student-id`.
  - `--add-to-basket` (with `--set`) — actually POSTs the portal's own
    "Process" form (`/guardians/basket/process-meal-provisions/...`, the only
    write URL the tool will use; checkout / pay / top-up / basket-page URLs are
    refused), then re-reads the day and reports `verified`. **This only puts
    choices in the basket; they are not confirmed until checkout in the
    portal**, which this tool never does (at some schools the basket page
    redirects to the dashboard and checkout must be done another way). Only use
    it when the user explicitly asks to change a meal choice.
- `invoices [--student-id ID]... [--term-id ID] [--kind invoices|top-ups|credit-notes]...`
  — invoice, top-up and credit-note history for the current term (all three
  lists by default). **Guardian-level** per school (all children's accounts)
  unless `--student-id` is given, which uses the portal's own per-child filter.
- `report-cards [--student-id ID]... [--download DIR [--card-id ID]...]` —
  list of report cards per child; `--download` (experimental) also saves each
  PDF as `DIR/report-card-<student>-<kind>-<id>.pdf` (one view GET + one
  download request per card; `--card-id` limits which). Not cached.
- `clubs [--student-id ID]... [--details] [--files]` — clubs per child, grouped
  `registered` / `available` / `past`; `--details` adds each club's overview
  (registration status, sessions, prices, timetable); `--files` lists the
  overview's attachments (names/metadata only, never download links).
- `trips [--student-id ID]... [--details] [--files]` — trips per child, grouped
  `upcoming` / `open` / `past`; `--details` adds the overview (location,
  price, place status, invoice/paid amounts); `--files` as for clubs.
- `shop [--student-id ID]... [--details]` — school-shop catalogue per child
  (name, price; `--details` adds description + purchase-limit notice).
  **List only — never buys or touches the basket.**
- `account-spend [--student-id ID]... [--since D] [--until D] [--details]
  [--term-id ID]` — spend per week and per day on each child's **meals
  account** for the current term (the account is found from the meals-balance
  link). `--details` adds the items bought on each non-zero day (1 GET per
  day, so combine with `--since/--until`). `--account-id ID` (repeatable,
  needs one `--school` + one `--student-id`) reads other customer accounts
  instead, e.g. wraparound care — ids are in `invoices` → `accounts`.
- `behaviour [--student-id ID]... [--academic-year-id ID] [--since D]
  [--until D]` — behaviour points / incident totals (lifetime, year, term,
  recent range) and the events behind them, plus detentions. `--since/--until`
  filter events only. Schools that publish nothing return
  `behaviour_available:false`.
- `exams [--student-id ID]... [--academic-year-id ID] [--candidate-id ID]` —
  **experimental**: exam timetable for children who are exam candidates
  (usually secondary). The candidate id is read from the child's menu; children
  without an Examinations item return `exams_available:false`.

`--list-schools` prints `{"schools":[{name, subdomain}]}` and exits.
`--student-id` (repeatable) limits per-child commands to specific children.

### Caching (SQLite, automatic)
Results are cached in `$ARBOR_CACHE_DB` (default `~/.cache/arbor/cache.sqlite3`).
- **Message bodies are immutable** → cached forever. Re-reading a message serves
  from cache: no request, and it is **not re-marked read**. Great for re-reads.
- `--offline` — serve everything from cache; makes **no requests to Arbor** at
  all (useful for re-reads or when the portal is unavailable). Each message then
  carries `from_cache`; per-child results get a top-level `from_cache`.
- `--max-age SEC` — reuse a cached `attendance`/`timetable`/`balances`/`children`
  result if it is younger than SEC seconds (otherwise refetch); applies to every
  read-only subcommand except `messages`.
- `--no-cache` — bypass the cache entirely.
Prefer `--offline` (or a generous `--max-age`) when re-reading data you already
pulled, to avoid hitting Arbor again.

## Examples
```bash
python3 arbor_guardian.py                       # unread messages, all schools
python3 arbor_guardian.py messages --no-bodies  # preview, marks nothing read
python3 arbor_guardian.py children
python3 arbor_guardian.py attendance
python3 arbor_guardian.py timetable --date 2026-07-02
python3 arbor_guardian.py balances
python3 arbor_guardian.py meals --student-id 123 --options --since 2026-10-19 --until 2026-10-20
python3 arbor_guardian.py meals --school S --student-id 123 --set 2026-10-19=100_01          # dry run
python3 arbor_guardian.py meals --school S --student-id 123 --set 2026-10-19=100_01 --add-to-basket
python3 arbor_guardian.py report-cards --student-id 123 --download /tmp/rc
python3 arbor_guardian.py account-spend --since 2026-09-28 --details   # meals spend + items
python3 arbor_guardian.py account-spend --school S --student-id 123 --account-id 456
python3 arbor_guardian.py behaviour --student-id 123 --since 2026-09-01
python3 arbor_guardian.py exams
python3 arbor_guardian.py attendance-by-date --student-id 123 --certificate /tmp/att
python3 arbor_guardian.py trips --files
```

## Output shapes (parse this)
Every result is a JSON object with a `cmd` field naming the subcommand.

`messages`: `{cmd, mode, schools:[{school,name,total_in_inbox,count}], count,
messages:[{school,id,unread,datetime,subject,snippet, received,sent_by,body}]}`.
With `--no-bodies`, messages omit `received/sent_by/body`.

`children`: `{cmd, schools:[{school, children:[{student_id, name}]}]}`.

`attendance`: `{cmd, students:[{school,student_id,name,
attendance:[{title, value_pct, text}]}]}` — `value_pct` is the headline number
(e.g. "99.4"); `text` is the flattened KPI text.

`timetable`: `{cmd, students:[{school,student_id,name,
events:[{start,end,title,location}]}]}` (datetimes "YYYY-MM-DD HH:MM:SS").

`balances`: `{cmd, students:[{school,student_id,name,meals_balance}],
outstanding:[{school,title,value}]}` (money as strings like "£28.71").

`attendance-by-date`: `{cmd, students:[{school,student_id,name,
academic_year:{id,label}, academic_years:[{id,label}], summary:{category:n},
sessions:[{date,session,mark,description,category}]}]}` — `date` is
YYYY-MM-DD, `session` AM|PM, `mark` the register code (`/` present, `L` late,
`U` late after register closed, `I`, `O`, `#`, `V`, `Y4`, `-` …),
`description` the portal's text (e.g. "Illness"), `category` one of
`present|late|authorised_absence|unauthorised|not_counted|no_mark|other`
(from the portal's colour coding; `not_counted` = closures, trips etc.).
Academic-year ids are **school-specific** (e.g. 27 at one school, 11 at
another); pick from `academic_years` and pass `--school` with
`--academic-year-id`.

`assignments`: `{cmd, students:[{school,student_id,name,
academic_year:{id,label}, counts:{due,overdue,submitted},
due|overdue|submitted:[{id,class,title,due,status}]}]}` — `class` is the
teaching-group code (e.g. "10R/Be1"), `due` YYYY-MM-DD, `status` e.g.
"Waiting for student to submit" / "Submitted". With `--details` each item also
has `course, marking, submission_type, instructions, attachments:[name]`
(attachment URLs are dropped — they embed auth tokens).

`meals`: `{cmd, students:[{school,student_id,name, meal_choices_available,
menus:[{menu_id, period, availability, title, basket_pending,
days:[{date, weekday, code, meal, status, editable}]}]}]}` — `status` e.g.
"In basket" / "Deadline passed" (null on holidays); `basket_pending:true` means
the portal says choices sit in the basket unconfirmed; `code` is the leading
number on the portal's label (no legend in the portal; jacket potatoes are
always 2–5, allergy-free 9/99 — treat it as a menu slot number).

`meals --options`: menus additionally carry `deadline_note` and
`options:[{date, editable, selected, selected_label,
options:[{value, label, code, meal, price, selected}]}]` (the "No choice
selected" entry has `value:null`).

`meals --set`: `{…, meal_updates:[{menu_id, process_url,
changes:[{date, before, before_label, after, after_label}], status,
body? (dry run), success?, notifications?, validation_errors?, verified?}],
note}` — `status` is `dry-run …`, `unchanged (no POST)`, `basket updated` or
`NOT verified`.

`invoices`: `{cmd, schools:[{school, [student_id,name,] term:{id,label},
terms:[{id,label}], accounts:[{id,label}], invoices:[{date,account,amount,
items,status}], top_ups:[{date,account,amount,note,method}],
credit_notes:[{date,account,amount,note,method}]}]}` — money as strings
("£3.20"); `status` e.g. "Issued" / "Paid". Term ids are school-specific;
`terms` lists the current year's (older ids also work with `--term-id`, label
then null). Lists omitted via `--kind` are absent.

`report-cards`: `{cmd, students:[{…, report_cards:[{id, kind:standard|custom,
academic_year, date, title, download?:{file, bytes} | {error}}]}]}`.

`clubs`: `{cmd, students:[{…, clubs:[{club_id, name, group, academic_year,
wraparound, description, membership_dates:[str], timetable:[str], details?}]}]}`.
A club can appear twice (e.g. `registered` and also `available` to book more
sessions).

`trips`: `{cmd, students:[{…, trips:[{trip_id, name, group, dates,
signup_window, details?}]}]}`.

`shop`: `{cmd, students:[{…, products:[{product_id, name, price,
description?, notices?}]}]}`.

`details` (clubs/trips) is the overview page flattened:
`{title, notices:[str], sections:{section:{label: text | {sublabel: text}}},
schedules:{section:[str]}}`. `files` (with `--files`) is a list of the
attachment records as the portal sends them minus any URL-like field (format
not yet seen with real files; empty list when there are none).

`account-spend`: `{cmd, students:[{…, accounts:[{account_id, title,
term_total, term:{id,label}, terms:[{id,label}], range_total,
weeks:[{week_start, total, days:[{date, weekday, amount,
items?:[{item, amount}]}]}]}]}]}` — money as strings ("£3.20");
`range_total` is a number summed over the days kept by `--since/--until`
(null without them); week `total` is the portal's own figure for the whole
week. A child with no meals account gets `accounts:[]` and a `note`.

`behaviour`: `{cmd, students:[{…, behaviour_available, academic_year,
academic_years, behaviour:{<slug>:{title, totals:[{group, period, value,
number}], items:[{date, time?, …fields}]}}}]}` — section slugs follow the
portal's titles (typically `demerits`, `merits`, `positive_incidents`,
`negative_incidents`, `detentions`). Item fields are the portal's labels
snake-cased, e.g. `points` ("+2"), `category`, `awarded_by`/`recorded_by`,
`behaviour`, `event`; detentions use `issued` (date) with `detention_type`,
`reason`, `session`, `room`, `attendance_mark`.

`exams`: `{cmd, students:[{…, exams_available, candidate_id, academic_year,
academic_years, period:{start,end}, exams:{<slug>:{title, rows:[{label, date?,
time?, …fields | text, description?}]}}, count}]}` — rows are passed through
generically until a school with entries has been seen.

`attendance-by-date --certificate`: each student also has
`certificate:{file, bytes} | {error}`.

## Notes for the agent
- Messages are **guardian-level** (per school, shared across children). The
  other subcommands are **per child**; children differ per school. All schools
  are processed by default; each record carries its `school` (and `student_id`).
- Side effects: `messages` with bodies marks read; `meals --set …
  --add-to-basket` changes basket meal choices (never without that flag, never
  checkout). Everything else is read-only: no command pays, tops up, signs up,
  consents or checks out, and portal-supplied links are refused if they look
  like such actions.
- Acting on the data (reminders, notifications) is the agent's job, not this
  skill's.
- Auth is a cookie session cached per school under `$ARBOR_COOKIE_DIR`
  (default `~/.cache/arbor/<school>.cookies`), reused until expiry (~24h);
  re-login is automatic.

## Not yet covered
Exam results (only the timetable list is read), the exam-timetable PDF, and
attachment downloads. Payment, top-up, sign-up, consent, basket checkout are
deliberately out of scope (the only basket write is `meals --add-to-basket`).
