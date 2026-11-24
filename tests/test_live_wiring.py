"""Live wiring tests for Phase 2 V2 backend (S1-S5 + S7).

Covers evaluate_live_import / ingestion_summary, the live-mode FastAPI
surface, staleness, and the S5 failure protocol. The 845-row fixture DB
reproduces the live-run two-tier metrics exactly: 845 ingested
(14 eligible / 326 review / 505 rejected; 510 single / 217 exact /
118 uncertain) serving 688 (12 shortlist / 303 review / 373 excluded)
with sum(exact_duplicate_count) == 845.

Decision levers (default policy, $19/hr floor, Pittsburgh/Philadelphia
primary): "$25 an hour" -> eligible, "" (missing pay) -> review,
"$10 an hour" (below floor) -> rejected. All fixture locations are
primary cities and no row mentions driving, so pay alone decides.
"""
import hashlib
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from career_navigator.app import create_app, job_id
from career_navigator.live_adapter import (
    LiveAdapterError,
    evaluate_live_import,
    ingestion_summary,
    load_live_database,
)
from career_navigator.policy import Policy

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA = FIXTURES / "jobfinder_schema.sql"
PAY = {"eligible": "$25 an hour", "review": "", "rejected": "$10 an hour"}
PAY_TOP = {"eligible": 25.0, "review": None, "rejected": 10.0}


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row(job_id, url, title, company, location, details, pay_text, **overrides):
    values = {
        "id": job_id, "url": url, "source": "indeed_email", "title": title,
        "company": company, "location": location, "details": details,
        "family": "office_it", "city": "pittsburgh", "remote": 0,
        "pay_text": pay_text, "pay_top": 25.0, "salary_unknown": 0,
        "driving": "unknown", "verdict": "ok", "reasons": "fixture",
        "status": "new", "posted": "2026-11-18", "first_seen": "2026-11-18",
        "last_seen": "2026-11-19 08:00:00", "dupkey": "", "dup_of": None,
        "picked_on": None, "pick_city": None, "needs_details": 0,
    }
    values.update(overrides)
    return values


def _write_db(path, rows, last_updated="2026-11-19 09:00",
              sources=(("indeed_email", 1, 0, "", "2026-11-19 09:00:00"),)):
    path = Path(path)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    if last_updated is not None:
        conn.execute("INSERT INTO state VALUES (?, ?)", ("last_updated", last_updated))
    for name, ok, count, error, ran_at in sources:
        conn.execute(
            "INSERT INTO source_runs VALUES (?, ?, ?, ?, ?)",
            (name, ok, count, error, ran_at),
        )
    for values in rows:
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        conn.execute(f"INSERT INTO jobs ({columns}) VALUES ({marks})", tuple(values.values()))
    conn.commit()
    conn.close()


def _details(units):
    # 25 chars per unit; no driving / location / pay trigger words.
    return "Support Windows systems. " * units


