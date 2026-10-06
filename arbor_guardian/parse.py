"""Shared Arbor component-tree parsers (page toggles, detail pages, attachments)."""

import json
import re
import urllib.error

from .constants import _FILE_FIELD_SKIP, FILES_LIST_RE, JS
from .http import req
from .util import html_text, labeled_fields, safe_path, strip_html, walk


def page_toggle(data, label):
    """Options of the mis-page-toggle with this fieldLabel: [{label,value,selected}]."""
    found = []

    def visit(n):
        if (
            n.get("xtype") == "mis-page-toggle"
            and (n.get("props", {}) or {}).get("fieldLabel") == label
        ):
            found.extend(n["props"].get("options", []) or [])

    walk(data, visit)
    return found


def academic_years(data, label):
    """Academic-year switcher -> ([{id,label,selected}], selected_or_None)."""
    years = []
    for o in page_toggle(data, label):
        m = re.search(r"/academic-year-id/(\d+)", o.get("value", "") or "")
        if m:
            years.append(
                {
                    "id": m.group(1),
                    "label": re.sub(r"^Year\s+", "", o.get("label") or ""),
                    "selected": bool(o.get("selected")),
                }
            )
    sel = next((y for y in years if y["selected"]), None)
    return years, sel


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
        if xt in (
            "mis-info-panel",
            "mis-subnavcolumn",
            "mis-widget-attachments",
            "mis-actionpanel",
            "hidden",
            "mis-number",
        ):
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
            if f:  # '<b>Status:</b> … <b>Dates:</b> …' blocks
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
            out.append(
                {"id": m.group(1), "label": o.get("label"), "selected": bool(o.get("selected"))}
            )
    return out


def year_of(title):
    m = re.search(r"(\d{4}/\d{4})", title or "")
    return m.group(1) if m else None


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
            u = "%s/%s/%s" % (
                (p.get("module") or "").rstrip("/"),
                p.get("fileController") or "",
                p.get("readAction") or "",
            )
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
