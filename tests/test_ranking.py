"""Synthetic acceptance tests for the blueprint-weighted ranking engine (CN-005)."""
import unittest

from career_navigator.contracts import build_contract
from career_navigator.demo import _FIXTURES
from career_navigator.fixtures import GOLDEN_EXAMPLES
from career_navigator.models import Posting
from career_navigator.pipeline import evaluate_postings
from career_navigator.policy import Policy
from career_navigator.ranking import (
    FIT_WEIGHTS,
    RankedResults,
    final_classification,
    fit_score,
    rank_evaluation,
    rank_evaluations,
)


def posting(title="IT Support Technician", company="Acme Tech", location="Pittsburgh, PA",
            details="Support Windows systems and networks.", pay_text="$25 an hour",
            url="https://x.example/1", **kw):
    return Posting(url=url, title=title, company=company,
                   location=location, details=details, pay_text=pay_text, **kw)


def ranked(p, policy=None):
    policy = policy or Policy()
    evaluation = evaluate_postings([p], policy)[0]
    return rank_evaluation(evaluation, policy)


def factor(result, name):
    return next(f for f in result.fit_factors if f.name == name)


class FitWeightsTests(unittest.TestCase):
    def test_blueprint_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(FIT_WEIGHTS.values()), 1.0)
        self.assertEqual(
            FIT_WEIGHTS,
            {"skills": 0.30, "career_growth": 0.30, "hiring_requirements": 0.20,
             "commute_schedule": 0.10, "comp": 0.10},
        )

    def test_fit_score_is_the_weighted_sum(self):
        result = ranked(posting())
        expected = round(100.0 * sum(f.contribution for f in result.fit_factors), 2)
        self.assertEqual(result.fit_score, expected)
        self.assertEqual(result.fit_score, fit_score(result.fit_factors))


class SkillsFactorTests(unittest.TestCase):
    def test_technical_posting_scores_full_skills(self):
        self.assertEqual(factor(ranked(posting()), "skills").raw, 1.0)

    def test_non_technical_posting_scores_zero_skills(self):
        cafe = posting(title="Cafe Manager", details="", pay_text="$45,000 a year")
        self.assertEqual(factor(ranked(cafe), "skills").raw, 0.0)


