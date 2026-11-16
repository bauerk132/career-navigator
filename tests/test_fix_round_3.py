"""Fix Round 3 regressions for candidate 0d16559 (FR3-01/02/03/04).

Authority: GPT Orchestrator handoff "Fix Round 3: negative PA restrictions and
conditional geography" (Notion 41a2b8e72a0881458146dbb804cdb0b2, P0) and
Antigravity's Fix Round 2 G1/G2 verdict (Notion 41a2b8e72a088110a9a6eeefd522d0d1,
G2 FAIL: FR2-G2-01 verb-only PA exclusions, FR2-G2-02 exclusion-list headers,
FR2-G2-03 negated nationwide scope, FR2-G2-04 conditional approvals).

Synthetic postings only. Defect cases fail on pristine 0d16559 and pass after
the targeted fix; guardrail cases lock preserved policy semantics (189 prior
tests untouched, incl. Hermes 40, 7 controls, 22 probes (A09 resolved by
Kyle's Nov 16, 2026 decision: remote across the US is eligible).
Cases marked "already covered" pass on the baseline via the legacy prefix
pattern and must keep passing with unchanged evidence.
"""
import unittest

from career_navigator.eligibility import evaluate
from career_navigator.models import Posting
from career_navigator.policy import Policy
from career_navigator.relevance import score


def posting(location, details, pay_text="$25 an hour", title="IT Support Technician"):
    return Posting(url="https://example.invalid/synthetic", title=title,
                   company="Synthetic", location=location, details=details,
                   pay_text=pay_text)


def decide(location, details, pay_text="$25 an hour"):
    p = posting(location, details, pay_text)
    decision = evaluate(p, Policy())
    result = score(p, decision, Policy())
    return decision, result


def location_reason(decision):
    return next(r for r in decision.reasons if r.factor == "location")


# FR3-01: verb-only negative PA hiring restrictions (no applicant noun).
FR3_01_VERB_ONLY = (
    ("V01", "Remote - US", "We cannot hire in Pennsylvania."),
    ("V02", "Remote - US", "We cannot employ in PA."),
    ("V03", "Remote - US", "Unable to hire in Pennsylvania."),
    ("V04", "Remote - US", "We can't hire in Pennsylvania."),
    ("V05", "Remote - US", "We won't employ in PA."),
    ("V06", "Remote - US", "We are unable to employ in Pennsylvania."),
    ("V07", "Remote - US", "The company refuses to hire in PA."),
    ("V08", "Remote - US", "Our company declines to employ in Pennsylvania."),
    ("V09", "Remote - US", "We prohibit recruiting in Pennsylvania."),
    ("V10", "Remote - US", "The company prohibits employing in PA."),
    ("V11", "Remote - US", "We don't recruit in PA."),
    ("V12", "Remote - US", "Cannot hire in PA."),
    ("V13", "Remote - US", "We cannot recruit from Pennsylvania."),
    ("V14", "Remote - US", "We cannot hire in PA!"),
    ("V15", "Remote - Pennsylvania", "We cannot hire in Pennsylvania."),
    ("V16", "Remote (US)", "Unable to hire in PA."),
    ("V17", "Remote", "We cannot employ in PA."),
    ("V18", "Remote - US", "PA applicants are not welcome."),
    ("V19", "Remote - US", "Candidates in PA cannot be hired."),
)

# Verb-only forms the legacy prefix pattern already catches on 0d16559;
# preservation guardrails, not red cases.
FR3_01_ALREADY_COVERED = (
    ("W01", "Remote - US", "We will not hire in PA."),
    ("W02", "Remote - US", "We do not hire in Pennsylvania."),
    ("W03", "Remote - US", "We will not employ in PA."),
)

# Logistical observations and incidental PA mentions are not restrictions.
FR3_01_BENIGN = (
    ("B01", "Remote - US", "We cannot hire enough engineers in Pennsylvania."),
    ("B02", "Remote - US", "We can't hire enough engineers in PA."),
    ("B03", "Remote - US", "We are unable to hire enough staff in Pennsylvania."),
    ("B04", "Remote - US", "We cannot employ enough workers in PA."),
    ("B05", "Remote - US", "We cannot hire in Tampa."),
    ("B06", "Remote - US", "Our headquarters is in Pennsylvania."),
    ("B07", "Remote - US", "The company was incorporated in Pennsylvania."),
    ("B08", "Remote - US", "We can not hire enough engineers in Pennsylvania."),
    ("B09", "Remote - US", "We will not hire enough engineers in PA."),
)