def build_845_rows():
    """845 fixture rows reproducing the live-run two-tier metrics.

    Exact groups mirror the real data (EGM F-02): 1 eligible group of 3,
    16 review groups of 39 rows, 43 rejected groups of 175 rows. The
    representative of each group is its longest-details member (member 0).
    Uncertain pairs share (title, company) across two locations. Singles
    are unique everywhere. Decisions: eligible 14, review 326,
    rejected 505; duplicates: single 510, exact 217, uncertain 118.
    """
    rows = []
    counter = [0]

    def add(title, company, location, details_units, decision, verdict="ok"):
        counter[0] += 1
        i = counter[0]
        rows.append(_row(
            i, f"https://www.indeed.com/viewjob?jk=fx{i:04d}",
            title, company, location, _details(details_units),
            PAY[decision], pay_top=PAY_TOP[decision], verdict=verdict,
        ))
        return i

    def exact_group(gnum, rep_decision, sibling_decisions):
        title = f"IT Support Technician G{gnum:03d}"
        company = f"Company G{gnum:03d}"
        sizes = [10, 9, 8, 7, 6]
        add(title, company, "Pittsburgh, PA", sizes[0], rep_decision)
        for k, dec in enumerate(sibling_decisions):
            add(title, company, "Pittsburgh, PA", sizes[1 + k], dec)

    # 1 eligible group of 3: rep eligible + 2 eligible siblings.
    exact_group(1, "eligible", ["eligible", "eligible"])
    # 16 review groups, 39 rows: 7 groups of 3, 9 groups of 2; all review.
    gnum = 2
    for _ in range(7):
        exact_group(gnum, "review", ["review", "review"])
        gnum += 1
    for _ in range(9):
        exact_group(gnum, "review", ["review"])
        gnum += 1
    # 43 rejected groups, 175 rows: 3 groups of 5, 40 groups of 4.
    for _ in range(3):
        exact_group(gnum, "rejected", ["rejected"] * 4)
        gnum += 1
    for _ in range(40):
        exact_group(gnum, "rejected", ["rejected"] * 3)
        gnum += 1
    assert gnum == 61, gnum  # 60 exact groups

    # 59 uncertain pairs: same (title, company), two locations.
    pair_decisions = (
        [("eligible", "eligible")]
        + [("eligible", "review")]
        + [("review", "review")] * 17
        + [("review", "rejected")] * 15
        + [("rejected", "rejected")] * 25
    )
    assert len(pair_decisions) == 59
    for u, (d1, d2) in enumerate(pair_decisions, start=1):
        title = f"Help Desk Analyst U{u:03d}"
        company = f"Company U{u:03d}"
        add(title, company, "Pittsburgh, PA", 10, d1)
        add(title, company, "Philadelphia, PA", 9, d2)

    # 510 singles: 8 eligible, 237 review, 265 rejected.
    single_decisions = ["eligible"] * 8 + ["review"] * 237 + ["rejected"] * 265
    for s, dec in enumerate(single_decisions, start=1):
        add(f"IT Support Technician S{s:04d}", f"Company S{s:04d}",
            "Pittsburgh, PA", 10, dec)

    assert len(rows) == 845, len(rows)
    return rows


