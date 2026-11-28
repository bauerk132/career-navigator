"""Synthetic live WS6 provenance and privacy checks."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from career_navigator.app import create_app
from career_navigator import text
from career_navigator.live_adapter import load_live_database
from test_live_wiring import _row, _write_db, FIXTURES


class ProvenanceTests(unittest.TestCase):
    def test_excerpt(self):
        for raw, limit, expected in (
            ("", 300, ""), (" \n\t ", 300, ""),
            ("abc def ghi", 7, "abc def…"),
            (" a\n b  ", 3, "a b"), ("abc d", 4, "abc…"),
            ("ééééé", 4, "éééé…"), ("x" * 300, 300, "x" * 300),
        ):
            with self.subTest(raw=raw[:12], limit=limit):
                self.assertEqual(text.make_excerpt(raw, limit), expected)

    def test_live_rows_and_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "fixture.db"
            private = "private-feed@example.test"
            stamp = "2026-11-19 09:00:00"
            tail = "UNSERVED_TAIL_SENTINEL"
            rows = [
                _row(1, "https://www.indeed.com/viewjob?jk=aaaaaaaaaaaaaaaa",
                     "IT Support Technician", "Acme", "Pittsburgh, PA",
                     "<script>alert(1)</script><style>x{}</style><b>Fixture</b> "
                     + "word " * 90 + tail, "$25 an hour", source="linkedin_email"),
                _row(2, "https://fixture.example/job/2", "Help Desk Technician",
                     "Example", "Pittsburgh, PA", "", "", source=private),
            ]
            _write_db(db, rows, sources=(
                ("linkedin_email", 0, 0, "synthetic", stamp),
                ("manual", 1, 0, "", "bad timestamp"),
                (private, 1, 0, "", stamp),
            ))
            loaded = load_live_database(db)
            row_two = next(row for row in loaded.rows if row.upstream_records["id"] == 2)
            self.assertEqual(row_two.posting.source, private)
            before = hashlib.sha256(db.read_bytes()).hexdigest()
            app = create_app(db_path=db, reload_interval=0,
                             state_dir=Path(folder) / "state",
                             tracker_path=FIXTURES / "job_tracker.csv")
            with TestClient(app) as client:
                jobs = client.get("/api/jobs").json()
                by_id = {row["jobfinder_id"]: row for tier in ("shortlist", "review", "excluded")
                         for row in jobs[tier]}
                first, second = by_id[1], by_id[2]
                upstream = first["contract"]["upstream_records"]
                self.assertEqual(upstream["source"], "linkedin_email")
                self.assertEqual(first["contract"]["source"]["provider"], "indeed")
                for key in ("trusted_host", "rewritten"):
                    self.assertIsInstance(first["contract"]["source"][key], bool)
                self.assertTrue(first["contract"]["reasons"])
                self.assertIsInstance(first["gaps"], list)
                self.assertTrue(upstream["details_excerpt"].startswith("Fixture word"))
                self.assertTrue(upstream["details_excerpt"].endswith("…"))
                self.assertLessEqual(len(upstream["details_excerpt"]), 301)
                self.assertNotIn("details", upstream)
                self.assertNotIn("details", first["contract"]["posting"])
                self.assertNotIn(tail, upstream["details_excerpt"])
                self.assertNotIn("alert(1)", upstream["details_excerpt"])
                fallback = second["contract"]["upstream_records"]
                self.assertEqual((fallback["source"], fallback["details_excerpt"]), ("jobfinder", ""))
                self.assertNotIn(private, str(jobs))
                provenance = jobs["snapshot_provenance"]
                self.assertEqual(provenance, {
                    "loaded_at": jobs["refreshed_at"],
                    "source_runs_status": "available",
                    "source_runs": {"linkedin_email": stamp},
                })
                self.assertEqual(client.get("/api/jobs/shortlist").json()["snapshot_provenance"], provenance)
                self.assertEqual(client.get("/api/jobs/" + first["id"]).json(),
                                 {**first, "snapshot_provenance": provenance})
                self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)
                db.unlink()
                self.assertEqual(client.post("/api/refresh").status_code, 503)
                self.assertEqual(client.get("/api/jobs").json()["snapshot_provenance"], provenance)

    def test_no_usable_run(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "fixture.db"
            _write_db(db, [], sources=())
            with TestClient(create_app(db_path=db, reload_interval=0,
                                        state_dir=Path(folder) / "state",
                                        tracker_path=FIXTURES / "job_tracker.csv")) as client:
                jobs = client.get("/api/jobs").json()
                self.assertEqual(jobs["snapshot_provenance"], {
                    "loaded_at": jobs["refreshed_at"],
                    "source_runs_status": "unavailable",
                    "source_runs": {},
                })

    def test_synthetic_endpoints_omit_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            with TestClient(create_app(reload_interval=0,
                                        state_dir=Path(folder) / "state",
                                        tracker_path=FIXTURES / "job_tracker.csv")) as client:
                jobs = client.get("/api/jobs").json()
                shortlist = client.get("/api/jobs/shortlist").json()
                self.assertNotIn("snapshot_provenance", jobs)
                self.assertNotIn("snapshot_provenance", shortlist)
                item = next(row for tier in ("shortlist", "review", "excluded")
                            for row in jobs[tier])
                detail = client.get("/api/jobs/" + item["id"]).json()
                self.assertNotIn("snapshot_provenance", detail)

