"""Small shared helpers (logging, .env, HTML / date parsing)."""

import datetime as dt
import html
import json
import os
import re
import sys

from .constants import UNSAFE_URL_RE


def log(*a):
    print(*a, file=sys.stderr)


def load_dotenv(path):
    """Minimal .env loader: KEY=VALUE lines; existing env vars are not overridden."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k, v)


def normalize_school(host):
    """Validate a school host used for URLs and cookie filenames.

    Rejects path separators / ``..`` (cookie-jar path traversal) and hosts that
    are not under ``*.arbor.sc`` (avoids posting credentials to a typo / phishing
    host via ``--school``).
    """
    h = (host or "").strip()
    h = re.sub(r"^https?://", "", h, flags=re.I).strip().strip("/")
    if not h or "/" in h or "\\" in h or ".." in h:
        raise SystemExit(f"invalid school host: {host!r}")
    if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?", h):
        raise SystemExit(f"invalid school host: {host!r}")
    if not h.lower().endswith(".arbor.sc"):
        raise SystemExit(
            f"refusing non-Arbor host {h!r} (expected a *.arbor.sc subdomain; "
            "use --list-schools to see yours)"
        )
    return h


def ensure_private_dir(path):
    """Create *path* as a user-only directory (``0700``) when possible."""
    if not path:
        return
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def walk(node, fn):
    if isinstance(node, dict):
        fn(node)
        for v in node.values():
            walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            walk(v, fn)


def strip_html(s):
    s = re.sub(r"(?i)<br\s*/?>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).replace("\xa0", " ")


def parse_date(raw):
    """'17 June 2026, 14:07' (possibly with NBSP) -> (iso_str, date)."""
    if not raw:
        return None, None
    s = raw.replace("\xa0", " ").strip()
    for fmt in ("%d %B %Y, %H:%M", "%d %B %Y"):
        try:
            d = dt.datetime.strptime(s, fmt)
            return d.isoformat(sep=" "), d.date()
        except ValueError:
            continue
    return s, None


def to_date(s):
    return dt.datetime.strptime(s, "%Y-%m-%d").date() if s else None


def parse_short_date(raw):
    """'06 Oct 2026' / '06\xa0Oct\xa02026' -> 'YYYY-MM-DD' (or None)."""
    s = (raw or "").replace("\xa0", " ").strip()
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_short_datetime(raw):
    """'06 Oct 2026, 10:12' / 'Issued 14 Sep 2026' -> (prefix, 'YYYY-MM-DD', 'HH:MM')."""
    s = (raw or "").replace("\xa0", " ").strip()
    m = re.match(r"^(.*?)\s*(\d{1,2} [A-Za-z]{3,9} \d{4})(?:,\s*(\d{1,2}:\d{2}))?\s*$", s)
    if not m:
        return None, None, None
    return (m.group(1).strip() or None), parse_short_date(m.group(2)), m.group(3)


def html_text(s):
    """HTML fragment -> clean text; block boundaries become newlines."""
    s = re.sub(r"(?i)</(div|p|li|tr)>|<br\s*/?>", "\n", s or "")
    t = strip_html(s)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r",\n", ", ", t)
    return re.sub(r"\n{2,}", "\n", t).strip()


def labeled_fields(value_html):
    """'<b>Label:</b> value …' blocks -> (fields{label: text}, leading_text).

    Only bold text ending in ':' counts as a label, so values that contain
    colons ('Fridays: 15:15 - 16:10') are left alone."""
    parts = re.split(r"<b>\s*([^<]{1,60}?)\s*:\s*</b>", value_html or "")
    lead = html_text(parts[0]) or None
    fields = {}
    for i in range(1, len(parts) - 1, 2):
        fields[html_text(parts[i])] = html_text(parts[i + 1]) or None
    return fields, lead


def safe_path(path):
    """Refuse absolute URLs, protocol-relative URLs, and write-looking paths."""
    if (
        not path
        or not path.startswith("/")
        or path.startswith("//")
        or "://" in path
        or "\\" in path
        or UNSAFE_URL_RE.search(path)
    ):
        raise ValueError(f"refusing non-read URL: {path!r}")
    return path


def download_token():
    return "downloadtoken" + os.urandom(6).hex()


def slug(title):
    return re.sub(r"[^a-z0-9]+", "_", (title or "").lower()).strip("_") or "_"


def money(s):
    m = re.search(r"(-?)£\s?(-?[\d,]+\.\d{2})", s or "")
    return float(m.group(1) + m.group(2).replace(",", "")) if m else None


def first_number(s):
    m = re.search(r"-?\d+(?:\.\d+)?", s or "")
    if not m:
        return None
    return float(m.group(0)) if "." in m.group(0) else int(m.group(0))


def kpi_number(html_val):
    m = re.search(r"measure-value[^>]*>\s*([\d.]+)", html_val or "")
    return m.group(1) if m else None


def kpi_main_value(raw):
    try:
        arr = json.loads(raw)
        return arr[0].get("mainValue") if arr else None
    except Exception:
        return None
