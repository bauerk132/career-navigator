"""Fix Round 1 regressions for policy candidate 6da5663 (G2-01/G2-02/G2-03).

Authority: ChatGPT coordinator handoff (2026-11-15) and Antigravity's
independent review policy-commit-6da5663-review.md (hof_01M7FQJZMMKXVAZDG3).
Kyle approved G2-03: a bounded range whose low is below the $19/hr floor but
whose high reaches it goes to Needs Review, not Excluded.

Synthetic fixtures only. Every defect case below fails on 6da5663 and passes
after the targeted fix; guardrail cases lock the preserved policy semantics.
"""
import unittest

from career_navigator.eligibility import evaluate
from career_navigator.models import Posting
from career_navigator.pay import parse_pay
from career_navigator.policy import Policy


def posting(title="IT Support Technician", company="Acme Tech", location="Pittsburgh, PA",
            details="", pay_text="$25 an hour", url="https://x.example/1", **kw):
    return Posting(url=url, title=title, company=company, location=location,
                   details=details, pay_text=pay_text, **kw)


def factors(decision):
    return {r.factor: r for r in decision.reasons}


def annual_high_value(pay, **kw):
    from career_navigator.pay import annual_high
    return annual_high(pay, **kw)


# Ordinary English phrases ending in "only" that are not geographic claims.
# State-abbreviation collisions: in=IN, or=OR, id=ID, me=ME, oh=OH, hi=HI.
NON_GEOGRAPHIC_ONLY = (
    "Candidates with experience in Python only.",
    "Valid ID only.",
    "Bachelors or higher only.",
    "Full time only.",
    "Him or her only.",
    "Python only.",
    "ID only.",
)


class G201NonGeographicOnlyTests(unittest.TestCase):
    """Break caught: _ONLY captures arbitrary phrases; stopword abbrs invent regions."""

    def test_non_geo_phrase_does_not_degrade_explicit_pa(self):
        for phrase in NON_GEOGRAPHIC_ONLY:
            with self.subTest(phrase=phrase):
                d = evaluate(posting(location="Remote - Pennsylvania", details=phrase), Policy())
                self.assertEqual(d.status, "eligible")
                self.assertEqual(factors(d)["location"].outcome, "pass")

    def test_non_geo_phrase_does_not_degrade_nationwide(self):
        for phrase in NON_GEOGRAPHIC_ONLY:
            with self.subTest(phrase=phrase):
                d = evaluate(posting(location="Remote (United States)", details=phrase), Policy())
                self.assertEqual(d.status, "eligible")
                self.assertEqual(factors(d)["location"].outcome, "pass")

    def test_bare_remote_non_geo_phrase_invents_no_region(self):
        for phrase in NON_GEOGRAPHIC_ONLY:
            with self.subTest(phrase=phrase):
                d = evaluate(posting(location="Remote", details=phrase), Policy())
                self.assertEqual(d.status, "review")
                evidence = factors(d)["location"].evidence
                self.assertNotIn("restricted", evidence)
                self.assertNotIn("unclear", evidence)

    def test_pronoun_us_only_phrase_is_not_nationwide(self):
        d = evaluate(posting(location="Remote", details="Contact us only by email."), Policy())
        self.assertEqual(d.status, "review")
        evidence = factors(d)["location"].evidence
        self.assertNotIn("unclear", evidence)
        self.assertNotIn("nationwide", evidence)

    def test_genuine_geographic_only_still_classifies(self):
        cases = (
            ("California only.", "rejected"),
            ("CA, OR, ID only.", "rejected"),
            ("New York or New Jersey only.", "rejected"),
            ("Pennsylvania only.", "eligible"),
            ("US only.", "eligible"),
        )
        for details, status in cases:
            with self.subTest(details=details):
                d = evaluate(posting(location="Remote", details=details), Policy())
                self.assertEqual(d.status, status)


