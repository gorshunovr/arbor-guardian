"""Argparse CLI for arbor_guardian subcommands."""

import argparse
import re

from .constants import ACCOUNT_DASHBOARDS, ASSIGN_SEGMENTS


def numeric_id(value):
    """Argparse type: portal ids are decimal digits only (blocks path injection)."""
    if not re.fullmatch(r"\d+", value or ""):
        raise argparse.ArgumentTypeError(f"expected a numeric id, got {value!r}")
    return value


SUBCMDS = (
    "messages",
    "children",
    "attendance",
    "timetable",
    "balances",
    "attendance-by-date",
    "assignments",
    "meals",
    "invoices",
    "report-cards",
    "clubs",
    "trips",
    "shop",
    "account-spend",
    "behaviour",
    "exams",
)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--school",
        action="append",
        metavar="HOST",
        help="school subdomain (repeatable; overrides $ARBOR_SCHOOL)",
    )
    common.add_argument(
        "--all-schools", action="store_true", help="process every school on the account"
    )
    common.add_argument(
        "--list-schools", action="store_true", help="print schools (name + subdomain) and exit"
    )
    common.add_argument(
        "--offline",
        action="store_true",
        help="serve from the cache only; make no requests to Arbor",
    )
    common.add_argument(
        "--max-age",
        type=int,
        default=0,
        metavar="SEC",
        help="reuse cached result if younger than SEC seconds (all subcommands except messages)",
    )
    common.add_argument("--no-cache", action="store_true", help="bypass the SQLite cache entirely")
    child = argparse.ArgumentParser(add_help=False)
    child.add_argument(
        "--student-id",
        action="append",
        type=numeric_id,
        metavar="ID",
        help="limit to these child student-ids (repeatable)",
    )

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
    year.add_argument(
        "--academic-year-id",
        type=numeric_id,
        metavar="ID",
        help="school-specific academic-year id (default: current year; "
        "ids differ per school, see `academic_years` in the output)",
    )
    a = sub.add_parser(
        "attendance-by-date", parents=[common, child, year], help="per-session attendance marks"
    )
    a.add_argument("--date", help="single day YYYY-MM-DD")
    a.add_argument("--since")
    a.add_argument("--until")
    a.add_argument(
        "--non-present",
        action="store_true",
        help="only list sessions that are not plain 'present' (summary still counts all)",
    )
    a.add_argument(
        "--certificate",
        metavar="DIR",
        help="also save the year's attendance-certificate PDF into DIR (not cached)",
    )
    h = sub.add_parser("assignments", parents=[common, child, year], help="homework / assignments")
    h.add_argument(
        "--segment",
        action="append",
        choices=ASSIGN_SEGMENTS,
        help="list(s) to fetch (repeatable; default: all three)",
    )
    h.add_argument(
        "--details",
        action="store_true",
        help="also fetch each assignment's detail page (1 request per item)",
    )
    ml = sub.add_parser(
        "meals", parents=[common, child], help="(experimental) meal choices per day"
    )
    ml.add_argument("--date", help="single day YYYY-MM-DD")
    ml.add_argument("--since")
    ml.add_argument("--until")
    ml.add_argument(
        "--menu-id", type=numeric_id, metavar="ID", help="limit to this meal-rotation menu"
    )
    ml.add_argument(
        "--options",
        action="store_true",
        help="list each editable day's options (value id, label, selected); 1 GET per slideover",
    )
    ml.add_argument(
        "--set",
        action="append",
        metavar="DATE=VALUE",
        help="plan a basket change, VALUE = option id (e.g. 100_01) or 'none'. "
        "Dry run unless --add-to-basket. Needs exactly one --student-id",
    )
    ml.add_argument(
        "--add-to-basket",
        action="store_true",
        help="actually POST the --set changes (process-meal-provisions only; "
        "never checks out or pays; checkout in the portal still needed)",
    )
    iv = sub.add_parser(
        "invoices", parents=[common, child], help="invoices, top-ups and credit notes (read-only)"
    )
    iv.add_argument(
        "--term-id",
        type=numeric_id,
        metavar="ID",
        help="school-specific term id (default: current term; see `terms`)",
    )
    iv.add_argument(
        "--kind",
        action="append",
        choices=list(ACCOUNT_DASHBOARDS),
        help="which list(s) (repeatable; default: all three)",
    )
    rc = sub.add_parser(
        "report-cards", parents=[common, child], help="report cards (list; optional PDF download)"
    )
    rc.add_argument(
        "--download", metavar="DIR", help="(experimental) also download each card's PDF into DIR"
    )
    rc.add_argument(
        "--card-id",
        action="append",
        type=numeric_id,
        metavar="ID",
        help="with --download: only these card ids (repeatable)",
    )
    det = argparse.ArgumentParser(add_help=False)
    det.add_argument(
        "--details",
        action="store_true",
        help="also fetch each item's overview page (1 GET per item)",
    )
    files = argparse.ArgumentParser(add_help=False)
    files.add_argument(
        "--files",
        action="store_true",
        help="also list each item's attachments (names only; "
        "1 overview GET + 1 list read per item)",
    )
    sub.add_parser("clubs", parents=[common, child, det, files], help="clubs")
    sub.add_parser("trips", parents=[common, child, det, files], help="trips")
    sub.add_parser(
        "shop", parents=[common, child, det], help="school-shop catalogue (list only; never buys)"
    )
    sp = sub.add_parser(
        "account-spend",
        parents=[common, child],
        help="customer-account (default: meals) spend by week and day",
    )
    sp.add_argument(
        "--account-id",
        action="append",
        type=numeric_id,
        metavar="ID",
        help="customer-account id(s) instead of the child's meals account "
        "(see `invoices` → accounts); needs one --school and one --student-id",
    )
    sp.add_argument(
        "--term-id",
        type=numeric_id,
        metavar="ID",
        help="school-specific term id (default: current term; see `terms`)",
    )
    sp.add_argument("--since")
    sp.add_argument("--until")
    sp.add_argument(
        "--details",
        action="store_true",
        help="also list the items bought on each non-zero day (1 GET per day)",
    )
    bh = sub.add_parser(
        "behaviour",
        parents=[common, child, year],
        help="behaviour points, incidents and detentions",
    )
    bh.add_argument("--since", help="only list events on/after this date (totals unaffected)")
    bh.add_argument("--until", help="only list events on/before this date")
    ex = sub.add_parser(
        "exams",
        parents=[common, child, year],
        help="(experimental) exam timetable (secondary / exam candidates)",
    )
    ex.add_argument(
        "--candidate-id",
        type=numeric_id,
        metavar="ID",
        help="skip discovery (normally read from the child's menu)",
    )
    return ap
