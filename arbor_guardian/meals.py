"""Meal choices (experimental) — list, options, and opt-in basket writes."""

import datetime as dt
import json
import re
import urllib.error

from .constants import JS, MEAL_CHOICES_PATH, MEAL_GRID_PATH, MEAL_OPTIONS_PATH, WEEKDAYS
from .http import req
from .util import html_text, parse_short_date, strip_html, walk


def parse_meal_menus(raw):
    """meal-choices page -> [{menu_id, period, availability}]."""
    out = []

    def visit(n):
        if n.get("xtype") != "mis-property-row":
            return
        p = n.get("props", {}) or {}
        m = re.search(r"/meal-rotation-menu-id/(\d+)", p.get("url", "") or "")
        if m:
            out.append(
                {
                    "menu_id": m.group(1),
                    "period": strip_html(p.get("fieldLabel") or "").strip() or None,
                    "availability": strip_html(str(p.get("value") or "")).strip() or None,
                }
            )

    walk(json.loads(raw), visit)
    return out


def parse_meal_grid(raw):
    """setup-meal-choices grid -> (title, basket_pending, days).

    Sections are 'Week beginning DD Mon YYYY'; rows are weekday -> 'N Dish -
    Pudding' with a status description ('In basket', 'Deadline passed', ...).
    The leading number's meaning is not yet known; it is kept as `code`."""
    data = json.loads(raw)
    title, pending, days = [None], [False], []

    def visit(n, week=None):
        if not isinstance(n, (dict, list)):
            return
        if isinstance(n, list):
            for v in n:
                visit(v, week)
            return
        xt = n.get("xtype")
        p = n.get("props", {}) or {}
        if xt == "mis-layoutcolumn" and not title[0]:
            title[0] = (p.get("columnTitle") or {}).get("title")
        elif xt == "mis-banner" and "basket" in (p.get("title") or "").lower():
            pending[0] = True
        elif xt == "mis-section":
            m = re.search(r"Week beginning\s+(.+)$", (p.get("title") or "").replace("\xa0", " "))
            if m:
                week = parse_short_date(m.group(1))
        elif xt == "mis-property-row":
            wd = (p.get("fieldLabel") or "").strip()
            m = re.search(r"/date/(\d{4}-\d{2}-\d{2})", p.get("url", "") or "")
            date = m.group(1) if m else None
            if not date and week and wd in WEEKDAYS:
                date = (
                    dt.date.fromisoformat(week) + dt.timedelta(days=WEEKDAYS.index(wd))
                ).isoformat()
            val = strip_html(str(p.get("value") or "")).strip()
            cm = re.match(r"(\d+)\s+(.*)$", val)
            days.append(
                {
                    "date": date,
                    "weekday": wd or None,
                    "code": cm.group(1) if cm else None,
                    "meal": (cm.group(2) if cm else val) or None,
                    "status": strip_html(p.get("description") or "").strip() or None,
                    "editable": bool(m),
                }
            )
        for k, v in n.items():
            if k != "props":
                visit(v, week)

    visit(data)
    return title[0], pending[0], days


def get_meals(opener, base, child, since, until):
    sid = child["student_id"]
    try:
        menus = parse_meal_menus(req(opener, base + MEAL_CHOICES_PATH.format(id=sid) + JS))
    except urllib.error.HTTPError as e:
        return {
            "meal_choices_available": False,
            "note": f"no meal-choice UI (HTTP {e.code})",
            "menus": [],
        }
    for mn in menus:
        t, pending, days = parse_meal_grid(
            req(opener, base + MEAL_GRID_PATH.format(menu=mn["menu_id"], id=sid) + JS)
        )
        if since:
            days = [d for d in days if d["date"] and d["date"] >= since.isoformat()]
        if until:
            days = [d for d in days if d["date"] and d["date"] <= until.isoformat()]
        mn.update({"title": t, "basket_pending": pending, "days": days})
    return {"meal_choices_available": bool(menus), "menus": menus}


# ---- meals: per-day options and (opt-in) basket provisions ------------------
#
# The per-day slideover (setup-meal-choice/.../date/D) is a form: hidden
# `student` + `is_mobile_app`, one TagField `meal_provision_<unix>` per date in
# the slideover's range, and a "Process" FormAction whose actionUrl is
# /guardians/basket/process-meal-provisions/... . The portal JS
# (Mis.application.Form.submitMisForm, mode "form") posts every field mapped to
# the action's formActionName as JSON {"fields": {name: {"value": v}}} to
# actionUrl + "/?format=json"; a hidden field submits its raw string, a
# single-select TagField its value ("100_01") or null for "No choice selected".
# `basketItems` belongs to a different form action (delete-meal-provisions) and
# is NOT part of the Process body. Nothing here ever checks out or pays.


def submit_value(field):
    """Mirror the portal's getSubmitValue for the field kinds in the slideover."""
    if field["kind"] == "tag":
        sel = [o["value"] for o in field["options"] if o["selected"]]
        v = sel[0] if sel else None
        return v if v not in ("", None) else None
    return field["value"]


