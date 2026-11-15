"""Synthetic acceptance tests for deterministic eligibility (C6/C9, A2, JF-25/39)."""
import unittest

from career_navigator.eligibility import evaluate
from career_navigator.models import Posting
from career_navigator.policy import Policy


def posting(title="IT Support Technician", company="Acme Tech", location="Pittsburgh, PA",
            details="", pay_text="", **kw):
    return Posting(url="https://x.example/1", title=title, company=company,
                   location=location, details=details, pay_text=pay_text, **kw)


def factors(decision):
    return {r.factor: r for r in decision.reasons}


class DrivingTests(unittest.TestCase):
    def test_mandatory_license_excludes(self):
        d = evaluate(posting(details="Must have a valid driver's license."), Policy())
        self.assertEqual(d.status, "rejected")
        self.assertEqual(factors(d)["driving"].outcome, "exclude")

    def test_mandatory_driving_excludes(self):
        d = evaluate(posting(title="Delivery Driver", details="Must drive to client sites daily."), Policy())
        self.assertEqual(d.status, "rejected")

    def test_cdl_required_excludes(self):
        d = evaluate(posting(details="CDL required."), Policy())
        self.assertEqual(d.status, "rejected")

    def test_negated_driving_does_not_exclude(self):
        d = evaluate(posting(details="No driving required."), Policy())
        self.assertEqual(d.status, "eligible")
        self.assertEqual(factors(d)["driving"].outcome, "pass")

    def test_negated_license_does_not_exclude(self):
        d = evaluate(posting(details="No driver's license needed."), Policy())
        self.assertEqual(d.status, "eligible")

    def test_contradiction_goes_to_review_not_exclude(self):
        d = evaluate(posting(details="No driving required; must have a valid driver's license."), Policy())
        self.assertEqual(d.status, "review")
        self.assertEqual(factors(d)["driving"].outcome, "review")

    def test_no_mention_stays_unknown_not_verified_no_driving(self):
        d = evaluate(posting(), Policy())
        self.assertEqual(d.status, "eligible")
        reason = factors(d)["driving"]
        self.assertEqual(reason.outcome, "info")
        self.assertIn("unknown", reason.evidence)

    def test_preferred_driving_is_ambiguous_review(self):
        d = evaluate(posting(details="Driving preferred."), Policy())
        self.assertEqual(d.status, "review")

    def test_script_injected_driving_phrase_is_ignored(self):
        d = evaluate(posting(details="<script>must have a valid driver's license</script>Remote IT support."), Policy())
        self.assertEqual(d.status, "eligible")


class LocationTests(unittest.TestCase):
    def test_pittsburgh_primary_eligible(self):
        d = evaluate(posting(location="Pittsburgh, PA"), Policy())
        self.assertEqual(d.status, "eligible")
        self.assertIn("pittsburgh", factors(d)["location"].evidence)

    def test_philadelphia_optional_eligible(self):
        d = evaluate(posting(location="Philadelphia, PA"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_remote_unrestricted_eligible(self):
        d = evaluate(posting(location="Remote"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_remote_compatible_state_restriction_eligible(self):
        d = evaluate(posting(location="Remote", details="Must reside in Pennsylvania."), Policy())
        self.assertEqual(d.status, "eligible")

    def test_remote_incompatible_restriction_goes_to_review(self):
        d = evaluate(posting(location="Remote", details="California residents only."), Policy())
        self.assertEqual(d.status, "review")

    def test_missing_location_is_review_not_out_of_area(self):
        d = evaluate(posting(location=""), Policy())
        self.assertEqual(d.status, "review")
        self.assertEqual(factors(d)["location"].outcome, "review")

    def test_out_of_area_city_rejected(self):
        d = evaluate(posting(location="Erie, PA"), Policy())
        self.assertEqual(d.status, "rejected")
        self.assertEqual(factors(d)["location"].outcome, "exclude")

    def test_hybrid_pittsburgh_eligible(self):
        d = evaluate(posting(location="Hybrid - Pittsburgh, PA"), Policy())
        self.assertEqual(d.status, "eligible")


class RoleTests(unittest.TestCase):
    def test_restaurant_general_manager_excluded(self):
        d = evaluate(posting(title="Restaurant General Manager", company="Diner Corp"), Policy())
        self.assertEqual(d.status, "rejected")
        self.assertEqual(factors(d)["role"].outcome, "exclude")

    def test_kitchen_manager_excluded(self):
        d = evaluate(posting(title="Kitchen Manager"), Policy())
        self.assertEqual(d.status, "rejected")

    def test_line_cook_excluded(self):
        d = evaluate(posting(title="Line Cook"), Policy())
        self.assertEqual(d.status, "rejected")

    def test_pos_technician_at_restaurant_employer_not_excluded(self):
        d = evaluate(posting(title="POS Support Technician", company="Eat'n Park Restaurant Group"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_restaurant_pos_technician_technical_precedence(self):
        d = evaluate(posting(title="Restaurant POS Technician"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_hyphenated_food_service_it_analyst_allowed(self):
        d = evaluate(posting(title="Food-Service IT Analyst"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_restaurant_shift_leader_excluded(self):
        d = evaluate(posting(title="Restaurant Shift Leader"), Policy())
        self.assertEqual(d.status, "rejected")

    def test_excluded_employer_case_insensitive(self):
        d = evaluate(posting(company="ACME DINING"), Policy(excluded_employers=("Acme Dining",)))
        self.assertEqual(d.status, "rejected")
        self.assertEqual(factors(d)["employer"].outcome, "exclude")


class SourceRecordTests(unittest.TestCase):
    def test_upstream_ok_does_not_override_cn_exclusion(self):
        d = evaluate(posting(details="CDL required.", upstream_verdict="ok"), Policy())
        self.assertEqual(d.status, "rejected")

    def test_upstream_rejected_does_not_override_cn_eligibility(self):
        d = evaluate(posting(upstream_verdict="rejected", upstream_status="dismissed"), Policy())
        self.assertEqual(d.status, "eligible")


class PayFloorTests(unittest.TestCase):
    def test_bounded_below_floor_rejected(self):
        d = evaluate(posting(pay_text="$15 an hour"), Policy(salary_floor_annual=41600.0))
        self.assertEqual(d.status, "rejected")
        self.assertEqual(factors(d)["pay"].outcome, "exclude")

    def test_open_ended_below_floor_is_review(self):
        d = evaluate(posting(pay_text="From $15 an hour"), Policy(salary_floor_annual=41600.0))
        self.assertEqual(d.status, "review")

    def test_unknown_salary_not_excluded_stays_unknown(self):
        d = evaluate(posting(pay_text=""), Policy(salary_floor_annual=41600.0))
        self.assertEqual(d.status, "eligible")
        self.assertIn("unknown", factors(d)["pay"].evidence)

    def test_no_floor_configured_no_pay_exclusion(self):
        d = evaluate(posting(pay_text="$15 an hour"), Policy())
        self.assertEqual(d.status, "eligible")


if __name__ == "__main__":
    unittest.main()