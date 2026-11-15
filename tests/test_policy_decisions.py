"""Synthetic regression tests for Kyle's 2026-11-15 policy decisions (hof_01M7FPD05XTSBPK4T7).

Decisions (hivemind mem_01M7FP498GDPD5D5C2 / mem_01M7FP76DD2CRMCYN3 / mem_01M7FP863YA8TVBCCH):
- Missing salary => Needs Review; open-ended starting pay below $19/hour with no
  maximum => Needs Review. Bounded ranges entirely below the floor stay excluded.
- Philadelphia is a regular default alongside Pittsburgh (not opt-in).
- Remote explicitly open to Pennsylvania => eligible on geography; explicitly
  excludes Pennsylvania => excluded; unclear/unstated => Needs Review. Nationwide
  US scope includes Pennsylvania; no geographic restriction is invented.
Policy outcomes never override other verified disqualifications.
"""
import unittest

from career_navigator.eligibility import evaluate
from career_navigator.models import Posting
from career_navigator.policy import Policy
from career_navigator.relevance import score


def posting(title="IT Support Technician", company="Acme Tech", location="Pittsburgh, PA",
            details="", pay_text="$25 an hour", url="https://x.example/1", **kw):
    return Posting(url=url, title=title, company=company,
                   location=location, details=details, pay_text=pay_text, **kw)


def factors(decision):
    return {r.factor: r for r in decision.reasons}