class G201CandidateScopedOnlyTests(unittest.TestCase):
    """Break caught: strict geo vocab drops genuine candidate-scope 'only' restrictions."""

    def test_candidate_scoped_state_only_still_excludes(self):
        cases = (
            "Applicants from California only.",
            "Candidates in Texas only.",
            "California and Oregon only.",
            "Employees based in NY only.",
        )
        for details in cases:
            with self.subTest(details=details):
                d = evaluate(posting(location="Remote", details=details), Policy())
                self.assertEqual(d.status, "rejected")
                self.assertEqual(factors(d)["location"].outcome, "exclude")

    def test_candidate_scoped_other_state_conflicts_with_pa_suffix(self):
        d = evaluate(posting(location="Remote - PA", details="Candidates in California only."), Policy())
        self.assertEqual(d.status, "review")
        self.assertEqual(factors(d)["location"].outcome, "review")

    def test_candidate_scoped_us_only_is_nationwide_not_other(self):
        for details in ("Candidates in the US only.", "Hiring in the US only."):
            with self.subTest(details=details):
                d = evaluate(posting(location="Remote", details=details), Policy())
                self.assertEqual(d.status, "eligible")
                self.assertEqual(factors(d)["location"].outcome, "pass")

    def test_candidate_scoped_pa_only_eligible(self):
        d = evaluate(posting(location="Remote", details="Applicants from Pennsylvania only."), Policy())
        self.assertEqual(d.status, "eligible")

    def test_candidate_subject_with_non_geo_content_invents_nothing(self):
        d = evaluate(posting(location="Remote - Pennsylvania",
                             details="Candidates with experience in Python only."), Policy())
        self.assertEqual(d.status, "eligible")
        self.assertEqual(factors(d)["location"].outcome, "pass")


class G202NationwideIncidentalTests(unittest.TestCase):
    """Break caught: unanchored state-name scan excludes nationwide/PA-eligible jobs."""

    def test_nationwide_with_california_headquarters_eligible(self):
        d = evaluate(posting(location="Remote",
                             details="Remote nationwide US. Headquartered in California."), Policy())
        self.assertEqual(d.status, "eligible")
        self.assertEqual(factors(d)["location"].outcome, "pass")

    def test_nationwide_with_delaware_incorporation_eligible(self):
        d = evaluate(posting(location="Remote",
                             details="Delaware corporation. Open to candidates nationwide."), Policy())
        self.assertEqual(d.status, "eligible")
        self.assertEqual(factors(d)["location"].outcome, "pass")

    def test_explicit_pa_with_incidental_headquarters_eligible(self):
        d = evaluate(posting(location="Remote - Pennsylvania",
                             details="Headquartered in California."), Policy())
        self.assertEqual(d.status, "eligible")
        self.assertEqual(factors(d)["location"].outcome, "pass")

    def test_client_location_invents_no_restriction(self):
        for details in ("Our customers live in California.", "Our clients reside in Texas."):
            with self.subTest(details=details):
                d = evaluate(posting(location="Remote", details=details), Policy())
                self.assertEqual(d.status, "review")
                self.assertNotIn("restricted", factors(d)["location"].evidence)

    def test_nationwide_with_explicit_pa_exclusion_rejected(self):
        d = evaluate(posting(location="Remote",
                             details="Remote nationwide US. Not available in Pennsylvania."), Policy())
        self.assertEqual(d.status, "rejected")
        self.assertEqual(factors(d)["location"].outcome, "exclude")

    def test_genuine_restriction_after_incidental_sentence_excludes(self):
        d = evaluate(posting(location="Remote",
                             details="Headquartered in California. Must reside in Texas."), Policy())
        self.assertEqual(d.status, "rejected")
        self.assertEqual(factors(d)["location"].outcome, "exclude")

    def test_candidate_residency_restriction_still_excludes(self):
        d = evaluate(posting(location="Remote",
                             details="Candidates must reside in California."), Policy())
        self.assertEqual(d.status, "rejected")

    def test_unverified_remote_eligibility_still_review(self):
        d = evaluate(posting(location="Remote", details="Great benefits and dental."), Policy())
        self.assertEqual(d.status, "review")
        self.assertEqual(factors(d)["location"].outcome, "review")


