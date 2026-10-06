"""School Messages subcommand (guardian-level)."""

import datetime as dt
import json
import re
import urllib.error

from .auth import session
from .cache import msg_get_body, msg_list_cached, msg_store_body, msg_upsert_meta
from .constants import LIST_PATH, VIEW_PATH
from .http import req
from .util import parse_date, strip_html, to_date, walk


def parse_list(raw):
    data = json.loads(raw)
    out, seen = [], set()

    def visit(n):
        if n.get("xtype") != "mis-property-row":
            return
        p = n.get("props", {})
        m = re.search(r"/id/(\d+)", p.get("url", "") or "")
        if not m or m.group(1) in seen:
            return
        seen.add(m.group(1))
        value = p.get("value", "") or ""
        unread = bool(re.search(r"·\s*NEW|\bNEW\b", value.replace("\xa0", " ")))
        parts = re.split(r"(?i)<br\s*/?>", value, maxsplit=1)
        subject = re.sub(r"·?\s*NEW\s*$", "", strip_html(parts[0])).strip()
        snippet = strip_html(parts[1]).strip() if len(parts) > 1 else ""
        iso, dobj = parse_date(p.get("description", ""))
        out.append(
            {
                "id": m.group(1),
                "unread": unread,
                "subject": subject,
                "snippet": snippet,
                "datetime": iso,
                "date_obj": dobj,
            }
        )

    walk(data, visit)
    return out


def parse_body(raw):
    data = json.loads(raw)
    fields, body_parts = {}, []

    def visit(n):
        xt = n.get("xtype")
        if xt == "mis-property-row":
            p = n.get("props", {})
            label = (p.get("fieldLabel") or "").strip()
            if label:
                fields[label] = strip_html(str(p.get("value", ""))).strip()
        elif xt == "mis-simple-text":
            body_parts.append(strip_html(str(n.get("content", ""))).strip())

    walk(data, visit)
    return {
        "subject": fields.get("Subject"),
        "received": fields.get("Received"),
        "sent_by": fields.get("Sent by"),
        "body": "\n\n".join([b for b in body_parts if b]) or None,
    }


def do_messages(args, email, pw, cookie_dir, schools, con):
    now = dt.datetime.now().isoformat(sep=" ", timespec="seconds")
    since, until = to_date(args.since), to_date(args.until)

    def keep(m):
        if args.mode == "unread" and not m["unread"]:
            return False
        if since and (m["date_obj"] is None or m["date_obj"] < since):
            return False
        if until and (m["date_obj"] is None or m["date_obj"] > until):
            return False
        return True

    summaries, records = [], []
    for s in schools:
        school = s["subdomain"]
        opener = base = None
        if args.offline:
            if con is None:
                raise SystemExit("--offline needs the cache (drop --no-cache).")
            msgs = msg_list_cached(con, school)
        else:
            opener, base = session(school, email, pw, cookie_dir)
            msgs = parse_list(req(opener, base + LIST_PATH))
            if con is not None:
                for m in msgs:
                    msg_upsert_meta(con, school, m, now)
                con.commit()

        selected = [m for m in msgs if keep(m)]
        if args.limit > 0:
            selected = selected[: args.limit]

        for m in selected:
            rec = {
                "school": school,
                "id": m["id"],
                "unread": m["unread"],
                "datetime": m["datetime"],
                "subject": m["subject"],
                "snippet": m["snippet"],
            }
            if args.bodies:
                cached = msg_get_body(con, school, m["id"]) if con is not None else None
                if cached:
                    rec.update(cached)
                    rec["from_cache"] = True
                elif args.offline:
                    rec["body"] = None
                    rec["error"] = "body not cached (offline)"
                else:
                    try:
                        body = parse_body(req(opener, base + VIEW_PATH.format(id=m["id"])))
                        rec.update(body)
                        rec["from_cache"] = False
                        if con is not None:
                            msg_store_body(con, school, m["id"], body, now)
                            con.commit()
                    except urllib.error.HTTPError as e:
                        rec["error"] = f"body fetch failed: HTTP {e.code}"
            records.append(rec)

        summaries.append(
            {
                "school": school,
                "name": s.get("name"),
                "total_in_inbox": len(msgs),
                "count": len(selected),
            }
        )

    return {
        "cmd": "messages",
        "mode": args.mode,
        "since": args.since,
        "until": args.until,
        "bodies": args.bodies,
        "offline": args.offline,
        "schools": summaries,
        "count": len(records),
        "messages": records,
    }
