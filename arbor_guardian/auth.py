"""School discovery, login and per-school session cookies."""

import json
import os
import re
import urllib.request

from .constants import LOGIN_PATH, SEARCH_BY_EMAIL, WHOAMI_PATH
from .http import _open, build_opener, req
from .util import ensure_private_dir, log, normalize_school


def discover_schools(email, pw):
    """Resolve the school subdomain(s) for an account from its credentials."""
    r = urllib.request.Request(
        SEARCH_BY_EMAIL,
        data=json.dumps({"email": email, "password": pw}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    j = json.loads(_open(r))
    out = []
    for p in j.get("payload", []):
        host = re.sub(r"^https?://", "", p.get("sisUrl") or "").strip("/")
        if host:
            try:
                host = normalize_school(host)
            except SystemExit:
                log("warning: skipping unexpected school host from discovery")
                continue
            out.append({"name": p.get("name"), "subdomain": host})
    return out


def is_logged_in(opener, base):
    try:
        j = json.loads(req(opener, base + WHOAMI_PATH))
        return bool(j.get("items", [{}])[0].get("logged_in"))
    except Exception:
        return False


def login(opener, cj, base, email, pw):
    j = json.loads(
        req(opener, base + LOGIN_PATH, data={"items": [{"username": email, "password": pw}]})
    )
    if not j.get("success") or not j.get("items", [{}])[0].get("logged_in"):
        issues = j.get("items", [{}])[0].get("loginIssues")
        raise SystemExit(f"login failed for {base}: success={j.get('success')} issues={issues}")
    try:
        ensure_private_dir(os.path.dirname(cj.filename))
        cj.save(ignore_discard=True, ignore_expires=True)
        try:
            os.chmod(cj.filename, 0o600)
        except OSError:
            pass
    except Exception as e:
        log(f"warning: could not persist cookies: {e}")


def session(school, email, pw, cookie_dir):
    """Return (opener, base_url) for a school, logging in if needed."""
    school = normalize_school(school)
    base = "https://" + school
    ensure_private_dir(cookie_dir)
    opener, cj = build_opener(os.path.join(cookie_dir, school + ".cookies"))
    if not is_logged_in(opener, base):
        login(opener, cj, base, email, pw)
    return opener, base