class G203SalaryStraddleTests(unittest.TestCase):
    """Break caught: bounded range crossing the floor is hard-excluded (Kyle approved review)."""

    def test_bounded_range_straddling_floor_is_review(self):
        # low annualized < 39520.0 <= high annualized (19/hr x 40 x 52)
        cases = (
            "$18 - $25 an hour",        # 37440 < 39520 <= 52000
            "$18-$19 an hour",          # high exactly at floor -> review
            "$17.50 - $19.50 an hour",  # 36400 < 39520 <= 40560
            "$37,440 - $52,000 a year",
            "$700 - $800 a week",       # 36400 < 39520 <= 41600
            "$3,000 - $3,500 per month",  # 36000 < 39520 <= 42000
        )
        for pay_text in cases:
            with self.subTest(pay_text=pay_text):
                d = evaluate(posting(pay_text=pay_text), Policy())
                self.assertEqual(d.status, "review")
                self.assertEqual(factors(d)["pay"].outcome, "review")

    def test_bounded_range_entirely_below_floor_rejected(self):
        for pay_text in ("$17 - $18 an hour", "$15-$17 an hour", "$37,000 - $38,000 a year"):
            with self.subTest(pay_text=pay_text):
                d = evaluate(posting(pay_text=pay_text), Policy())
                self.assertEqual(d.status, "rejected")
                self.assertEqual(factors(d)["pay"].outcome, "exclude")

    def test_bounded_range_at_or_above_floor_eligible(self):
        for pay_text in ("$19 - $25 an hour", "$19 an hour", "$25 an hour"):
            with self.subTest(pay_text=pay_text):
                d = evaluate(posting(pay_text=pay_text), Policy())
                self.assertEqual(d.status, "eligible")
                self.assertEqual(factors(d)["pay"].outcome, "pass")

    def test_open_ended_below_floor_still_review(self):
        d = evaluate(posting(pay_text="From $18 an hour"), Policy())
        self.assertEqual(d.status, "review")
        self.assertEqual(factors(d)["pay"].outcome, "review")

    def test_missing_salary_still_review(self):
        d = evaluate(posting(pay_text=""), Policy())
        self.assertEqual(d.status, "review")
        self.assertEqual(factors(d)["pay"].outcome, "review")

    def test_straddle_review_does_not_override_cdl_exclusion(self):
        d = evaluate(posting(pay_text="$18 - $25 an hour", details="CDL required."), Policy())
        self.assertEqual(d.status, "rejected")

    def test_straddle_review_preserves_other_factor_outcomes(self):
        d = evaluate(posting(pay_text="$18 - $25 an hour"), Policy())
        self.assertEqual(factors(d)["location"].outcome, "pass")
        self.assertEqual(factors(d)["pay"].outcome, "review")

    def test_straddle_rule_uses_configured_floor(self):
        d = evaluate(posting(pay_text="$18 - $25 an hour"), Policy(salary_floor_annual=41600.0))
        self.assertEqual(d.status, "review")
        self.assertEqual(factors(d)["pay"].outcome, "review")

    def test_no_floor_configured_straddle_stays_visible(self):
        d = evaluate(posting(pay_text="$18 - $25 an hour"), Policy(salary_floor_annual=None))
        self.assertEqual(d.status, "eligible")
        self.assertEqual(factors(d)["pay"].outcome, "info")


class AnnualHighTests(unittest.TestCase):
    """Break caught: no annualized high exists, so the straddle rule cannot be evaluated."""

    def test_hourly_range_high_annualized(self):
        self.assertAlmostEqual(annual_high_value(parse_pay("$18 - $25 an hour")), 52000.0)

    def test_annual_range_high(self):
        self.assertAlmostEqual(annual_high_value(parse_pay("$50,000 - $60,000 a year")), 60000.0)

    def test_weekly_high_annualized(self):
        self.assertAlmostEqual(annual_high_value(parse_pay("$700 - $800 a week")), 41600.0)

    def test_monthly_high_annualized(self):
        self.assertAlmostEqual(annual_high_value(parse_pay("$3,000 - $3,500 per month")), 42000.0)

    def test_missing_high_unknown(self):
        self.assertIsNone(annual_high_value(parse_pay("")))

    def test_unparseable_high_unknown(self):
        self.assertIsNone(annual_high_value(parse_pay("competitive")))

    def test_open_ended_from_high_unknown(self):
        self.assertIsNone(annual_high_value(parse_pay("From $18 an hour")))

    def test_up_to_high_known(self):
        self.assertAlmostEqual(annual_high_value(parse_pay("Up to $25 an hour")), 52000.0)

    def test_explicit_conversion_assumptions(self):
        self.assertAlmostEqual(
            annual_high_value(parse_pay("$18 - $25 an hour"), hours_per_week=30, weeks_per_year=50),
            37500.0)


if __name__ == "__main__":
    unittest.main()