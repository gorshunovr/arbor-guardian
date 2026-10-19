"""WAF-block detection and school-discovery fallback (no network)."""

import email.message
import io
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

from arbor_guardian import http, main
from arbor_guardian.auth import load_saved_schools, save_schools
from arbor_guardian.http import ArborBlocked, is_waf_block

WAF_BODY = b"This request has been blocked by Arbor's firewalls due to matching security filters."


def _headers(**kv):
    h = email.message.Message()
    for k, v in kv.items():
        h[k.replace("_", "-")] = v
    return h


class _Opener:
    def __init__(self, err):
        self.err = err
        self.calls = 0

    def open(self, request, timeout=None):
        self.calls += 1
        raise self.err


class WafTests(unittest.TestCase):
    def test_detect_by_header_or_body(self):
        self.assertTrue(is_waf_block(403, _headers(x_waf_arbor="denied-request"), b""))
        self.assertTrue(is_waf_block(403, _headers(), WAF_BODY))
        self.assertFalse(is_waf_block(403, _headers(), b'{"success":false}'))
        self.assertFalse(is_waf_block(401, _headers(x_waf_arbor="denied-request"), b""))
        self.assertFalse(is_waf_block(429, _headers(x_waf_arbor="denied-request"), b""))

    def test_open_raises_without_retry(self):
        url = "https://login.arbor.sc/applications/search-by-email"
        err = urllib.error.HTTPError(
            url, 403, "Forbidden", _headers(x_waf_arbor="denied-request"), io.BytesIO(WAF_BODY)
        )
        op = _Opener(err)
        with mock.patch.object(http, "MIN_INTERVAL", 0), self.assertRaises(ArborBlocked) as cm:
            http._open(urllib.request.Request(url), op)
        self.assertEqual(op.calls, 1)
        self.assertIn("login.arbor.sc", str(cm.exception))

    def test_honest_user_agent_added(self):
        r = urllib.request.Request("https://x.uk.arbor.sc/")
        op = _Opener(urllib.error.HTTPError(r.full_url, 404, "nf", _headers(), io.BytesIO(b"")))
        with mock.patch.object(http, "MIN_INTERVAL", 0), self.assertRaises(urllib.error.HTTPError):
            http._open(r, op)
        self.assertTrue(r.get_header("User-agent").startswith("arbor-guardian/"))


class DiscoveryFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "arbor")
        self.args = SimpleNamespace(all_schools=False, school=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_saved_schools_roundtrip_and_validation(self):
        save_schools(self.dir, [{"name": "A", "subdomain": "a.uk.arbor.sc"}])
        self.assertEqual(
            load_saved_schools(self.dir), [{"name": "A", "subdomain": "a.uk.arbor.sc"}]
        )
        self.assertEqual(os.stat(os.path.join(self.dir, "schools.json")).st_mode & 0o777, 0o600)
        with open(os.path.join(self.dir, "schools.json"), "w") as f:
            f.write('{"schools": [{"subdomain": "evil.example.com"}]}')
        self.assertEqual(load_saved_schools(self.dir), [])

    def test_fallback_when_blocked(self):
        save_schools(self.dir, [{"name": "A", "subdomain": "a.uk.arbor.sc"}])
        with (
            mock.patch.dict(os.environ, {"ARBOR_SCHOOL": ""}),
            mock.patch.object(
                main, "discover_schools", side_effect=ArborBlocked("https://login.arbor.sc/")
            ),
        ):
            got = main.resolve_schools(self.args, "e", "p", self.dir, None)
        self.assertEqual([s["subdomain"] for s in got], ["a.uk.arbor.sc"])

    def test_blocked_without_fallback_raises(self):
        with (
            mock.patch.dict(os.environ, {"ARBOR_SCHOOL": ""}),
            mock.patch.object(
                main, "discover_schools", side_effect=ArborBlocked("https://login.arbor.sc/")
            ),
            self.assertRaises(ArborBlocked),
        ):
            main.resolve_schools(self.args, "e", "p", self.dir, None)

    def test_successful_discovery_is_saved(self):
        found = [{"name": "B", "subdomain": "b.uk.arbor.sc"}]
        with (
            mock.patch.dict(os.environ, {"ARBOR_SCHOOL": ""}),
            mock.patch.object(main, "discover_schools", return_value=found),
        ):
            main.resolve_schools(self.args, "e", "p", self.dir, None)
        self.assertEqual(load_saved_schools(self.dir), found)


if __name__ == "__main__":
    unittest.main()
