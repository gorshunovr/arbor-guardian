"""Invoices, top-ups and credit notes (read-only)."""

import json
import re

from .auth import session
from .children import discover_children
from .constants import ACCOUNT_DASHBOARDS, JS
from .http import req
from .parse import toggle_ids
from .util import html_text, labeled_fields, parse_short_date, strip_html, walk


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
            rec = {
                "date": date,
                "account": f.pop("Account", None),
                "amount": f.pop("Amount", None),
                "items": f.pop("Items", None),
                "status": f.pop("Status", None),
            }
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
        if want:  # per-child view (server-side filter)
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
                    rec["term"] = (
                        {"id": sel["id"], "label": sel["label"]}
                        if sel
                        else ({"id": str(args.term_id), "label": None} if args.term_id else None)
                    )
                    rec["terms"] = [{"id": t["id"], "label": t["label"]} for t in terms]
                if accounts and "accounts" not in rec:
                    rec["accounts"] = [{"id": a["id"], "label": a["label"]} for a in accounts]
                rec[kind.replace("-", "_")] = rows
            out.append(rec)
    return {"cmd": "invoices", "schools": out}
