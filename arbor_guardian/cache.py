"""SQLite cache for immutable message bodies and response snapshots."""

import datetime as dt
import json
import os
import sqlite3
import time


def cache_open():
    path = os.path.expanduser(os.environ.get("ARBOR_CACHE_DB", "~/.cache/arbor/cache.sqlite3"))
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS message(
            school TEXT, msg_id TEXT, subject TEXT, snippet TEXT, datetime TEXT,
            received TEXT, sent_by TEXT, body TEXT, has_body INTEGER DEFAULT 0,
            unread INTEGER, first_seen TEXT, updated TEXT,
            PRIMARY KEY(school, msg_id));
        CREATE TABLE IF NOT EXISTS response(
            key TEXT PRIMARY KEY, json TEXT, fetched_at REAL);
    """)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return con


def msg_upsert_meta(con, school, m, now):
    con.execute(
        """INSERT INTO message(school,msg_id,subject,snippet,datetime,unread,first_seen,updated)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(school,msg_id) DO UPDATE SET
             subject=excluded.subject, snippet=excluded.snippet,
             datetime=excluded.datetime, unread=excluded.unread,
             updated=excluded.updated""",
        (school, m["id"], m["subject"], m["snippet"], m["datetime"], int(m["unread"]), now, now),
    )


def msg_store_body(con, school, mid, body, now):
    con.execute(
        """UPDATE message SET received=?, sent_by=?, body=?,
             subject=COALESCE(?, subject), has_body=1, updated=?
           WHERE school=? AND msg_id=?""",
        (body["received"], body["sent_by"], body["body"], body["subject"], now, school, mid),
    )


def msg_get_body(con, school, mid):
    r = con.execute(
        "SELECT subject,received,sent_by,body FROM message "
        "WHERE school=? AND msg_id=? AND has_body=1",
        (school, mid),
    ).fetchone()
    return dict(r) if r else None


def msg_list_cached(con, school):
    out = []
    for r in con.execute(
        "SELECT msg_id,subject,snippet,datetime,unread "
        "FROM message WHERE school=? "
        "ORDER BY datetime IS NULL, datetime DESC",
        (school,),
    ):
        dobj = None
        if r["datetime"]:
            try:
                dobj = dt.datetime.fromisoformat(r["datetime"]).date()
            except ValueError:
                pass
        out.append(
            {
                "id": r["msg_id"],
                "unread": bool(r["unread"]),
                "subject": r["subject"],
                "snippet": r["snippet"],
                "datetime": r["datetime"],
                "date_obj": dobj,
            }
        )
    return out


def resp_get(con, key):
    r = con.execute("SELECT json,fetched_at FROM response WHERE key=?", (key,)).fetchone()
    return (json.loads(r["json"]), r["fetched_at"]) if r else (None, None)


def resp_store(con, key, obj):
    con.execute(
        "INSERT INTO response(key,json,fetched_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET json=excluded.json, "
        "fetched_at=excluded.fetched_at",
        (key, json.dumps(obj, ensure_ascii=False), time.time()),
    )
    con.commit()
