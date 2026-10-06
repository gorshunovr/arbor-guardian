"""Entry point: resolve schools, dispatch subcommands, print JSON."""

import json
import os
import re
import sys
import time
import urllib.error

from .account_spend import get_account_spend
from .assignments import get_assignments
from .attendance import (
    get_attendance,
    get_attendance_by_date,
    get_events,
    get_meals_balance,
    get_outstanding,
)
from .auth import discover_schools, session
from .behaviour import get_behaviour
from .cache import cache_open, resp_get, resp_store
from .children import discover_children
from .cli import SUBCMDS, build_parser
from .clubs_trips_shop import get_clubs, get_shop, get_trips
from .constants import ASSIGN_SEGMENTS
from .exams import get_exams
from .invoices import compute_invoices
from .meals import get_meal_options, get_meals, set_meal_provisions
from .messages import do_messages
from .report_cards import get_report_cards
from .util import load_dotenv, log, normalize_school, to_date


def compute_children(email, pw, cookie_dir, schools):
    rows = []
    for s in schools:
        opener, base = session(s["subdomain"], email, pw, cookie_dir)
        rows.append({"school": s["subdomain"], "children": discover_children(opener, base)})
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
        children = [
            c for c in discover_children(opener, base) if not want or c["student_id"] in want
        ]
        for c in children:
            rec = {"school": s["subdomain"], "student_id": c["student_id"], "name": c["name"]}
            if args.cmd == "attendance":
                rec["attendance"] = get_attendance(opener, base, c)
            elif args.cmd == "timetable":
                rec["events"] = get_events(opener, base, c, since, until)
            elif args.cmd == "balances":
                rec["meals_balance"] = get_meals_balance(opener, base, c)
            elif args.cmd == "attendance-by-date":
                rec.update(
                    get_attendance_by_date(
                        opener,
                        base,
                        c,
                        args.academic_year_id,
                        since,
                        until,
                        args.non_present,
                        args.certificate,
                    )
                )
            elif args.cmd == "meals" and args.set:
                rec.update(
                    set_meal_provisions(
                        opener, base, c, parse_meal_sets(args.set), args.menu_id, args.add_to_basket
                    )
                )
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
                rec.update(
                    get_account_spend(
                        opener, base, c, args.account_id, args.term_id, since, until, args.details
                    )
                )
            elif args.cmd == "behaviour":
                rec.update(get_behaviour(opener, base, c, args.academic_year_id, since, until))
            elif args.cmd == "exams":
                rec.update(get_exams(opener, base, c, args.academic_year_id, args.candidate_id))
            elif args.cmd == "shop":
                rec.update(get_shop(opener, base, c, args.details))
            elif args.cmd == "assignments":
                rec.update(
                    get_assignments(
                        opener,
                        base,
                        c,
                        args.academic_year_id,
                        args.segment or list(ASSIGN_SEGMENTS),
                        args.details,
                    )
                )
            students.append(rec)
        if args.cmd == "balances" and children:
            o = get_outstanding(opener, base, children[0]["student_id"])
            if o:
                outstanding.append({"school": s["subdomain"], **o})
    out = {"cmd": args.cmd, "students": students}
    if args.cmd == "balances":
        out["outstanding"] = outstanding
    return out


def chosen_schools_from_args(args):
    """Explicit --school / $ARBOR_SCHOOL list (normalized), or empty."""
    chosen = args.school or [
        s.strip() for s in (os.environ.get("ARBOR_SCHOOL") or "").split(",") if s.strip()
    ]
    return [{"subdomain": normalize_school(h), "name": None} for h in chosen]


def resolve_schools(args, email, pw):
    if not args.all_schools:
        chosen = chosen_schools_from_args(args)
        if chosen:
            return chosen
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
    out = []
    for x in sorted(s):
        try:
            out.append({"subdomain": normalize_school(x), "name": None})
        except SystemExit:
            continue
    return out


def cache_key(args, schools, want):
    key = {
        "cmd": args.cmd,
        "schools": sorted(s["subdomain"] for s in schools),
        "students": sorted(want),
        "date": getattr(args, "date", None),
        "since": getattr(args, "since", None),
        "until": getattr(args, "until", None),
    }
    # Options of newer subcommands: only added when set, so keys of the older
    # subcommands (and their cached results) are unchanged.
    for opt in (
        "academic_year_id",
        "non_present",
        "segment",
        "details",
        "term_id",
        "kind",
        "options",
        "menu_id",
        "download",
        "card_id",
        "files",
        "account_id",
        "candidate_id",
    ):
        v = getattr(args, opt, None)
        if v:
            key[opt] = sorted(v) if isinstance(v, list) else v
    return json.dumps(key, sort_keys=True)


def main():
    # Prefer .env next to the thin wrapper / repo root, then cwd, then package dir.
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    repo_dir = os.path.dirname(pkg_dir)
    load_dotenv(os.path.join(os.getcwd(), ".env"))
    load_dotenv(os.path.join(repo_dir, ".env"))
    load_dotenv(os.path.join(pkg_dir, ".env"))

    argv = sys.argv[1:]
    first = next((a for a in argv if not a.startswith("-")), None)
    # Backward-compat: bare flags default to `messages`, but keep top-level
    # `--help`/`-h` (otherwise they become `messages --help`).
    if first not in SUBCMDS:
        if first is None and any(a in ("-h", "--help") for a in argv):
            pass
        else:
            argv = ["messages"] + argv
    args = build_parser().parse_args(argv)

    email, pw = os.environ.get("ARBOR_EMAIL"), os.environ.get("ARBOR_PW")
    # Offline cache reads need no credentials; everything else does.
    if not args.offline and (not email or not pw):
        raise SystemExit("ARBOR_EMAIL and ARBOR_PW must be set (env or .env).")

    if args.list_schools:
        if not email or not pw:
            raise SystemExit("ARBOR_EMAIL and ARBOR_PW must be set (env or .env).")
        json.dump(
            {"schools": discover_schools(email, pw)}, sys.stdout, ensure_ascii=False, indent=2
        )
        sys.stdout.write("\n")
        return

    con = None
    if not args.no_cache:
        try:
            con = cache_open()
        except Exception as e:
            log(f"cache disabled: {e}")

    cookie_dir = os.path.expanduser(os.environ.get("ARBOR_COOKIE_DIR", "~/.cache/arbor"))
    # Offline must never hit the network (including school discovery).
    if args.offline:
        chosen = chosen_schools_from_args(args)
        schools = chosen or cached_schools(con)
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
    if args.cmd == "account-spend" and args.account_id and (len(want) != 1 or len(schools) != 1):
        raise SystemExit("--account-id needs exactly one --school and one --student-id")
    side_effects = (
        writes
        or (args.cmd == "report-cards" and args.download)
        or (args.cmd == "attendance-by-date" and args.certificate)
    )
    if args.offline and side_effects:
        raise SystemExit("--offline cannot be combined with meal writes or PDF downloads")

    if args.cmd == "messages":
        out = do_messages(args, email, pw, cookie_dir, schools, con)
    elif side_effects:  # never cached / served from cache
        out = compute_perchild(args, email, pw, cookie_dir, schools, want)
        out["from_cache"] = False
    else:
        key = cache_key(args, schools, want)
        cached, fetched_at = resp_get(con, key) if con is not None else (None, None)
        fresh = (
            cached is not None and args.max_age > 0 and (time.time() - fetched_at) <= args.max_age
        )
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


def run():
    """CLI entry used by ``python -m arbor_guardian`` and the thin wrapper."""
    try:
        main()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP error {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise SystemExit(f"network error: {e.reason}")
