"""Exam timetable (experimental; exam candidates only)."""

import json

from .behaviour import year_rec
from .constants import ASSIGN_PATH, EXAMS_LINK_RE, EXAMS_PATH, JS, YEAR_SUFFIX
from .http import req
from .parse import academic_years
from .util import (
    html_text,
    labeled_fields,
    parse_short_datetime,
    slug,
    walk,
)


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
        tree = (
            (n.get("props", {}) or {}).get("treeData") if isinstance(n.get("props"), dict) else None
        )
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
                rec.update({slug(k): v for k, v in f.items()})
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
        return {
            "exams_available": False,
            "note": "no Examinations item in this child's menu (not an exam candidate)",
        }
    path = EXAMS_PATH.format(cid=cid)
    if year:
        path += YEAR_SUFFIX.format(year=year)
    years, sel, period, sections = parse_exams(req(opener, base + path + JS))
    return {
        "exams_available": True,
        "candidate_id": cid,
        "academic_year": year_rec(sel, year),
        "academic_years": [{"id": y["id"], "label": y["label"]} for y in years],
        "period": {"start": period.get("startDate"), "end": period.get("endDate")},
        "exams": {slug(k): {"title": k, "rows": v} for k, v in sections.items()},
        "count": sum(len(v) for v in sections.values()),
    }