# FR3-02: explicit exclusion-list headings and enumerations.
FR3_02_LISTS = (
    ("L01", "Remote - US", "Excluded states: Pennsylvania."),
    ("L02", "Remote - US", "Ineligible states: PA."),
    ("L03", "Remote - US", "Excluded states: California, Pennsylvania, Texas."),
    ("L04", "Remote - US", "Ineligible states: NY; PA; TX."),
    ("L05", "Remote - US", "Excluded locations: Pennsylvania."),
    ("L06", "Remote - US", "Hiring excluded in Pennsylvania."),
    ("L07", "Remote - PA", "Excluded states: PA."),
    ("L08", "Remote - US", "Excluded states: California; Texas; Pennsylvania."),
)

# Reverse-order exclusion wording already covered by legacy Pattern 2.
FR3_02_ALREADY_COVERED = (
    ("M01", "Remote - US", "PA is excluded from hiring eligibility."),
    ("M02", "Remote - US", "Pennsylvania is ineligible for this role."),
)

# Lists without PA, collision words, and incidental addresses invent nothing.
FR3_02_BENIGN = (
    ("N01", "Remote - US", "Excluded states: California, Texas."),
    ("N02", "Remote - US", "Excluded items: paper products."),
    ("N03", "Remote - US", "Our legal address is in Pennsylvania."),
    ("N04", "Remote - US", "Ineligible states: NY; TX."),
)

# FR3-03: negated nationwide/US candidate scope prohibits US applicants incl. PA.
FR3_03_NEGATED = (
    ("G01", "Remote - US", "Not open to candidates in US."),
    ("G02", "Remote - US", "Not available to US residents."),
    ("G03", "Remote - US", "Not hiring anywhere in the United States."),
    ("G04", "Remote - US", "Doesn't accept US-based applicants."),
    ("G05", "Remote - US", "We do not hire in the US."),
    ("G06", "Remote - US", "We are not accepting US applicants."),
    ("G07", "Remote - US", "No longer hiring in the United States."),
    ("G08", "Remote", "Not hiring anywhere in the United States."),
    ("G09", "Remote - PA", "Not open to candidates in US."),
    ("G10", "Remote - US", "US applicants are not welcome."),
    ("G11", "Remote - US", "Not open to candidates in the U.S."),
    ("G12", "Remote - US", "Not open to candidates in the USA."),
)

# Contradictory scope is ambiguous: Needs Review, never confident eligible.
FR3_03_CONTRADICTORY = (
    ("X01", "Remote - US", "Not open to candidates in US. US applicants are welcome."),
)

# Negated restrictions, welcome wording, and unrelated negatives stay eligible.
FR3_03_BENIGN = (
    ("P01", "Remote - US", "Not restricted to US."),
    ("P02", "Remote - US", "US applicants are welcome."),
    ("P03", "Remote - US", "We do not require relocation."),
    ("P04", "Remote - US", "We do not discriminate."),
    ("P05", "Remote - US", "No experience required."),
    ("P06", "Remote - US", "We cannot accept US dollars."),
)

# FR3-04: conditional approvals are not eligibility confirmations.
FR3_04_CONDITIONAL = (
    ("D01", "Remote - US", "Nationwide US if approved."),
    ("D02", "Remote - PA", "Open to candidates in PA; if approved."),
    ("D03", "Remote - US", "Open to US applicants, subject to approval."),
    ("D04", "Remote - US", "Nationwide US pending approval."),
    ("D05", "Remote - US", "Open to PA residents upon authorization."),
    ("D06", "Remote - US", "Nationwide US contingent on approval."),
    ("D07", "Remote - US", "If approved, nationwide US."),
    ("D08", "Remote - US", "If approved, open to candidates in PA."),
    ("D09", "Remote - PA", "Open to candidates in PA; subject to approval."),
    ("D10", "Remote - US", "Hiring in PA if approval is granted."),
    # Deliberate conservative choice: approval-conditional language anywhere in
    # a remote posting makes the hiring authorization uncertain -> Needs Review.
    ("D11", "Remote - US", "Salary subject to approval."),
    ("D12", "Remote - US", "Nationwide US, subject to board approval."),
    ("D13", "Remote - US", "Open to PA residents pending board authorization."),
)

# Explicit granted approval is source evidence of unconditional authorization.
FR3_04_GRANTED = (
    ("E01", "Remote - US", "Approval granted for nationwide US hiring."),
    ("E02", "Remote - PA", "Approved: open to candidates in PA."),
    ("E03", "Remote - US", "Nationwide US if approved. Approval has been granted."),
)

# Hard exclusions beat conditional-approval review.
FR3_04_EXCLUSION_PRIORITY = (
    ("R01", "Remote - US", "We cannot hire in PA. Subject to approval."),
    ("R02", "Remote - US", "Excluded states: PA. Pending approval."),
)

# Non-approval conditions and the non-remote path are unaffected.
FR3_04_BENIGN = (
    ("T01", "Remote - US", "Subject to background check."),
    ("T02", "Remote - US", "Pending background check."),
    ("T03", "Pittsburgh, PA", "Subject to approval."),
)


