"""Behaviour points, incidents and detentions."""

import json
import re
import urllib.error

from .constants import BEHAVIOUR_PATH, JS, YEAR_SUFFIX
from .http import req
from .parse import academic_years
from .util import first_number, html_text, labeled_fields, parse_short_datetime, slug


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
                rec = {(slug(prefix) if prefix else "date"): d}
                if tm:
                    rec["time"] = tm
                rec.update({slug(k): v for k, v in f.items()})
                if lead:
                    rec["text"] = lead
                sections[sec]["items"].append(rec)
            else:
                txt = html_text(val) or None
                sections[sec]["totals"].append(
                    {
                        "group": sub,
                        "period": label or None,
                        "value": txt,
                        "number": first_number(txt),
                    }
                )
        for k, v in n.items():
            if k != "props":
                visit(v, sec, sub)

    visit(data.get("content", data))
    return years, sel, {slug(k): dict(v, title=k) for k, v in sections.items()}


def year_rec(sel, year):
    return (
        {"id": sel["id"], "label": sel["label"]}
        if sel
        else ({"id": str(year), "label": None} if year else None)
    )


def get_behaviour(opener, base, child, year, since, until):
    path = BEHAVIOUR_PATH.format(id=child["student_id"])
    if year:
        path += YEAR_SUFFIX.format(year=year)
    try:
        raw = req(opener, base + path + JS)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return {
                "behaviour_available": False,
                "note": f"behaviour page not available at this school (HTTP {e.code})",
            }
        raise
    years, sel, sections = parse_behaviour(raw)
    lo, hi = (since.isoformat() if since else None), (until.isoformat() if until else None)
    if lo or hi:
        for s in sections.values():

            def keep(r):
                d = next(
                    (
                        v
                        for k, v in r.items()
                        if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v)
                    ),
                    None,
                )
                return d and (not lo or d >= lo) and (not hi or d <= hi)

            s["items"] = [r for r in s["items"] if keep(r)]
    out = {
        "behaviour_available": bool(sections),
        "academic_year": year_rec(sel, year),
        "academic_years": [{"id": y["id"], "label": y["label"]} for y in years],
        "behaviour": sections,
    }
    if not sections:
        out["note"] = "the school publishes no behaviour data to guardians"
    return out
