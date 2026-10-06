"""Homework / assignments."""

import json
import re
import urllib.error

from .constants import (
    ASSIGN_KPI_PATH,
    ASSIGN_LIST_PATH,
    ASSIGN_PATH,
    JS,
    SCHOOLWORK_PATH,
)
from .http import req
from .parse import academic_years
from .util import log, parse_short_date, strip_html, walk


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
        cm = re.match(r"(\S+):\s+(.*)$", head)  # "10R/Be1: Title" (class has no spaces)
        cls, title = (cm.group(1), cm.group(2)) if cm else (None, head)
        out.append(
            {
                "id": m.group(1),
                "class": cls,
                "title": title.strip(),
                "due": parse_short_date(due.group(1)) if due else None,
                "status": strip_html(p.get("description") or "").strip() or None,
            }
        )

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
    att = [
        strip_html(a).strip()
        for a in re.findall(r"<a\b[^>]*>(.*?)</a>", fields.get("Attachments", ""), re.S)
    ]

    def txt(k):
        v = re.sub(r"[ \t]*\n[ \t]*", "\n", strip_html(fields.get(k, ""))).strip()
        return re.sub(r"\n{3,}", "\n\n", v) or None

    return {
        "title": txt("Title"),
        "due": parse_short_date(txt("Due")),
        "course": txt("Course"),
        "marking": txt("Marking"),
        "status": txt("Status"),
        "submission_type": txt("Submission Type"),
        "instructions": txt("Instructions"),
        "attachments": [a for a in att if a],
    }


def get_assignments(opener, base, child, year, segments, details):
    sid = child["student_id"]
    years = []
    if not year:  # discover the selected (current) academic year
        years, sel = academic_years(
            json.loads(req(opener, base + ASSIGN_PATH.format(id=sid) + JS)), "Academic year"
        )
        year = sel["id"] if sel else None
        if not year:
            return {"academic_year": None, "error": "no academic year found"}
    label = next((y["label"] for y in years if y["id"] == str(year)), None)
    counts = {}
    try:
        for k in json.loads(req(opener, base + ASSIGN_KPI_PATH.format(id=sid, year=year))):
            seg = re.search(r"assignments-(\w+)/", k.get("url", "") or "")
            if seg:
                counts[seg.group(1)] = (
                    int(k["mainValue"])
                    if str(k.get("mainValue", "")).isdigit()
                    else k.get("mainValue")
                )
    except Exception:
        log("assignments KPI failed")
    rec = {"academic_year": {"id": str(year), "label": label}, "counts": counts}
    for seg in segments:
        rows = parse_assignment_rows(
            req(opener, base + ASSIGN_LIST_PATH.format(segment=seg, id=sid, year=year) + JS)
        )
        if details:
            for r in rows:
                try:
                    d = parse_schoolwork(
                        req(
                            opener,
                            base
                            + SCHOOLWORK_PATH.format(wid=r["id"], id=sid, segment=seg, year=year)
                            + JS,
                        )
                    )
                    r.update(
                        {
                            k: d[k]
                            for k in (
                                "course",
                                "marking",
                                "submission_type",
                                "instructions",
                                "attachments",
                            )
                        }
                    )
                    r["status"] = d["status"] or r["status"]
                except urllib.error.HTTPError as e:
                    r["error"] = f"detail fetch failed: HTTP {e.code}"
        rec[seg] = rows
    return rec
