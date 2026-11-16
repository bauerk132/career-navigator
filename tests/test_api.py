"""Tests for the synthetic adapter + FastAPI backend (CN-004; Slice C)."""
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from career_navigator.adapters import default_source_path, load_postings
from career_navigator.app import create_app, job_id_for_url
from career_navigator.pipeline import evaluate_postings
from career_navigator.policy import Policy
from career_navigator.ranking import rank_evaluations


class AdapterTests(unittest.TestCase):
    def test_loads_nineteen_postings_in_seed_order(self):
        postings = load_postings()
        self.assertEqual(len(postings), 19)
        self.assertEqual(postings[0].title, "IT Support Technician")
        self.assertEqual(postings[0].company, "Acme Tech")

    def test_load_is_deterministic(self):
        self.assertEqual(load_postings(), load_postings())

    def test_default_source_is_the_packaged_seed(self):
        path = default_source_path()
        self.assertTrue(path.name == "synthetic_postings.json")
        self.assertTrue(path.exists())

    def test_missing_file_raises_with_path(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            load_postings("/tmp/definitely-not-here.json")
        self.assertIn("definitely-not-here", str(ctx.exception))

    def test_bad_shape_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps({"postings": [{"url": "https://x.example/1"}]}))
            with self.assertRaises(ValueError) as ctx:
                load_postings(bad)
            self.assertIn("missing key 'title'", str(ctx.exception))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_health(self):
        with self.client as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["source"], "synthetic")
        self.assertEqual(body["postings"], 19)

    def test_lazy_load_without_lifespan(self):
        # No context manager: the startup event never runs, so the lazy
        # load path must serve the request anyway.
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["postings"], 19)

    def test_jobs_matches_direct_computation(self):
        expected = rank_evaluations(
            evaluate_postings(load_postings(), Policy()), Policy()
        )
        with self.client as client:
            response = client.get("/api/jobs")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["shortlist"]), len(expected.shortlist))
        self.assertEqual(len(body["review"]), len(expected.review))
        self.assertEqual(len(body["excluded"]), len(expected.excluded))
        self.assertEqual(body["total"], 19)
        self.assertEqual(
            [job["id"] for job in body["shortlist"]],
            [job_id_for_url(r.contract.to_dict()["source"]["url"]) for r in expected.shortlist],
        )
        scores = [job["fit_score"] for job in body["shortlist"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_shortlist_ranks_are_one_based(self):
        with self.client as client:
            body = client.get("/api/jobs/shortlist").json()
        self.assertEqual(
            [job["shortlist_rank"] for job in body["shortlist"]],
            list(range(1, len(body["shortlist"]) + 1)),
        )

    def test_job_detail_found_and_not_found(self):
        with self.client as client:
            shortlist = client.get("/api/jobs/shortlist").json()["shortlist"]
            first = shortlist[0]
            found = client.get(f"/api/jobs/{first['id']}")
            self.assertEqual(found.status_code, 200)
            self.assertEqual(found.json()["contract"], first["contract"])
            self.assertEqual(found.json()["final_classification"], first["final_classification"])
            missing = client.get("/api/jobs/000000000000")
            self.assertEqual(missing.status_code, 404)

    def test_stats_reports_policy(self):
        with self.client as client:
            body = client.get("/api/stats").json()
        self.assertEqual(body["totals"]["total"], 19)
        policy = body["policy"]
        self.assertEqual(policy["salary_floor_hourly"], 19.0)
        self.assertEqual(policy["current_pay_hourly"], 21.0)
        self.assertEqual(policy["fit_strong_threshold"], 70.0)
        self.assertEqual(policy["fit_realistic_threshold"], 45.0)
        self.assertIn("pittsburgh", policy["primary_cities"])

    def test_refresh_reloads_and_source_stays_unchanged(self):
        source = default_source_path()
        before = source.read_bytes()
        with self.client as client:
            body = client.post("/api/refresh").json()
        self.assertEqual(body["totals"]["total"], 19)
        self.assertIsNotNone(body["refreshed_at"])
        self.assertEqual(source.read_bytes(), before)

    def test_cors_allows_localhost_dashboard_only(self):
        headers = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"}
        with self.client as client:
            allowed = client.options("/api/jobs", headers=headers)
            self.assertEqual(allowed.headers.get("access-control-allow-origin"), "http://localhost:5173")
            denied = client.options(
                "/api/jobs",
                headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
            )
            self.assertNotIn("access-control-allow-origin", denied.headers)


if __name__ == "__main__":
    unittest.main()
