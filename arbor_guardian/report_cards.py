"""Report cards list and optional PDF download."""

import json
import os
import re
import urllib.error

from .constants import JS, RC_CUSTOM_DOWNLOAD_RE, RC_DOWNLOAD_RE, REPORT_CARDS_PATH
from .http import req, req_bytes, req_form
from .parse import section_rows, year_of
from .util import download_token, html_text, parse_short_date, safe_path, walk


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
    token = download_token()
    for xt, a in acts:
        u, fan = a["actionUrl"], a.get("formActionName")
        if xt == "mis-button-form-download" and RC_DOWNLOAD_RE.match(u):
            fields = {
                h["name"]: {"value": h.get("value")}
                for h in hidden
                if fan in (h.get("actionMappings") or {})
            }
            body, hdr = req_form(
                opener,
                base + u,
                {
                    "payload": json.dumps({"fields": fields}, ensure_ascii=False),
                    "download-token": token,
                },
            )
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
        rec = {
            "id": rid,
            "kind": kind,
            "academic_year": year_of(sec),
            "date": parse_short_date(p.get("fieldLabel")),
            "title": html_text(str(p.get("value") or "")) or None,
        }
        if download_dir and (not card_ids or rid in card_ids):
            dest = os.path.join(download_dir, f"report-card-{child['student_id']}-{kind}-{rid}.pdf")
            try:
                rec["download"] = download_report_card(opener, base, p["url"], dest)
            except (urllib.error.HTTPError, ValueError) as e:
                rec["download"] = {"error": str(e)}
        cards.append(rec)
    return {"report_cards": cards}