def parse_meal_slideover(raw):
    """setup-meal-choice slideover -> {action_url, form_action, deadline_note,
    basket_items, fields:[...], days:[...]} (field names discovered, not assumed)."""
    data = json.loads(raw)
    action, fields, notes, basket = [None], [], [], []

    def visit(n):
        xt = n.get("xtype")
        p = n.get("props", {}) or {}
        if xt == "mis-button-form-action" and "process-meal-provisions" in str(
            (p.get("currentAction") or {}).get("actionUrl") or ""
        ):
            action[0] = p["currentAction"]
        elif xt == "mis-simple-text" and isinstance(n.get("content"), str):
            notes.append(html_text(n["content"]))
        elif xt in ("hidden", "mis-tagfield") and p.get("name"):
            maps = list((p.get("actionMappings") or {}).keys())
            if p["name"] == "basketItems":
                try:
                    basket.extend(x.get("value") for x in json.loads(p.get("value") or "[]"))
                except (ValueError, AttributeError):
                    pass
            f = {
                "name": p["name"],
                "mappings": maps,
                "kind": "tag" if xt == "mis-tagfield" else "hidden",
            }
            if xt == "mis-tagfield":
                f["label"] = (p.get("fieldLabel") or "").replace("\xa0", " ").strip()
                f["editable"] = (
                    bool(p.get("editable", True))
                    and not p.get("readOnly")
                    and not p.get("disabled")
                )
                f["options"] = []
                for o in p.get("options") or []:
                    of = o.get("fields", {}) or {}
                    f["options"].append(
                        {
                            "value": (of.get("value") or {}).get("value"),
                            "label": html_text((of.get("label") or {}).get("value") or ""),
                            "selected": bool((of.get("selected") or {}).get("value")),
                        }
                    )
            else:
                f["value"] = p.get("value")
            fields.append(f)

    walk(data, visit)
    act = action[0] or {}
    fan = act.get("formActionName")
    form_fields = [f for f in fields if fan and fan in f["mappings"]]
    days = []
    for f in form_fields:
        if f["kind"] != "tag":
            continue
        date = parse_short_date(re.sub(r"^[A-Za-z]+,\s*", "", f["label"]))
        m = re.match(r"meal_provision_(\d+)$", f["name"])
        if not date and m:  # unix midnight, school-local
            date = dt.datetime.fromtimestamp(int(m.group(1))).date().isoformat()
        opts = []
        for o in f["options"]:
            lm = re.match(r"(\d+)\s+(.*?)(?:\s+-\s+(£[\d.]+))?$", o["label"])
            pm = re.search(r"\s+-\s+(£[\d.]+)$", o["label"])
            opts.append(
                {
                    "value": o["value"] or None,
                    "label": o["label"],
                    "code": lm.group(1) if lm else None,
                    "meal": (lm.group(2) if lm else re.sub(r"\s+-\s+£[\d.]+$", "", o["label"]))
                    or None,
                    "price": pm.group(1) if pm else None,
                    "selected": o["selected"],
                }
            )
        sel = next((o for o in opts if o["selected"]), None)
        days.append(
            {
                "date": date,
                "field": f["name"],
                "editable": f["editable"],
                "selected": sel["value"] if sel else None,
                "selected_label": sel["label"] if sel else None,
                "options": opts,
            }
        )
    return {
        "action_url": act.get("actionUrl"),
        "form_action": fan,
        "deadline_note": next((t for t in notes if "deadline" in t.lower()), None),
        "basket_items": len(basket),
        "fields": form_fields,
        "days": days,
    }


PROCESS_MEALS_RE = re.compile(
    r"^/guardians/basket/process-meal-provisions/meal-rotation-menu-id/\d+"
    r"/start-date/\d{4}-\d{2}-\d{2}/end-date/\d{4}-\d{2}-\d{2}/?$"
)
FORBIDDEN_WRITE_RE = re.compile(
    r"(?i)checkout|my-basket|/pay|pay-|top-up|topup|buy|"
    r"card|stripe|delete|remove"
)


def meal_process_path(action_url):
    """The ONLY write this tool can make. Anything else is refused."""
    u = action_url or ""
    if not PROCESS_MEALS_RE.match(u) or FORBIDDEN_WRITE_RE.search(u):
        raise SystemExit(f"refusing write to non-meal-process URL: {u!r}")
    return u.rstrip("/") + "/?format=json"


def menus_for(opener, base, child, menu_id):
    menus = parse_meal_menus(
        req(opener, base + MEAL_CHOICES_PATH.format(id=child["student_id"]) + JS)
    )
    if menu_id:
        menus = [m for m in menus if m["menu_id"] == str(menu_id)]
    return menus


def get_meal_slideover(opener, base, menu, sid, date):
    return parse_meal_slideover(
        req(opener, base + MEAL_OPTIONS_PATH.format(menu=menu, id=sid, date=date) + JS)
    )


def public_days(so):
    return [
        {k: d[k] for k in ("date", "editable", "selected", "selected_label", "options")}
        for d in so["days"]
    ]