class SalaryDecisionTests(unittest.TestCase):
    def test_default_floor_is_kyle_19_hourly(self):
        self.assertEqual(Policy().salary_floor_annual, 19.0 * 40.0 * 52.0)

    def test_missing_salary_goes_to_review(self):
        d = evaluate(posting(pay_text=""), Policy())
        self.assertEqual(d.status, "review")
        reason = factors(d)["pay"]
        self.assertEqual(reason.outcome, "review")
        self.assertIn("unknown", reason.evidence)

    def test_missing_salary_review_does_not_override_hard_exclusion(self):
        d = evaluate(posting(pay_text="", details="CDL required."), Policy())
        self.assertEqual(d.status, "rejected")

    def test_open_ended_low_below_floor_is_review(self):
        d = evaluate(posting(pay_text="From $18 an hour"), Policy())
        self.assertEqual(d.status, "review")
        self.assertEqual(factors(d)["pay"].outcome, "review")

    def test_open_ended_low_just_below_floor_is_review(self):
        d = evaluate(posting(pay_text="From $18.99 an hour"), Policy())
        self.assertEqual(d.status, "review")

    def test_open_ended_low_at_floor_is_eligible_inclusive_boundary(self):
        d = evaluate(posting(pay_text="From $19 an hour"), Policy())
        self.assertEqual(d.status, "eligible")
        self.assertEqual(factors(d)["pay"].outcome, "pass")

    def test_open_ended_low_above_floor_is_eligible(self):
        d = evaluate(posting(pay_text="From $21 an hour"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_bounded_below_floor_still_rejected(self):
        d = evaluate(posting(pay_text="$15-$17 an hour"), Policy())
        self.assertEqual(d.status, "rejected")
        self.assertEqual(factors(d)["pay"].outcome, "exclude")

    def test_bounded_at_floor_is_eligible_inclusive_boundary(self):
        d = evaluate(posting(pay_text="$19 an hour"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_bounded_above_floor_is_eligible(self):
        d = evaluate(posting(pay_text="$25 an hour"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_unparseable_pay_unchanged_stays_unknown_visible(self):
        d = evaluate(posting(pay_text="competitive"), Policy())
        self.assertEqual(d.status, "eligible")
        self.assertEqual(factors(d)["pay"].outcome, "info")

    def test_up_to_open_ended_unchanged_floor_unknown(self):
        d = evaluate(posting(pay_text="Up to $15 an hour"), Policy())
        self.assertEqual(d.status, "eligible")
        self.assertEqual(factors(d)["pay"].outcome, "info")

    def test_floor_still_configurable_none_disables(self):
        d = evaluate(posting(pay_text="$15 an hour"), Policy(salary_floor_annual=None))
        self.assertEqual(d.status, "eligible")

    def test_floor_still_configurable_custom_value(self):
        d = evaluate(posting(pay_text="$15 an hour"), Policy(salary_floor_annual=41600.0))
        self.assertEqual(d.status, "rejected")

    def test_missing_salary_review_preserves_other_pass_reasons(self):
        d = evaluate(posting(pay_text=""), Policy())
        self.assertEqual(factors(d)["location"].outcome, "pass")
        self.assertEqual(factors(d)["driving"].outcome, "info")


class PhiladelphiaDecisionTests(unittest.TestCase):
    def test_philadelphia_is_primary_by_default(self):
        policy = Policy()
        self.assertIn("philadelphia", policy.primary_cities)
        self.assertNotIn("philadelphia", policy.optional_cities)

    def test_philadelphia_eligible_with_primary_evidence(self):
        d = evaluate(posting(location="Philadelphia, PA"), Policy())
        self.assertEqual(d.status, "eligible")
        self.assertIn("philadelphia (primary)", factors(d)["location"].evidence)

    def test_philadelphia_relevance_equals_pittsburgh(self):
        policy = Policy()
        pitt = posting(location="Pittsburgh, PA")
        phil = posting(location="Philadelphia, PA", url="https://x.example/2")
        rp = score(pitt, evaluate(pitt, policy), policy)
        rf = score(phil, evaluate(phil, policy), policy)
        self.assertAlmostEqual(rp.value, rf.value)
        loc_p = next(f for f in rp.factors if f.name == "location_fit")
        loc_f = next(f for f in rf.factors if f.name == "location_fit")
        self.assertEqual(loc_p.raw, 1.0)
        self.assertEqual(loc_f.raw, 1.0)

    def test_philadelphia_missing_salary_is_review(self):
        d = evaluate(posting(location="Philadelphia, PA", pay_text=""), Policy())
        self.assertEqual(d.status, "review")

    def test_philadelphia_still_configurable_out(self):
        d = evaluate(posting(location="Philadelphia, PA"),
                     Policy(primary_cities=("pittsburgh",), optional_cities=()))
        self.assertEqual(d.status, "rejected")

    def test_pittsburgh_still_primary(self):
        d = evaluate(posting(location="Pittsburgh, PA"), Policy())
        self.assertEqual(d.status, "eligible")
        self.assertIn("pittsburgh (primary)", factors(d)["location"].evidence)


class RemoteDecisionTests(unittest.TestCase):
    def test_remote_pa_statewide_eligible(self):
        d = evaluate(posting(location="Remote - Pennsylvania"), Policy())
        self.assertEqual(d.status, "eligible")
        reason = factors(d)["location"]
        self.assertEqual(reason.outcome, "pass")
        self.assertIn("pennsylvania", reason.evidence)

    def test_remote_pa_reside_restriction_eligible(self):
        d = evaluate(posting(location="Remote", details="Must reside in Pennsylvania."), Policy())
        self.assertEqual(d.status, "eligible")

    def test_remote_pa_residents_only_eligible(self):
        d = evaluate(posting(location="Remote", details="Pennsylvania residents only."), Policy())
        self.assertEqual(d.status, "eligible")

    def test_remote_pa_abbr_eligible(self):
        d = evaluate(posting(location="Remote - PA"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_remote_nationwide_united_states_eligible(self):
        d = evaluate(posting(location="Remote (United States)"), Policy())
        self.assertEqual(d.status, "eligible")
        self.assertIn("nationwide", factors(d)["location"].evidence)

    def test_remote_us_only_eligible(self):
        d = evaluate(posting(location="Remote", details="US only."), Policy())
        self.assertEqual(d.status, "eligible")

    def test_remote_nationwide_word_eligible(self):
        d = evaluate(posting(location="Remote - nationwide"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_remote_us_suffix_eligible(self):
        d = evaluate(posting(location="Remote - US"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_remote_other_state_residents_only_rejected(self):
        d = evaluate(posting(location="Remote", details="California residents only."), Policy())
        self.assertEqual(d.status, "rejected")
        self.assertEqual(factors(d)["location"].outcome, "exclude")

    def test_remote_other_state_reside_rejected(self):
        d = evaluate(posting(location="Remote", details="Must reside in Texas."), Policy())
        self.assertEqual(d.status, "rejected")

    def test_remote_other_state_abbr_rejected(self):
        d = evaluate(posting(location="Remote - CA"), Policy())
        self.assertEqual(d.status, "rejected")

    def test_remote_state_list_without_pa_rejected(self):
        d = evaluate(posting(location="Remote - CA, TX"), Policy())
        self.assertEqual(d.status, "rejected")

    def test_remote_explicit_pa_exclusion_rejected(self):
        d = evaluate(posting(location="Remote", details="Not available in Pennsylvania."), Policy())
        self.assertEqual(d.status, "rejected")
        self.assertEqual(factors(d)["location"].outcome, "exclude")

    def test_remote_pa_excluded_word_rejected(self):
        d = evaluate(posting(location="Remote", details="Pennsylvania excluded."), Policy())
        self.assertEqual(d.status, "rejected")

    def test_remote_unstated_eligibility_is_review(self):
        d = evaluate(posting(location="Remote"), Policy())
        self.assertEqual(d.status, "review")
        reason = factors(d)["location"]
        self.assertEqual(reason.outcome, "review")
        self.assertIn("unstated", reason.evidence)

    def test_remote_unclear_region_is_review(self):
        d = evaluate(posting(location="Remote - East Coast"), Policy())
        self.assertEqual(d.status, "review")

    def test_remote_conflicting_pa_and_other_state_is_review(self):
        d = evaluate(posting(location="Remote - PA", details="Must reside in California."), Policy())
        self.assertEqual(d.status, "review")

    def test_remote_pa_in_mixed_state_list_eligible(self):
        d = evaluate(posting(location="Remote - PA, NY"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_remote_pronoun_us_is_not_nationwide(self):
        d = evaluate(posting(location="Remote", details="Join us!"), Policy())
        self.assertEqual(d.status, "review")

    def test_remote_exclusion_overrides_good_pay(self):
        d = evaluate(posting(location="Remote - CA", pay_text="$30 an hour"), Policy())
        self.assertEqual(d.status, "rejected")

    def test_remote_pa_eligible_does_not_override_cdl(self):
        d = evaluate(posting(location="Remote - Pennsylvania", details="CDL required."), Policy())
        self.assertEqual(d.status, "rejected")

    def test_remote_review_does_not_override_restaurant_exclusion(self):
        d = evaluate(posting(title="Line Cook", location="Remote"), Policy())
        self.assertEqual(d.status, "rejected")

    def test_remote_pa_eligible_missing_salary_is_review(self):
        d = evaluate(posting(location="Remote - PA", pay_text=""), Policy())
        self.assertEqual(d.status, "review")
        self.assertEqual(factors(d)["location"].outcome, "pass")
        self.assertEqual(factors(d)["pay"].outcome, "review")

    def test_allow_remote_false_still_excludes(self):
        d = evaluate(posting(location="Remote - PA"), Policy(allow_remote=False))
        self.assertEqual(d.status, "rejected")

    def test_non_remote_city_path_unaffected(self):
        d = evaluate(posting(location="Pittsburgh, PA"), Policy())
        self.assertEqual(d.status, "eligible")

    def test_remote_excluded_gets_no_priority(self):
        p = posting(location="Remote - CA")
        r = score(p, evaluate(p, Policy()), Policy())
        self.assertIsNone(r.priority)

    def test_remote_review_gets_no_priority(self):
        p = posting(location="Remote")
        r = score(p, evaluate(p, Policy()), Policy())
        self.assertIsNone(r.priority)


if __name__ == "__main__":
    unittest.main()