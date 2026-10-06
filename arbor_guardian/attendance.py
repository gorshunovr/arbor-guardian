"""Attendance KPIs, timetable events, balances, and attendance-by-date."""

import json
import os
import re
import urllib.error

from .constants import (
    ATT_BY_DATE_PATH,
    ATT_BY_DATE_YEAR,
    ATT_CERT_RE,
    CALENDAR_PATH,
    JS,
    KPIS_PATH,
    MARK_COLOURS,
    MEALS_BALANCE_PATH,
    OUTSTANDING_PATH,
)
from .http import req, req_bytes
from .parse import academic_years
from .util import (
    download_token,
    kpi_main_value,
    kpi_number,
    parse_short_date,
    strip_html,
    to_date,
    walk,
)


def get_attendance(opener, base, child):
    j = json.loads(req(opener, base + KPIS_PATH.format(id=child["student_id"])))
    kpis = []
    for it in j.get("items", []):
        f = it.get("fields", {})
        html_val = f.get("html", {}).get("value", "")
        kpis.append(
            {
                "title": f.get("title", {}).get("value"),
                "value_pct": kpi_number(html_val),
                "text": strip_html(html_val),
            }
        )
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
        events.append(
            {
                "start": start,
                "end": (f.get("end_datetime", {}) or {}).get("value"),
                "title": (f.get("title", {}) or {}).get("value"),
                "location": (f.get("location", {}) or {}).get("value"),
            }
        )
    return events


def get_meals_balance(opener, base, child):
    return kpi_main_value(req(opener, base + MEALS_BALANCE_PATH.format(id=child["student_id"])))


def get_outstanding(opener, base, any_child_id):
    try:
        arr = json.loads(req(opener, base + OUTSTANDING_PATH.format(id=any_child_id)))
        if arr:
            return {"title": arr[0].get("title"), "value": arr[0].get("mainValue")}
    except Exception:
        pass
    return None


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
            mark = "U"  # late after register closed: red clock, code U
        sessions.append(
            {
                "date": parse_short_date(m.group(1)),
                "session": m.group(2),
                "mark": mark,
                "description": re.sub(r"\s+(AM|PM)$", "", desc),
                "category": cat,
            }
        )

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
    body, hdr = req_bytes(opener, base + u + "?download-token=" + download_token())
    if not body.startswith(b"%PDF"):
        return {"error": f"response is not a PDF ({hdr.get('Content-Type')}, {len(body)} bytes)"}
    m = re.search(r"/academic-year-id/(\d+)", u)
    name = f"attendance-certificate-{sid}" + (f"-{m.group(1)}" if m else "") + ".pdf"
    dest = os.path.join(dest_dir, name)
    os.makedirs(dest_dir, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(body)
    return {"file": dest, "bytes": len(body)}


def get_attendance_by_date(
    opener, base, child, year, since, until, non_present, certificate_dir=None
):
    path = ATT_BY_DATE_PATH.format(id=child["student_id"])
    if year:
        path += ATT_BY_DATE_YEAR.format(year=year)
    raw = req(opener, base + path + JS)
    years, sel, sessions = parse_attendance_by_date(raw)
    cert = None
    if certificate_dir:
        try:
            cert = download_attendance_certificate(
                opener, base, raw, child["student_id"], certificate_dir
            )
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
    return {
        "academic_year": (
            {"id": sel["id"], "label": sel["label"]}
            if sel
            else ({"id": str(year), "label": None} if year else None)
        ),
        "academic_years": [{"id": y["id"], "label": y["label"]} for y in years],
        "summary": summary,
        "sessions": sessions,
        **({"certificate": cert} if certificate_dir else {}),
    }
