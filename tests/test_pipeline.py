"""Synthetic acceptance tests for the end-to-end pipeline and stdout demo (A2, C10)."""
import unittest

from career_navigator.demo import render_report
from career_navigator.models import Posting
from career_navigator.pipeline import evaluate_postings
from career_navigator.policy import Policy


def posting(url="https://x.example/1", title="IT Support Technician", company="Acme Tech",
            location="Pittsburgh, PA", details="Support Windows systems.", pay_text="$25 an hour",
            provider="other", provider_id=None, **kw):
    return Posting(url=url, title=title, company=company, location=location, details=details,
                   pay_text=pay_text, provider=provider, provider_id=provider_id, **kw)


class PipelineTests(unittest.TestCase):
    def test_end_to_end_eligible_technical_pittsburgh(self):
        results = evaluate_postings([posting()], Policy())
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.decision.status, "eligible")
        self.assertIsNotNone(r.relevance.priority)
        self.assertEqual(r.identity.provider, "other")
        self.assertTrue(r.relevance.factors)

    def test_upstream_records_preserved_but_cn_decides(self):
        p = posting(details="CDL required.", upstream_verdict="ok", upstream_status="new")
        r = evaluate_postings([p], Policy())[0]
        self.assertEqual(r.upstream_records, {"verdict": "ok", "status": "new"})
        self.assertEqual(r.decision.status, "rejected")

    def test_uncertain_duplicates_both_visible(self):
        a = posting(url="https://x.example/a", title="Cafe Manager", provider="indeed", provider_id="J1")
        b = posting(url="https://x.example/b", title="Café Supervisor", provider="indeed", provider_id="J1")
        results = evaluate_postings([a, b], Policy())
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.duplicate_confidence == "uncertain" for r in results))

    def test_exact_duplicates_both_visible(self):
        a = posting(url="https://x.example/a", title="Cafe Manager")
        b = posting(url="https://x.example/b", title="Cafe Manager")
        results = evaluate_postings([a, b], Policy())
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.duplicate_confidence == "exact" for r in results))

    def test_ordering_deterministic_regardless_of_input_order(self):
        a = posting(url="https://x.example/a", title="Zebra Analyst")
        b = posting(url="https://x.example/b", title="Alpha Analyst")
        first = [r.posting.url for r in evaluate_postings([a, b], Policy())]
        second = [r.posting.url for r in evaluate_postings([b, a], Policy())]
        self.assertEqual(first, second)

    def test_lookalike_url_provenance_retained(self):
        p = posting(url="https://indeed.com.evil.example/viewjob?jk=x")
        r = evaluate_postings([p], Policy())[0]
        self.assertFalse(r.identity.trusted_host)
        self.assertEqual(r.identity.original_url, p.url)
        self.assertEqual(r.identity.url, p.url)


class DemoTests(unittest.TestCase):
    def test_report_is_stdout_text_with_explanations_and_uncertainty(self):
        report = render_report()
        lowered = report.lower()
        self.assertIn("job relevance index", lowered)
        self.assertIn("eligible", lowered)
        self.assertIn("rejected", lowered)
        self.assertIn("review", lowered)
        self.assertIn("uncertain", lowered)
        self.assertIn("not verified candidate fit", lowered)


if __name__ == "__main__":
    unittest.main()