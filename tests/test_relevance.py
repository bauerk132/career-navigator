"""Synthetic acceptance tests for the explainable job-relevance index (C9, plan #5)."""
import unittest

from career_navigator.eligibility import evaluate
from career_navigator.models import Posting
from career_navigator.policy import Policy
from career_navigator.relevance import rank, score


def posting(title="IT Support Technician", company="Acme Tech", location="Pittsburgh, PA",
            details="Support Windows systems and networks.", pay_text="$25 an hour", url="https://x.example/1"):
    return Posting(url=url, title=title, company=company, location=location,
                   details=details, pay_text=pay_text)


class ScoreTests(unittest.TestCase):
    def test_score_equals_weighted_factor_sum(self):
        p = posting()
        r = score(p, evaluate(p, Policy()), Policy())
        self.assertAlmostEqual(r.value, 100.0 * sum(f.contribution for f in r.factors))
        self.assertTrue(0.0 <= r.value <= 100.0)

    def test_custom_weights_change_score(self):
        p = posting()
        d = evaluate(p, Policy())
        weights = {"technical_alignment": 1.0, "location_fit": 0.0,
                   "pay_clarity": 0.0, "driving_compatibility": 0.0}
        r = score(p, d, Policy(weights=weights))
        tech = next(f for f in r.factors if f.name == "technical_alignment")
        self.assertAlmostEqual(r.value, 100.0 * tech.raw)

    def test_invalid_weight_sum_rejected(self):
        with self.assertRaises(ValueError):
            score(posting(), evaluate(posting(), Policy()),
                  Policy(weights={"technical_alignment": 0.5, "location_fit": 0.0,
                                  "pay_clarity": 0.0, "driving_compatibility": 0.0}))

    def test_unknown_factor_weight_rejected(self):
        with self.assertRaises(ValueError):
            score(posting(), evaluate(posting(), Policy()),
                  Policy(weights={"nonexistent": 1.0, "location_fit": 0.0,
                                  "pay_clarity": 0.0, "driving_compatibility": 0.0}))

    def test_rejected_record_gets_no_misleading_priority(self):
        p = posting(title="Delivery Driver", details="CDL required.")
        r = score(p, evaluate(p, Policy()), Policy())
        self.assertIsNone(r.priority)

    def test_review_record_gets_no_misleading_priority(self):
        p = posting(location="")
        r = score(p, evaluate(p, Policy()), Policy())
        self.assertIsNone(r.priority)

    def test_eligible_at_threshold_is_high_inclusive_edge(self):
        p = posting()
        policy = Policy(priority_threshold=0.0)
        r = score(p, evaluate(p, policy), policy)
        self.assertEqual(r.priority, "high")

    def test_eligible_below_threshold_is_standard(self):
        p = posting()
        policy = Policy(priority_threshold=101.0)
        r = score(p, evaluate(p, policy), policy)
        self.assertEqual(r.priority, "standard")

    def test_missing_pay_clarity_zero_but_visible(self):
        p = posting(pay_text="")
        r = score(p, evaluate(p, Policy()), Policy())
        pay = next(f for f in r.factors if f.name == "pay_clarity")
        self.assertEqual(pay.raw, 0.0)
        self.assertIn("unknown", pay.evidence)

    def test_wording_never_claims_verified_candidate_fit(self):
        r = score(posting(), evaluate(posting(), Policy()), Policy())
        self.assertIn("job relevance index", r.wording)
        self.assertIn("not verified candidate fit", r.wording)
        for f in r.factors:
            self.assertNotIn("proficient", f.evidence.lower())
            self.assertNotIn("candidate knows", f.evidence.lower())

    def test_technical_evidence_is_posting_side(self):
        r = score(posting(), evaluate(posting(), Policy()), Policy())
        tech = next(f for f in r.factors if f.name == "technical_alignment")
        self.assertIn("posting mentions", tech.evidence)


class RankTests(unittest.TestCase):
    def test_ties_broken_deterministically_by_title(self):
        policy = Policy()
        a = posting(title="Zebra Analyst", url="https://x.example/a")
        b = posting(title="Alpha Analyst", url="https://x.example/b")
        ra = score(a, evaluate(a, policy), policy)
        rb = score(b, evaluate(b, policy), policy)
        self.assertAlmostEqual(ra.value, rb.value)
        self.assertEqual([r.posting.title for r in rank([ra, rb])], ["Alpha Analyst", "Zebra Analyst"])
        self.assertEqual([r.posting.title for r in rank([rb, ra])], ["Alpha Analyst", "Zebra Analyst"])


if __name__ == "__main__":
    unittest.main()