class GrowthFactorTests(unittest.TestCase):
    def test_growth_signals_score_full(self):
        p = posting(details="Training provided. Certification support and career advancement.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 1.0)

    def test_one_growth_signal_scores_half(self):
        p = posting(details="Training provided.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.5)

    def test_no_growth_language_scores_zero(self):
        self.assertEqual(factor(ranked(posting()), "career_growth").raw, 0.0)


class RequirementsFactorTests(unittest.TestCase):
    def test_no_requirements_scores_full(self):
        self.assertEqual(factor(ranked(posting()), "hiring_requirements").raw, 1.0)

    def test_five_years_and_degree_bind_the_factor(self):
        p = posting(details="Requires 5+ years of experience. Bachelor's degree required.")
        f = factor(ranked(p), "hiring_requirements")
        self.assertAlmostEqual(f.raw, max(0.0, 1.0 - 5 / 6.0))
        self.assertIn("5+ years", f.evidence)

    def test_two_years_scores_two_thirds(self):
        p = posting(details="2 years of experience preferred.")
        f = factor(ranked(p), "hiring_requirements")
        self.assertAlmostEqual(f.raw, 1.0 - 2 / 6.0)

    def test_degree_preferred_softens_the_factor(self):
        p = posting(details="Bachelor's degree preferred.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 0.75)


class CommuteFactorTests(unittest.TestCase):
    def test_primary_city_scores_full(self):
        self.assertEqual(factor(ranked(posting()), "commute_schedule").raw, 1.0)

    def test_remote_unstated_scores_half(self):
        p = posting(location="Remote", details="Answer support tickets.")
        self.assertEqual(factor(ranked(p), "commute_schedule").raw, 0.5)

    def test_flexible_scheduling_adds_a_tenth(self):
        p = posting(location="Remote", details="Flexible hours. Answer support tickets.")
        self.assertEqual(factor(ranked(p), "commute_schedule").raw, 0.6)

    def test_out_of_area_scores_zero(self):
        p = posting(location="Cleveland, OH")
        self.assertEqual(factor(ranked(p), "commute_schedule").raw, 0.0)


class CompFactorTests(unittest.TestCase):
    def test_pay_above_current_scores_full(self):
        self.assertEqual(factor(ranked(posting()), "comp").raw, 1.0)

    def test_pay_at_floor_but_below_current_scores_three_quarters(self):
        p = posting(pay_text="$19.50 an hour")
        self.assertEqual(factor(ranked(p), "comp").raw, 0.75)

    def test_pay_straddle_scores_half(self):
        example = next(e for e in GOLDEN_EXAMPLES if e.name == "pay-straddle-review")
        self.assertEqual(factor(ranked(example.posting), "comp").raw, 0.5)

    def test_pay_unknown_scores_zero(self):
        example = next(e for e in GOLDEN_EXAMPLES if e.name == "missing-pay-review")
        self.assertEqual(factor(ranked(example.posting), "comp").raw, 0.0)

    def test_upper_bound_only_scores_half_when_high_meets_floor(self):
        p = posting(pay_text="Up to $25 an hour")
        self.assertEqual(factor(ranked(p), "comp").raw, 0.5)


class ClassificationBandTests(unittest.TestCase):
    def test_strong_fit_at_the_threshold(self):
        contract = build_contract(evaluate_postings([posting()], Policy())[0], Policy())
        self.assertEqual(final_classification(contract, 70.0, Policy()), "Strong Fit")

    def test_realistic_below_strong_and_at_realistic(self):
        contract = build_contract(evaluate_postings([posting()], Policy())[0], Policy())
        self.assertEqual(final_classification(contract, 69.99, Policy()), "Realistic")
        self.assertEqual(final_classification(contract, 45.0, Policy()), "Realistic")

    def test_stretch_below_realistic(self):
        contract = build_contract(evaluate_postings([posting()], Policy())[0], Policy())
        self.assertEqual(final_classification(contract, 44.99, Policy()), "Stretch")

    def test_review_and_excluded_never_change(self):
        policy = Policy()
        for example in GOLDEN_EXAMPLES:
            evaluation = evaluate_postings([example.posting], policy)[0]
            contract = build_contract(evaluation, policy)
            for score_value in (0.0, 50.0, 100.0):
                with self.subTest(example=example.name, score=score_value):
                    result = final_classification(contract, score_value, policy)
                    if contract.decision_status == "review":
                        self.assertEqual(result, "Needs Review")
                    elif contract.decision_status == "rejected":
                        self.assertEqual(result, "Excluded")


class GoldenRankingTests(unittest.TestCase):
    def test_eligible_pittsburgh_is_strong_fit_with_a_growth_gap(self):
        example = next(e for e in GOLDEN_EXAMPLES if e.name == "eligible-pittsburgh")
        result = ranked(example.posting)
        self.assertEqual(result.final_classification, "Strong Fit")
        self.assertEqual(result.fit_score, 70.0)
        self.assertEqual(len(result.gaps), 1)
        self.assertIn("career growth", result.gaps[0])

    def test_cafe_manager_is_a_stretch(self):
        cafe = posting(title="Cafe Manager", company="Morning Brew",
                       location="Pittsburgh, PA", details="", pay_text="$45,000 a year")
        result = ranked(cafe)
        self.assertEqual(result.final_classification, "Stretch")
        self.assertEqual(result.fit_score, 40.0)
        self.assertEqual(len(result.gaps), 2)

    def test_growth_rich_posting_scores_100_with_no_gaps(self):
        p = posting(details="Support Windows systems and networks. "
                            "Training provided. Certification support and career advancement.")
        result = ranked(p)
        self.assertEqual(result.fit_score, 100.0)
        self.assertEqual(result.final_classification, "Strong Fit")
        self.assertEqual(result.gaps, ())

    def test_a09_remote_across_us_is_realistic_on_fit(self):
        # The contract says Strong Fit (eligible, high relevance priority);
        # the ranking layer scores it Realistic on the blueprint factors
        # (thin technical match, no growth language). The two layers may differ.
        example = next(e for e in GOLDEN_EXAMPLES if e.name == "a09-remote-across-us")
        result = ranked(example.posting)
        self.assertEqual(result.fit_score, 60.0)
        self.assertEqual(result.final_classification, "Realistic")
        self.assertEqual(result.contract.classification, "Strong Fit")

    def test_golden_examples_keep_their_contract_classifications(self):
        policy = Policy()
        for example in GOLDEN_EXAMPLES:
            with self.subTest(example=example.name):
                result = ranked(example.posting, policy)
                if example.expected_classification in ("Needs Review", "Excluded"):
                    self.assertEqual(result.final_classification, example.expected_classification)
                else:
                    self.assertIn(
                        result.final_classification, ("Strong Fit", "Realistic", "Stretch")
                    )


class RankedResultsTests(unittest.TestCase):
    def test_shortlist_is_sorted_by_fit_then_relevance(self):
        policy = Policy()
        results = rank_evaluations(evaluate_postings(_FIXTURES, policy), policy)
        self.assertIsInstance(results, RankedResults)
        scores = [r.fit_score for r in results.shortlist]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(
            [r.shortlist_rank for r in results.shortlist],
            list(range(1, len(results.shortlist) + 1)),
        )

    def test_groups_cover_every_posting_once(self):
        policy = Policy()
        evaluations = evaluate_postings(_FIXTURES, policy)
        results = rank_evaluations(evaluations, policy)
        total = len(results.shortlist) + len(results.review) + len(results.excluded)
        self.assertEqual(total, len(_FIXTURES))
        urls = (
            [r.contract.source["url"] for r in results.shortlist]
            + [r.contract.source["url"] for r in results.review]
            + [r.contract.source["url"] for r in results.excluded]
        )
        self.assertEqual(len(set(urls)), len(_FIXTURES))

    def test_ranking_is_deterministic_regardless_of_input_order(self):
        policy = Policy()
        first = rank_evaluations(evaluate_postings(_FIXTURES, policy), policy)
        second = rank_evaluations(evaluate_postings(tuple(reversed(_FIXTURES)), policy), policy)
        self.assertEqual(
            [r.contract.source["url"] for r in first.shortlist],
            [r.contract.source["url"] for r in second.shortlist],
        )
        self.assertEqual(
            [r.contract.source["url"] for r in first.review],
            [r.contract.source["url"] for r in second.review],
        )

    def test_review_and_excluded_groups_have_no_shortlist_rank(self):
        policy = Policy()
        results = rank_evaluations(evaluate_postings(_FIXTURES, policy), policy)
        for result in list(results.review) + list(results.excluded):
            self.assertIsNone(result.shortlist_rank)


class FixRoundBTests(unittest.TestCase):
    # B-G2-01: negated requirements must not score as requirements.
    def test_no_degree_required_does_not_score_as_requirement(self):
        p = posting(details="No bachelor's degree required.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 1.0)

    def test_degree_not_required_does_not_score_as_requirement(self):
        p = posting(details="Bachelor's degree not required.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 1.0)

    def test_no_cert_required_does_not_score_as_requirement(self):
        p = posting(details="No CompTIA A+ required.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 1.0)

    def test_cert_not_required_does_not_score_as_requirement(self):
        p = posting(details="CompTIA A+ not required.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 1.0)

    def test_network_plus_required_scores(self):
        # Cert-name boundary fix: "network+" must match (the old \b after
        # "+" never matched, so Network+/Security+ requirements were missed).
        p = posting(details="Network+ certification required.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 0.7)

    def test_security_plus_not_required_does_not_score(self):
        p = posting(details="Security+ not required.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 1.0)

    def test_not_flexible_adds_no_perk(self):
        p = posting(location="Remote", details="Not flexible hours. Answer support tickets.")
        commute = factor(ranked(p), "commute_schedule")
        self.assertEqual(commute.raw, 0.5)
        self.assertNotIn("flexible", commute.evidence)

    # B2-N1: the perk-negation guard allows a short gap, so "isn't flexible",
    # "not very flexible", and "never hybrid" are caught too.
    def test_isnt_flexible_adds_no_perk(self):
        p = posting(location="Remote", details="Isn't flexible hours. Answer support tickets.")
        commute = factor(ranked(p), "commute_schedule")
        self.assertEqual(commute.raw, 0.5)
        self.assertNotIn("flexible", commute.evidence)

    def test_not_very_flexible_adds_no_perk(self):
        p = posting(location="Remote", details="Not very flexible. Answer support tickets.")
        commute = factor(ranked(p), "commute_schedule")
        self.assertEqual(commute.raw, 0.5)
        self.assertNotIn("flexible", commute.evidence)

    def test_never_hybrid_adds_no_perk(self):
        p = posting(location="Remote", details="Never hybrid. Answer support tickets.")
        commute = factor(ranked(p), "commute_schedule")
        self.assertEqual(commute.raw, 0.5)
        self.assertNotIn("hybrid", commute.evidence)

    def test_flexible_hours_still_adds_the_perk(self):
        # Remote base is 0.5; the flexible perk adds 0.1.
        p = posting(location="Remote", details="Flexible hours. Answer support tickets.")
        commute = factor(ranked(p), "commute_schedule")
        self.assertEqual(commute.raw, 0.6)
        self.assertIn("flexible", commute.evidence)

    # B3-N3: the perk-negation gap excludes commas, so a negation cannot
    # reach across a clause ("not remote" must not suppress "flexible").
    def test_not_remote_flexible_schedule_keeps_perk(self):
        p = posting(location="Remote", details="This is not remote, flexible schedule offered.")
        commute = factor(ranked(p), "commute_schedule")
        self.assertEqual(commute.raw, 0.6)
        self.assertIn("flexible", commute.evidence)

    def test_degree_required_still_scores(self):
        p = posting(details="Bachelor's degree required.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 0.5)

    # B2-N2: contradictory multi-track text — the explicit positive
    # requirement wins (conservative), not the negated one.
    def test_degree_required_for_senior_track_beats_not_required_for_entry(self):
        p = posting(details="No degree required for entry. Bachelor's degree required for senior track.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 0.5)

    def test_cert_required_for_senior_track_beats_not_required_for_entry(self):
        p = posting(details="No certification required for entry. CompTIA A+ required for senior track.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 0.7)

    # B3-N4: clauses count separately — in comma-joined contradictory text
    # the explicit positive requirement wins.
    def test_cert_required_beats_not_required_for_interns_in_same_sentence(self):
        p = posting(details="Security+ certification required, no certification required for interns.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 0.7)

    # B-G2-02: years count only in a candidate-requirement framing.
    def test_employer_history_years_not_extracted(self):
        p = posting(details="Our company has 10 years of experience in the industry. Support Windows systems.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 1.0)

    def test_minimum_years_extracted(self):
        p = posting(details="Minimum 3 years experience.")
        self.assertAlmostEqual(factor(ranked(p), "hiring_requirements").raw, 0.5)

    def test_bare_years_without_framing_not_extracted(self):
        p = posting(details="3 years of experience with Windows systems.")
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 1.0)

    def test_plus_years_extracted(self):
        p = posting(details="3+ years of experience.")
        self.assertAlmostEqual(factor(ranked(p), "hiring_requirements").raw, 0.5)

    # B-G2-03: growth signals count once per distinct concept; requirement
    # context is not growth.
    def test_mentorship_counts_once(self):
        p = posting(details="Mentorship available.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.5)

    def test_certification_variants_count_once(self):
        p = posting(details="Certification and certifications offered.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.5)

    def test_certification_required_is_not_growth(self):
        p = posting(details="Certification required for this role.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.0)

    def test_training_required_is_not_growth(self):
        p = posting(details="Training required for this role.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.0)

    # B2-R1: the growth guard is two-sided within the sentence — a
    # requirement anchor BEFORE the term ("Must have CompTIA
    # certification.") leaks into growth just as one after it does, and the
    # false credit flips the class (79 Strong Fit -> 64 Realistic on the
    # reviewer's probe).
    def test_must_have_certification_is_not_growth(self):
        p = posting(details="Must have CompTIA certification.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.0)
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 0.7)
        self.assertEqual(ranked(p).final_classification, "Realistic")

    def test_required_security_plus_certification_is_not_growth(self):
        p = posting(details="Required: Security+ certification.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.0)
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 0.7)

    def test_we_require_certification_is_not_growth(self):
        p = posting(details="We require certification.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.0)

    def test_training_provided_is_growth(self):
        p = posting(details="Training provided.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.5)

    def test_certification_support_is_growth(self):
        p = posting(details="Certification support.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.5)

    def test_growth_in_next_sentence_still_counts(self):
        p = posting(details="Must have CompTIA certification. Training provided.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.5)

    # B3-R1: the before-guard covers the FULL preceding sentence fragment —
    # a 30-char window cuts off the anchor before a cert list ("Must have
    # CompTIA A+, Network+, or Security+ certification."). The title gives
    # skills 1.0 so the fit lands exactly on the reviewer's 64.00 Realistic.
    def test_cert_list_a_plus_or_security_plus_is_not_growth(self):
        p = posting(title="Network Support Technician",
                    details="Must have a valid CompTIA A+ or Security+ certification.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.0)
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 0.7)
        self.assertEqual(ranked(p).fit_score, 64.00)
        self.assertEqual(ranked(p).final_classification, "Realistic")

    def test_cert_list_with_network_plus_is_not_growth(self):
        p = posting(title="Network Support Technician",
                    details="Must have CompTIA A+, Network+, or Security+ certification.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.0)
        self.assertEqual(factor(ranked(p), "hiring_requirements").raw, 0.7)
        self.assertEqual(ranked(p).fit_score, 64.00)
        self.assertEqual(ranked(p).final_classification, "Realistic")

    # B3-N1: "mandatory" is a requirement anchor.
    def test_mandatory_certification_is_not_growth(self):
        p = posting(details="Mandatory certification.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.0)

    def test_certification_is_mandatory_is_not_growth(self):
        p = posting(details="Certification is mandatory.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.0)

    # B3-N2: the after-guard is clause-aware — a "minimum" about pay/hours
    # in a later clause must not suppress genuinely offered training.
    def test_training_with_minimum_pay_in_next_clause_is_growth(self):
        p = posting(details="We provide training, minimum starting pay $25/hour.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.5)

    def test_training_with_minimum_hours_in_next_clause_is_growth(self):
        p = posting(details="We offer paid training, minimum 40 hours per week.")
        self.assertEqual(factor(ranked(p), "career_growth").raw, 0.5)

    # B-G1-01: fit weights are Policy-configurable with validation.
    def test_default_policy_weights_are_the_blueprint(self):
        self.assertEqual(Policy().fit_weights, FIT_WEIGHTS)

    def test_custom_weights_change_the_score(self):
        cafe = posting(title="Cafe Manager", company="Morning Brew",
                       location="Pittsburgh, PA", details="", pay_text="$45,000 a year")
        custom = Policy(fit_weights={
            "skills": 0.0, "career_growth": 0.0, "hiring_requirements": 1.0,
            "commute_schedule": 0.0, "comp": 0.0,
        })
        self.assertEqual(ranked(cafe, custom).fit_score, 100.0)

    def test_invalid_weights_raise(self):
        for bad in (
            {"skills": 0.3, "career_growth": 0.3, "hiring_requirements": 0.2,
             "commute_schedule": 0.1},  # missing comp
            {"skills": 0.5, "career_growth": 0.3, "hiring_requirements": 0.2,
             "commute_schedule": 0.1, "comp": 0.1},  # sums to 1.2
            {"skills": -0.1, "career_growth": 0.3, "hiring_requirements": 0.2,
             "commute_schedule": 0.1, "comp": 0.5},  # negative value
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    Policy(fit_weights=bad)


if __name__ == "__main__":
    unittest.main()
