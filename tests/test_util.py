"""Tiny unit tests for pure helpers (no network)."""

import unittest

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


if __name__ == "__main__":
    unittest.main()
