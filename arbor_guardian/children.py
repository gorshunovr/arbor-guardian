"""Discover children on a school's guardian account."""

import json
import re

from .constants import DASHBOARD_PATH
from .http import req
from .util import strip_html, walk


def discover_children(opener, base):
    """Children on a school's account, discovered from the dashboard.

    Multi-child schools expose an Arbor.selector.PageToggle listing every child.
    Single-child schools have no switcher, so fall back to the mis-info-panel
    (the current — and only — student's card)."""
    data = json.loads(req(opener, base + DASHBOARD_PATH))
    out, seen = [], set()

    def add(sid, name):
        if sid and sid not in seen:
            seen.add(sid)
            out.append({"student_id": sid, "name": (name or "").strip() or None})

    def toggle(n):
        if n.get("xtype") != "mis-page-toggle":
            return
        for opt in (n.get("props", {}) or {}).get("options", []) or []:
            m = re.search(r"/student-id/(\d+)", opt.get("value", "") or "")
            if m:
                add(m.group(1), opt.get("label"))

    walk(data, toggle)
    if not out:  # single-child school: use the info panel

        def panel(n):
            if n.get("xtype") != "mis-info-panel":
                return
            p = n.get("props", {}) or {}
            m = re.search(r"/(?:overview/id|student-id)/(\d+)", p.get("url", "") or "")
            if m:
                add(m.group(1), strip_html(p.get("title", "")))

        walk(data, panel)
    return out