class FR301VerbOnlyPAExclusionTests(unittest.TestCase):
    """Break caught: _PA_EXCLUDED required applicant nouns after PA."""

    def test_verb_only_restrictions_exclude(self):
        for cid, loc, details in FR3_01_VERB_ONLY:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "rejected")
                self.assertEqual(location_reason(decision).outcome, "exclude")
                self.assertIsNone(result.priority)

    def test_legacy_covered_verb_forms_still_exclude(self):
        for cid, loc, details in FR3_01_ALREADY_COVERED:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "rejected")
                self.assertEqual(location_reason(decision).outcome, "exclude")
                self.assertIsNone(result.priority)

    def test_logistical_and_incidental_mentions_stay_eligible(self):
        for cid, loc, details in FR3_01_BENIGN:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "eligible")
                self.assertEqual(location_reason(decision).outcome, "pass")
                self.assertEqual(result.priority, "high")


class FR302ExclusionListTests(unittest.TestCase):
    """Break caught: exclusion-list prefixes missing from Pattern 1."""

    def test_exclusion_lists_exclude(self):
        for cid, loc, details in FR3_02_LISTS:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "rejected")
                self.assertEqual(location_reason(decision).outcome, "exclude")
                self.assertIsNone(result.priority)

    def test_reverse_order_exclusions_still_exclude(self):
        for cid, loc, details in FR3_02_ALREADY_COVERED:
            with self.subTest(case=cid, location=loc, details=details):
                decision, _ = decide(loc, details)
                self.assertEqual(decision.status, "rejected")
                self.assertEqual(location_reason(decision).outcome, "exclude")

    def test_pa_free_lists_and_collisions_stay_eligible(self):
        for cid, loc, details in FR3_02_BENIGN:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "eligible")
                self.assertEqual(location_reason(decision).outcome, "pass")
                self.assertEqual(result.priority, "high")


class FR303NegatedNationwideTests(unittest.TestCase):
    """Break caught: negated US scope unconsumed under nationwide suffix."""

    def test_negated_us_scope_excludes(self):
        for cid, loc, details in FR3_03_NEGATED:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "rejected")
                self.assertEqual(location_reason(decision).outcome, "exclude")
                self.assertIsNone(result.priority)

    def test_contradictory_us_scope_is_review(self):
        for cid, loc, details in FR3_03_CONTRADICTORY:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "review")
                self.assertEqual(location_reason(decision).outcome, "review")
                self.assertIsNone(result.priority)

    def test_negated_restrictions_and_unrelated_negatives_stay_eligible(self):
        for cid, loc, details in FR3_03_BENIGN:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "eligible")
                self.assertEqual(location_reason(decision).outcome, "pass")
                self.assertEqual(result.priority, "high")


class FR304ConditionalApprovalTests(unittest.TestCase):
    """Break caught: conditional approvals evaluated as confident eligible."""

    def test_conditional_approvals_are_review(self):
        for cid, loc, details in FR3_04_CONDITIONAL:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "review")
                self.assertEqual(location_reason(decision).outcome, "review")
                self.assertIsNone(result.priority)

    def test_granted_approval_stays_eligible(self):
        for cid, loc, details in FR3_04_GRANTED:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "eligible")
                self.assertEqual(location_reason(decision).outcome, "pass")
                self.assertEqual(result.priority, "high")

    def test_hard_exclusion_beats_conditional_review(self):
        for cid, loc, details in FR3_04_EXCLUSION_PRIORITY:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "rejected")
                self.assertEqual(location_reason(decision).outcome, "exclude")
                self.assertIsNone(result.priority)

    def test_non_approval_conditions_and_non_remote_unaffected(self):
        for cid, loc, details in FR3_04_BENIGN:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "eligible")
                self.assertEqual(location_reason(decision).outcome, "pass")
                self.assertEqual(result.priority, "high")


class FR3GrammarSafetyTests(unittest.TestCase):
    """Guardrail: new grammar stays bounded and deterministic."""

    def test_bounded_scan_completes_on_adversarial_whitespace(self):
        for fragment in ("cannot hire in", "if", "excluded states:"):
            details = (fragment + " " * 5000 + "pennsylvania. ") * 8
            with self.subTest(fragment=fragment):
                decision, _ = decide("Remote - US", details)
                self.assertIn(decision.status, ("eligible", "review", "rejected"))

    def test_repeated_evaluation_is_deterministic(self):
        for loc, details in (("Remote - US", "We cannot hire in PA."),
                             ("Remote - US", "Nationwide US if approved."),
                             ("Remote - US", "Not open to candidates in US.")):
            with self.subTest(details=details):
                d1, r1 = decide(loc, details)
                d2, r2 = decide(loc, details)
                self.assertEqual(d1, d2)
                self.assertEqual(r1.value, r2.value)
                self.assertEqual(r1.priority, r2.priority)


if __name__ == "__main__":
    unittest.main()