def build_845_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO state VALUES (?, ?)", ("last_updated", "2026-11-19 09:00"))
    conn.execute(
        "INSERT INTO source_runs VALUES (?, ?, ?, ?, ?)",
        ("indeed_email", 1, 845, "", "2026-11-19 09:00:00"),
    )
    conn.execute(
        "INSERT INTO source_runs VALUES (?, ?, ?, ?, ?)",
        ("linkedin_email", 1, 0, "", "2026-11-19 09:00:00"),
    )
    for values in build_845_rows():
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        conn.execute(f"INSERT INTO jobs ({columns}) VALUES ({marks})", tuple(values.values()))
    conn.commit()
    conn.close()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "jobfinder.db"

    def tearDown(self):
        self.temp.cleanup()

    def _two_singles(self):
        rows = [
            _row(1, "https://www.indeed.com/viewjob?jk=a1", "IT Support Technician",
                 "Acme", "Pittsburgh, PA", _details(10), "$25 an hour"),
            _row(2, "https://www.indeed.com/viewjob?jk=a2", "Help Desk Analyst",
                 "Beta", "Pittsburgh, PA", _details(10), "$25 an hour"),
        ]
        _write_db(self.db, rows)
        return rows

    def test_bridge_preserves_all_upstream_fields_and_assessment(self):
        self._two_singles()
        live = load_live_database(self.db)
        evaluations = evaluate_live_import(live)
        self.assertEqual(len(evaluations), 2)
        ev = next(e for e in evaluations if e.upstream_records["id"] == 1)
        expected_keys = {
            "id", "posted", "first_seen", "last_seen", "dupkey", "dup_of",
            "picked_on", "pick_city", "reasons", "driving", "family", "city",
            "salary_unknown", "pay_top", "remote", "needs_details",
            "url", "canonical_url", "provider", "provider_id", "pay_facts",
        }
        self.assertEqual(len(expected_keys), 21)
        self.assertTrue(expected_keys <= set(ev.upstream_records))
        self.assertIn("cn_assessment", ev.upstream_records)
        self.assertEqual(ev.upstream_records["verdict"], "ok")
        self.assertEqual(ev.upstream_records["status"], "new")
        self.assertEqual(ev.upstream_records["cn_assessment"]["status"], "eligible")
        self.assertEqual(ev.decision.status, "eligible")
        self.assertEqual(ev.duplicate_confidence, "single")
        self.assertEqual(ev.upstream_records["exact_duplicate_count"], 1)
        self.assertEqual(ev.posting.title, "IT Support Technician")

    def test_bridge_reuses_precomputed_decision_without_reevaluating(self):
        # Load under the default policy (Acme row is eligible), then bridge
        # under a policy that would exclude Acme: the decision must stay
        # the load-time one — evaluate() is not re-run in the bridge.
        self._two_singles()
        live = load_live_database(self.db)
        custom = Policy(excluded_employers=("Acme",))
        evaluations = evaluate_live_import(live, policy=custom)
        ev = next(e for e in evaluations if e.upstream_records["id"] == 1)
        self.assertEqual(ev.decision.status, "eligible")

    def test_exact_group_folding_picks_longest_details(self):
        rows = [
            _row(1, "https://www.indeed.com/viewjob?jk=e1", "IT Support Technician",
                 "Acme", "Pittsburgh, PA", _details(5), "$25 an hour"),
            _row(2, "https://www.indeed.com/viewjob?jk=e2", "IT Support Technician",
                 "Acme", "Pittsburgh, PA", _details(10), "$25 an hour"),
            _row(3, "https://www.indeed.com/viewjob?jk=e3", "IT Support Technician",
                 "Acme", "Pittsburgh, PA", _details(7), "$25 an hour"),
        ]
        _write_db(self.db, rows)
        live = load_live_database(self.db)
        by_id = {r.upstream_records["id"]: r for r in live.rows}
        self.assertEqual(len(live.rows), 3)
        self.assertTrue(all(r.duplicate_confidence == "exact" for r in live.rows))
        self.assertTrue(by_id[2].is_representative)
        self.assertEqual(by_id[2].exact_duplicate_count, 3)
        self.assertFalse(by_id[1].is_representative)
        self.assertFalse(by_id[3].is_representative)
        self.assertEqual(by_id[1].exact_duplicate_count, 0)
        self.assertEqual(sum(r.exact_duplicate_count for r in live.rows), 3)
        evaluations = evaluate_live_import(live)
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(evaluations[0].upstream_records["id"], 2)
        self.assertEqual(evaluations[0].upstream_records["exact_duplicate_count"], 3)

    def test_tiebreak_prefers_known_pay_then_lowest_id(self):
        # Same details length: known pay beats missing pay.
        rows = [
            _row(1, "https://www.indeed.com/viewjob?jk=t1", "IT Support Technician",
                 "Acme", "Pittsburgh, PA", _details(5), ""),
            _row(2, "https://www.indeed.com/viewjob?jk=t2", "IT Support Technician",
                 "Acme", "Pittsburgh, PA", _details(5), "$25 an hour"),
        ]
        _write_db(self.db, rows)
        evaluations = evaluate_live_import(load_live_database(self.db))
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(evaluations[0].upstream_records["id"], 2)
        # Same details length, both known pay: lowest id wins.
        rows = [
            _row(1, "https://www.indeed.com/viewjob?jk=t1", "IT Support Technician",
                 "Acme", "Pittsburgh, PA", _details(5), "$25 an hour"),
            _row(2, "https://www.indeed.com/viewjob?jk=t2", "IT Support Technician",
                 "Acme", "Pittsburgh, PA", _details(5), "$10 an hour"),
        ]
        _write_db(self.db, rows)
        evaluations = evaluate_live_import(load_live_database(self.db))
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(evaluations[0].upstream_records["id"], 1)
        self.assertEqual(evaluations[0].decision.status, "eligible")

    def test_uncertain_rows_preserved_one_to_one(self):
        rows = [
            _row(1, "https://www.indeed.com/viewjob?jk=u1", "Help Desk Analyst",
                 "Acme", "Pittsburgh, PA", _details(10), "$25 an hour"),
            _row(2, "https://www.indeed.com/viewjob?jk=u2", "Help Desk Analyst",
                 "Acme", "Philadelphia, PA", _details(9), "$25 an hour"),
        ]
        _write_db(self.db, rows)
        live = load_live_database(self.db)
        self.assertTrue(all(r.duplicate_confidence == "uncertain" for r in live.rows))
        evaluations = evaluate_live_import(live)
        self.assertEqual(len(evaluations), 2)
        self.assertTrue(all(
            e.upstream_records["exact_duplicate_count"] == 1 for e in evaluations
        ))

    def test_ingestion_summary_counts_and_matrix(self):
        rows = [
            _row(1, "https://www.indeed.com/viewjob?jk=m1", "IT Support Technician",
                 "Acme", "Pittsburgh, PA", _details(10), "$25 an hour", verdict="ok"),
            _row(2, "https://www.indeed.com/viewjob?jk=m2", "Help Desk Analyst",
                 "Beta", "Pittsburgh, PA", _details(10), "", verdict="ok"),
            _row(3, "https://www.indeed.com/viewjob?jk=m3", "IT Support Technician",
                 "Gamma", "Pittsburgh, PA", _details(10), "$10 an hour", verdict="rejected"),
        ]
        _write_db(self.db, rows)
        summary = ingestion_summary(load_live_database(self.db))
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["by_decision"], {"eligible": 1, "review": 1, "rejected": 1})
        self.assertEqual(summary["by_duplicate_confidence"], {"single": 3, "exact": 0, "uncertain": 0})
        self.assertEqual(summary["matrix"], {
            "ok": {"eligible": 1, "review": 1, "rejected": 0},
            "rejected": {"eligible": 0, "review": 0, "rejected": 1},
        })


class LiveAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "jobfinder.db"
        rows = [
            _row(1, "https://www.indeed.com/viewjob?jk=a1", "IT Support Technician",
                 "Acme", "Pittsburgh, PA", _details(10), "$25 an hour"),
            _row(2, "https://www.indeed.com/viewjob?jk=a2", "Help Desk Analyst",
                 "Beta", "Pittsburgh, PA", _details(10), "$25 an hour"),
        ]
        _write_db(self.db, rows)
        self.rows = rows

    def tearDown(self):
        self.temp.cleanup()

    def test_health_live_reports_both_postings_keys(self):
        app = create_app(db_path=self.db)
        with TestClient(app) as client:
            body = client.get("/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["source"], "live")
        self.assertEqual(body["postings"], 2)
        self.assertEqual(body["jobs_count"], 2)
        self.assertEqual(body["max_last_seen"], "2026-11-19 08:00:00")
        self.assertEqual(body["last_updated"], "2026-11-19 09:00")
        self.assertEqual(len(body["sources"]), 1)
        self.assertEqual(body["sources"][0]["name"], "indeed_email")
        self.assertTrue(body["sources"][0]["ok"])

    def test_jobs_top_level_fields_and_stable_ids(self):
        app = create_app(db_path=self.db)
        with TestClient(app) as client:
            body = client.get("/api/jobs").json()
        self.assertEqual(body["total"], 2)
        self.assertFalse(body["stale"])
        for group in (body["shortlist"], body["review"], body["excluded"]):
            for job in group:
                self.assertIsInstance(job["jobfinder_id"], int)
                self.assertEqual(job["cn_status"], "eligible")
                self.assertEqual(job["duplicate_confidence"], "single")
                self.assertEqual(job["duplicate_reason"], "")
                self.assertEqual(job["exact_duplicate_count"], 1)
                self.assertEqual(job["upstream_verdict"], "ok")
                self.assertEqual(job["id"], job_id(job["contract"]["source"]))
        with TestClient(app) as client:
            first = body["shortlist"][0]
            found = client.get(f"/api/jobs/{first['id']}")
            self.assertEqual(found.status_code, 200)
            self.assertEqual(found.json()["jobfinder_id"], first["jobfinder_id"])
            self.assertEqual(client.get("/api/jobs/000000000000").status_code, 404)

    def test_stats_live_blocks(self):
        app = create_app(db_path=self.db)
        with TestClient(app) as client:
            body = client.get("/api/stats").json()
        self.assertEqual(body["ingestion"]["total"], 2)
        self.assertEqual(body["ingestion"]["by_decision"], {"eligible": 2, "review": 0, "rejected": 0})
        self.assertEqual(body["matrix"], {"ok": {"eligible": 2, "review": 0, "rejected": 0}})
        self.assertEqual(body["served"]["total"], 2)
        self.assertEqual(body["served"]["exact_duplicate_count_sum"], 2)
        staleness = body["staleness"]
        self.assertEqual(staleness["last_updated"], "2026-11-19 09:00")
        self.assertEqual(staleness["max_last_seen"], "2026-11-19 08:00:00")
        self.assertEqual(staleness["stale_threshold_hours"], 26.0)
        self.assertEqual(staleness["basis"], "last_updated")
        self.assertGreater(staleness["hours_since_refresh"], 26.0)
        self.assertTrue(staleness["is_stale"])

    def test_staleness_fresh_when_last_updated_is_now(self):
        now_eastern = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M")
        _write_db(self.db, self.rows, last_updated=now_eastern)
        app = create_app(db_path=self.db)
        with TestClient(app) as client:
            staleness = client.get("/api/stats").json()["staleness"]
        self.assertFalse(staleness["is_stale"])
        self.assertLess(staleness["hours_since_refresh"], 1.0)
        self.assertEqual(staleness["basis"], "last_updated")

    def test_staleness_falls_back_to_max_last_seen(self):
        _write_db(self.db, self.rows, last_updated=None)
        app = create_app(db_path=self.db)
        with TestClient(app) as client:
            staleness = client.get("/api/stats").json()["staleness"]
        self.assertIsNone(staleness["last_updated"])
        self.assertEqual(staleness["basis"], "max_last_seen")
        self.assertGreater(staleness["hours_since_refresh"], 26.0)
        self.assertTrue(staleness["is_stale"])

    def test_staleness_unknown_when_no_timestamps(self):
        _write_db(self.db, [], last_updated=None)
        app = create_app(db_path=self.db)
        with TestClient(app) as client:
            body = client.get("/api/stats").json()
        staleness = body["staleness"]
        self.assertIsNone(staleness["hours_since_refresh"])
        self.assertIsNone(staleness["basis"])
        self.assertTrue(staleness["is_stale"])
        self.assertEqual(body["ingestion"]["total"], 0)

    def test_startup_fails_closed_on_corrupt_database(self):
        conn = sqlite3.connect(self.db)
        conn.execute("ALTER TABLE jobs ADD COLUMN unexpected TEXT")
        conn.commit()
        conn.close()
        app = create_app(db_path=self.db)
        with self.assertRaises(LiveAdapterError):
            with TestClient(app):
                pass

    def test_refresh_failure_keeps_snapshot_and_returns_503(self):
        app = create_app(db_path=self.db)
        with TestClient(app) as client:
            before = client.get("/api/jobs").json()
            self.assertEqual(before["total"], 2)
            self.assertFalse(before["stale"])
            conn = sqlite3.connect(self.db)
            conn.execute("ALTER TABLE jobs ADD COLUMN unexpected TEXT")
            conn.commit()
            conn.close()
            resp = client.post("/api/refresh")
            self.assertEqual(resp.status_code, 503)
            body = resp.json()
            self.assertEqual(body["status"], "degraded")
            self.assertIn("unexpected", body["error"])
            self.assertEqual(body["snapshot_refreshed_at"], before["refreshed_at"])
            health = client.get("/health").json()
            self.assertEqual(health["status"], "degraded")
            self.assertTrue(health["degraded"])
            self.assertIn("unexpected", health["last_error"])
            jobs = client.get("/api/jobs").json()
            self.assertTrue(jobs["stale"])
            self.assertEqual(jobs["total"], 2)
            self.assertEqual(
                [j["id"] for j in jobs["shortlist"]],
                [j["id"] for j in before["shortlist"]],
            )
            # Restore the good database: a clean refresh clears degraded.
            self.db.unlink()
            _write_db(self.db, self.rows)
            resp = client.post("/api/refresh")
            self.assertEqual(resp.status_code, 200)
            health = client.get("/health").json()
            self.assertEqual(health["status"], "ok")
            self.assertNotIn("degraded", health)
            self.assertFalse(client.get("/api/jobs").json()["stale"])

    def test_env_var_selects_live_mode(self):
        with mock.patch.dict(os.environ, {"JOBFINDER_DB_PATH": str(self.db)}):
            app = create_app()
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json()["source"], "live")
        with mock.patch.dict(os.environ, {"JOBFINDER_DB_PATH": ""}):
            app = create_app()
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json()["source"], "synthetic")

    def test_synthetic_mode_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JOBFINDER_DB_PATH", None)
            app = create_app()
        with TestClient(app) as client:
            health = client.get("/health").json()
            self.assertEqual(health["source"], "synthetic")
            self.assertEqual(health["postings"], 19)
            jobs = client.get("/api/jobs").json()
            self.assertEqual(jobs["total"], 19)
            self.assertFalse(jobs["stale"])
            stats = client.get("/api/stats").json()
            self.assertNotIn("ingestion", stats)
            self.assertNotIn("staleness", stats)
            for group in (jobs["shortlist"], jobs["review"], jobs["excluded"]):
                for job in group:
                    self.assertIsNone(job["jobfinder_id"])
                    self.assertEqual(job["exact_duplicate_count"], 1)

    def test_folded_sibling_detail_is_404(self):
        rows = [
            _row(1, "https://www.indeed.com/viewjob?jk=e1", "IT Support Technician",
                 "Acme", "Pittsburgh, PA", _details(5), "$25 an hour"),
            _row(2, "https://www.indeed.com/viewjob?jk=e2", "IT Support Technician",
                 "Acme", "Pittsburgh, PA", _details(10), "$25 an hour"),
        ]
        _write_db(self.db, rows)
        app = create_app(db_path=self.db)
        with TestClient(app) as client:
            body = client.get("/api/jobs").json()
            self.assertEqual(body["total"], 1)
            rep = body["shortlist"][0]
            self.assertEqual(rep["jobfinder_id"], 2)
            self.assertEqual(rep["exact_duplicate_count"], 2)
            self.assertEqual(client.get(f"/api/jobs/{rep['id']}").status_code, 200)
            sibling_id = job_id({"original_url": "https://www.indeed.com/viewjob?jk=e1",
                                 "url": "https://www.indeed.com/viewjob?jk=e1"})
            self.assertEqual(client.get(f"/api/jobs/{sibling_id}").status_code, 404)


