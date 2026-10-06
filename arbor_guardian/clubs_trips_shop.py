"""Clubs, trips and school-shop catalogue (list / details only)."""

import json
import re
import urllib.error

from .constants import CLUBS_PATH, JS, SHOP_LIST_RE, SHOP_PATH, TRIPS_PATH
from .http import req
from .parse import overview_extras, parse_detail_page, section_rows, year_of
from .util import html_text, labeled_fields, safe_path


def club_group(title):
    t = (title or "").lower()
    if "can be registered" in t:
        return "available"
    if " was registered" in t:
        return "past"
    if "is registered" in t:
        return "registered"
    return None


def get_clubs(opener, base, child, details, files=False):
    data = json.loads(req(opener, base + CLUBS_PATH.format(id=child["student_id"]) + JS))
    clubs = []
    for sec, p, cid in section_rows(data, r"/club-id/(\d+)"):
        f, lead = labeled_fields(str(p.get("value") or ""))
        name = html_text(p.get("fieldLabel") or "")
        dates = f.pop("Club membership dates", None)
        tt = f.pop("Timetable", None)
        rec = {
            "club_id": cid,
            "name": re.sub(r"\s*\(\d{4}/\d{4}\)$", "", name) or None,
            "group": club_group(sec),
            "academic_year": year_of(name) or year_of(sec),
            "wraparound": "wraparound" in (p.get("url") or ""),
            "description": f.pop("Club description", None) or lead,
            "membership_dates": (
                [x.strip() for x in re.split(r",\s*", dates) if x.strip()] if dates else []
            ),
            "timetable": tt.split("\n") if tt else [],
        }
        rec.update({k.lower().replace(" ", "_"): v for k, v in f.items()})
        if details or files:
            rec.update(overview_extras(opener, base, p["url"], details, files))
        clubs.append(rec)
    return {"clubs": clubs}


def trip_group(title):
    t = (title or "").lower()
    return (
        "upcoming"
        if "upcoming" in t
        else "open"
        if "open to" in t
        else "past"
        if "past" in t
        else None
    )


def get_trips(opener, base, child, details, files=False):
    data = json.loads(req(opener, base + TRIPS_PATH.format(id=child["student_id"]) + JS))
    trips = []
    for sec, p, tid in section_rows(data, r"/trip-id/(\d+)"):
        f, lead = labeled_fields(str(p.get("value") or ""))
        rec = {
            "trip_id": tid,
            "name": html_text(p.get("fieldLabel") or "") or None,
            "group": trip_group(sec),
            "dates": f.pop("Trip Date(s)", None) or lead,
            "signup_window": f.pop("Signup Window", None),
        }
        rec.update({k.lower().replace(" ", "_"): v for k, v in f.items()})
        if details or files:
            rec.update(overview_extras(opener, base, p["url"], details, files))
        trips.append(rec)
    return {"trips": trips}


def get_shop(opener, base, child, details):
    """Catalogue only. The product list is an Ext data-store read (POST with
    paging params, no side effects); its URL is taken from the page and must
    match SHOP_LIST_RE. Product pages are GET-only; their 'Buy product' form
    is never submitted."""
    sid = child["student_id"]
    page = json.loads(req(opener, base + SHOP_PATH.format(id=sid) + JS))
    url = None
    for st in page.get("stores", []) or []:
        u = (((st.get("proxy") or {}).get("api") or {}).get("read")) or ""
        if SHOP_LIST_RE.match(u):
            url = u
    if not url:
        return {"products": [], "note": "no shop catalogue on this page"}
    j = json.loads(req(opener, base + url, data={"page": 1, "start": 0, "limit": 200}))
    products = []
    for it in j.get("items", []) or []:
        f = it.get("fields", {}) or {}
        view = ((it.get("actions") or {}).get("view")) or ""
        m = re.search(r"/product-id/(\d+)", view)
        rec = {
            "product_id": m.group(1) if m else None,
            "name": (f.get("name") or {}).get("value"),
            "price": (f.get("price") or {}).get("value"),
        }
        if details and view:
            try:
                d = parse_detail_page(req(opener, base + safe_path(view) + JS))
                pd = d["sections"].get("Product Details", {})
                rec.update({"description": pd.get("Description"), "notices": d["notices"]})
            except (urllib.error.HTTPError, ValueError) as e:
                rec["details_error"] = str(e)
        products.append(rec)
    return {"products": products}


# ---- attachment lists (clubs / trips) ---------------------------------------