def get_meal_options(opener, base, child, since, until, menu_id):
    """Per-day options for editable days in range. One GET per slideover (a
    slideover can cover several days, so covered dates are skipped)."""
    sid = child["student_id"]
    try:
        menus = menus_for(opener, base, child, menu_id)
    except urllib.error.HTTPError as e:
        return {
            "meal_choices_available": False,
            "note": f"no meal-choice UI (HTTP {e.code})",
            "menus": [],
        }
    for mn in menus:
        if since and until and since == until:
            dates = [since.isoformat()]
        else:
            _, _, grid = parse_meal_grid(
                req(opener, base + MEAL_GRID_PATH.format(menu=mn["menu_id"], id=sid) + JS)
            )
            dates = sorted(
                {
                    d["date"]
                    for d in grid
                    if d["editable"]
                    and d["date"]
                    and (not since or d["date"] >= since.isoformat())
                    and (not until or d["date"] <= until.isoformat())
                }
            )
        seen, days, note = set(), [], None
        for d in dates:
            if d in seen:
                continue
            so = get_meal_slideover(opener, base, mn["menu_id"], sid, d)
            note = note or so["deadline_note"]
            for day in public_days(so):
                if day["date"] in seen:
                    continue
                seen.add(day["date"])
                if (not since or day["date"] >= since.isoformat()) and (
                    not until or day["date"] <= until.isoformat()
                ):
                    days.append(day)
        mn.update({"deadline_note": note, "options": sorted(days, key=lambda x: x["date"] or "")})
    return {"meal_choices_available": bool(menus), "menus": menus}


def notification_text(resp):
    out = []
    for n in resp.get("notifications") or []:
        if isinstance(n, dict):
            t = " ".join(
                html_text(str(n.get(k) or "")) for k in ("title", "message", "text") if n.get(k)
            )
            out.append(t.strip() or html_text(json.dumps(n, ensure_ascii=False)))
        else:
            out.append(html_text(str(n)))
    return out


def set_meal_provisions(opener, base, child, sets, menu_id, commit):
    """Apply {date: value|None} via the slideover's own Process form.

    Without commit: dry run (shows before/after + the exact body; no POST).
    With commit: POST process-meal-provisions only (adds/changes BASKET items;
    confirmation still needs portal checkout), then re-read and verify."""
    sid = child["student_id"]
    menus = menus_for(opener, base, child, menu_id)
    if len(menus) != 1:
        raise SystemExit(
            f"--set needs exactly one meal menu (found {[m['menu_id'] for m in menus]}); "
            "pass --menu-id"
        )
    menu = menus[0]["menu_id"]
    pending = dict(sets)
    results = []
    while pending:
        first = min(pending)
        so = get_meal_slideover(opener, base, menu, sid, first)
        url = meal_process_path(so["action_url"])
        body = {f["name"]: submit_value(f) for f in so["fields"]}
        changes = []
        for day in so["days"]:
            if day["date"] not in pending:
                continue
            want = pending.pop(day["date"])
            valid = {o["value"] for o in day["options"] if o["value"]}
            if want is not None and want not in valid:
                raise SystemExit(
                    f"{day['date']}: {want!r} is not an option (valid: {sorted(valid)} or 'none')"
                )
            if not day["editable"]:
                raise SystemExit(f"{day['date']}: not editable (deadline passed?)")
            lab = next(
                (o["label"] for o in day["options"] if o["value"] == want), "No choice selected"
            )
            changes.append(
                {
                    "date": day["date"],
                    "before": day["selected"],
                    "before_label": day["selected_label"],
                    "after": want,
                    "after_label": lab,
                }
            )
            body[day["field"]] = want
        if first in pending:
            raise SystemExit(
                f"{first}: no meal choice for this date in menu {menu} "
                "(holiday, deadline passed, or outside the menu range)"
            )
        payload = {"fields": {k: {"value": v} for k, v in body.items()}}
        rec = {"menu_id": menu, "process_url": url.split("?")[0].rstrip("/"), "changes": changes}
        if not any(c["before"] != c["after"] for c in changes):
            rec["status"] = "unchanged (no POST)"
        elif not commit:
            rec.update({"status": "dry-run (pass --add-to-basket to POST)", "body": payload})
        else:
            resp = json.loads(req(opener, base + url, data=payload))
            rec["success"] = bool(resp.get("success"))
            rec["notifications"] = notification_text(resp)
            errs = (resp.get("action_params") or {}).get("validation_errors")
            if errs:
                rec["validation_errors"] = errs
            after = get_meal_slideover(opener, base, menu, sid, first)
            now = {d["date"]: d["selected"] for d in after["days"]}
            rec["verified"] = all(now.get(c["date"]) == c["after"] for c in changes)
            rec["status"] = "basket updated" if rec["verified"] else "NOT verified"
        results.append(rec)
    return {
        "meal_updates": results,
        "note": (
            "Basket only: choices are NOT confirmed until checkout in the portal "
            "(this tool never checks out or pays)."
        ),
    }
