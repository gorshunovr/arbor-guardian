"""HTTP helpers: opener, throttled requests, form/bytes variants."""

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from http.cookiejar import MozillaCookieJar

from .constants import (
    MAX_BACKOFF,
    MAX_RETRIES,
    MIN_INTERVAL,
    TIMEOUT,
    USER_AGENT,
    WAF_BODY_MARKER,
    WAF_HEADER,
)
from .util import log

_last_request = [0.0]

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": USER_AGENT,
}


class ArborBlocked(Exception):
    """Arbor's edge firewall (WAF) refused the request. Retrying will not help."""

    def __init__(self, url, code=403):
        self.url = url
        self.code = code
        host = urllib.parse.urlsplit(url).hostname or url
        super().__init__(
            f"Arbor's firewall is refusing requests to {host} from this network "
            f"(HTTP {code}, WAF block, not a credentials problem). Not retrying. "
            "Run from a different network, pass --school/$ARBOR_SCHOOL to skip "
            "login.arbor.sc discovery, or ask Arbor (myteam@arbor-education.com) "
            "to review the block."
        )


def is_waf_block(code, headers, body):
    """True when a response is Arbor's WAF refusal rather than an app-level error."""
    if code not in (403, 405, 406):  # 429 is throttling: back off instead
        return False
    if headers is not None and headers.get(WAF_HEADER):
        return True
    if isinstance(body, bytes):
        body = body[:2048].decode("utf-8", "replace")
    return WAF_BODY_MARKER.lower() in (body or "").lower()


def _retry_after_delay(headers, attempt):
    """Seconds to wait from Retry-After (integer or HTTP-date), else exponential."""
    ra = headers.get("Retry-After") if headers else None
    if ra is not None:
        s = str(ra).strip()
        if s.isdigit():
            return float(s)
        try:
            when = parsedate_to_datetime(s)
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt.timezone.utc)
            return max(0.0, (when - dt.datetime.now(dt.timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError, IndexError):
            pass
    return 2.0**attempt


def build_opener(cookie_file):
    cj = MozillaCookieJar(cookie_file)
    if os.path.exists(cookie_file):
        try:
            cj.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = list(DEFAULT_HEADERS.items())
    return opener, cj


def _open(request, opener=None, raw=False):
    """Perform a request with inter-request throttling and 429/503 backoff.
    A WAF refusal raises ArborBlocked immediately (never retried).
    raw=True returns (bytes, headers) instead of decoded text."""
    for name, value in DEFAULT_HEADERS.items():
        if not request.has_header(name.capitalize()):
            request.add_header(name, value)
    for attempt in range(MAX_RETRIES + 1):
        wait = MIN_INTERVAL - (time.monotonic() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        try:
            fn = opener.open if opener is not None else urllib.request.urlopen
            with fn(request, timeout=TIMEOUT) as resp:
                body = resp.read()
                if raw:
                    return body, resp.headers
                return body.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                try:
                    err_body = e.read(4096)
                except Exception:
                    err_body = b""
                if is_waf_block(e.code, e.headers, err_body):
                    raise ArborBlocked(request.full_url, e.code) from None
                if e.code in (429, 503) and attempt < MAX_RETRIES:
                    delay = min(_retry_after_delay(e.headers, attempt), MAX_BACKOFF)
                    log(
                        f"Arbor returned {e.code}; backing off {delay:.0f}s "
                        f"(attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    time.sleep(delay)
                    continue
                raise
            finally:
                try:
                    e.read()
                except Exception:
                    pass
                try:
                    e.close()
                except Exception:
                    pass
        finally:
            _last_request[0] = time.monotonic()
    raise SystemExit("giving up after repeated 429/503 from Arbor")


def req(opener, url, data=None, content_type=None):
    headers = {}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = content_type or "application/json"
    r = urllib.request.Request(
        url, data=body, headers=headers, method="POST" if body is not None else "GET"
    )
    return _open(r, opener)


def req_form(opener, url, fields):
    """POST application/x-www-form-urlencoded; returns (bytes, headers)."""
    body = urllib.parse.urlencode(fields).encode("utf-8")
    r = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    return _open(r, opener, raw=True)


def req_bytes(opener, url):
    return _open(urllib.request.Request(url), opener, raw=True)
