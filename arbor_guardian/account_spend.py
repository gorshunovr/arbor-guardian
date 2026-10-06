"""Customer-account spend by week / day (default: meals account)."""

import json
import re
import urllib.error

from .constants import ACCOUNT_DASH_PATH, ACCOUNT_DAY_RE, JS, MEALS_BALANCE_PATH
from .http import req
from .parse import section_rows, toggle_ids
from .util import html_text, money, parse_short_date, walk


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
            weeks.append(
                {
                    "_sec": sec,
                    "week_start": start,
                    "total": m.group(2).strip() if m else None,
                    "days": [],
                }
            )
        weeks[-1]["days"].append(
            {
                "date": d,
                "weekday": html_text(p.get("fieldLabel") or "") or None,
                "amount": html_text(str(p.get("value") or "")) or None,
                "_url": p.get("url"),
            }
        )
    total = None
    if title and ":" in title:
        total = title.rsplit(":", 1)[1].strip() or None
    terms = toggle_ids(data, "Term", "term-id")
    return title, total, terms, weeks


def parse_day_payments(raw):
    """view-payments-on-date slideover -> [{item, amount}]."""
    items = []

    def visit(n):
        if n.get("xtype") == "mis-property-row":
            p = n.get("props", {}) or {}
            items.append(
                {
                    "item": html_text(p.get("fieldLabel") or "") or None,
                    "amount": html_text(str(p.get("value") or "")) or None,
                }
            )

    walk(json.loads(raw), visit)
    return items


def meals_account_id(opener, base, sid):
    """The child's meals customer-account id, from the meals-balance KPI link."""
    try:
        arr = json.loads(req(opener, base + MEALS_BALANCE_PATH.format(id=sid)))
        m = (
            re.search(r"/customer-account-id/(\d+)", (arr[0] or {}).get("url") or "")
            if arr
            else None
        )
        return m.group(1) if m else None
    except (ValueError, urllib.error.HTTPError, IndexError, AttributeError):
        return None


def get_account_spend(opener, base, child, account_ids, term_id, since, until, details):
    accts = list(account_ids or [])
    if not accts:
        a = meals_account_id(opener, base, child["student_id"])
        if not a:
            return {
                "accounts": [],
                "note": "no meals account found for this child "
                "(pass --account-id; ids are in `invoices` → accounts)",
            }
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
            days = [
                d
                for d in w["days"]
                if d["date"] and (not lo or d["date"] >= lo) and (not hi or d["date"] <= hi)
            ]
            if not days:
                continue
            for d in days:
                url = d.pop("_url", None)
                if details and (money(d["amount"]) or 0) != 0 and url and ACCOUNT_DAY_RE.match(url):
                    try:
                        d["items"] = parse_day_payments(req(opener, base + url + JS))
                    except (urllib.error.HTTPError, ValueError) as e:
                        d["items_error"] = str(e)
            w = {k: v for k, v in w.items() if k != "_sec"}
            w["days"] = days
            kept.append(w)
        sel = next((t for t in terms if t["selected"]), None)
        spent = round(sum(money(d["amount"]) or 0 for w in kept for d in w["days"]), 2)
        out.append(
            {
                "account_id": acct,
                "title": title,
                "term_total": total,
                "term": (
                    {"id": sel["id"], "label": sel["label"]}
                    if sel
                    else ({"id": str(term_id), "label": None} if term_id else None)
                ),
                "terms": [{"id": t["id"], "label": t["label"]} for t in terms],
                "range_total": spent if (since or until) else None,
                "weeks": kept,
            }
        )
    return {"accounts": out}
