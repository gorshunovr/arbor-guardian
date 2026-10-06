#!/usr/bin/env python3
"""
arbor_guardian.py — read data from the Arbor guardian/parent portal as JSON.

Subcommands (default is `messages` for backward compatibility):
  messages     School Messages: unread / by date period / all (guardian-level).
  children     List the children on the account (name + student id) per school.
  attendance   Attendance KPI(s) per child.
  timetable    Calendar events / lessons per child (optionally a date range).
  balances     Account balances per child (meals) + outstanding total.
  attendance-by-date  Per-session (AM/PM) attendance marks per child for an
               academic year (optionally a date range / non-present only).
  assignments  Homework: due / overdue / submitted lists + counts per child
               (optionally each assignment's detail page).
  meals        (experimental) Meal choices per day from the meal-menu grid, on
               schools that offer pre-ordering. `--options` lists each editable
               day's choices. Opt-in write: `--set DATE=VALUE` plans a change
               (dry run); only with `--add-to-basket` does it POST the portal's
               own process-meal-provisions form (basket only — never checkout,
               pay or top-up; confirming still needs checkout in the portal).
  invoices     Invoices, top-ups and credit notes per school (optionally per
               child / term). Read-only history; never pays or tops up.
  report-cards Report cards per child; `--download DIR` saves the PDFs.
  clubs        Clubs per child: registered / open for registration / past.
  trips        Trips per child: upcoming / open / past (+ details on request).
  shop         School-shop catalogue per child. List only: never buys or
               touches the basket.
  account-spend  Customer-account spend by week / day per child (default: the
               child's meals account); `--details` lists each day's items.
  behaviour    Behaviour points / incidents totals + events, detentions.
  exams        (experimental) Exam timetable for children who are exam
               candidates (candidate id discovered from the child's menu).

Auth is the Arbor session COOKIE only (no Bearer needed). Logs in once via
POST /auth/login, persists a per-school cookie jar, and reuses it until expiry.

Configuration comes from the environment (a `.env` file next to this script or
in the current directory is loaded automatically; real env vars win):
  ARBOR_EMAIL   guardian login email                      (required)
  ARBOR_PW      guardian password                         (required)
  ARBOR_SCHOOL  school subdomain host(s), comma-separated (optional: if unset,
                ALL schools on the account are processed; auto-discovered)
  ARBOR_COOKIE_DIR  dir for per-school cookie jars  (default: ~/.cache/arbor)
  ARBOR_CACHE_DB    SQLite cache path       (default: ~/.cache/arbor/cache.sqlite3)
  ARBOR_MIN_INTERVAL  min seconds between requests to Arbor (default: 0.5)

Caching (SQLite): message bodies are immutable, so once fetched they are cached
and served from cache forever — re-reads cost no request AND never re-mark a
message read. `--offline` serves everything from cache (no requests at all);
`--max-age SEC` reuses cached attendance/timetable/balances if fresh. Requests
are also throttled (>= ARBOR_MIN_INTERVAL apart) and back off on 429/503, so the
script stays a polite client and never bursts the portal.

Nothing about a specific account is hard-coded: credentials come from the
environment, schools are discovered from the credentials, and children are
discovered from each school's dashboard. No external dependencies (stdlib only).

NOTE: `messages` with bodies marks a message read on the server the FIRST time
its body is fetched (for all children); cached re-reads and --no-bodies do not.
`meals --set ... --add-to-basket` changes basket meal choices. Everything else
is read-only.
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from http.cookiejar import MozillaCookieJar

TIMEOUT = 30
# Politeness: keep at least this many seconds between requests to Arbor, and
# back off (rather than hammer or fail) when the portal throttles us. This
# prevents the script from creating a DoS-like burst, e.g. when fetching many
# message bodies. Tunable via $ARBOR_MIN_INTERVAL.
MIN_INTERVAL = max(0.0, float(os.environ.get("ARBOR_MIN_INTERVAL", "0.5")))
MAX_RETRIES = 4          # on 429/503
MAX_BACKOFF = 30.0       # seconds
_last_request = [0.0]

LIST_PATH = "/guardians/communication-center-ui/school-messages/?format=javascript"
VIEW_PATH = "/guardians/outbound-in-app-message-ui/view-outbound-in-app-message/id/{id}?format=javascript"
WHOAMI_PATH = "/auth/current-user-settings/format/json"
LOGIN_PATH = "/auth/login?lang=en"
SEARCH_BY_EMAIL = "https://login.arbor.sc/applications/search-by-email"
DASHBOARD_PATH = "/guardians/home-ui/dashboard?format=javascript"
KPIS_PATH = "/guardians/student/kpis/id/{id}/"
CALENDAR_PATH = "/guardians/widget-data/get-calendar-data/student-id/{id}/"
MEALS_BALANCE_PATH = "/guardians/customer-account/meals-balance-kpi/student-id/{id}"
OUTSTANDING_PATH = "/guardians/customer-account/payment-total-kpi/student-id/{id}"
ATT_BY_DATE_PATH = "/guardians/student-ui/attendance-by-date/student-id/{id}"
ATT_BY_DATE_YEAR = "/period/academic-year/academic-year-id/{year}"
ASSIGN_PATH = "/guardians/student-ui/assignments/student-id/{id}"
ASSIGN_KPI_PATH = "/guardians/student/assignments-kpi/student-id/{id}/academic-year-id/{year}"
ASSIGN_LIST_PATH = "/guardians/student-ui/assignments-{segment}/student-id/{id}/academic-year-id/{year}"
SCHOOLWORK_PATH = ("/guardians/student-ui/schoolwork-overview/schoolwork-id/{wid}/student-id/{id}"
                   "/list-segment/assignments-{segment}/academic-year-id/{year}")
ASSIGN_SEGMENTS = ("due", "overdue", "submitted")
MEAL_CHOICES_PATH = "/guardians/meal-ui/meal-choices/student-id/{id}"
MEAL_GRID_PATH = "/guardians/meal-ui/setup-meal-choices/meal-rotation-menu-id/{menu}/student-id/{id}"
MEAL_OPTIONS_PATH = ("/guardians/meal-ui/setup-meal-choice/meal-rotation-menu-id/{menu}"
                     "/student-id/{id}/date/{date}")
ACCOUNT_DASHBOARDS = {"invoices": "/guardians/customer-account-ui/invoices-dashboard",
                      "top-ups": "/guardians/customer-account-ui/top-ups-dashboard",
                      "credit-notes": "/guardians/customer-account-ui/credit-notes-dashboard"}
REPORT_CARDS_PATH = "/guardians/student-ui/report-cards/student-id/{id}"
CLUBS_PATH = "/guardians/club-ui/dashboard/student-id/{id}"
TRIPS_PATH = "/guardians/trip-ui/dashboard/student-id/{id}"
SHOP_PATH = "/guardians/school-shop-ui/dashboard/student-id/{id}"
SHOP_LIST_RE = re.compile(r"^/guardians/school-shop/list/student-id/\d+$")
ACCOUNT_DASH_PATH = "/guardians/customer-account-ui/dashboard/customer-account-id/{acct}"
ACCOUNT_DAY_RE = re.compile(r"^/guardians/customer-account-ui/view-payments-on-date/"
                            r"date/(\d{4}-\d{2}-\d{2})/customer-account-id/\d+$")
BEHAVIOUR_PATH = "/guardians/behaviour-ui/student-behaviour/id/{id}"
EXAMS_PATH = "/guardians/student-ui/examinations/candidate-id/{cid}"
EXAMS_LINK_RE = re.compile(r"^/guardians/student-ui/examinations/candidate-id/(\d+)/?$")
YEAR_SUFFIX = "/academic-year-id/{year}"
ATT_CERT_RE = re.compile(r"^/guardians/student/download-attendance-certificate/"
                         r"student-id/\d+(?:/academic-year-id/\d+)?$")
FILES_LIST_RE = re.compile(r"^/guardians/(club|trip)/list-files/(club|trip)-id/\d+/student-id/\d+$")
# Anything that could spend money or change state. The read helpers refuse to
# follow a portal-supplied URL that matches this, as a belt-and-braces guard.
UNSAFE_URL_RE = re.compile(r"(?i)basket|checkout|buy-product|top-up-by|pay-|/pay\b|"
                           r"process-|sign-?up|register-|consent|/save|/delete|/create|/update")
JS = "?format=javascript"


def log(*a):
    print(*a, file=sys.stderr)


def load_dotenv(path):
    """Minimal .env loader: KEY=VALUE lines; existing env vars are not overridden."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k, v)


# ---- HTTP / auth ------------------------------------------------------------

def build_opener(cookie_file):
    cj = MozillaCookieJar(cookie_file)
    if os.path.exists(cookie_file):
        try:
            cj.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [
        ("Accept", "application/json, text/plain, */*"),
        ("User-Agent", "arbor-guardian/1.0"),
    ]
    return opener, cj


