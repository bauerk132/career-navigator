"""Synthetic acceptance tests for CN-owned pay parsing (A1; JF-08/40/41)."""
import unittest

from career_navigator.pay import annual_low, floor_check, parse_pay


class ParsePayTests(unittest.TestCase):
    def test_hourly_range(self):
        p = parse_pay("$20 - $25 an hour")
        self.assertEqual((p.status, p.low, p.high, p.unit), ("bounded", 20.0, 25.0, "hour"))

    def test_annual_range_with_commas(self):
        p = parse_pay("$50,000 - $60,000 a year")
        self.assertEqual((p.status, p.low, p.high, p.unit), ("bounded", 50000.0, 60000.0, "year"))

    def test_weekly_single_value(self):
        p = parse_pay("$800 a week")
        self.assertEqual((p.status, p.low, p.high, p.unit), ("bounded", 800.0, 800.0, "week"))

    def test_monthly_single_value(self):
        p = parse_pay("$3,000 per month")
        self.assertEqual((p.status, p.low, p.unit), ("bounded", 3000.0, "month"))

    def test_en_dash_and_slash_unit(self):
        p = parse_pay("$20–$25/hour")
        self.assertEqual((p.status, p.low, p.high, p.unit), ("bounded", 20.0, 25.0, "hour"))

    def test_open_ended_from(self):
        p = parse_pay("From $15 an hour")
        self.assertEqual((p.status, p.low, p.high), ("open_ended", 15.0, None))
        self.assertTrue(p.open_ended)

    def test_open_ended_up_to(self):
        p = parse_pay("Up to $20 an hour")
        self.assertEqual((p.status, p.low, p.high), ("open_ended", None, 20.0))

    def test_missing_stays_missing(self):
        for text in ("", None, "   "):
            p = parse_pay(text)
            self.assertEqual(p.status, "missing")
            self.assertIsNone(p.low)
            self.assertIsNone(p.high)

    def test_present_but_unparseable(self):
        p = parse_pay("competitive")
        self.assertEqual(p.status, "unparseable")

    def test_amount_without_unit_is_unparseable_not_guessed(self):
        p = parse_pay("$20")
        self.assertEqual(p.status, "unparseable")

    def test_non_usd_currency_not_coerced(self):
        p = parse_pay("€20 an hour")
        self.assertEqual(p.status, "unparseable")


class FloorTests(unittest.TestCase):
    def test_unrounded_hourly_below_floor(self):
        p = parse_pay("$19.999 an hour")
        self.assertAlmostEqual(annual_low(p), 19.999 * 40 * 52)
        self.assertIs(floor_check(p, 20.0 * 40 * 52), False)

    def test_exact_floor_passes(self):
        p = parse_pay("$20 an hour")
        self.assertIs(floor_check(p, 20.0 * 40 * 52), True)

    def test_weekly_annualized_with_explicit_assumptions(self):
        p = parse_pay("$800 a week")
        self.assertAlmostEqual(annual_low(p), 800.0 * 52)
        self.assertIs(floor_check(p, 41600.0), True)

    def test_monthly_annualized(self):
        p = parse_pay("$3,000 per month")
        self.assertAlmostEqual(annual_low(p), 36000.0)

    def test_missing_pay_floor_is_unknown_not_false(self):
        self.assertIsNone(floor_check(parse_pay(""), 41600.0))
        self.assertIsNone(annual_low(parse_pay("")))

    def test_unparseable_pay_floor_is_unknown(self):
        self.assertIsNone(floor_check(parse_pay("competitive"), 41600.0))

    def test_open_ended_low_below_floor_fails(self):
        self.assertIs(floor_check(parse_pay("From $15 an hour"), 41600.0), False)

    def test_open_ended_up_to_only_is_unknown(self):
        self.assertIsNone(floor_check(parse_pay("Up to $30 an hour"), 41600.0))

    def test_conversion_assumptions_are_explicit(self):
        p = parse_pay("$10 an hour")
        self.assertAlmostEqual(annual_low(p, hours_per_week=30, weeks_per_year=50), 15000.0)


if __name__ == "__main__":
    unittest.main()