"""Tiny unit tests for pure helpers (no network)."""

import unittest

from arbor_guardian.cli import numeric_id
from arbor_guardian.constants import UNSAFE_URL_RE
from arbor_guardian.http import _retry_after_delay
from arbor_guardian.main import parse_meal_sets
from arbor_guardian.util import (
    labeled_fields,
    money,
    normalize_school,
    parse_short_date,
    safe_path,
    slug,
    strip_html,
)


class UtilTests(unittest.TestCase):
    def test_strip_html(self):
        self.assertEqual(strip_html("<b>Hi</b><br>there"), "Hi\nthere")

    def test_parse_short_date(self):
        self.assertEqual(parse_short_date("06 Oct 2026"), "2026-10-06")
        self.assertEqual(parse_short_date("06\xa0Oct\xa02026"), "2026-10-06")
        self.assertIsNone(parse_short_date("not a date"))

    def test_money(self):
        self.assertEqual(money("£12.50"), 12.5)
        self.assertEqual(money("-£1.00"), -1.0)
        self.assertIsNone(money("n/a"))

    def test_slug(self):
        self.assertEqual(slug("Positive Incidents"), "positive_incidents")

    def test_labeled_fields(self):
        fields, lead = labeled_fields("<b>Status:</b> Open <b>Dates:</b> Mon")
        self.assertEqual(fields["Status"], "Open")
        self.assertEqual(fields["Dates"], "Mon")
        self.assertIsNone(lead)

    def test_safe_path_refuses_writes(self):
        with self.assertRaises(ValueError):
            safe_path("/guardians/basket/checkout")
        self.assertEqual(
            safe_path("/guardians/student-ui/report-cards/student-id/1"),
            "/guardians/student-ui/report-cards/student-id/1",
        )
        # Absolute / protocol-relative URLs must not be joined onto the school base.
        for bad in ("https://evil.example/x", "//evil.example/x", "relative", ""):
            with self.assertRaises(ValueError):
                safe_path(bad)

    def test_parse_meal_sets(self):
        self.assertEqual(
            parse_meal_sets(["2026-10-19=100_01", "2026-10-20=none"]),
            {"2026-10-19": "100_01", "2026-10-20": None},
        )
        with self.assertRaises(SystemExit):
            parse_meal_sets(["bad"])

    def test_normalize_school(self):
        self.assertEqual(
            normalize_school("https://Example.uk.arbor.sc/"),
            "Example.uk.arbor.sc",
        )
        for bad in ("../../tmp/evil", "evil.com", "a/b.uk.arbor.sc", ""):
            with self.assertRaises(SystemExit):
                normalize_school(bad)


class CliArgvTests(unittest.TestCase):
    def test_top_level_help_not_messages(self):
        """`arbor_guardian.py --help` must show top-level help, not messages."""
        import subprocess
        import sys

        r = subprocess.run(
            [sys.executable, "arbor_guardian.py", "--help"],
            capture_output=True,
            text=True,
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        # Top-level lists the command set; messages-only help is
        # "arbor_guardian.py messages [-h]".
        self.assertNotIn("arbor_guardian.py messages", out)
        self.assertIn("{messages,children,", out)


class UnsafeUrlTests(unittest.TestCase):
    def test_segment_boundaries(self):
        # False positives the old regex had on read-looking paths.
        for ok in (
            "/guardians/foo/updated-thing",
            "/guardians/foo/created",
            "/guardians/foo/deleted",
            "/guardians/customer-account/payment-total-kpi/student-id/1",
        ):
            self.assertIsNone(UNSAFE_URL_RE.search(ok), ok)
        for bad in (
            "/guardians/basket/checkout",
            "/guardians/club-ui/register/club-id/1",
            "/guardians/basket/process-meal-provisions/x",
            "/guardians/something/pay-now",
            "/guardians/consent/form",
        ):
            self.assertIsNotNone(UNSAFE_URL_RE.search(bad), bad)


class RetryAfterTests(unittest.TestCase):
    def test_integer_seconds(self):
        self.assertEqual(_retry_after_delay({"Retry-After": "7"}, 0), 7.0)

    def test_http_date(self):
        import datetime as dt
        from email.utils import format_datetime

        when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=12)
        hdr = {"Retry-After": format_datetime(when)}
        delay = _retry_after_delay(hdr, 0)
        self.assertGreater(delay, 8.0)
        self.assertLess(delay, 15.0)

    def test_fallback_exponential(self):
        self.assertEqual(_retry_after_delay({"Retry-After": "soon"}, 3), 8.0)
        self.assertEqual(_retry_after_delay({}, 2), 4.0)


class NumericIdTests(unittest.TestCase):
    def test_numeric_id(self):
        import argparse

        self.assertEqual(numeric_id("12345"), "12345")
        with self.assertRaises(argparse.ArgumentTypeError):
            numeric_id("../etc/passwd")
        with self.assertRaises(argparse.ArgumentTypeError):
            numeric_id("12a")


class MealTimestampTests(unittest.TestCase):
    def test_provision_field_uses_utc(self):
        """meal_provision_<unix> fallback must not depend on machine local TZ."""
        import datetime as dt
        import json

        from arbor_guardian.meals import parse_meal_slideover

        # 2026-10-19 00:00:00 UTC
        ts = int(dt.datetime(2026, 10, 19, tzinfo=dt.timezone.utc).timestamp())
        # Minimal slideover: need form action + mapped field; empty label forces
        # the meal_provision_<unix> timestamp fallback.
        raw = json.dumps(
            {
                "xtype": "container",
                "items": [
                    {
                        "xtype": "mis-button-form-action",
                        "props": {
                            "currentAction": {
                                "actionUrl": (
                                    "/guardians/basket/process-meal-provisions/"
                                    "meal-rotation-menu-id/1/start-date/2026-10-19/"
                                    "end-date/2026-10-19"
                                ),
                                "formActionName": "processMealProvisions",
                            }
                        },
                    },
                    {
                        "xtype": "mis-tagfield",
                        "props": {
                            "name": f"meal_provision_{ts}",
                            "actionMappings": {"processMealProvisions": True},
                            "fieldLabel": "",
                            "options": [
                                {
                                    "fields": {
                                        "value": {"value": "100_01"},
                                        "label": {"value": "1 Pizza"},
                                        "selected": {"value": True},
                                    }
                                }
                            ],
                            "editable": True,
                        },
                    },
                ],
            }
        )
        so = parse_meal_slideover(raw)
        self.assertEqual(so["days"][0]["date"], "2026-10-19")


if __name__ == "__main__":
    unittest.main()