def _open(request, opener=None, raw=False):
    """Perform a request with inter-request throttling and 429/503 backoff.
    raw=True returns (bytes, headers) instead of decoded text."""
    for attempt in range(MAX_RETRIES + 1):
        wait = MIN_INTERVAL - (time.monotonic() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        try:
            fn = opener.open if opener is not None else urllib.request.urlopen
            with fn(request, timeout=TIMEOUT) as resp:
                body = resp.read()
                if raw:
                    return body, resp.headers
                return body.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < MAX_RETRIES:
                ra = e.headers.get("Retry-After") if e.headers else None
                delay = float(ra) if (ra and str(ra).isdigit()) else 2.0 ** attempt
                delay = min(delay, MAX_BACKOFF)
                log(f"Arbor returned {e.code}; backing off {delay:.0f}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(delay)
                continue
            raise
        finally:
            _last_request[0] = time.monotonic()
    raise SystemExit("giving up after repeated 429/503 from Arbor")


def req(opener, url, data=None, content_type=None):
    headers = {}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = content_type or "application/json"
    r = urllib.request.Request(url, data=body, headers=headers,
                               method="POST" if body is not None else "GET")
    return _open(r, opener)


def req_form(opener, url, fields):
    """POST application/x-www-form-urlencoded; returns (bytes, headers)."""
    body = urllib.parse.urlencode(fields).encode("utf-8")
    r = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded"})
    return _open(r, opener, raw=True)


def req_bytes(opener, url):
    return _open(urllib.request.Request(url), opener, raw=True)


def discover_schools(email, pw):
    """Resolve the school subdomain(s) for an account from its credentials."""
    r = urllib.request.Request(
        SEARCH_BY_EMAIL,
        data=json.dumps({"email": email, "password": pw}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    j = json.loads(_open(r))
    out = []
    for p in j.get("payload", []):
        host = re.sub(r"^https?://", "", p.get("sisUrl") or "").strip("/")
        if host:
            out.append({"name": p.get("name"), "subdomain": host})
    return out


def is_logged_in(opener, base):
    try:
        j = json.loads(req(opener, base + WHOAMI_PATH))
        return bool(j.get("items", [{}])[0].get("logged_in"))
    except Exception:
        return False


def login(opener, cj, base, email, pw):
    j = json.loads(req(opener, base + LOGIN_PATH,
                       data={"items": [{"username": email, "password": pw}]}))
    if not j.get("success") or not j.get("items", [{}])[0].get("logged_in"):
        issues = j.get("items", [{}])[0].get("loginIssues")
        raise SystemExit(f"login failed for {base}: success={j.get('success')} issues={issues}")
    try:
        os.makedirs(os.path.dirname(cj.filename), exist_ok=True)
        cj.save(ignore_discard=True, ignore_expires=True)
        try:
            os.chmod(cj.filename, 0o600)
        except OSError:
            pass
    except Exception as e:
        log(f"warning: could not persist cookies: {e}")


def session(school, email, pw, cookie_dir):
    """Return (opener, base_url) for a school, logging in if needed."""
    base = "https://" + school
    opener, cj = build_opener(os.path.join(cookie_dir, school + ".cookies"))
    if not is_logged_in(opener, base):
        login(opener, cj, base, email, pw)
    return opener, base


# ---- SQLite cache -----------------------------------------------------------

def cache_open():
    path = os.path.expanduser(os.environ.get("ARBOR_CACHE_DB", "~/.cache/arbor/cache.sqlite3"))
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS message(
            school TEXT, msg_id TEXT, subject TEXT, snippet TEXT, datetime TEXT,
            received TEXT, sent_by TEXT, body TEXT, has_body INTEGER DEFAULT 0,
            unread INTEGER, first_seen TEXT, updated TEXT,
            PRIMARY KEY(school, msg_id));
        CREATE TABLE IF NOT EXISTS response(
            key TEXT PRIMARY KEY, json TEXT, fetched_at REAL);
    """)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return con


def msg_upsert_meta(con, school, m, now):
    con.execute(
        """INSERT INTO message(school,msg_id,subject,snippet,datetime,unread,first_seen,updated)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(school,msg_id) DO UPDATE SET
             subject=excluded.subject, snippet=excluded.snippet,
             datetime=excluded.datetime, unread=excluded.unread,
             updated=excluded.updated""",
        (school, m["id"], m["subject"], m["snippet"], m["datetime"],
         int(m["unread"]), now, now))


def msg_store_body(con, school, mid, body, now):
    con.execute(
        """UPDATE message SET received=?, sent_by=?, body=?,
             subject=COALESCE(?, subject), has_body=1, updated=?
           WHERE school=? AND msg_id=?""",
        (body["received"], body["sent_by"], body["body"], body["subject"],
         now, school, mid))


def msg_get_body(con, school, mid):
    r = con.execute("SELECT subject,received,sent_by,body FROM message "
                    "WHERE school=? AND msg_id=? AND has_body=1",
                    (school, mid)).fetchone()
    return dict(r) if r else None


def msg_list_cached(con, school):
    out = []
    for r in con.execute("SELECT msg_id,subject,snippet,datetime,unread "
                         "FROM message WHERE school=? "
                         "ORDER BY datetime IS NULL, datetime DESC", (school,)):
        dobj = None
        if r["datetime"]:
            try:
                dobj = dt.datetime.fromisoformat(r["datetime"]).date()
            except ValueError:
                pass
        out.append({"id": r["msg_id"], "unread": bool(r["unread"]),
                    "subject": r["subject"], "snippet": r["snippet"],
                    "datetime": r["datetime"], "date_obj": dobj})
    return out


def resp_get(con, key):
    r = con.execute("SELECT json,fetched_at FROM response WHERE key=?", (key,)).fetchone()
    return (json.loads(r["json"]), r["fetched_at"]) if r else (None, None)


def resp_store(con, key, obj):
    con.execute("INSERT INTO response(key,json,fetched_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET json=excluded.json, "
                "fetched_at=excluded.fetched_at",
                (key, json.dumps(obj, ensure_ascii=False), time.time()))
    con.commit()


# ---- component-tree parsing -------------------------------------------------

def walk(node, fn):
    if isinstance(node, dict):
        fn(node)
        for v in node.values():
            walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            walk(v, fn)


def strip_html(s):
    s = re.sub(r"(?i)<br\s*/?>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).replace("\xa0", " ")


def parse_date(raw):
    """'17 June 2026, 14:07' (possibly with NBSP) -> (iso_str, date)."""
    if not raw:
        return None, None
    s = raw.replace("\xa0", " ").strip()
    for fmt in ("%d %B %Y, %H:%M", "%d %B %Y"):
        try:
            d = dt.datetime.strptime(s, fmt)
            return d.isoformat(sep=" "), d.date()
        except ValueError:
            continue
    return s, None


def to_date(s):
    return dt.datetime.strptime(s, "%Y-%m-%d").date() if s else None


def discover_children(opener, base):
    """Children on a school's account, discovered from the dashboard.

    Multi-child schools expose an Arbor.selector.PageToggle listing every child.
    Single-child schools have no switcher, so fall back to the mis-info-panel
    (the current — and only — student's card)."""
    data = json.loads(req(opener, base + DASHBOARD_PATH))
    out, seen = [], set()

    def add(sid, name):
        if sid and sid not in seen:
            seen.add(sid)
            out.append({"student_id": sid, "name": (name or "").strip() or None})

    def toggle(n):
        if n.get("xtype") != "mis-page-toggle":
            return
        for opt in (n.get("props", {}) or {}).get("options", []) or []:
            m = re.search(r"/student-id/(\d+)", opt.get("value", "") or "")
            if m:
                add(m.group(1), opt.get("label"))

    walk(data, toggle)
    if not out:                         # single-child school: use the info panel
        def panel(n):
            if n.get("xtype") != "mis-info-panel":
                return
            p = n.get("props", {}) or {}
            m = re.search(r"/(?:overview/id|student-id)/(\d+)", p.get("url", "") or "")
            if m:
                add(m.group(1), strip_html(p.get("title", "")))
        walk(data, panel)
    return out


# ---- messages ---------------------------------------------------------------

def parse_list(raw):
    data = json.loads(raw)
    out, seen = [], set()

    def visit(n):
        if n.get("xtype") != "mis-property-row":
            return
        p = n.get("props", {})
        m = re.search(r"/id/(\d+)", p.get("url", "") or "")
        if not m or m.group(1) in seen:
            return
        seen.add(m.group(1))
        value = p.get("value", "") or ""
        unread = bool(re.search(r"·\s*NEW|\bNEW\b", value.replace("\xa0", " ")))
        parts = re.split(r"(?i)<br\s*/?>", value, maxsplit=1)
        subject = re.sub(r"·?\s*NEW\s*$", "", strip_html(parts[0])).strip()
        snippet = strip_html(parts[1]).strip() if len(parts) > 1 else ""
        iso, dobj = parse_date(p.get("description", ""))
        out.append({"id": m.group(1), "unread": unread, "subject": subject,
                    "snippet": snippet, "datetime": iso, "date_obj": dobj})

    walk(data, visit)
    return out


def parse_body(raw):
    data = json.loads(raw)
    fields, body_parts = {}, []

    def visit(n):
        xt = n.get("xtype")
        if xt == "mis-property-row":
            p = n.get("props", {})
            label = (p.get("fieldLabel") or "").strip()
            if label:
                fields[label] = strip_html(str(p.get("value", ""))).strip()
        elif xt == "mis-simple-text":
            body_parts.append(strip_html(str(n.get("content", ""))).strip())

    walk(data, visit)
    return {"subject": fields.get("Subject"), "received": fields.get("Received"),
            "sent_by": fields.get("Sent by"),
            "body": "\n\n".join([b for b in body_parts if b]) or None}


def do_messages(args, email, pw, cookie_dir, schools, con):
    now = dt.datetime.now().isoformat(sep=" ", timespec="seconds")
    since, until = to_date(args.since), to_date(args.until)

    def keep(m):
        if args.mode == "unread" and not m["unread"]:
            return False
        if since and (m["date_obj"] is None or m["date_obj"] < since):
            return False
        if until and (m["date_obj"] is None or m["date_obj"] > until):
            return False
        return True

    summaries, records = [], []
    for s in schools:
        school = s["subdomain"]
        opener = base = None
        if args.offline:
            if con is None:
                raise SystemExit("--offline needs the cache (drop --no-cache).")
            msgs = msg_list_cached(con, school)
        else:
            opener, base = session(school, email, pw, cookie_dir)
            msgs = parse_list(req(opener, base + LIST_PATH))
            if con is not None:
                for m in msgs:
                    msg_upsert_meta(con, school, m, now)
                con.commit()

        selected = [m for m in msgs if keep(m)]
        if args.limit > 0:
            selected = selected[:args.limit]

        for m in selected:
            rec = {"school": school, "id": m["id"], "unread": m["unread"],
                   "datetime": m["datetime"], "subject": m["subject"],
                   "snippet": m["snippet"]}
            if args.bodies:
                cached = msg_get_body(con, school, m["id"]) if con is not None else None
                if cached:
                    rec.update(cached)
                    rec["from_cache"] = True
                elif args.offline:
                    rec["body"] = None
                    rec["error"] = "body not cached (offline)"
                else:
                    try:
                        body = parse_body(req(opener, base + VIEW_PATH.format(id=m["id"])))
                        rec.update(body)
                        rec["from_cache"] = False
                        if con is not None:
                            msg_store_body(con, school, m["id"], body, now)
                            con.commit()
                    except urllib.error.HTTPError as e:
                        rec["error"] = f"body fetch failed: HTTP {e.code}"
            records.append(rec)

        summaries.append({"school": school, "name": s.get("name"),
                          "total_in_inbox": len(msgs), "count": len(selected)})

    return {"cmd": "messages", "mode": args.mode, "since": args.since,
            "until": args.until, "bodies": args.bodies, "offline": args.offline,
            "schools": summaries, "count": len(records), "messages": records}


# ---- per-child read features ------------------------------------------------

def _kpi_number(html_val):
    m = re.search(r"measure-value[^>]*>\s*([\d.]+)", html_val or "")
    return m.group(1) if m else None


def get_attendance(opener, base, child):
    j = json.loads(req(opener, base + KPIS_PATH.format(id=child["student_id"])))
    kpis = []
    for it in j.get("items", []):
        f = it.get("fields", {})
        html_val = f.get("html", {}).get("value", "")
        kpis.append({"title": f.get("title", {}).get("value"),
                     "value_pct": _kpi_number(html_val),
                     "text": strip_html(html_val)})
    return kpis


def get_events(opener, base, child, since, until):
    j = json.loads(req(opener, base + CALENDAR_PATH.format(id=child["student_id"])))
    events = []
    for it in j.get("items", []):
        f = it.get("fields", {})
        start = (f.get("start_datetime", {}) or {}).get("value")
        d = to_date(start.split(" ")[0]) if start else None
        if since and (d is None or d < since):
            continue
        if until and (d is None or d > until):
            continue
        events.append({"start": start,
                       "end": (f.get("end_datetime", {}) or {}).get("value"),
                       "title": (f.get("title", {}) or {}).get("value"),
                       "location": (f.get("location", {}) or {}).get("value")})
    return events


def _kpi_main_value(raw):
    try:
        arr = json.loads(raw)
        return arr[0].get("mainValue") if arr else None
    except Exception:
        return None


def get_meals_balance(opener, base, child):
    return _kpi_main_value(req(opener, base + MEALS_BALANCE_PATH.format(id=child["student_id"])))


def get_outstanding(opener, base, any_child_id):
    try:
        arr = json.loads(req(opener, base + OUTSTANDING_PATH.format(id=any_child_id)))
        if arr:
            return {"title": arr[0].get("title"), "value": arr[0].get("mainValue")}
    except Exception:
        pass
    return None


def parse_short_date(raw):
    """'06 Oct 2026' / '06\xa0Oct\xa02026' -> 'YYYY-MM-DD' (or None)."""
    s = (raw or "").replace("\xa0", " ").strip()
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def page_toggle(data, label):
    """Options of the mis-page-toggle with this fieldLabel: [{label,value,selected}]."""
    found = []

    def visit(n):
        if (n.get("xtype") == "mis-page-toggle"
                and (n.get("props", {}) or {}).get("fieldLabel") == label):
            found.extend(n["props"].get("options", []) or [])
    walk(data, visit)
    return found


def academic_years(data, label):
    """Academic-year switcher -> ([{id,label,selected}], selected_or_None)."""
    years = []
    for o in page_toggle(data, label):
        m = re.search(r"/academic-year-id/(\d+)", o.get("value", "") or "")
        if m:
            years.append({"id": m.group(1),
                          "label": re.sub(r"^Year\s+", "", o.get("label") or ""),
                          "selected": bool(o.get("selected"))})
    sel = next((y for y in years if y["selected"]), None)
    return years, sel


# Mark colours used by the attendance-by-date grid -> coarse category. The
# portal shows statutory codes (tick=present, clock=late, I, O, #, Y4, V, ...);
# colour is the portal's own grouping, so it generalises across schools.
MARK_COLOURS = {"#68aa22": "present", "#fce015": "late",
                "#f7931e": "authorised_absence", "#cb0d0d": "unauthorised",
                "#776e6a": "not_counted"}


def parse_attendance_by_date(raw):
    """Attendance register by date -> (years, selected_year, sessions)."""
    data = json.loads(raw)
    years, sel = academic_years(data, "Attendance for")
    sessions = []

    def visit(n):
        if n.get("xtype") != "mis-property-row":
            return
        p = n.get("props", {}) or {}
        m = re.match(r"(.+?)\s+(AM|PM)\s*$", (p.get("fieldLabel") or "").replace("\xa0", " "))
        if not m:
            return
        val = p.get("value", "") or ""
        desc = (p.get("description") or p.get("tooltipMIS") or "").strip()
        if "mis-icon-tick" in val:
            mark = "/"
        elif "mis-icon-clock" in val:
            mark = "L"
        else:
            mark = strip_html(val).strip() or None
        colour = re.search(r"color:\s*(#[0-9a-fA-F]{6})", val)
        colour = colour.group(1).lower() if colour else None
        if mark == "-":
            cat = "no_mark"
        else:
            cat = MARK_COLOURS.get(colour, "other")
        if mark == "L" and cat == "unauthorised":
            mark = "U"                 # late after register closed: red clock, code U
        sessions.append({"date": parse_short_date(m.group(1)), "session": m.group(2),
                         "mark": mark, "description": re.sub(r"\s+(AM|PM)$", "", desc),
                         "category": cat})

    walk(data, visit)
    sessions.sort(key=lambda r: (r["date"] or "", r["session"]))
    return years, sel, sessions


def download_attendance_certificate(opener, base, raw, sid, dest_dir):
    """Save the 'Attendance Certificate' PDF offered on the attendance-by-date
    page (Arbor.button.DownloadFile: a plain GET of its pageUrl plus a
    download-token). Only URLs matching ATT_CERT_RE are followed."""
    urls = []

    def visit(n):
        if n.get("xtype") == "mis-button-download-file":
            u = (n.get("props", {}) or {}).get("pageUrl") or ""
            if ATT_CERT_RE.match(u):
                urls.append(u)
    walk(json.loads(raw), visit)
    if not urls:
        return {"error": "no attendance-certificate button on the page"}
    u = urls[0]
    body, hdr = req_bytes(opener, base + u + "?download-token=" + _download_token())
    if not body.startswith(b"%PDF"):
        return {"error": f"response is not a PDF ({hdr.get('Content-Type')}, {len(body)} bytes)"}
    m = re.search(r"/academic-year-id/(\d+)", u)
    name = f"attendance-certificate-{sid}" + (f"-{m.group(1)}" if m else "") + ".pdf"
    dest = os.path.join(dest_dir, name)
    os.makedirs(dest_dir, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(body)
    return {"file": dest, "bytes": len(body)}


def get_attendance_by_date(opener, base, child, year, since, until, non_present,
                           certificate_dir=None):
    path = ATT_BY_DATE_PATH.format(id=child["student_id"])
    if year:
        path += ATT_BY_DATE_YEAR.format(year=year)
    raw = req(opener, base + path + JS)
    years, sel, sessions = parse_attendance_by_date(raw)
    cert = None
    if certificate_dir:
        try:
            cert = download_attendance_certificate(opener, base, raw, child["student_id"],
                                                   certificate_dir)
        except (urllib.error.HTTPError, ValueError) as e:
            cert = {"error": str(e)}
    if since:
        sessions = [r for r in sessions if r["date"] and r["date"] >= since.isoformat()]
    if until:
        sessions = [r for r in sessions if r["date"] and r["date"] <= until.isoformat()]
    summary = {}
    for r in sessions:
        summary[r["category"]] = summary.get(r["category"], 0) + 1
    if non_present:
        sessions = [r for r in sessions if r["category"] != "present"]
    return {"academic_year": ({"id": sel["id"], "label": sel["label"]} if sel
                              else ({"id": str(year), "label": None} if year else None)),
            "academic_years": [{"id": y["id"], "label": y["label"]} for y in years],
            "summary": summary, "sessions": sessions,
            **({"certificate": cert} if certificate_dir else {})}


def parse_assignment_rows(raw):
    """assignments-{due,overdue,submitted} list -> [{id,class,title,due,status}].

    Rows are mis-property-rows whose value is '<b>CLASS: Title</b> (Due DD Mon YYYY)'
    and whose description is the submission status. All rows are in the payload
    (hiddenRowsCount only collapses them in the UI)."""
    data = json.loads(raw)
    out, seen = [], set()

    def visit(n):
        if n.get("xtype") != "mis-property-row":
            return
        p = n.get("props", {}) or {}
        m = re.search(r"/schoolwork-id/(\d+)", p.get("url", "") or "")
        if not m or m.group(1) in seen:
            return
        seen.add(m.group(1))
        val = p.get("value", "") or ""
        b = re.search(r"<b>(.*?)</b>", val, re.S)
        head = re.sub(r"\s+", " ", strip_html(b.group(1) if b else val)).strip()
        due = re.search(r"\(Due\s+([^)]+)\)", strip_html(val))
        cm = re.match(r"(\S+):\s+(.*)$", head)     # "10R/Be1: Title" (class has no spaces)
        cls, title = (cm.group(1), cm.group(2)) if cm else (None, head)
        out.append({"id": m.group(1), "class": cls, "title": title.strip(),
                    "due": parse_short_date(due.group(1)) if due else None,
                    "status": strip_html(p.get("description") or "").strip() or None})

    walk(data, visit)
    return out


def parse_schoolwork(raw):
    """schoolwork-overview detail page -> {title,due,course,marking,status,
    submission_type,instructions,attachments:[name]} (attachment URLs carry
    auth tokens and are deliberately dropped)."""
    data = json.loads(raw)
    fields = {}

    def visit(n):
        if n.get("xtype") != "mis-property-row":
            return
        p = n.get("props", {}) or {}
        label = (p.get("fieldLabel") or "").strip()
        if label and label not in fields:
            fields[label] = str(p.get("value", "") or "")
    walk(data, visit)
    att = [strip_html(a).strip() for a in
           re.findall(r"<a\b[^>]*>(.*?)</a>", fields.get("Attachments", ""), re.S)]

    def txt(k):
        v = re.sub(r"[ \t]*\n[ \t]*", "\n", strip_html(fields.get(k, ""))).strip()
        return re.sub(r"\n{3,}", "\n\n", v) or None
    return {"title": txt("Title"), "due": parse_short_date(txt("Due")),
            "course": txt("Course"), "marking": txt("Marking"), "status": txt("Status"),
            "submission_type": txt("Submission Type"),
            "instructions": txt("Instructions"), "attachments": [a for a in att if a]}


def get_assignments(opener, base, child, year, segments, details):
    sid = child["student_id"]
    years = []
    if not year:                       # discover the selected (current) academic year
        years, sel = academic_years(
            json.loads(req(opener, base + ASSIGN_PATH.format(id=sid) + JS)), "Academic year")
        year = sel["id"] if sel else None
        if not year:
            return {"academic_year": None, "error": "no academic year found"}
    label = next((y["label"] for y in years if y["id"] == str(year)), None)
    counts = {}
    try:
        for k in json.loads(req(opener, base + ASSIGN_KPI_PATH.format(id=sid, year=year))):
            seg = re.search(r"assignments-(\w+)/", k.get("url", "") or "")
            if seg:
                counts[seg.group(1)] = int(k["mainValue"]) if str(k.get("mainValue", "")).isdigit() \
                    else k.get("mainValue")
    except Exception as e:
        log(f"assignments KPI failed for {sid}: {e}")
    rec = {"academic_year": {"id": str(year), "label": label}, "counts": counts}
    for seg in segments:
        rows = parse_assignment_rows(req(opener, base + ASSIGN_LIST_PATH.format(
            segment=seg, id=sid, year=year) + JS))
        if details:
            for r in rows:
                try:
                    d = parse_schoolwork(req(opener, base + SCHOOLWORK_PATH.format(
                        wid=r["id"], id=sid, segment=seg, year=year) + JS))
                    r.update({k: d[k] for k in ("course", "marking", "submission_type",
                                                "instructions", "attachments")})
                    r["status"] = d["status"] or r["status"]
                except urllib.error.HTTPError as e:
                    r["error"] = f"detail fetch failed: HTTP {e.code}"
        rec[seg] = rows
    return rec


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def parse_meal_menus(raw):
    """meal-choices page -> [{menu_id, period, availability}]."""
    out = []

    def visit(n):
        if n.get("xtype") != "mis-property-row":
            return
        p = n.get("props", {}) or {}
        m = re.search(r"/meal-rotation-menu-id/(\d+)", p.get("url", "") or "")
        if m:
            out.append({"menu_id": m.group(1),
                        "period": strip_html(p.get("fieldLabel") or "").strip() or None,
                        "availability": strip_html(str(p.get("value") or "")).strip() or None})
    walk(json.loads(raw), visit)
    return out


def parse_meal_grid(raw):
    """setup-meal-choices grid -> (title, basket_pending, days).

    Sections are 'Week beginning DD Mon YYYY'; rows are weekday -> 'N Dish -
    Pudding' with a status description ('In basket', 'Deadline passed', ...).
    The leading number's meaning is not yet known; it is kept as `code`."""
    data = json.loads(raw)
    title, pending, days = [None], [False], []

    def visit(n, week=None):
        if not isinstance(n, (dict, list)):
            return
        if isinstance(n, list):
            for v in n:
                visit(v, week)
            return
        xt = n.get("xtype")
        p = n.get("props", {}) or {}
        if xt == "mis-layoutcolumn" and not title[0]:
            title[0] = ((p.get("columnTitle") or {}).get("title"))
        elif xt == "mis-banner" and "basket" in (p.get("title") or "").lower():
            pending[0] = True
        elif xt == "mis-section":
            m = re.search(r"Week beginning\s+(.+)$", (p.get("title") or "").replace("\xa0", " "))
            if m:
                week = parse_short_date(m.group(1))
        elif xt == "mis-property-row":
            wd = (p.get("fieldLabel") or "").strip()
            m = re.search(r"/date/(\d{4}-\d{2}-\d{2})", p.get("url", "") or "")
            date = m.group(1) if m else None
            if not date and week and wd in WEEKDAYS:
                date = (dt.date.fromisoformat(week)
                        + dt.timedelta(days=WEEKDAYS.index(wd))).isoformat()
            val = strip_html(str(p.get("value") or "")).strip()
            cm = re.match(r"(\d+)\s+(.*)$", val)
            days.append({"date": date, "weekday": wd or None,
                         "code": cm.group(1) if cm else None,
                         "meal": (cm.group(2) if cm else val) or None,
                         "status": strip_html(p.get("description") or "").strip() or None,
                         "editable": bool(m)})
        for k, v in n.items():
            if k != "props":
                visit(v, week)

    visit(data)
    return title[0], pending[0], days


def get_meals(opener, base, child, since, until):
    sid = child["student_id"]
    try:
        menus = parse_meal_menus(req(opener, base + MEAL_CHOICES_PATH.format(id=sid) + JS))
    except urllib.error.HTTPError as e:
        return {"meal_choices_available": False, "note": f"no meal-choice UI (HTTP {e.code})",
                "menus": []}
    for mn in menus:
        t, pending, days = parse_meal_grid(req(opener, base + MEAL_GRID_PATH.format(
            menu=mn["menu_id"], id=sid) + JS))
        if since:
            days = [d for d in days if d["date"] and d["date"] >= since.isoformat()]
        if until:
            days = [d for d in days if d["date"] and d["date"] <= until.isoformat()]
        mn.update({"title": t, "basket_pending": pending, "days": days})
    return {"meal_choices_available": bool(menus), "menus": menus}


# ---- meals: per-day options and (opt-in) basket provisions ------------------
#
# The per-day slideover (setup-meal-choice/.../date/D) is a form: hidden
# `student` + `is_mobile_app`, one TagField `meal_provision_<unix>` per date in
# the slideover's range, and a "Process" FormAction whose actionUrl is
# /guardians/basket/process-meal-provisions/... . The portal JS
# (Mis.application.Form.submitMisForm, mode "form") posts every field mapped to
# the action's formActionName as JSON {"fields": {name: {"value": v}}} to
# actionUrl + "/?format=json"; a hidden field submits its raw string, a
# single-select TagField its value ("100_01") or null for "No choice selected".
# `basketItems` belongs to a different form action (delete-meal-provisions) and
# is NOT part of the Process body. Nothing here ever checks out or pays.

def _submit_value(field):
    """Mirror the portal's getSubmitValue for the field kinds in the slideover."""
    if field["kind"] == "tag":
        sel = [o["value"] for o in field["options"] if o["selected"]]
        v = sel[0] if sel else None
        return v if v not in ("", None) else None
    return field["value"]


def parse_meal_slideover(raw):
    """setup-meal-choice slideover -> {action_url, form_action, deadline_note,
    basket_items, fields:[...], days:[...]} (field names discovered, not assumed)."""
    data = json.loads(raw)
    action, fields, notes, basket = [None], [], [], []

    def visit(n):
        xt = n.get("xtype")
        p = n.get("props", {}) or {}
        if xt == "mis-button-form-action" and "process-meal-provisions" in str(
                (p.get("currentAction") or {}).get("actionUrl") or ""):
            action[0] = p["currentAction"]
        elif xt == "mis-simple-text" and isinstance(n.get("content"), str):
            notes.append(html_text(n["content"]))
        elif xt in ("hidden", "mis-tagfield") and p.get("name"):
            maps = list((p.get("actionMappings") or {}).keys())
            if p["name"] == "basketItems":
                try:
                    basket.extend(x.get("value") for x in json.loads(p.get("value") or "[]"))
                except (ValueError, AttributeError):
                    pass
            f = {"name": p["name"], "mappings": maps,
                 "kind": "tag" if xt == "mis-tagfield" else "hidden"}
            if xt == "mis-tagfield":
                f["label"] = (p.get("fieldLabel") or "").replace("\xa0", " ").strip()
                f["editable"] = bool(p.get("editable", True)) and not p.get("readOnly") \
                    and not p.get("disabled")
                f["options"] = []
                for o in p.get("options") or []:
                    of = o.get("fields", {}) or {}
                    f["options"].append({
                        "value": (of.get("value") or {}).get("value"),
                        "label": html_text((of.get("label") or {}).get("value") or ""),
                        "selected": bool((of.get("selected") or {}).get("value"))})
            else:
                f["value"] = p.get("value")
            fields.append(f)

    walk(data, visit)
    act = action[0] or {}
    fan = act.get("formActionName")
    form_fields = [f for f in fields if fan and fan in f["mappings"]]
    days = []
    for f in form_fields:
        if f["kind"] != "tag":
            continue
        date = parse_short_date(re.sub(r"^[A-Za-z]+,\s*", "", f["label"]))
        m = re.match(r"meal_provision_(\d+)$", f["name"])
        if not date and m:   # unix midnight, school-local
            date = dt.datetime.fromtimestamp(int(m.group(1))).date().isoformat()
        opts = []
        for o in f["options"]:
            lm = re.match(r"(\d+)\s+(.*?)(?:\s+-\s+(£[\d.]+))?$", o["label"])
            pm = re.search(r"\s+-\s+(£[\d.]+)$", o["label"])
            opts.append({"value": o["value"] or None, "label": o["label"],
                         "code": lm.group(1) if lm else None,
                         "meal": (lm.group(2) if lm else re.sub(r"\s+-\s+£[\d.]+$", "",
                                                               o["label"])) or None,
                         "price": pm.group(1) if pm else None,
                         "selected": o["selected"]})
        sel = next((o for o in opts if o["selected"]), None)
        days.append({"date": date, "field": f["name"], "editable": f["editable"],
                     "selected": sel["value"] if sel else None,
                     "selected_label": sel["label"] if sel else None,
                     "options": opts})
    return {"action_url": act.get("actionUrl"), "form_action": fan,
            "deadline_note": next((t for t in notes if "deadline" in t.lower()), None),
            "basket_items": len(basket), "fields": form_fields, "days": days}


PROCESS_MEALS_RE = re.compile(r"^/guardians/basket/process-meal-provisions/meal-rotation-menu-id/\d+"
                              r"/start-date/\d{4}-\d{2}-\d{2}/end-date/\d{4}-\d{2}-\d{2}/?$")
FORBIDDEN_WRITE_RE = re.compile(r"(?i)checkout|my-basket|/pay|pay-|top-up|topup|buy|"
                                r"card|stripe|delete|remove")


def meal_process_path(action_url):
    """The ONLY write this tool can make. Anything else is refused."""
    u = action_url or ""
    if not PROCESS_MEALS_RE.match(u) or FORBIDDEN_WRITE_RE.search(u):
        raise SystemExit(f"refusing write to non-meal-process URL: {u!r}")
    return u.rstrip("/") + "/?format=json"


def _menus_for(opener, base, child, menu_id):
    menus = parse_meal_menus(req(opener, base + MEAL_CHOICES_PATH.format(id=child["student_id"]) + JS))
    if menu_id:
        menus = [m for m in menus if m["menu_id"] == str(menu_id)]
    return menus


def get_meal_slideover(opener, base, menu, sid, date):
    return parse_meal_slideover(req(opener, base + MEAL_OPTIONS_PATH.format(
        menu=menu, id=sid, date=date) + JS))


def _public_days(so):
    return [{k: d[k] for k in ("date", "editable", "selected", "selected_label", "options")}
            for d in so["days"]]


def get_meal_options(opener, base, child, since, until, menu_id):
    """Per-day options for editable days in range. One GET per slideover (a
    slideover can cover several days, so covered dates are skipped)."""
    sid = child["student_id"]
    try:
        menus = _menus_for(opener, base, child, menu_id)
    except urllib.error.HTTPError as e:
        return {"meal_choices_available": False, "note": f"no meal-choice UI (HTTP {e.code})",
                "menus": []}
    for mn in menus:
        if since and until and since == until:
            dates = [since.isoformat()]
        else:
            _, _, grid = parse_meal_grid(req(opener, base + MEAL_GRID_PATH.format(
                menu=mn["menu_id"], id=sid) + JS))
            dates = sorted({d["date"] for d in grid if d["editable"] and d["date"]
                            and (not since or d["date"] >= since.isoformat())
                            and (not until or d["date"] <= until.isoformat())})
        seen, days, note = set(), [], None
        for d in dates:
            if d in seen:
                continue
            so = get_meal_slideover(opener, base, mn["menu_id"], sid, d)
            note = note or so["deadline_note"]
            for day in _public_days(so):
                if day["date"] in seen:
                    continue
                seen.add(day["date"])
                if ((not since or day["date"] >= since.isoformat())
                        and (not until or day["date"] <= until.isoformat())):
                    days.append(day)
        mn.update({"deadline_note": note, "options": sorted(days, key=lambda x: x["date"] or "")})
    return {"meal_choices_available": bool(menus), "menus": menus}


def _notification_text(resp):
    out = []
    for n in resp.get("notifications") or []:
        if isinstance(n, dict):
            t = " ".join(html_text(str(n.get(k) or "")) for k in ("title", "message", "text")
                         if n.get(k))
            out.append(t.strip() or html_text(json.dumps(n, ensure_ascii=False)))
        else:
            out.append(html_text(str(n)))
    return out


def set_meal_provisions(opener, base, child, sets, menu_id, commit):
    """Apply {date: value|None} via the slideover's own Process form.

    Without commit: dry run (shows before/after + the exact body; no POST).
    With commit: POST process-meal-provisions only (adds/changes BASKET items;
    confirmation still needs portal checkout), then re-read and verify."""
    sid = child["student_id"]
    menus = _menus_for(opener, base, child, menu_id)
    if len(menus) != 1:
        raise SystemExit(f"--set needs exactly one meal menu (found {[m['menu_id'] for m in menus]}); "
                         "pass --menu-id")
    menu = menus[0]["menu_id"]
    pending = dict(sets)
    results = []
    while pending:
        first = min(pending)
        so = get_meal_slideover(opener, base, menu, sid, first)
        url = meal_process_path(so["action_url"])
        body = {f["name"]: _submit_value(f) for f in so["fields"]}
        changes = []
        for day in so["days"]:
            if day["date"] not in pending:
                continue
            want = pending.pop(day["date"])
            valid = {o["value"] for o in day["options"] if o["value"]}
            if want is not None and want not in valid:
                raise SystemExit(f"{day['date']}: {want!r} is not an option "
                                 f"(valid: {sorted(valid)} or 'none')")
            if not day["editable"]:
                raise SystemExit(f"{day['date']}: not editable (deadline passed?)")
            lab = next((o["label"] for o in day["options"] if o["value"] == want),
                       "No choice selected")
            changes.append({"date": day["date"], "before": day["selected"],
                            "before_label": day["selected_label"], "after": want,
                            "after_label": lab})
            body[day["field"]] = want
        if first in pending:
            raise SystemExit(f"{first}: no meal choice for this date in menu {menu} "
                             "(holiday, deadline passed, or outside the menu range)")
        payload = {"fields": {k: {"value": v} for k, v in body.items()}}
        rec = {"menu_id": menu, "process_url": url.split("?")[0].rstrip("/"), "changes": changes}
        if not any(c["before"] != c["after"] for c in changes):
            rec["status"] = "unchanged (no POST)"
        elif not commit:
            rec.update({"status": "dry-run (pass --add-to-basket to POST)", "body": payload})
        else:
            resp = json.loads(req(opener, base + url, data=payload))
            rec["success"] = bool(resp.get("success"))
            rec["notifications"] = _notification_text(resp)
            errs = (resp.get("action_params") or {}).get("validation_errors")
            if errs:
                rec["validation_errors"] = errs
            after = get_meal_slideover(opener, base, menu, sid, first)
            now = {d["date"]: d["selected"] for d in after["days"]}
            rec["verified"] = all(now.get(c["date"]) == c["after"] for c in changes)
            rec["status"] = "basket updated" if rec["verified"] else "NOT verified"
        results.append(rec)
    return {"meal_updates": results,
            "note": ("Basket only: choices are NOT confirmed until checkout in the portal "
                     "(this tool never checks out or pays).")}


# ---- generic helpers for display-only pages ---------------------------------

def html_text(s):
    """HTML fragment -> clean text; block boundaries become newlines."""
    s = re.sub(r"(?i)</(div|p|li|tr)>|<br\s*/?>", "\n", s or "")
    t = strip_html(s)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r",\n", ", ", t)
    return re.sub(r"\n{2,}", "\n", t).strip()


def labeled_fields(value_html):
    """'<b>Label:</b> value …' blocks -> (fields{label: text}, leading_text).

    Only bold text ending in ':' counts as a label, so values that contain
    colons ('Fridays: 15:15 - 16:10') are left alone."""
    parts = re.split(r"<b>\s*([^<]{1,60}?)\s*:\s*</b>", value_html or "")
    lead = html_text(parts[0]) or None
    fields = {}
    for i in range(1, len(parts) - 1, 2):
        fields[html_text(parts[i])] = html_text(parts[i + 1]) or None
    return fields, lead


def safe_path(path):
    if not path or UNSAFE_URL_RE.search(path):
        raise ValueError(f"refusing non-read URL: {path!r}")
    return path


def parse_detail_page(raw):
    """A display-only overview page -> {title, notices:[text],
    sections:{title:{label:text}}, schedules:{title:[text]}}. Drops forms,
    attachment widgets and the student info panel (its picture URL carries an
    auth token)."""
    data = json.loads(raw)
    out = {"title": None, "notices": [], "sections": {}, "schedules": {}}

    def visit(n, sec=None):
        if isinstance(n, list):
            for v in n:
                visit(v, sec)
            return
        if not isinstance(n, dict):
            return
        xt = n.get("xtype")
        p = n.get("props", {}) or {}
        if xt in ("mis-info-panel", "mis-subnavcolumn", "mis-widget-attachments",
                  "mis-actionpanel", "hidden", "mis-number"):
            return
        if xt == "mis-layoutcolumn" and not out["title"]:
            out["title"] = (p.get("columnTitle") or {}).get("title")
        elif xt == "mis-section":
            sec = (p.get("title") or "").strip() or "_"
            tree = p.get("treeData")
            if isinstance(tree, dict):
                lines = []

                def leaves(t):
                    for c in t.get("children", []) or []:
                        if c.get("leaf"):
                            f = c.get("fields", {})
                            txt = html_text((f.get("text") or {}).get("value"))
                            d = html_text((f.get("description") or {}).get("value"))
                            lines.append(txt + (f" ({d})" if d else ""))
                        else:
                            leaves(c)
                leaves(tree)
                if lines:
                    out["schedules"][sec] = lines
        elif xt == "mis-simple-text" and isinstance(n.get("content"), str):
            t = html_text(n["content"])
            if t:
                out["notices"].append(t)
        elif xt == "mis-property-row":
            label = html_text(p.get("fieldLabel") or "") or "_"
            raw_v = str(p.get("value") or "")
            f, lead = labeled_fields(raw_v)
            if f:                      # '<b>Status:</b> … <b>Dates:</b> …' blocks
                val = dict(f, **({"_text": lead} if lead else {}))
            else:
                val = html_text(raw_v) or None
            out["sections"].setdefault(sec or "_", {})[label] = val
        for k, v in n.items():
            if k not in ("props", "subNav"):
                visit(v, sec)

    visit(data.get("content", data))
    return out


def section_rows(data, id_re):
    """[(section_title, row_props, id)] for mis-property-rows whose url matches id_re."""
    out = []

    def visit(n, sec=None):
        if isinstance(n, list):
            for v in n:
                visit(v, sec)
            return
        if not isinstance(n, dict):
            return
        xt = n.get("xtype")
        p = n.get("props", {}) or {}
        if xt == "mis-subnavcolumn":
            return
        if xt == "mis-section":
            sec = strip_html(p.get("title") or "").strip()
        elif xt == "mis-property-row":
            m = re.search(id_re, p.get("url", "") or "")
            if m:
                out.append((sec, p, m.group(1)))
        for k, v in n.items():
            if k != "props":
                visit(v, sec)
    visit(data.get("content", data))
    return out


def toggle_ids(data, label, key):
    """Page-toggle options -> [{id,label,selected}] matched by key/ID in the value."""
    out = []
    for o in page_toggle(data, label):
        m = re.search(r"/%s/(\d+)" % re.escape(key), o.get("value", "") or "")
        if m:
            out.append({"id": m.group(1), "label": o.get("label"),
                        "selected": bool(o.get("selected"))})
    return out


def _year_of(title):
    m = re.search(r"(\d{4}/\d{4})", title or "")
    return m.group(1) if m else None


# ---- invoices / top-ups / credit notes --------------------------------------

def parse_account_rows(raw, kind):
    """Rows of an invoices / top-ups / credit-notes dashboard."""
    data = json.loads(raw)
    rows = []

    def visit(n):
        if n.get("xtype") != "mis-property-row":
            return
        p = n.get("props", {}) or {}
        date = parse_short_date(p.get("fieldLabel"))
        if not date:
            return
        val = str(p.get("value") or "")
        if kind == "invoices":
            f, lead = labeled_fields(val)
            rec = {"date": date, "account": f.pop("Account", None),
                   "amount": f.pop("Amount", None), "items": f.pop("Items", None),
                   "status": f.pop("Status", None)}
        else:
            # top-ups / credit notes: 'Account: X' line, a bare money line, a note
            f, rec = {}, {"date": date, "account": None, "amount": None}
            rest = []
            for ln in html_text(val).split("\n"):
                if ln.startswith("Account:") and rec["account"] is None:
                    rec["account"] = ln.split(":", 1)[1].strip() or None
                elif rec["amount"] is None and re.match(r"^-?£\s?-?[\d,]+\.\d{2}$", ln):
                    rec["amount"] = ln
                elif ":" in ln and ln.split(":", 1)[0].strip() in ("Amount", "Status", "Reason"):
                    k, v = ln.split(":", 1)
                    f[k.strip()] = v.strip() or None
                elif ln:
                    rest.append(ln)
            rec["note"] = "\n".join(rest) or None
            rec["method"] = strip_html(p.get("description") or "").strip() or None
        rec.update({k.lower().replace(" ", "_"): v for k, v in f.items()})
        rows.append(rec)

    walk(data, visit)
    terms = toggle_ids(data, "Term", "term-id")
    accounts = [a for a in toggle_ids(data, "Account", "customer-account-id")]
    return rows, terms, accounts


def compute_invoices(args, email, pw, cookie_dir, schools, want):
    kinds = args.kind or list(ACCOUNT_DASHBOARDS)
    out = []
    for s in schools:
        opener, base = session(s["subdomain"], email, pw, cookie_dir)
        scopes = [None]
        if want:                       # per-child view (server-side filter)
            scopes = [c for c in discover_children(opener, base) if c["student_id"] in want]
            if not scopes:
                continue
        for c in scopes:
            rec = {"school": s["subdomain"]}
            if c:
                rec.update({"student_id": c["student_id"], "name": c["name"]})
            for kind in kinds:
                path = ACCOUNT_DASHBOARDS[kind]
                if c:
                    path += f"/student-id/{c['student_id']}"
                if args.term_id:
                    path += f"/term-id/{args.term_id}"
                rows, terms, accounts = parse_account_rows(req(opener, base + path + JS), kind)
                if "term" not in rec:
                    sel = next((t for t in terms if t["selected"]), None)
                    rec["term"] = ({"id": sel["id"], "label": sel["label"]} if sel
                                   else ({"id": str(args.term_id), "label": None}
                                         if args.term_id else None))
                    rec["terms"] = [{"id": t["id"], "label": t["label"]} for t in terms]
                if accounts and "accounts" not in rec:
                    rec["accounts"] = [{"id": a["id"], "label": a["label"]} for a in accounts]
                rec[kind.replace("-", "_")] = rows
            out.append(rec)
    return {"cmd": "invoices", "schools": out}


# ---- report cards / clubs / trips / shop ------------------------------------

RC_DOWNLOAD_RE = re.compile(r"^/guardians/student/download-student-report-card/format/pdf$")
RC_CUSTOM_DOWNLOAD_RE = re.compile(r"^/custom-report-card-student/download/"
                                   r"custom-report-card-student-id/\d+$")


def _download_token():
    return "downloadtoken" + os.urandom(6).hex()


def download_report_card(opener, base, view_url, dest):
    """Download one report-card PDF the way the portal's Download button does.

    Standard cards (Mis.button.FormDownload, mode "download"): form POST to
    /guardians/student/download-student-report-card/format/pdf with
    payload=JSON {"fields": {"student_report_card": {"value": <hidden obj>}}}
    and a download-token. Custom cards (FormAction, mode "form"): JSON POST
    {"fields": {}} to the action + "/?format=json", whose response carries
    action_params.downloadUrl, then GET that. Derived from the portal JS, not a
    browser capture: treat as experimental. Only these two URL shapes are used."""
    data = json.loads(req(opener, base + safe_path(view_url) + JS))
    acts, hidden = [], []

    def visit(n):
        p = n.get("props", {}) or {}
        if n.get("xtype") in ("mis-button-form-download", "mis-button-form-action"):
            a = p.get("currentAction") or {}
            if a.get("actionUrl"):
                acts.append((n["xtype"], a))
        elif n.get("xtype") == "hidden" and p.get("name"):
            hidden.append(p)
    walk(data, visit)
    token = _download_token()
    for xt, a in acts:
        u, fan = a["actionUrl"], a.get("formActionName")
        if xt == "mis-button-form-download" and RC_DOWNLOAD_RE.match(u):
            fields = {h["name"]: {"value": h.get("value")} for h in hidden
                      if fan in (h.get("actionMappings") or {})}
            body, hdr = req_form(opener, base + u, {
                "payload": json.dumps({"fields": fields}, ensure_ascii=False),
                "download-token": token})
            break
        if xt == "mis-button-form-action" and RC_CUSTOM_DOWNLOAD_RE.match(u):
            r = json.loads(req(opener, base + u + "/?format=json", data={"fields": {}}))
            durl = ((r.get("action_params") or {}).get("downloadUrl")) or ""
            durl = re.sub(r"^https?://[^/]+", "", durl)
            if not durl.startswith("/"):
                return {"error": "no downloadUrl in response"}
            sep = "&" if "?" in durl else "?"
            body, hdr = req_bytes(opener, base + safe_path(durl) + sep + "download-token=" + token)
            break
    else:
        return {"error": "no recognised Download action on the card page"}
    if not body.startswith(b"%PDF"):
        return {"error": f"response is not a PDF ({hdr.get('Content-Type')}, {len(body)} bytes)"}
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as f:
        f.write(body)
    return {"file": dest, "bytes": len(body)}


def get_report_cards(opener, base, child, download_dir=None, card_ids=None):
    data = json.loads(req(opener, base + REPORT_CARDS_PATH.format(id=child["student_id"]) + JS))
    cards = []
    for sec, p, rid in section_rows(data, r"/view-student-(?:custom-)?report-card/id/(\d+)"):
        kind = "custom" if "custom-report-card" in p.get("url", "") else "standard"
        rec = {"id": rid, "kind": kind,
               "academic_year": _year_of(sec),
               "date": parse_short_date(p.get("fieldLabel")),
               "title": html_text(str(p.get("value") or "")) or None}
        if download_dir and (not card_ids or rid in card_ids):
            dest = os.path.join(download_dir, f"report-card-{child['student_id']}-{kind}-{rid}.pdf")
            try:
                rec["download"] = download_report_card(opener, base, p["url"], dest)
            except (urllib.error.HTTPError, ValueError) as e:
                rec["download"] = {"error": str(e)}
        cards.append(rec)
    return {"report_cards": cards}


def _club_group(title):
    t = (title or "").lower()
    if "can be registered" in t:
        return "available"
    if " was registered" in t:
        return "past"
    if "is registered" in t:
        return "registered"
    return None


def get_clubs(opener, base, child, details, files=False):
    data = json.loads(req(opener, base + CLUBS_PATH.format(id=child["student_id"]) + JS))
    clubs = []
    for sec, p, cid in section_rows(data, r"/club-id/(\d+)"):
        f, lead = labeled_fields(str(p.get("value") or ""))
        name = html_text(p.get("fieldLabel") or "")
        dates = f.pop("Club membership dates", None)
        tt = f.pop("Timetable", None)
        rec = {"club_id": cid, "name": re.sub(r"\s*\(\d{4}/\d{4}\)$", "", name) or None,
               "group": _club_group(sec), "academic_year": _year_of(name) or _year_of(sec),
               "wraparound": "wraparound" in (p.get("url") or ""),
               "description": f.pop("Club description", None) or lead,
               "membership_dates": ([x.strip() for x in re.split(r",\s*", dates) if x.strip()]
                                    if dates else []),
               "timetable": tt.split("\n") if tt else []}
        rec.update({k.lower().replace(" ", "_"): v for k, v in f.items()})
        if details or files:
            rec.update(overview_extras(opener, base, p["url"], details, files))
        clubs.append(rec)
    return {"clubs": clubs}


def _trip_group(title):
    t = (title or "").lower()
    return ("upcoming" if "upcoming" in t else "open" if "open to" in t
            else "past" if "past" in t else None)


def get_trips(opener, base, child, details, files=False):
    data = json.loads(req(opener, base + TRIPS_PATH.format(id=child["student_id"]) + JS))
    trips = []
    for sec, p, tid in section_rows(data, r"/trip-id/(\d+)"):
        f, lead = labeled_fields(str(p.get("value") or ""))
        rec = {"trip_id": tid, "name": html_text(p.get("fieldLabel") or "") or None,
               "group": _trip_group(sec),
               "dates": f.pop("Trip Date(s)", None) or lead,
               "signup_window": f.pop("Signup Window", None)}
        rec.update({k.lower().replace(" ", "_"): v for k, v in f.items()})
        if details or files:
            rec.update(overview_extras(opener, base, p["url"], details, files))
        trips.append(rec)
    return {"trips": trips}


def get_shop(opener, base, child, details):
    """Catalogue only. The product list is an Ext data-store read (POST with
    paging params, no side effects); its URL is taken from the page and must
    match SHOP_LIST_RE. Product pages are GET-only; their 'Buy product' form
    is never submitted."""
    sid = child["student_id"]
    page = json.loads(req(opener, base + SHOP_PATH.format(id=sid) + JS))
    url = None
    for st in page.get("stores", []) or []:
        u = (((st.get("proxy") or {}).get("api") or {}).get("read")) or ""
        if SHOP_LIST_RE.match(u):
            url = u
    if not url:
        return {"products": [], "note": "no shop catalogue on this page"}
    j = json.loads(req(opener, base + url, data={"page": 1, "start": 0, "limit": 200}))
    products = []
    for it in j.get("items", []) or []:
        f = it.get("fields", {}) or {}
        view = ((it.get("actions") or {}).get("view")) or ""
        m = re.search(r"/product-id/(\d+)", view)
        rec = {"product_id": m.group(1) if m else None,
               "name": (f.get("name") or {}).get("value"),
               "price": (f.get("price") or {}).get("value")}
        if details and view:
            try:
                d = parse_detail_page(req(opener, base + safe_path(view) + JS))
                pd = d["sections"].get("Product Details", {})
                rec.update({"description": pd.get("Description"),
                            "notices": d["notices"]})
            except (urllib.error.HTTPError, ValueError) as e:
                rec["details_error"] = str(e)
        products.append(rec)
    return {"products": products}


# ---- attachment lists (clubs / trips) ---------------------------------------

_FILE_FIELD_SKIP = re.compile(r"(?i)url|link|href|token|download|src|path|hash|key")


def list_attachments(opener, base, page):
    """File list of an overview page's Attachments widget (names / metadata
    only). The list is an Ext store read (POST with paging params, no side
    effects) at {module}/{fileController}/{readAction}, which must match
    FILES_LIST_RE. Download links are never followed or output (they can embed
    auth tokens). Field names are passed through as the portal sends them,
    minus anything URL-like (format not yet seen with real files)."""
    urls = []

    def visit(n):
        if n.get("xtype") == "mis-widget-attachments":
            p = n.get("props", {}) or {}
            u = "%s/%s/%s" % ((p.get("module") or "").rstrip("/"),
                              p.get("fileController") or "", p.get("readAction") or "")
            if FILES_LIST_RE.match(u):
                urls.append(u)
    walk(page, visit)
    files = []
    for u in urls[:1]:
        j = json.loads(req(opener, base + u, data={"page": 1, "start": 0, "limit": 200}))
        for it in j.get("items", []) or []:
            f = it.get("fields", it) if isinstance(it, dict) else {}
            rec = {}
            for k, v in (f or {}).items():
                if _FILE_FIELD_SKIP.search(k):
                    continue
                v = v.get("value") if isinstance(v, dict) and "value" in v else v
                if isinstance(v, (str, int, float, bool)) or v is None:
                    rec[k] = v
            files.append(rec)
    return files if urls else None


def overview_extras(opener, base, url, details, files):
    """--details / --files for a club or trip row (one overview GET shared)."""
    out = {}
    try:
        raw = req(opener, base + safe_path(url) + JS)
        if details:
            out["details"] = parse_detail_page(raw)
        if files:
            fl = list_attachments(opener, base, json.loads(raw))
            out["files"] = fl if fl is not None else []
            if fl is None:
                out["files_note"] = "no attachments widget on the overview page"
    except (urllib.error.HTTPError, ValueError) as e:
        out["details_error"] = str(e)
    return out


# ---- customer-account spend by week ------------------------------------------

def parse_account_dashboard(raw):
    """Customer-account dashboard -> (title, term_total, terms, weeks).

    Column title 'Autumn Total Payments: £62.64'; one section per week
    'Week beginning 05 Oct 2026: £5.22' whose rows are weekday -> amount, each
    linking view-payments-on-date/date/YYYY-MM-DD (the per-day item list)."""
    data = json.loads(raw)
    title = None

    def col(n):
        nonlocal title
        if n.get("xtype") == "mis-layoutcolumn" and title is None:
            title = ((n.get("props", {}) or {}).get("columnTitle") or {}).get("title")
    walk(data, col)
    weeks = []
    for sec, p, d in section_rows(data, r"/view-payments-on-date/date/(\d{4}-\d{2}-\d{2})/"):
        m = re.match(r"(?i)week beginning\s+(.+?)\s*:\s*(.+)$", (sec or "").replace("\xa0", " "))
        start = parse_short_date(m.group(1)) if m else None
        if not weeks or weeks[-1]["_sec"] != sec:
            weeks.append({"_sec": sec, "week_start": start,
                          "total": m.group(2).strip() if m else None, "days": []})
        weeks[-1]["days"].append({"date": d, "weekday": html_text(p.get("fieldLabel") or "") or None,
                                  "amount": html_text(str(p.get("value") or "")) or None,
                                  "_url": p.get("url")})
    total = None
    if title and ":" in title:
        total = title.rsplit(":", 1)[1].strip() or None
    terms = toggle_ids(data, "Term", "term-id")
    return title, total, terms, weeks


def _money(s):
    m = re.search(r"(-?)£\s?(-?[\d,]+\.\d{2})", s or "")
    return float(m.group(1) + m.group(2).replace(",", "")) if m else None


def parse_day_payments(raw):
    """view-payments-on-date slideover -> [{item, amount}]."""
    items = []

    def visit(n):
        if n.get("xtype") == "mis-property-row":
            p = n.get("props", {}) or {}
            items.append({"item": html_text(p.get("fieldLabel") or "") or None,
                          "amount": html_text(str(p.get("value") or "")) or None})
    walk(json.loads(raw), visit)
    return items


def meals_account_id(opener, base, sid):
    """The child's meals customer-account id, from the meals-balance KPI link."""
    try:
        arr = json.loads(req(opener, base + MEALS_BALANCE_PATH.format(id=sid)))
        m = re.search(r"/customer-account-id/(\d+)", (arr[0] or {}).get("url") or "") if arr else None
        return m.group(1) if m else None
    except (ValueError, urllib.error.HTTPError, IndexError, AttributeError):
        return None


def get_account_spend(opener, base, child, account_ids, term_id, since, until, details):
    accts = list(account_ids or [])
    if not accts:
        a = meals_account_id(opener, base, child["student_id"])
        if not a:
            return {"accounts": [], "note": "no meals account found for this child "
                                            "(pass --account-id; ids are in `invoices` → accounts)"}
        accts = [a]
    out = []
    for acct in accts:
        path = ACCOUNT_DASH_PATH.format(acct=acct)
        if term_id:
            path += f"/term-id/{term_id}"
        title, total, terms, weeks = parse_account_dashboard(req(opener, base + path + JS))
        lo, hi = (since.isoformat() if since else None), (until.isoformat() if until else None)
        kept = []
        for w in weeks:
            days = [d for d in w["days"]
                    if d["date"] and (not lo or d["date"] >= lo) and (not hi or d["date"] <= hi)]
            if not days:
                continue
            for d in days:
                url = d.pop("_url", None)
                if details and (_money(d["amount"]) or 0) != 0 and url and ACCOUNT_DAY_RE.match(url):
                    try:
                        d["items"] = parse_day_payments(req(opener, base + url + JS))
                    except (urllib.error.HTTPError, ValueError) as e:
                        d["items_error"] = str(e)
            w = {k: v for k, v in w.items() if k != "_sec"}
            w["days"] = days
            kept.append(w)
        sel = next((t for t in terms if t["selected"]), None)
        spent = round(sum(_money(d["amount"]) or 0 for w in kept for d in w["days"]), 2)
        out.append({"account_id": acct, "title": title, "term_total": total,
                    "term": ({"id": sel["id"], "label": sel["label"]} if sel
                             else ({"id": str(term_id), "label": None} if term_id else None)),
                    "terms": [{"id": t["id"], "label": t["label"]} for t in terms],
                    "range_total": spent if (since or until) else None,
                    "weeks": kept})
    return {"accounts": out}


# ---- behaviour ----------------------------------------------------------------

def _slug(title):
    return re.sub(r"[^a-z0-9]+", "_", (title or "").lower()).strip("_") or "_"


def parse_short_datetime(raw):
    """'06 Oct 2026, 10:12' / 'Issued 14 Sep 2026' -> (prefix, 'YYYY-MM-DD', 'HH:MM')."""
    s = (raw or "").replace("\xa0", " ").strip()
    m = re.match(r"^(.*?)\s*(\d{1,2} [A-Za-z]{3,9} \d{4})(?:,\s*(\d{1,2}:\d{2}))?\s*$", s)
    if not m:
        return None, None, None
    return (m.group(1).strip() or None), parse_short_date(m.group(2)), m.group(3)


def _first_number(s):
    m = re.search(r"-?\d+(?:\.\d+)?", s or "")
    if not m:
        return None
    return float(m.group(0)) if "." in m.group(0) else int(m.group(0))


def parse_behaviour(raw):
    """Behaviour page -> (years, selected_year, sections).

    Sections (e.g. Demerits / Merits / Positive Incidents / Negative Incidents /
    Detentions — titles come from the portal) contain sub-sections of totals
    ('Lifetime', '2026/2027', 'Autumn', a date range -> '76 Merits') and of
    events ('… Breakdown': rows dated 'DD Mon YYYY[, HH:MM]' with
    '<b>Label:</b> value' blocks). Detentions are dated 'Issued DD Mon YYYY'."""
    data = json.loads(raw)
    years, sel = academic_years(data, "Behaviour for:")
    sections = {}

    def visit(n, sec=None, sub=None):
        if isinstance(n, list):
            for v in n:
                visit(v, sec, sub)
            return
        if not isinstance(n, dict):
            return
        xt = n.get("xtype")
        p = n.get("props", {}) or {}
        if xt in ("mis-subnavcolumn", "mis-info-panel"):
            return
        if xt == "mis-section":
            sec, sub = html_text(p.get("title") or "") or "_", None
            sections.setdefault(sec, {"totals": [], "items": []})
        elif xt == "mis-subsection":
            sub = html_text(p.get("title") or "") or None
        elif xt == "mis-property-row" and sec:
            label = html_text(p.get("fieldLabel") or "")
            val = str(p.get("value") or "")
            f, lead = labeled_fields(val)
            prefix, d, tm = parse_short_datetime(label)
            if f or (d and "breakdown" in (sub or "").lower()):
                rec = {(_slug(prefix) if prefix else "date"): d}
                if tm:
                    rec["time"] = tm
                rec.update({_slug(k): v for k, v in f.items()})
                if lead:
                    rec["text"] = lead
                sections[sec]["items"].append(rec)
            else:
                txt = html_text(val) or None
                sections[sec]["totals"].append({"group": sub, "period": label or None,
                                                "value": txt, "number": _first_number(txt)})
        for k, v in n.items():
            if k != "props":
                visit(v, sec, sub)

    visit(data.get("content", data))
    return years, sel, {_slug(k): dict(v, title=k) for k, v in sections.items()}


def _year_rec(sel, year):
    return ({"id": sel["id"], "label": sel["label"]} if sel
            else ({"id": str(year), "label": None} if year else None))


def get_behaviour(opener, base, child, year, since, until):
    path = BEHAVIOUR_PATH.format(id=child["student_id"])
    if year:
        path += YEAR_SUFFIX.format(year=year)
    try:
        raw = req(opener, base + path + JS)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return {"behaviour_available": False,
                    "note": f"behaviour page not available at this school (HTTP {e.code})"}
        raise
    years, sel, sections = parse_behaviour(raw)
    lo, hi = (since.isoformat() if since else None), (until.isoformat() if until else None)
    if lo or hi:
        for s in sections.values():
            def keep(r):
                d = next((v for k, v in r.items() if isinstance(v, str)
                          and re.match(r"^\d{4}-\d{2}-\d{2}$", v)), None)
                return d and (not lo or d >= lo) and (not hi or d <= hi)
            s["items"] = [r for r in s["items"] if keep(r)]
    out = {"behaviour_available": bool(sections), "academic_year": _year_rec(sel, year),
           "academic_years": [{"id": y["id"], "label": y["label"]} for y in years],
           "behaviour": sections}
    if not sections:
        out["note"] = "the school publishes no behaviour data to guardians"
    return out


# ---- exams ----------------------------------------------------------------------

def find_candidate_id(page):
    """Exam candidate id from a student page's sub-nav ('Examinations' item,
    present only for children who are exam candidates)."""
    found = []

    def leaves(items):
        for it in items or []:
            u = ((it.get("fields") or {}).get("url") or {}).get("value") or ""
            m = EXAMS_LINK_RE.match(u)
            if m:
                found.append(m.group(1))
            leaves(it.get("items"))

    def visit(n):
        tree = (n.get("props", {}) or {}).get("treeData") if isinstance(n.get("props"), dict) else None
        if isinstance(tree, dict):
            leaves(tree.get("items"))
    walk(page.get("subNav") or {}, visit)
    return found[0] if found else None


def parse_exams(raw):
    """Examinations page -> (years, selected_year, period, sections).

    Each section (e.g. 'Exam Timetable') lists property rows; their exact
    shape hasn't been seen with real entries yet, so rows are passed through
    generically: label, date/time if the label is a date, '<b>Label:</b>'
    fields or plain text, description. The page's hidden candidate / student
    objects (names, candidate number) are never output."""
    data = json.loads(raw)
    years, sel = academic_years(data, "Academic year")
    period = {}
    sections = {}

    def visit(n, sec=None):
        if isinstance(n, list):
            for v in n:
                visit(v, sec)
            return
        if not isinstance(n, dict):
            return
        xt = n.get("xtype")
        p = n.get("props", {}) or {}
        if xt in ("mis-subnavcolumn", "mis-info-panel", "mis-actionpanel"):
            return
        if xt == "hidden" and p.get("name") in ("startDate", "endDate"):
            period[p["name"]] = p.get("value")
        elif xt == "mis-section":
            sec = html_text(p.get("title") or "") or "_"
            sections.setdefault(sec, [])
        elif xt == "mis-property-row" and sec:
            label = html_text(p.get("fieldLabel") or "")
            f, lead = labeled_fields(str(p.get("value") or ""))
            _, d, tm = parse_short_datetime(label)
            rec = {"label": label or None}
            if d:
                rec["date"] = d
            if tm:
                rec["time"] = tm
            if f:
                rec.update({_slug(k): v for k, v in f.items()})
                if lead:
                    rec["text"] = lead
            else:
                rec["text"] = html_text(str(p.get("value") or "")) or None
            desc = html_text(str(p.get("description") or ""))
            if desc:
                rec["description"] = desc
            sections[sec].append(rec)
        for k, v in n.items():
            if k != "props":
                visit(v, sec)

    visit(data.get("content", data))
    return years, sel, period, sections


def get_exams(opener, base, child, year, candidate_id):
    cid = candidate_id
    if not cid:
        # any student page carries the sub-nav; the assignments overview is small
        page = json.loads(req(opener, base + ASSIGN_PATH.format(id=child["student_id"]) + JS))
        cid = find_candidate_id(page)
    if not cid:
        return {"exams_available": False,
                "note": "no Examinations item in this child's menu (not an exam candidate)"}
    path = EXAMS_PATH.format(cid=cid)
    if year:
        path += YEAR_SUFFIX.format(year=year)
    years, sel, period, sections = parse_exams(req(opener, base + path + JS))
    return {"exams_available": True, "candidate_id": cid,
            "academic_year": _year_rec(sel, year),
            "academic_years": [{"id": y["id"], "label": y["label"]} for y in years],
            "period": {"start": period.get("startDate"), "end": period.get("endDate")},
            "exams": {_slug(k): {"title": k, "rows": v} for k, v in sections.items()},
            "count": sum(len(v) for v in sections.values())}


def compute_children(email, pw, cookie_dir, schools):
    rows = []
    for s in schools:
        opener, base = session(s["subdomain"], email, pw, cookie_dir)
        rows.append({"school": s["subdomain"],
                     "children": discover_children(opener, base)})
    return {"cmd": "children", "schools": rows}


def parse_meal_sets(items):
    out = {}
    for it in items:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})=(\d+_\d+|none)$", it.strip())
        if not m:
            raise SystemExit(f"--set expects DATE=VALUE (YYYY-MM-DD=100_01 or =none), got {it!r}")
        out[m.group(1)] = None if m.group(2) == "none" else m.group(2)
    return out


def compute_perchild(args, email, pw, cookie_dir, schools, want):
    day = to_date(getattr(args, "date", None))
    since = day or to_date(getattr(args, "since", None))
    until = day or to_date(getattr(args, "until", None))
    students, outstanding = [], []
    for s in schools:
        opener, base = session(s["subdomain"], email, pw, cookie_dir)
        children = [c for c in discover_children(opener, base)
                    if not want or c["student_id"] in want]
        for c in children:
            rec = {"school": s["subdomain"], "student_id": c["student_id"],
                   "name": c["name"]}
            if args.cmd == "attendance":
                rec["attendance"] = get_attendance(opener, base, c)
            elif args.cmd == "timetable":
                rec["events"] = get_events(opener, base, c, since, until)
            elif args.cmd == "balances":
                rec["meals_balance"] = get_meals_balance(opener, base, c)
            elif args.cmd == "attendance-by-date":
                rec.update(get_attendance_by_date(opener, base, c, args.academic_year_id,
                                                  since, until, args.non_present,
                                                  args.certificate))
            elif args.cmd == "meals" and args.set:
                rec.update(set_meal_provisions(opener, base, c, parse_meal_sets(args.set),
                                               args.menu_id, args.add_to_basket))
            elif args.cmd == "meals" and args.options:
                rec.update(get_meal_options(opener, base, c, since, until, args.menu_id))
            elif args.cmd == "meals":
                rec.update(get_meals(opener, base, c, since, until))
            elif args.cmd == "report-cards":
                rec.update(get_report_cards(opener, base, c, args.download, args.card_id))
            elif args.cmd == "clubs":
                rec.update(get_clubs(opener, base, c, args.details, args.files))
            elif args.cmd == "trips":
                rec.update(get_trips(opener, base, c, args.details, args.files))
            elif args.cmd == "account-spend":
                rec.update(get_account_spend(opener, base, c, args.account_id, args.term_id,
                                             since, until, args.details))
            elif args.cmd == "behaviour":
                rec.update(get_behaviour(opener, base, c, args.academic_year_id, since, until))
            elif args.cmd == "exams":
                rec.update(get_exams(opener, base, c, args.academic_year_id, args.candidate_id))
            elif args.cmd == "shop":
                rec.update(get_shop(opener, base, c, args.details))
            elif args.cmd == "assignments":
                rec.update(get_assignments(opener, base, c, args.academic_year_id,
                                           args.segment or list(ASSIGN_SEGMENTS),
                                           args.details))
            students.append(rec)
        if args.cmd == "balances" and children:
            o = get_outstanding(opener, base, children[0]["student_id"])
            if o:
                outstanding.append({"school": s["subdomain"], **o})
    out = {"cmd": args.cmd, "students": students}
    if args.cmd == "balances":
        out["outstanding"] = outstanding
    return out


# ---- CLI --------------------------------------------------------------------

SUBCMDS = ("messages", "children", "attendance", "timetable", "balances",
           "attendance-by-date", "assignments", "meals", "invoices",
           "report-cards", "clubs", "trips", "shop", "account-spend", "behaviour",
           "exams")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--school", action="append", metavar="HOST",
                        help="school subdomain (repeatable; overrides $ARBOR_SCHOOL)")
    common.add_argument("--all-schools", action="store_true",
                        help="process every school on the account")
    common.add_argument("--list-schools", action="store_true",
                        help="print schools (name + subdomain) and exit")
    common.add_argument("--offline", action="store_true",
                        help="serve from the cache only; make no requests to Arbor")
    common.add_argument("--max-age", type=int, default=0, metavar="SEC",
                        help="reuse cached result if younger than SEC seconds "
                             "(all subcommands except messages)")
    common.add_argument("--no-cache", action="store_true",
                        help="bypass the SQLite cache entirely")
    child = argparse.ArgumentParser(add_help=False)
    child.add_argument("--student-id", action="append", metavar="ID",
                       help="limit to these child student-ids (repeatable)")

    ap = argparse.ArgumentParser(description="Read Arbor guardian-portal data as JSON.")
    sub = ap.add_subparsers(dest="cmd")

    m = sub.add_parser("messages", parents=[common], help="school messages")
    m.add_argument("--mode", choices=["unread", "all", "period"], default="unread")
    m.add_argument("--since")
    m.add_argument("--until")
    m.add_argument("--limit", type=int, default=0)
    g = m.add_mutually_exclusive_group()
    g.add_argument("--bodies", dest="bodies", action="store_true")
    g.add_argument("--no-bodies", dest="bodies", action="store_false")
    m.set_defaults(bodies=True)

    sub.add_parser("children", parents=[common], help="list children")
    sub.add_parser("attendance", parents=[common, child], help="attendance KPIs")
    t = sub.add_parser("timetable", parents=[common, child], help="calendar events")
    t.add_argument("--date", help="single day YYYY-MM-DD")
    t.add_argument("--since")
    t.add_argument("--until")
    sub.add_parser("balances", parents=[common, child], help="account balances")
    year = argparse.ArgumentParser(add_help=False)
    year.add_argument("--academic-year-id", metavar="ID",
                      help="school-specific academic-year id (default: current year; "
                           "ids differ per school, see `academic_years` in the output)")
    a = sub.add_parser("attendance-by-date", parents=[common, child, year],
                       help="per-session attendance marks")
    a.add_argument("--date", help="single day YYYY-MM-DD")
    a.add_argument("--since")
    a.add_argument("--until")
    a.add_argument("--non-present", action="store_true",
                   help="only list sessions that are not plain 'present' (summary still counts all)")
    a.add_argument("--certificate", metavar="DIR",
                   help="also save the year's attendance-certificate PDF into DIR (not cached)")
    h = sub.add_parser("assignments", parents=[common, child, year],
                       help="homework / assignments")
    h.add_argument("--segment", action="append", choices=ASSIGN_SEGMENTS,
                   help="list(s) to fetch (repeatable; default: all three)")
    h.add_argument("--details", action="store_true",
                   help="also fetch each assignment's detail page (1 request per item)")
    ml = sub.add_parser("meals", parents=[common, child],
                        help="(experimental) meal choices per day")
    ml.add_argument("--date", help="single day YYYY-MM-DD")
    ml.add_argument("--since")
    ml.add_argument("--until")
    ml.add_argument("--menu-id", metavar="ID", help="limit to this meal-rotation menu")
    ml.add_argument("--options", action="store_true",
                    help="list each editable day's options (value id, label, selected); "
                         "1 GET per slideover")
    ml.add_argument("--set", action="append", metavar="DATE=VALUE",
                    help="plan a basket change, VALUE = option id (e.g. 100_01) or 'none'. "
                         "Dry run unless --add-to-basket. Needs exactly one --student-id")
    ml.add_argument("--add-to-basket", action="store_true",
                    help="actually POST the --set changes (process-meal-provisions only; "
                         "never checks out or pays; checkout in the portal still needed)")
    iv = sub.add_parser("invoices", parents=[common, child],
                        help="invoices, top-ups and credit notes (read-only)")
    iv.add_argument("--term-id", metavar="ID",
                    help="school-specific term id (default: current term; see `terms`)")
    iv.add_argument("--kind", action="append", choices=list(ACCOUNT_DASHBOARDS),
                    help="which list(s) (repeatable; default: all three)")
    rc = sub.add_parser("report-cards", parents=[common, child],
                        help="report cards (list; optional PDF download)")
    rc.add_argument("--download", metavar="DIR",
                    help="(experimental) also download each card's PDF into DIR")
    rc.add_argument("--card-id", action="append", metavar="ID",
                    help="with --download: only these card ids (repeatable)")
    det = argparse.ArgumentParser(add_help=False)
    det.add_argument("--details", action="store_true",
                     help="also fetch each item's overview page (1 GET per item)")
    files = argparse.ArgumentParser(add_help=False)
    files.add_argument("--files", action="store_true",
                       help="also list each item's attachments (names only; "
                            "1 overview GET + 1 list read per item)")
    sub.add_parser("clubs", parents=[common, child, det, files], help="clubs")
    sub.add_parser("trips", parents=[common, child, det, files], help="trips")
    sub.add_parser("shop", parents=[common, child, det],
                   help="school-shop catalogue (list only; never buys)")
    sp = sub.add_parser("account-spend", parents=[common, child],
                        help="customer-account (default: meals) spend by week and day")
    sp.add_argument("--account-id", action="append", metavar="ID",
                    help="customer-account id(s) instead of the child's meals account "
                         "(see `invoices` → accounts); needs one --school and one --student-id")
    sp.add_argument("--term-id", metavar="ID",
                    help="school-specific term id (default: current term; see `terms`)")
    sp.add_argument("--since")
    sp.add_argument("--until")
    sp.add_argument("--details", action="store_true",
                    help="also list the items bought on each non-zero day (1 GET per day)")
    bh = sub.add_parser("behaviour", parents=[common, child, year],
                        help="behaviour points, incidents and detentions")
    bh.add_argument("--since", help="only list events on/after this date (totals unaffected)")
    bh.add_argument("--until", help="only list events on/before this date")
    ex = sub.add_parser("exams", parents=[common, child, year],
                        help="(experimental) exam timetable (secondary / exam candidates)")
    ex.add_argument("--candidate-id", metavar="ID",
                    help="skip discovery (normally read from the child's menu)")
    return ap


def resolve_schools(args, email, pw):
    if not args.all_schools:
        chosen = args.school or [
            s.strip() for s in (os.environ.get("ARBOR_SCHOOL") or "").split(",")
            if s.strip()]
        if chosen:
            return [{"subdomain": h, "name": None} for h in chosen]
    schools = discover_schools(email, pw)
    if not schools:
        raise SystemExit("No Arbor schools found for these credentials.")
    return schools


def cached_schools(con):
    """School subdomains known to the cache (for offline runs without --school)."""
    s = set()
    if con is not None:
        for r in con.execute("SELECT DISTINCT school FROM message"):
            s.add(r["school"])
        for r in con.execute("SELECT key FROM response"):
            try:
                s.update(json.loads(r["key"]).get("schools", []))
            except Exception:
                pass
    return [{"subdomain": x, "name": None} for x in sorted(s)]


def cache_key(args, schools, want):
    key = {"cmd": args.cmd,
           "schools": sorted(s["subdomain"] for s in schools),
           "students": sorted(want),
           "date": getattr(args, "date", None),
           "since": getattr(args, "since", None),
           "until": getattr(args, "until", None)}
    # Options of newer subcommands: only added when set, so keys of the older
    # subcommands (and their cached results) are unchanged.
    for opt in ("academic_year_id", "non_present", "segment", "details", "term_id", "kind",
                "options", "menu_id", "download", "card_id", "files", "account_id",
                "candidate_id"):
        v = getattr(args, opt, None)
        if v:
            key[opt] = sorted(v) if isinstance(v, list) else v
    return json.dumps(key, sort_keys=True)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(os.getcwd(), ".env"))
    load_dotenv(os.path.join(here, ".env"))

    argv = sys.argv[1:]
    first = next((a for a in argv if not a.startswith("-")), None)
    if first not in SUBCMDS:            # backward-compat: default to `messages`
        argv = ["messages"] + argv
    args = build_parser().parse_args(argv)

    email, pw = os.environ.get("ARBOR_EMAIL"), os.environ.get("ARBOR_PW")
    if not email or not pw:
        raise SystemExit("ARBOR_EMAIL and ARBOR_PW must be set (env or .env).")

    if args.list_schools:
        json.dump({"schools": discover_schools(email, pw)}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    con = None
    if not args.no_cache:
        try:
            con = cache_open()
        except Exception as e:
            log(f"cache disabled: {e}")

    cookie_dir = os.path.expanduser(os.environ.get("ARBOR_COOKIE_DIR", "~/.cache/arbor"))
    # Offline runs must not hit the network to resolve schools: if none were given
    # explicitly, take the schools known to the cache.
    if args.offline and not args.school and not os.environ.get("ARBOR_SCHOOL"):
        schools = cached_schools(con)
        if not schools:
            raise SystemExit("--offline: no cached schools; run online once or pass --school.")
    else:
        schools = resolve_schools(args, email, pw)
    want = set(getattr(args, "student_id", None) or [])

    writes = args.cmd == "meals" and bool(args.set)
    if args.cmd == "meals" and args.add_to_basket and not args.set:
        raise SystemExit("--add-to-basket needs at least one --set DATE=VALUE")
    if writes and (len(want) != 1 or len(schools) != 1 or args.offline):
        raise SystemExit("--set needs exactly one --school and one --student-id (and no --offline)")
    if (args.cmd == "account-spend" and args.account_id
            and (len(want) != 1 or len(schools) != 1)):
        raise SystemExit("--account-id needs exactly one --school and one --student-id")
    side_effects = (writes or (args.cmd == "report-cards" and args.download)
                    or (args.cmd == "attendance-by-date" and args.certificate))

    if args.cmd == "messages":
        out = do_messages(args, email, pw, cookie_dir, schools, con)
    elif side_effects:     # never cached / served from cache
        out = compute_perchild(args, email, pw, cookie_dir, schools, want)
        out["from_cache"] = False
    else:
        key = cache_key(args, schools, want)
        cached, fetched_at = resp_get(con, key) if con is not None else (None, None)
        fresh = (cached is not None and args.max_age > 0
                 and (time.time() - fetched_at) <= args.max_age)
        if args.offline:
            if cached is None:
                raise SystemExit(f"--offline: no cached '{args.cmd}' result for these arguments.")
            out = cached
            out["from_cache"] = True
        elif fresh:
            out = cached
            out["from_cache"] = True
        else:
            if args.cmd == "children":
                out = compute_children(email, pw, cookie_dir, schools)
            elif args.cmd == "invoices":
                out = compute_invoices(args, email, pw, cookie_dir, schools, want)
            else:
                out = compute_perchild(args, email, pw, cookie_dir, schools, want)
            if con is not None:
                resp_store(con, key, out)
            out["from_cache"] = False

    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP error {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise SystemExit(f"network error: {e.reason}")
