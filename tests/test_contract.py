"""Synthetic acceptance tests for the typed result contract (CN-002; Slice A)."""
import json
import unittest

from career_navigator.contracts import (
    CURRENT_PAY_HOURLY,
    ResultContract,
    build_contract,
    classify,
    load_schema,
    validate_result,
)
from career_navigator.demo import _FIXTURES
from career_navigator.fixtures import GOLDEN_EXAMPLES
from career_navigator.models import Decision, Posting, Reason
from career_navigator.pipeline import evaluate_postings
from career_navigator.policy import Policy
from career_navigator.relevance import RelevanceResult, score


def _decision(status):
    return Decision(status, (Reason("role", "pass", "test"),))


def _relevance(priority, value=75.0):
    return RelevanceResult(
        Posting(url="https://x.example/1", title="T", company="C"),
        value, (), priority, "wording",
    )


def _contract_for(posting, policy=None):
    policy = policy or Policy()
    evaluation = evaluate_postings([posting], policy)[0]
    return build_contract(evaluation, policy)


class ClassifyTests(unittest.TestCase):
    def test_rejected_maps_to_excluded(self):
        self.assertEqual(classify(_decision("rejected"), _relevance(None)), "Excluded")

    def test_review_maps_to_needs_review(self):
        self.assertEqual(classify(_decision("review"), _relevance(None)), "Needs Review")

    def test_eligible_high_maps_to_strong_fit(self):
        self.assertEqual(classify(_decision("eligible"), _relevance("high")), "Strong Fit")

    def test_eligible_standard_maps_to_realistic(self):
        self.assertEqual(classify(_decision("eligible"), _relevance("standard")), "Realistic")

    def test_stretch_is_never_emitted_by_classify(self):
        for status, priority in (
            ("eligible", "high"), ("eligible", "standard"),
            ("review", None), ("rejected", None),
        ):
            self.assertNotEqual(classify(_decision(status), _relevance(priority)), "Stretch")

    def test_eligible_without_priority_raises(self):
        with self.assertRaises(ValueError):
            classify(_decision("eligible"), _relevance(None))

    def test_unknown_status_raises(self):
        with self.assertRaises(ValueError):
            classify(_decision("unknown"), _relevance(None))


class GoldenExampleTests(unittest.TestCase):
    def test_all_golden_examples_match_expected_classification(self):
        policy = Policy()
        for example in GOLDEN_EXAMPLES:
            with self.subTest(example=example.name):
                contract = _contract_for(example.posting, policy)
                self.assertEqual(
                    contract.classification, example.expected_classification,
                    f"{example.name}: {example.note}",
                )

    def test_a09_remote_across_us_is_review_not_eligible(self):
        example = next(e for e in GOLDEN_EXAMPLES if e.name == "a09-remote-across-us-review")
        contract = _contract_for(example.posting)
        self.assertEqual(contract.decision_status, "review")
        location = next(r for r in contract.reasons if r["factor"] == "location")
        self.assertEqual(location["outcome"], "review")
        self.assertTrue(contract.unknowns.remote_pa)

    def test_missing_pay_sets_pay_unknown_and_vs_current_unknown(self):
        example = next(e for e in GOLDEN_EXAMPLES if e.name == "missing-pay-review")
        contract = _contract_for(example.posting)
        self.assertTrue(contract.unknowns.pay)
        self.assertEqual(contract.pay.status, "missing")
        self.assertIsNone(contract.pay.annual_low)
        self.assertEqual(contract.pay.vs_current, "unknown")
        self.assertIsNone(contract.pay.meets_floor)
        self.assertIsNone(contract.pay.delta_annual)

    def test_pay_straddle_is_review_with_below_current_low(self):
        example = next(e for e in GOLDEN_EXAMPLES if e.name == "pay-straddle-review")
        contract = _contract_for(example.posting)
        self.assertEqual(contract.pay.status, "bounded")
        self.assertEqual(contract.pay.annual_low, 18.0 * 40.0 * 52.0)
        self.assertEqual(contract.pay.annual_high, 25.0 * 40.0 * 52.0)
        # Straddle: low below the floor but high above it -> meets_floor unknown.
        self.assertIsNone(contract.pay.meets_floor)
        self.assertEqual(contract.pay.vs_current, "below")

    def test_pay_at_21_hour_is_at_current(self):
        example = next(e for e in GOLDEN_EXAMPLES if e.name == "remote-pa-unknown-review")
        contract = _contract_for(example.posting)
        self.assertEqual(contract.pay.vs_current, "at")
        self.assertEqual(contract.pay.delta_annual, 0.0)
        self.assertEqual(contract.pay.current_annual, CURRENT_PAY_HOURLY * 40.0 * 52.0)

    def test_pay_above_21_hour_is_above_with_delta(self):
        example = next(e for e in GOLDEN_EXAMPLES if e.name == "eligible-pittsburgh")
        contract = _contract_for(example.posting)
        self.assertEqual(contract.pay.vs_current, "above")
        self.assertEqual(contract.pay.delta_annual, (25.0 - 21.0) * 40.0 * 52.0)
        self.assertTrue(contract.pay.meets_floor)

    def test_driving_excluded_has_below_floor_and_unknown_driving_false(self):
        example = next(e for e in GOLDEN_EXAMPLES if e.name == "driving-excluded")
        contract = _contract_for(example.posting)
        self.assertEqual(contract.pay.meets_floor, False)
        self.assertEqual(contract.pay.vs_current, "below")
        # Driving was mentioned (and excluded), so it is not unknown.
        self.assertFalse(contract.unknowns.driving)

    def test_driving_unmentioned_sets_driving_unknown(self):
        example = next(e for e in GOLDEN_EXAMPLES if e.name == "eligible-pittsburgh")
        contract = _contract_for(example.posting)
        self.assertTrue(contract.unknowns.driving)

    def test_source_fields_carry_canonical_apply_link(self):
        example = next(e for e in GOLDEN_EXAMPLES if e.name == "philadelphia-included")
        contract = _contract_for(example.posting)
        self.assertEqual(contract.source["url"], "https://www.indeed.com/viewjob?jk=GOLD5")
        self.assertEqual(contract.source["original_url"], example.posting.url)
        self.assertEqual(contract.source["provider"], "indeed")
        self.assertEqual(contract.source["provider_id"], "GOLD5")
        self.assertTrue(contract.source["trusted_host"])
        self.assertFalse(contract.source["rewritten"])

    def test_unknowns_list_matches_flags(self):
        for example in GOLDEN_EXAMPLES:
            with self.subTest(example=example.name):
                contract = _contract_for(example.posting)
                data = contract.to_dict()
                self.assertEqual(
                    set(data["unknowns"]["list"]),
                    {
                        name
                        for name, value in (
                            ("pay", data["unknowns"]["pay"]),
                            ("location", data["unknowns"]["location_missing"]),
                            ("driving", data["unknowns"]["driving"]),
                            ("remote_pa", data["unknowns"]["remote_pa"]),
                        )
                        if value
                    },
                )