class Fixture845Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "jobfinder.db"
        build_845_db(self.db)
        self.db_hash = _digest(self.db)

    def tearDown(self):
        self.assertEqual(_digest(self.db), self.db_hash, "fixture DB was modified")
        self.temp.cleanup()

    def test_two_tier_metrics_reproduced(self):
        app = create_app(db_path=self.db)
        with TestClient(app) as client:
            health = client.get("/health").json()
            self.assertEqual(health["source"], "live")
            self.assertEqual(health["postings"], 845)
            self.assertEqual(health["jobs_count"], 845)
            jobs = client.get("/api/jobs").json()
            self.assertEqual(jobs["total"], 688)
            self.assertEqual(len(jobs["shortlist"]), 12)
            self.assertEqual(len(jobs["review"]), 303)
            self.assertEqual(len(jobs["excluded"]), 373)
            self.assertFalse(jobs["stale"])
            count_sum = sum(
                job["exact_duplicate_count"]
                for group in (jobs["shortlist"], jobs["review"], jobs["excluded"])
                for job in group
            )
            self.assertEqual(count_sum, 845)
            stats = client.get("/api/stats").json()
            self.assertEqual(stats["ingestion"]["total"], 845)
            self.assertEqual(stats["ingestion"]["by_decision"],
                             {"eligible": 14, "review": 326, "rejected": 505})
            self.assertEqual(stats["ingestion"]["by_duplicate_confidence"],
                             {"single": 510, "exact": 217, "uncertain": 118})
            self.assertEqual(stats["matrix"],
                             {"ok": {"eligible": 14, "review": 326, "rejected": 505}})
            self.assertEqual(stats["served"]["total"], 688)
            self.assertEqual(stats["served"]["shortlist"], 12)
            self.assertEqual(stats["served"]["review"], 303)
            self.assertEqual(stats["served"]["excluded"], 373)
            self.assertEqual(stats["served"]["exact_duplicate_count_sum"], 845)
            self.assertTrue(stats["staleness"]["is_stale"])
            # Lists are homogeneous by CN decision status.
            self.assertTrue(all(j["cn_status"] == "eligible" for j in jobs["shortlist"]))
            self.assertTrue(all(j["cn_status"] == "review" for j in jobs["review"]))
            self.assertTrue(all(j["cn_status"] == "rejected" for j in jobs["excluded"]))
            # Every served row carries the new top-level fields.
            for group in (jobs["shortlist"], jobs["review"], jobs["excluded"]):
                for job in group:
                    self.assertIsInstance(job["jobfinder_id"], int)
                    self.assertIn(job["duplicate_confidence"], ("single", "exact", "uncertain"))
                    self.assertIsInstance(job["exact_duplicate_count"], int)
                    self.assertEqual(job["upstream_verdict"], "ok")
            # Exactly 60 exact representatives, covering all 217 exact rows.
            reps = [job for group in (jobs["shortlist"], jobs["review"], jobs["excluded"])
                    for job in group if job["exact_duplicate_count"] > 1]
            self.assertEqual(len(reps), 60)
            self.assertEqual(sum(job["exact_duplicate_count"] for job in reps), 217)
            # The eligible exact group (3 rows) collapses to jobfinder id 1.
            # (8 groups have size 3 in total: 1 eligible + 7 review.)
            elig_reps = [job for job in reps
                         if job["cn_status"] == "eligible"
                         and job["exact_duplicate_count"] == 3]
            self.assertEqual(len(elig_reps), 1)
            self.assertEqual(elig_reps[0]["jobfinder_id"], 1)
            # All 118 uncertain rows are served 1:1 with count 1.
            uncertain = [job for group in (jobs["shortlist"], jobs["review"], jobs["excluded"])
                         for job in group if job["duplicate_confidence"] == "uncertain"]
            self.assertEqual(len(uncertain), 118)
            self.assertTrue(all(job["exact_duplicate_count"] == 1 for job in uncertain))
            # Shortlist ranks are 1-based and contiguous.
            self.assertEqual(
                [job["shortlist_rank"] for job in jobs["shortlist"]],
                list(range(1, 13)),
            )


if __name__ == "__main__":
    unittest.main()
