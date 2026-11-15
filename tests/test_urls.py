"""Synthetic acceptance tests for CN-owned URL identity handling (A1, JF-15/45)."""
import unittest

from career_navigator.urls import canonical_url


class CanonicalUrlTests(unittest.TestCase):
    def test_indeed_jk_strips_tracking_params(self):
        r = canonical_url("https://www.indeed.com/viewjob?jk=abc123&utm_source=alert&from=hp")
        self.assertEqual(r.url, "https://www.indeed.com/viewjob?jk=abc123")
        self.assertEqual(r.provider, "indeed")
        self.assertEqual(r.provider_id, "abc123")
        self.assertTrue(r.trusted_host)

    def test_indeed_vjk_takes_precedence_over_jk(self):
        r = canonical_url("https://www.indeed.com/viewjob?vjk=V1&jk=J1")
        self.assertEqual(r.provider_id, "V1")

    def test_indeed_precedence_independent_of_param_order(self):
        a = canonical_url("https://www.indeed.com/viewjob?vjk=V1&jk=J1")
        b = canonical_url("https://www.indeed.com/viewjob?jk=J1&vjk=V1")
        self.assertEqual(a.provider_id, b.provider_id)
        self.assertEqual(a.url, b.url)

    def test_lookalike_suffix_host_is_not_trusted(self):
        r = canonical_url("https://indeed.com.evil.example/viewjob?jk=x&utm_source=y")
        self.assertFalse(r.trusted_host)
        self.assertEqual(r.provider, "other")
        self.assertIsNone(r.provider_id)
        # Untrusted hosts are never rewritten: provenance preserved verbatim.
        self.assertEqual(r.url, "https://indeed.com.evil.example/viewjob?jk=x&utm_source=y")

    def test_lookalike_prefix_host_is_not_trusted(self):
        for host in ("notindeed.com", "indeed.comcast.net", "fakeindeed.com"):
            r = canonical_url(f"https://{host}/viewjob?jk=x")
            self.assertFalse(r.trusted_host, host)
            self.assertEqual(r.provider, "other", host)

    def test_dot_boundary_subdomain_is_trusted(self):
        r = canonical_url("https://jobs.indeed.com/viewjob?jk=abc")
        self.assertTrue(r.trusted_host)
        self.assertEqual(r.provider, "indeed")

    def test_linkedin_job_id_extracted_from_path(self):
        r = canonical_url("https://www.linkedin.com/jobs/view/1234567890/?trk=public_jobs")
        self.assertEqual(r.provider, "linkedin")
        self.assertEqual(r.provider_id, "1234567890")
        self.assertEqual(r.url, "https://www.linkedin.com/jobs/view/1234567890/")

    def test_linkedin_lookalike_not_trusted(self):
        r = canonical_url("https://linkedin.com.evil.example/jobs/view/1/")
        self.assertFalse(r.trusted_host)
        self.assertEqual(r.provider, "other")

    def test_unknown_provider_preserved_verbatim(self):
        original = "https://boards.greenhouse.io/acme/jobs/42?utm_campaign=x"
        r = canonical_url(original)
        self.assertEqual(r.provider, "other")
        self.assertIsNone(r.provider_id)
        self.assertEqual(r.url, original)
        self.assertFalse(r.rewritten)

    def test_provenance_original_always_retained(self):
        original = "https://WWW.INDEED.COM/viewjob?jk=abc&utm_source=x"
        r = canonical_url(original)
        self.assertEqual(r.original_url, original)
        self.assertTrue(r.rewritten)

    def test_host_and_scheme_case_normalized_for_trusted_host(self):
        r = canonical_url("HTTPS://WWW.INDEED.COM/viewjob?jk=abc")
        self.assertEqual(r.url, "https://www.indeed.com/viewjob?jk=abc")

    def test_empty_url_rejected(self):
        with self.assertRaises(ValueError):
            canonical_url("")
        with self.assertRaises(ValueError):
            canonical_url("   ")


if __name__ == "__main__":
    unittest.main()