class ContractShapeTests(unittest.TestCase):
    def test_result_is_json_serializable_and_validates(self):
        policy = Policy()
        for example in GOLDEN_EXAMPLES:
            with self.subTest(example=example.name):
                contract = _contract_for(example.posting, policy)
                data = json.loads(contract.to_json())
                self.assertEqual(validate_result(data), [])

    def test_demo_fixtures_all_validate_and_never_emit_stretch(self):
        policy = Policy()
        for evaluation in evaluate_postings(_FIXTURES, policy):
            with self.subTest(url=evaluation.posting.url):
                contract = build_contract(evaluation, policy)
                self.assertNotEqual(contract.classification, "Stretch")
                self.assertEqual(validate_result(contract.to_dict()), [])

    def test_contract_is_deterministic(self):
        posting = GOLDEN_EXAMPLES[0].posting
        first = _contract_for(posting).to_dict()
        second = _contract_for(posting).to_dict()
        self.assertEqual(first, second)

    def test_result_contract_type(self):
        contract = _contract_for(GOLDEN_EXAMPLES[0].posting)
        self.assertIsInstance(contract, ResultContract)
        self.assertEqual(contract.decision_status, "eligible")
        self.assertEqual(contract.relevance_priority, "high")
        self.assertIn("job relevance index", contract.relevance_wording)

    def test_relevance_factors_and_reasons_are_copied(self):
        contract = _contract_for(GOLDEN_EXAMPLES[0].posting)
        self.assertTrue(contract.factors)
        self.assertEqual(
            {r["factor"] for r in contract.reasons},
            {"employer", "role", "driving", "location", "pay"},
        )


class ValidateResultTests(unittest.TestCase):
    def setUp(self):
        self.data = _contract_for(GOLDEN_EXAMPLES[0].posting).to_dict()

    def test_valid_result_has_no_errors(self):
        self.assertEqual(validate_result(self.data), [])

    def test_missing_field_is_rejected(self):
        data = dict(self.data)
        del data["pay"]
        self.assertTrue(validate_result(data))

    def test_unexpected_field_is_rejected(self):
        data = dict(self.data)
        data["surprise"] = 1
        self.assertTrue(validate_result(data))

    def test_wrong_classification_is_rejected(self):
        data = dict(self.data)
        data["classification"] = "Perfect Fit"
        self.assertTrue(validate_result(data))

    def test_classification_mismatching_status_is_rejected(self):
        data = dict(self.data)
        data["classification"] = "Excluded"  # decision_status is eligible
        self.assertTrue(validate_result(data))

    def test_unknowns_list_mismatch_is_rejected(self):
        data = json.loads(json.dumps(self.data))
        data["unknowns"]["list"] = []
        self.assertTrue(validate_result(data))

    def test_non_object_is_rejected(self):
        self.assertTrue(validate_result(["not", "an", "object"]))

    def test_relevance_value_out_of_range_is_rejected(self):
        data = json.loads(json.dumps(self.data))
        data["relevance"]["value"] = 150.0
        self.assertTrue(validate_result(data))


class SchemaTests(unittest.TestCase):
    def test_schema_loads_and_names_the_contract(self):
        schema = load_schema()
        self.assertEqual(schema["title"], "Career Navigator result contract")
        self.assertEqual(
            schema["properties"]["classification"]["enum"],
            ["Strong Fit", "Realistic", "Stretch", "Needs Review", "Excluded"],
        )

    def test_schema_requires_the_same_fields_as_validate_result(self):
        schema = load_schema()
        required = set(schema["required"])
        data = _contract_for(GOLDEN_EXAMPLES[0].posting).to_dict()
        self.assertEqual(required, set(data.keys()))


if __name__ == "__main__":
    unittest.main()
