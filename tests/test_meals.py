"""Meal-grid status normalization, summary and coverage (synthetic data only)."""

import json
import unittest

from arbor_guardian.meals import (
    coverage_block,
    meal_status,
    meal_summary,
    meals_text,
    option_price,
    parse_meal_grid,
)


def row(weekday, value, description=None, date=None):
    props = {"fieldLabel": weekday, "value": value}
    if description is not None:
        props["description"] = description
    if date:
        props["url"] = (
            f"/guardians/meal-ui/setup-meal-choice/meal-rotation-menu-id/1/student-id/1/date/{date}"
        )
    return {"xtype": "mis-property-row", "props": props}


GRID = {
    "xtype": "mis-layoutcolumn",
    "props": {"columnTitle": {"title": "Meal Choices - Lunch"}},
    "items": [
        {
            "xtype": "mis-section",
            "props": {"title": "Week beginning 02 Mar 2026"},
            "items": [
                row("Monday", "0 Pasta Bake - Fruit Pot", "Deadline passed"),
                row("Tuesday", "1 Veggie Curry - Yoghurt", "", "2026-03-03"),
                row("Wednesday", "No choice selected", None, "2026-03-04"),
                row("Thursday", "2 Fish Pie - Sponge", "In basket", "2026-03-05"),
                row("Friday", "School holiday - no choices available"),
            ],
        },
        {
            "xtype": "mis-section",
            "props": {"title": "Week beginning 09 Mar 2026"},
            "items": [
                row("Monday", "3 Jacket Potato - Cookie", None, "2026-03-09"),
                row("Tuesday", "0 Roast Dinner - Crumble", "Something new", "2026-03-10"),
            ],
        },
    ],
}


class MealStatusTests(unittest.TestCase):
    def test_meal_status(self):
        self.assertEqual(meal_status("Pasta Bake - Fruit Pot", None, "0"), "booked")
        self.assertEqual(meal_status("Pasta Bake - Fruit Pot", "", "0"), "booked")
        self.assertEqual(meal_status("Pasta Bake", "In basket", "0"), "in_basket")
        self.assertEqual(meal_status("No choice selected", None), "empty")
        self.assertEqual(meal_status(None, None), "empty")
        self.assertEqual(meal_status("No choice selected", "In basket"), "in_basket")
        self.assertEqual(meal_status("Pasta Bake", "Deadline passed", "0"), "deadline_passed")
        self.assertEqual(meal_status("School holiday - no choices available", None), "not_offered")
        self.assertEqual(meal_status("Pasta", "Some new label", "0"), "unknown")

    def test_parse_grid_statuses(self):
        title, pending, days = parse_meal_grid(json.dumps(GRID))
        self.assertEqual(title, "Meal Choices - Lunch")
        got = [(d["date"], d["status"], d["arbor_status"]) for d in days]
        self.assertEqual(
            got,
            [
                ("2026-03-02", "deadline_passed", "Deadline passed"),
                ("2026-03-03", "booked", None),
                ("2026-03-04", "empty", None),
                ("2026-03-05", "in_basket", "In basket"),
                ("2026-03-06", "not_offered", None),
                ("2026-03-09", "booked", None),
                ("2026-03-10", "unknown", "Something new"),
            ],
        )
        self.assertEqual(days[1]["code"], "1")
        self.assertEqual(days[1]["meal"], "Veggie Curry - Yoghurt")
        self.assertIsNone(days[2]["code"])

    def test_summary(self):
        _, _, days = parse_meal_grid(json.dumps(GRID))
        self.assertEqual(
            meal_summary(days),
            {
                "days_total": 6,
                "booked": 2,
                "in_basket": 1,
                "empty": 1,
                "deadline_passed": 1,
                "not_offered": 1,
                "unknown": 1,
            },
        )
        self.assertEqual(meal_summary([])["days_total"], 0)


def slideover(date, options):
    return {"days": [{"date": date, "options": options}]}


class CoverageTests(unittest.TestCase):
    def test_option_price(self):
        opts = [
            {"value": "10_01", "price": "£2.50", "selected": False},
            {"value": "10_02", "price": "£2.50", "selected": False},
            {"value": None, "price": None, "selected": True},
        ]
        self.assertEqual(option_price(slideover("2026-03-04", opts), "2026-03-04"), 2.5)
        mixed = [
            {"value": "10_01", "price": "£2.50", "selected": False},
            {"value": "10_02", "price": "£3.00", "selected": False},
        ]
        self.assertIsNone(option_price(slideover("2026-03-04", mixed), "2026-03-04"))
        mixed[1]["selected"] = True
        self.assertEqual(option_price(slideover("2026-03-04", mixed), "2026-03-04"), 3.0)
        self.assertIsNone(option_price(slideover("2026-03-04", opts), "2026-03-05"))

    def test_coverage_block(self):
        c = coverage_block(4, 2.5, 6.0)
        self.assertEqual(
            (c["cost"], c["shortfall"], c["covered"], c["reason"]), (10.0, 4.0, False, None)
        )
        c = coverage_block(2, 2.5, 6.0)
        self.assertEqual((c["cost"], c["shortfall"], c["covered"]), (5.0, 0.0, True))
        c = coverage_block(0, None, 6.0)
        self.assertEqual((c["cost"], c["covered"], c["reason"]), (0.0, True, None))
        c = coverage_block(3, None, 6.0, "price not shown")
        self.assertIsNone(c["cost"])
        self.assertIsNone(c["covered"])
        self.assertEqual(c["reason"], "price not shown")
        c = coverage_block(3, 2.5, None)
        self.assertIsNone(c["covered"])
        self.assertTrue(c["reason"])

    def test_text(self):
        _, _, days = parse_meal_grid(json.dumps(GRID))
        out = {
            "cmd": "meals",
            "students": [
                {
                    "school": "example.uk.arbor.sc",
                    "student_id": "1",
                    "name": "Child",
                    "meal_choices_available": True,
                    "summary": meal_summary(days),
                    "coverage": coverage_block(2, 2.5, 6.0),
                    "menus": [{"menu_id": "1", "period": "March", "days": days}],
                }
            ],
        }
        txt = meals_text(out)
        self.assertIn("2026-03-03 Tue booked", txt)
        self.assertIn("in_basket", txt)
        self.assertIn("[In basket]", txt)
        self.assertIn("summary: 2/6 booked, 1 in basket, 1 empty", txt)
        self.assertIn("= £5.00 vs balance £6.00 -> covered", txt)


if __name__ == "__main__":
    unittest.main()
