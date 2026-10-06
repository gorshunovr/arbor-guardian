"""HTTP helpers: opener, throttled requests, form/bytes variants."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import MozillaCookieJar

from .constants import MAX_BACKOFF, MAX_RETRIES, MIN_INTERVAL, TIMEOUT
from .util import log

_last_request = [0.0]


def build_opener(cookie_file):
    cj = MozillaCookieJar(cookie_file)
    if os.path.exists(cookie_file):
        try:
            cj.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [
        ("Accept", "application/json, text/plain, */*"),
        ("User-Agent", "arbor-guardian/1.0"),
    ]
    return opener, cj


def _open(request, opener=None, raw=False):
    """Perform a request with inter-request throttling and 429/503 backoff.
    raw=True returns (bytes, headers) instead of decoded text."""
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
            if e.code in (429, 503) and attempt < MAX_RETRIES:
                ra = e.headers.get("Retry-After") if e.headers else None
                delay = float(ra) if (ra and str(ra).isdigit()) else 2.0**attempt
                delay = min(delay, MAX_BACKOFF)
                log(
                    f"Arbor returned {e.code}; backing off {delay:.0f}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(delay)
                continue
            raise
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
