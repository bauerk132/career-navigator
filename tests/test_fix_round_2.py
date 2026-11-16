"""Fix Round 2 regressions for candidate 4330052 (FR2-01/FR2-02/FR2-03).

Authority: GPT Orchestrator handoff "Fix Round 2: PA restrictions and geography
conflicts" (Notion 41a2b8e72a0881068823ffa8f4563f6d, P1, release-blocking).

Corpus provenance (synthetic postings only):
- HERMES_40: all 40 mismatch rows recovered verbatim from Hermes's triage of
  4330052 (Notion 41a2b8e72a088178abdaef8905eb60f4), raw results file
  cn_triage_433_312.json (Hermes scratch cache), originally from Sol session
  local-0672494ed4d8dd51b48fa5a0910ef358fd96ef30ba95d8ae6464946607c399fa.
  On pristine 4330052: 12 meet expectations, 28 fail (18/18 geographic
  qualifiers, 10/16 incidental restrictions, 6/6 noncandidate fixed).
- HERMES_CONTROLS: the 7 named controls from the same triage.
- ANTIGRAVITY_PROBES: the 22 adversarial probes from Antigravity's Fix Round 1
  review (reviews/career-navigator/fix-round-1-qoder-4330052-review.md §3.2).
  Pay shorthand "$30/hr" transcribed to the parser's "$30 an hour" wording.
  Probe A09 is a documented discrepancy: the review table claims eligible,
  but pristine 4330052 returns review ("Remote across the US" is not consumed
  by the nationwide grammar). Locked at observed baseline behavior below and
  flagged for the coordinator; not silently expanded in scope.
- FR2 cases and phrasing variants: handoff §4 acceptance cases plus general
  negation/geographic variants (§5.4). Hermes's untracked experimental tests
  (cn/policy-decisions worktree) were compared read-only for phrasing ideas
  only; none of his unreviewed geography.py code or differing expectations
  were transplanted.

Every defect case fails on pristine 4330052 and passes after the targeted fix;
guardrail cases lock preserved policy semantics (174 prior tests untouched).
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


# group codes: gq=geographic-qualifier, ir=incidental-restriction, nc=noncandidate
HERMES_40 = (
    ("H01", "gq", "Remote - US", "Location: NY.", ("review",)),
    ("H02", "gq", "Remote - US", "Region: Canada.", ("review",)),
    ("H03", "gq", "Remote - US", "Geographic region: CA.", ("review",)),
    ("H04", "gq", "Remote - US", "Territory: Ontario.", ("review",)),
    ("H05", "gq", "Remote - US", "Location: Germany.", ("review",)),
    ("H06", "gq", "Remote - US", "NY required.", ("review",)),
    ("H07", "gq", "Remote - US", "Canada required.", ("review",)),
    ("H08", "gq", "Remote - US", "Canada preferred.", ("review",)),
    ("H09", "gq", "Remote - US", "Within Canada.", ("review",)),
    ("H10", "gq", "Remote", "Open to candidates in PA. Location: NY.", ("review",)),
    ("H11", "gq", "Remote", "Open to candidates in PA. Region: Canada.", ("review",)),
    ("H12", "gq", "Remote", "Open to candidates in PA. Geographic region: CA.", ("review",)),
    ("H13", "gq", "Remote", "Open to candidates in PA. Territory: Ontario.", ("review",)),
    ("H14", "gq", "Remote", "Open to candidates in PA. Location: Germany.", ("review",)),
    ("H15", "gq", "Remote", "Open to candidates in PA. NY required.", ("review",)),
    ("H16", "gq", "Remote", "Open to candidates in PA. Canada required.", ("review",)),
    ("H17", "gq", "Remote", "Open to candidates in PA. Canada preferred.", ("review",)),
    ("H18", "gq", "Remote", "Open to candidates in PA. Within Canada.", ("review",)),
    ("H19", "ir", "Remote - US", "Our company does not accept Pennsylvania applicants.", ("rejected", "review")),
    ("H20", "ir", "Remote - US", "Our company cannot employ Pennsylvania residents.", ("rejected", "review")),
    ("H21", "ir", "Remote - US", "Our company rejects Pennsylvania applicants.", ("rejected", "review")),
    ("H22", "ir", "Remote - US", "We are headquartered in Texas but Pennsylvania residents cannot apply.", ("rejected", "review")),
    ("H23", "ir", "Remote - US", "Our office is in Canada and Pennsylvania residents cannot apply.", ("rejected", "review")),
    ("H24", "ir", "Remote - US", "Our customers are in Texas and Pennsylvania residents cannot apply.", ("rejected", "review")),
    ("H25", "ir", "Remote - US", "Our office is in Canada and we do not accept Pennsylvania applicants.", ("rejected", "review")),
    ("H26", "ir", "Remote - US", "Our company says Pennsylvania is excluded.", ("rejected", "review")),
    ("H27", "ir", "Remote - US", "Our company does not accept PA applicants.", ("rejected", "review")),
    ("H28", "ir", "Remote - US", "Our company cannot employ PA residents.", ("rejected", "review")),
    ("H29", "ir", "Remote - US", "Our company rejects PA applicants.", ("rejected", "review")),
    ("H30", "ir", "Remote - US", "We are headquartered in Texas but PA residents cannot apply.", ("rejected", "review")),
    ("H31", "ir", "Remote - US", "Our office is in Canada and PA residents cannot apply.", ("rejected", "review")),
    ("H32", "ir", "Remote - US", "Our customers are in Texas and PA residents cannot apply.", ("rejected", "review")),
    ("H33", "ir", "Remote - US", "Our office is in Canada and we do not accept PA applicants.", ("rejected", "review")),
    ("H34", "ir", "Remote - US", "Our company says PA is excluded.", ("rejected", "review")),
    ("H35", "nc", "Remote - US", "Candidates receive onboarding.", ("eligible",)),
    ("H36", "nc", "Remote - US", "Employees receive onboarding.", ("eligible",)),
    ("H37", "nc", "Remote - US", "Workers receive onboarding.", ("eligible",)),
    ("H38", "nc", "Remote - US", "California users receive onboarding.", ("eligible",)),
    ("H39", "nc", "Remote - US", "Training provided to California users.", ("eligible",)),
    ("H40", "nc", "Remote - US", "We serve California customers.", ("eligible",)),
)

HERMES_CONTROLS = (
    ("C1", "Remote - US", "Our company does not accept Pennsylvania applicants.", ("rejected",)),
    ("C2", "Remote", "Location: NY.", ("review",)),
    ("C3", "Remote - PA", "Region: Canada.", ("review",)),
    ("C4", "Remote", "Remote nationwide US. Headquartered in California.", ("eligible",)),
    ("C5", "Remote", "Delaware corporation. Open to candidates nationwide.", ("eligible",)),
    ("C6", "Remote", "", ("review",)),
    ("C7", "Remote - US", "Not available in Pennsylvania.", ("rejected",)),
)

# (id, location, details, pay_text, expected) — Antigravity review §3.2 order.
ANTIGRAVITY_PROBES = (
    ("A01", "Remote", "Candidates with experience in Python only.", "$30 an hour", "review"),
    ("A02", "Remote", "Valid ID only.", "$30 an hour", "review"),
    ("A03", "Remote", "Bachelors or higher only.", "$30 an hour", "review"),
    ("A04", "Remote - PA", "Full time only.", "$30 an hour", "eligible"),
    ("A05", "Remote - PA", "Candidates with experience in Python only.", "$30 an hour", "eligible"),
    ("A06", "Remote (US)", "Valid ID only.", "$30 an hour", "eligible"),
    ("A07", "Remote", "Remote nationwide US. Headquartered in California.", "$25 an hour", "eligible"),
    ("A08", "Remote", "Delaware corporation. Open to candidates nationwide.", "$25 an hour", "eligible"),
    ("A09", "Remote", "Our company was founded in California. Remote across the US.", "$25 an hour", "eligible"),
    ("A10", "Remote - PA", "Offices in Texas and Washington. Remote in PA.", "$25 an hour", "eligible"),
    ("A11", "Pittsburgh, PA", "", "$18 - $25 an hour", "review"),
    ("A12", "Pittsburgh, PA", "", "$18 - $19 an hour", "review"),
    ("A13", "Pittsburgh, PA", "", "$17 - $18 an hour", "rejected"),
    ("A14", "Pittsburgh, PA", "", "$19 - $25 an hour", "eligible"),
    ("A15", "Pittsburgh, PA", "", "From $18 an hour", "review"),
    ("A16", "Pittsburgh, PA", "", "", "review"),
    ("A17", "Remote", "California residents only.", "$30 an hour", "rejected"),
    ("A18", "Remote", "California only.", "$30 an hour", "rejected"),
    ("A19", "Remote", "CA, TX, NY only.", "$30 an hour", "rejected"),
    ("A20", "Remote", "Must reside in Texas.", "$30 an hour", "rejected"),
    ("A21", "Remote (US)", "Not available in Pennsylvania.", "$30 an hour", "rejected"),
    ("A22", "Remote", "Pennsylvania excluded.", "$30 an hour", "rejected"),
)

# FR2-01: explicit PA exclusions beat nationwide/PA remote text (handoff §4).
FR2_01_QUOTED = (
    ("F01", "Remote - US", "Our company cannot employ Pennsylvania residents."),
    ("F02", "Remote - US", "We are headquartered in Texas but Pennsylvania residents cannot apply."),
    ("F03", "Remote - US", "Our company rejects Pennsylvania applicants."),
    ("F04", "Remote - US", "Our company does not accept Pennsylvania applicants."),
    ("F05", "Remote - US", "Not available in Pennsylvania."),
)

FR2_01_VARIANTS = (
    ("V01", "Remote - US", "Our company cannot hire Pennsylvania residents."),
    ("V02", "Remote - US", "Our company cannot employ PA residents."),
    ("V03", "Remote - US", "We will not consider PA candidates."),
    ("V04", "Remote - US", "Our company refuses Pennsylvania applicants."),
    ("V05", "Remote - US", "Our company prohibits Pennsylvania candidates."),
    ("V06", "Remote - US", "We are unable to employ Pennsylvania residents."),
    ("V07", "Remote - US", "Pennsylvania residents cannot apply."),
    ("V08", "Remote - US", "PA applicants will not be considered."),
    ("V09", "Remote - US", "Pennsylvania applicants are not eligible."),
    ("V10", "Remote - US", "PA residents may not apply."),
    ("V11", "Remote - US", "Pennsylvania applicants need not apply."),
    ("V12", "Remote - US", "Candidates cannot reside in Pennsylvania."),
    ("V13", "Remote", "Our company rejects PA applicants."),
    ("V14", "Remote - Pennsylvania", "Pennsylvania residents cannot apply."),
    ("V15", "Remote - US", "Pennsylvania is excluded and Texas is not excluded."),
)

# FR2-02: conflicting/unconsumed qualifiers never yield confident eligibility.
FR2_02_QUOTED = (
    ("Q01", "Remote - PA", "Region: Canada."),
    ("Q02", "Remote - US", "Location: NY."),
    ("Q03", "Remote", "Open to candidates in PA. Region: Canada."),
    ("Q04", "Remote", "Location: NY."),
)

FR2_02_VARIANTS = (
    ("Q05", "Remote - PA", "Location: Ontario."),
    ("Q06", "Remote - US", "Territory: Quebec."),
    ("Q07", "Remote - US", "Location: New York."),
    ("Q08", "Remote - US", "This role is limited to Ontario."),
    ("Q09", "Remote - US", "Work from Canada."),
    ("Q10", "Remote - US", "Canada-based role."),
    ("Q11", "Remote - PA", "Location: NY. Region: Canada."),
    ("Q12", "Remote", "Open to candidates in PA. Work from Canada."),
    ("Q13", "Remote - NY", "Location: PA."),
    ("Q14", "Remote - US", "Region: East Coast."),
)

# Compatible explicit qualifiers must not degrade genuine scope.
FR2_02_COMPATIBLE = (
    ("K01", "Remote - US", "Location: PA.", "eligible"),
    ("K02", "Remote - PA", "Region: United States.", "eligible"),
    ("K03", "Remote", "Location: US.", "eligible"),
    ("K04", "Remote", "Location: Pennsylvania.", "eligible"),
    ("K05", "Remote - US", "Within the US.", "eligible"),
    ("K06", "Remote - PA", "Location: PA.", "eligible"),
)

# FR2-03 guardrails: boilerplate and non-geographic phrases invent nothing.
FR2_03_SAFE_DETAILS = (
    ("S01", "Remote - US", "Our company is registered in Delaware."),
    ("S02", "Remote - US", "Headquartered in Texas."),
    ("S03", "Remote - US", "Our company was founded in California."),
    ("S04", "Remote - US", "We serve customers in Canada."),
    ("S05", "Remote - US", "Our office is in Canada."),
    ("S06", "Remote - US", "Our customers live in California and Pennsylvania."),
    ("S07", "Remote - US", "Candidates receive onboarding."),
    ("S08", "Remote - US", "We cannot hire enough engineers in Pennsylvania."),
)

FR2_03_NON_GEO_PHRASES = (
    ("N01", "Remote - PA", "Python required."),
    ("N02", "Remote - PA", "Valid ID required."),
    ("N03", "Remote - US", "Full time preferred."),
    ("N04", "Remote - Pennsylvania", "Bachelors degree mandatory."),
    ("N05", "Remote - PA", "Candidates with experience in Python only."),
    ("N06", "Remote - US", "Full time only."),
)


class Hermes40CorpusTests(unittest.TestCase):
    """Break caught: 28/40 recovered counterexamples still fail on 4330052."""

    def test_hermes_40_corpus(self):
        for cid, group, loc, details, allowed in HERMES_40:
            with self.subTest(case=cid, group=group, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertIn(decision.status, allowed)
                if decision.status == "eligible":
                    self.assertEqual(result.priority, "high")
                else:
                    self.assertIsNone(result.priority)


class HermesControlTests(unittest.TestCase):
    """Break caught: Remote - PA + Region: Canada evaluates eligible/high."""

    def test_seven_named_controls(self):
        for cid, loc, details, allowed in HERMES_CONTROLS:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertIn(decision.status, allowed)
                if decision.status != "eligible":
                    self.assertIsNone(result.priority)


class AntigravityProbeTests(unittest.TestCase):
    """Guardrail: the 22 prior adversarial probes must not regress."""

    def test_22_probes(self):
        for cid, loc, details, pay, expected in ANTIGRAVITY_PROBES:
            if cid == "A09":
                continue  # documented pre-existing discrepancy; locked below
            with self.subTest(case=cid, location=loc, details=details, pay=pay):
                decision, _ = decide(loc, details, pay)
                self.assertEqual(decision.status, expected)

    def test_a09_discrepancy_locked_at_observed_baseline(self):
        # Review table claims eligible; pristine 4330052 returns review because
        # "Remote across the US" is not consumed by the nationwide grammar.
        # Pre-existing, out of FR2 scope; flagged for coordinator decision.
        decision, _ = decide("Remote", "Our company was founded in California. Remote across the US.")
        self.assertEqual(decision.status, "review")


class FR201ExplicitPAExclusionTests(unittest.TestCase):
    """Break caught: _PA_EXCLUDED misses cannot/rejects/cannot-apply wordings."""

    def test_quoted_acceptance_cases_exclude(self):
        for cid, loc, details in FR2_01_QUOTED:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "rejected")
                self.assertEqual(location_reason(decision).outcome, "exclude")
                self.assertIsNone(result.priority)

    def test_negation_and_verb_variants_exclude(self):
        for cid, loc, details in FR2_01_VARIANTS:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "rejected")
                self.assertIsNone(result.priority)

    def test_pa_exclusion_beats_explicit_pa_suffix(self):
        decision, _ = decide("Remote - Pennsylvania", "Pennsylvania residents cannot apply.")
        self.assertEqual(decision.status, "rejected")


class FR202ConflictingQualifierTests(unittest.TestCase):
    """Break caught: unconsumed Region/Location labels pass PA/nationwide scope."""

    def test_quoted_acceptance_cases_review(self):
        for cid, loc, details in FR2_02_QUOTED:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "review")
                self.assertEqual(location_reason(decision).outcome, "review")
                self.assertIsNone(result.priority)

    def test_qualifier_variants_review(self):
        for cid, loc, details in FR2_02_VARIANTS:
            with self.subTest(case=cid, location=loc, details=details):
                decision, result = decide(loc, details)
                self.assertEqual(decision.status, "review")
                self.assertIsNone(result.priority)

    def test_compatible_qualifiers_do_not_degrade(self):
        for cid, loc, details, expected in FR2_02_COMPATIBLE:
            with self.subTest(case=cid, location=loc, details=details):
                decision, _ = decide(loc, details)
                self.assertEqual(decision.status, expected)

    def test_ambiguous_qualifier_never_hard_rejects(self):
        decision, _ = decide("Remote", "Location: NY.")
        self.assertEqual(decision.status, "review")
        self.assertNotEqual(location_reason(decision).outcome, "exclude")


class FR203PreservationTests(unittest.TestCase):
    """Guardrail: boilerplate/non-geographic text invents no restriction."""

    def test_incidental_boilerplate_stays_eligible(self):
        for cid, loc, details in FR2_03_SAFE_DETAILS:
            with self.subTest(case=cid, location=loc, details=details):
                decision, _ = decide(loc, details)
                self.assertEqual(decision.status, "eligible")
                self.assertEqual(location_reason(decision).outcome, "pass")

    def test_non_geo_required_preferred_invents_no_region(self):
        for cid, loc, details in FR2_03_NON_GEO_PHRASES:
            with self.subTest(case=cid, location=loc, details=details):
                decision, _ = decide(loc, details)
                self.assertEqual(decision.status, "eligible")
                self.assertEqual(location_reason(decision).outcome, "pass")

    def test_unknown_remote_geography_stays_review(self):
        decision, _ = decide("Remote", "Great benefits and dental.")
        self.assertEqual(decision.status, "review")

    def test_bounded_scan_completes_on_adversarial_whitespace(self):
        details = ("cannot" + " " * 5000 + "pennsylvania residents. ") * 8
        decision, _ = decide("Remote - US", details)
        self.assertIn(decision.status, ("eligible", "review", "rejected"))


if __name__ == "__main__":
    unittest.main()