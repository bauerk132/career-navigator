"""Phase 3a WS2 backend tests: freshness classification, read-only DB probe,
database watcher, the freshness block on /health, and /api/gate.

Every time-dependent classify test passes a fixed ``now``. Every app is built
with reload_interval=0 (no threads) and an injected task_check, so no test
calls the real schtasks, except the single thread start/stop test. All
databases are temp files; jobfinder.db is never opened.
"""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from career_navigator import freshness
from career_navigator.app import _parse_eastern_timestamp, _watch_once, create_app
from career_navigator.freshness import classify, probe_db
from test_live_wiring import _details, _row, _write_db

ET = ZoneInfo("America/New_York")
GATE_KEYS = {"available", "file", "date", "verdict", "finished_at",
             "hard_failures", "soft_failures", "checks", "error"}


def at(hour, minute=0, day=27):
    """Aware ET datetime on 2026-11-<day>."""
    return datetime(2026, 11, day, hour, minute, tzinfo=ET)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _job_rows():
    return [
        _row(1, "https://www.indeed.com/viewjob?jk=a1", "IT Support Technician",
             "Acme", "Pittsburgh, PA", _details(10), "$25 an hour"),
        _row(2, "https://www.indeed.com/viewjob?jk=a2", "Help Desk Analyst",
             "Beta", "Pittsburgh, PA", _details(10), "$25 an hour"),
    ]


class ClassifyTests(unittest.TestCase):
    def test_fresh_when_ran_today(self):
        result = classify("2026-11-27 09:05", now=at(11, 0))
        self.assertEqual(result["state"], "fresh")
        self.assertEqual(result["level"], "ok")
        self.assertEqual(result["run_date"], "2026-11-27")
        self.assertEqual(result["reasons"], ["JobFinder ran today at 09:05 ET"])

    def test_fresh_before_nine_when_yesterday_run_is_current(self):
        result = classify("2026-11-26 09:00", now=at(8, 0))
        self.assertEqual(result["state"], "fresh")
        self.assertEqual(result["level"], "ok")

    def test_stale_before_nine_when_run_older_than_yesterday(self):
        result = classify("2026-11-25 09:00", now=at(8, 0))
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["level"], "red")

    def test_late_between_nine_and_grace_when_yesterday_run(self):
        result = classify("2026-11-26 09:00", now=at(9, 30))
        self.assertEqual(result["state"], "late")
        self.assertEqual(result["level"], "amber")

    def test_stale_after_grace_window(self):
        result = classify("2026-11-26 09:00", now=at(10, 31))
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["level"], "red")

    def test_grace_boundary_is_stale_at_1030(self):
        self.assertEqual(classify("2026-11-26 09:00", now=at(10, 30))["state"], "stale")
        self.assertEqual(classify("2026-11-26 09:00", now=at(10, 29))["state"], "late")

    def test_missing_last_updated_is_stale(self):
        result = classify(None, now=at(11, 0))
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["level"], "red")
        self.assertIn("last_updated missing or unparseable", result["reasons"])
        self.assertIsNone(result["last_updated"])
        self.assertIsNone(result["run_date"])

    def test_unparseable_last_updated_is_stale(self):
        result = classify("not a timestamp", now=at(11, 0))
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["last_updated"], "not a timestamp")

    def test_utc_now_is_converted_to_eastern_for_the_date(self):
        # 16:00 UTC on Nov 27 is 11:00 ET on Nov 27.
        now = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)
        result = classify("2026-11-27 09:05", now=now)
        self.assertEqual(result["state"], "fresh")
        self.assertEqual(result["expected_run"], at(9, 0).isoformat())
        self.assertEqual(result["grace_until"], at(10, 30).isoformat())
        self.assertEqual(result["checked_at"], now.isoformat())

    def test_degraded_overlay_wins_over_fresh(self):
        result = classify("2026-11-27 09:05", now=at(11, 0), degraded=True)
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["level"], "red")
        self.assertIn("live backend is serving the last good snapshot after a failed reload",
                      result["reasons"])
        self.assertIn("JobFinder ran today at 09:05 ET", result["reasons"])

    def test_behind_overlay_when_db_is_newer(self):
        result = classify("2026-11-27 09:00", now=at(11, 0),
                          db_last_updated="2026-11-27 09:40")
        self.assertEqual(result["state"], "behind")
        self.assertEqual(result["level"], "amber")
        self.assertEqual(result["db_last_updated"], "2026-11-27 09:40")

    def test_behind_not_raised_when_db_is_same_or_older(self):
        same = classify("2026-11-27 09:00", now=at(11, 0), db_last_updated="2026-11-27 09:00")
        older = classify("2026-11-27 09:00", now=at(11, 0), db_last_updated="2026-11-26 09:00")
        self.assertEqual(same["state"], "fresh")
        self.assertEqual(older["state"], "fresh")

    def test_partial_overlay_when_a_source_failed(self):
        sources = [{"name": "indeed_email", "ok": False, "count": 0, "error": "boom"},
                   {"name": "linkedin_email", "ok": True, "count": 4, "error": ""}]
        result = classify("2026-11-27 09:00", now=at(11, 0), sources=sources)
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["level"], "amber")
        self.assertIn("source indeed_email failed in the last run", result["reasons"])

    def test_all_sources_ok_is_not_partial(self):
        sources = [{"name": "indeed_email", "ok": True}]
        result = classify("2026-11-27 09:00", now=at(11, 0), sources=sources)
        self.assertEqual(result["state"], "fresh")

    def test_severity_precedence(self):
        failed = [{"name": "indeed_email", "ok": False}]
        cases = [
            # (base kwargs, expected state)
            (dict(last_updated="2026-11-27 09:00", degraded=True, db_last_updated="2026-11-27 10:00",
                  sources=failed), "degraded"),
            (dict(last_updated="2026-11-25 09:00", db_last_updated="2026-11-27 10:00",
                  sources=failed), "stale"),
            (dict(last_updated="2026-11-27 09:00", db_last_updated="2026-11-27 10:00",
                  sources=failed), "behind"),
            (dict(last_updated="2026-11-27 09:00", sources=failed), "partial"),
            (dict(last_updated="2026-11-26 09:00", sources=failed), "late"),
            (dict(last_updated="2026-11-26 09:00", db_last_updated="2026-11-27 10:00"), "behind"),
            (dict(last_updated="2026-11-26 09:00"), "late"),
        ]
        for kwargs, expected in cases:
            with self.subTest(expected=expected):
                result = classify(now=at(9, 30), **kwargs)
                self.assertEqual(result["state"], expected)

    def test_late_beats_partial_when_both_apply_and_both_reasons_kept(self):
        failed = [{"name": "indeed_email", "ok": False}]
        result = classify("2026-11-26 09:00", now=at(9, 30), sources=failed)
        self.assertEqual(result["state"], "late")
        self.assertEqual(result["level"], "amber")
        self.assertEqual(len(result["reasons"]), 2)

    def test_result_shape(self):
        result = classify("2026-11-27 09:05", now=at(11, 0))
        self.assertEqual(set(result), {
            "state", "level", "reasons", "last_updated", "run_date",
            "expected_run", "grace_until", "checked_at", "db_last_updated",
        })


class ParseTests(unittest.TestCase):
    def test_app_alias_is_the_freshness_parser(self):
        self.assertIs(_parse_eastern_timestamp, freshness.parse_eastern)
        self.assertEqual(_parse_eastern_timestamp("2026-11-19 09:00").tzinfo, ET)
        self.assertIsNone(_parse_eastern_timestamp("nope"))


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "jobfinder.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_probe_reads_state_and_leaves_bytes_identical(self):
        _write_db(self.db, _job_rows())
        before = _digest(self.db)
        result = probe_db(self.db)
        self.assertEqual(_digest(self.db), before)
        self.assertIsNone(result["error"])
        self.assertEqual(result["last_updated"], "2026-11-19 09:00")
        self.assertEqual(result["size"], self.db.stat().st_size)
        self.assertAlmostEqual(result["mtime"], self.db.stat().st_mtime)

    def test_probe_missing_file_sets_error_without_raising(self):
        missing = Path(self.temp.name) / "absent.db"
        result = probe_db(missing)
        self.assertIsNotNone(result["error"])
        self.assertIsNone(result["mtime"])
        self.assertIsNone(result["size"])
        self.assertIsNone(result["last_updated"])
        self.assertFalse(missing.exists())

    def test_probe_non_database_file_sets_error_without_raising(self):
        self.db.write_bytes(b"this is not sqlite" * 50)
        before = _digest(self.db)
        result = probe_db(self.db)
        self.assertIsNotNone(result["error"])
        self.assertEqual(_digest(self.db), before)

    def test_probe_cannot_write(self):
        _write_db(self.db, _job_rows())
        conn = sqlite3.connect(self.db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            with self.assertRaises(sqlite3.Error):
                conn.execute("UPDATE state SET value = 'x'")
        finally:
            conn.close()


class TaskRunningTests(unittest.TestCase):
    def test_none_when_schtasks_cannot_run(self):
        with mock.patch.object(freshness.subprocess, "run", side_effect=OSError("no schtasks")):
            self.assertIsNone(freshness.task_running())

    def test_parses_status_line(self):
        def fake(args, **kwargs):
            out = "Folder: \\\nHostName: X\nTaskName: \\JobFinder daily refresh\nStatus: Running\n"
            return mock.Mock(returncode=0, stdout=out)
        with mock.patch.object(freshness.subprocess, "run", side_effect=fake):
            self.assertIs(freshness.task_running(), True)

    def test_ready_is_false_and_nonzero_exit_is_none(self):
        ready = mock.Mock(returncode=0, stdout="Status: Ready\n")
        missing = mock.Mock(returncode=1, stdout="ERROR: The system cannot find the file")
        with mock.patch.object(freshness.subprocess, "run", return_value=ready):
            self.assertIs(freshness.task_running(), False)
        with mock.patch.object(freshness.subprocess, "run", return_value=missing):
            self.assertIsNone(freshness.task_running())


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "jobfinder.db"
        _write_db(self.db, _job_rows())

    def tearDown(self):
        self.temp.cleanup()

    def _app(self, task_check=lambda: False):
        return create_app(db_path=self.db, reload_interval=0, task_check=task_check)

    def test_unchanged(self):
        app = self._app()
        with TestClient(app):
            self.assertEqual(_watch_once(app), "unchanged")
            self.assertIsNone(app.state.auto_reload_error)

    def test_reloaded_after_new_last_updated(self):
        app = self._app()
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json()["last_updated"], "2026-11-19 09:00")
            _write_db(self.db, _job_rows(), last_updated="2026-11-19 10:00")
            self.assertEqual(_watch_once(app), "reloaded")
            body = client.get("/health").json()
            self.assertEqual(body["last_updated"], "2026-11-19 10:00")
            self.assertNotIn("degraded", body)
            self.assertIsNotNone(app.state.last_auto_reload)
            self.assertEqual(_watch_once(app), "unchanged")

    def test_task_running_defers_reload(self):
        app = self._app(task_check=lambda: True)
        with TestClient(app) as client:
            before = client.get("/api/stats").json()["staleness"]["last_updated"]
            _write_db(self.db, _job_rows(), last_updated="2026-11-19 10:00")
            self.assertEqual(_watch_once(app), "task-running")
            self.assertEqual(client.get("/health").json()["last_updated"], before)
            self.assertEqual(before, "2026-11-19 09:00")

    def test_task_check_not_called_when_unchanged(self):
        calls = []
        app = self._app(task_check=lambda: calls.append(1) or False)
        with TestClient(app):
            self.assertEqual(_watch_once(app), "unchanged")
        self.assertEqual(calls, [])

    def test_reload_failure_keeps_snapshot_and_marks_degraded(self):
        app = self._app()
        with TestClient(app) as client:
            before = client.get("/api/jobs").json()
            # Schema drift plus a new last_updated: the probe sees a change and
            # the reload then fails the schema check.
            conn = sqlite3.connect(self.db)
            conn.execute("ALTER TABLE jobs ADD COLUMN unexpected TEXT")
            conn.execute("UPDATE state SET value = '2026-11-19 11:00' WHERE key = 'last_updated'")
            conn.commit()
            conn.close()
            self.assertEqual(_watch_once(app), "reload-failed")
            self.assertIn("unexpected", app.state.auto_reload_error)
            health = client.get("/health").json()
            self.assertEqual(health["status"], "degraded")
            self.assertTrue(health["degraded"])
            self.assertEqual(health["last_updated"], "2026-11-19 09:00")
            jobs = client.get("/api/jobs").json()
            self.assertTrue(jobs["stale"])
            self.assertEqual(jobs["total"], before["total"])
            self.assertEqual(
                [j["id"] for j in jobs["shortlist"]],
                [j["id"] for j in before["shortlist"]],
            )
            self.assertEqual(health["freshness"]["state"], "degraded")

    def test_probe_error_is_not_degradation(self):
        app = self._app()
        with TestClient(app) as client:
            self.db.write_bytes(b"not sqlite at all" * 100)
            self.assertEqual(_watch_once(app), "probe-error")
            body = client.get("/health").json()
            self.assertEqual(body["status"], "ok")
            self.assertNotIn("degraded", body)

    def test_repeated_probe_failures_become_degraded_then_recover(self):
        app = self._app()
        with TestClient(app) as client:
            good = self.db.read_bytes()
            self.db.write_bytes(b"not sqlite at all" * 100)
            for _ in range(2):
                self.assertEqual(_watch_once(app), "probe-error")
            self.assertEqual(app.state.consecutive_probe_failures, 2)
            block = client.get("/health").json()["freshness"]
            self.assertNotEqual(block["state"], "degraded")
            self.assertIsNone(block["probe_error"])
            self.assertEqual(_watch_once(app), "probe-error")
            body = client.get("/health").json()
            self.assertEqual(body["status"], "ok")
            block = body["freshness"]
            self.assertEqual(block["state"], "degraded")
            self.assertEqual(block["level"], "red")
            self.assertTrue(block["probe_error"])
            self.assertTrue(any(r.startswith("cannot read jobfinder.db: ") for r in block["reasons"]))
            self.assertFalse(any("failed reload" in r for r in block["reasons"]))
            self.assertEqual(client.get("/api/stats").json()["freshness"]["state"], "degraded")
            # A successful probe resets the counter and clears the block.
            self.db.write_bytes(good)
            self.assertIn(_watch_once(app), ("unchanged", "reloaded"))
            self.assertEqual(app.state.consecutive_probe_failures, 0)
            block = client.get("/health").json()["freshness"]
            self.assertNotEqual(block["state"], "degraded")
            self.assertIsNone(block["probe_error"])

    def test_missing_db_after_start_becomes_degraded_after_three_ticks(self):
        app = self._app()
        with TestClient(app) as client:
            self.db.unlink()
            for _ in range(3):
                self.assertEqual(_watch_once(app), "probe-error")
            block = client.get("/health").json()["freshness"]
            self.assertEqual(block["state"], "degraded")
            self.assertIn("cannot read jobfinder.db", block["reasons"][-1])

    def test_synthetic_mode_watch_is_a_no_op(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JOBFINDER_DB_PATH", None)
            app = create_app(reload_interval=0, task_check=lambda: False)
        with TestClient(app):
            self.assertEqual(_watch_once(app), "synthetic")

    def test_thread_starts_and_stops_cleanly(self):
        app = create_app(db_path=self.db, reload_interval=0.05,
                         task_check=lambda: False)
        with TestClient(app) as client:
            thread = app.state.watch_thread
            self.assertIsNotNone(thread)
            self.assertTrue(thread.is_alive())
            self.assertEqual(client.get("/health").status_code, 200)
        self.assertFalse(thread.is_alive())


class FreshnessBlockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "jobfinder.db"
        _write_db(self.db, _job_rows())

    def tearDown(self):
        self.temp.cleanup()

    def test_health_and_stats_carry_freshness_block_in_live_mode(self):
        app = create_app(db_path=self.db, reload_interval=0, task_check=lambda: False)
        with TestClient(app) as client:
            health = client.get("/health").json()
            stats = client.get("/api/stats").json()
        for body in (health, stats):
            block = body["freshness"]
            self.assertIn(block["state"], {"fresh", "late", "stale", "behind", "partial", "degraded"})
            self.assertEqual(block["last_updated"], "2026-11-19 09:00")
            self.assertEqual(block["rows"], 2)
            self.assertEqual(block["watch_interval_seconds"], 0.0)
            self.assertIn("loaded_at", block)
        # Existing keys are untouched.
        self.assertEqual(health["last_updated"], "2026-11-19 09:00")
        self.assertIn("staleness", stats)

    def test_synthetic_mode_has_no_freshness_block(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JOBFINDER_DB_PATH", None)
            app = create_app(reload_interval=0, task_check=lambda: False)
        with TestClient(app) as client:
            self.assertNotIn("freshness", client.get("/health").json())
            self.assertNotIn("freshness", client.get("/api/stats").json())


class GateEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _gate_json(self, reports_dir):
        """GET /api/gate with CN_GATE_REPORTS_DIR set for the whole request
        (the endpoint reads the env var per request, not at create_app)."""
        env = {"JOBFINDER_DB_PATH": ""}
        if reports_dir is not None:
            env["CN_GATE_REPORTS_DIR"] = str(reports_dir)
        with mock.patch.dict(os.environ, env):
            if reports_dir is None:
                os.environ.pop("CN_GATE_REPORTS_DIR", None)
            app = create_app(reload_interval=0, task_check=lambda: False)
            with TestClient(app) as client:
                return client.get("/api/gate").json()

    def test_no_env_is_unavailable(self):
        self.assertEqual(self._gate_json(None), {"available": False})

    def test_missing_dir_is_unavailable(self):
        self.assertEqual(self._gate_json(self.root / "nope"), {"available": False})

    def test_newest_dated_report_summarized_with_allowed_keys_only(self):
        reports = self.root / "gate-reports"
        reports.mkdir()
        (reports / "live_gate_report.2026-11-25.json").write_text(
            json.dumps({"verdict": "FAIL", "finished_at": "2026-11-25T14:45:00+00:00",
                        "hard_failures": ["x"], "checks": []}), encoding="utf-8")
        (reports / "live_gate_report.2026-11-26.json").write_text(json.dumps({
            "gate": "secret body", "args": {"db": "C:\\x"}, "verdict": "PASS",
            "finished_at": "2026-11-26T14:45:00+00:00", "hard_failures": [],
            "checks": [
                {"name": "a", "hard": True, "passed": True, "status": "pass"},
                {"name": "b", "hard": False, "passed": False, "status": "warn"},
                {"name": "c", "hard": True, "passed": True, "status": "pass"},
            ],
        }), encoding="utf-8")
        body = self._gate_json(reports)
        self.assertEqual(body, {
            "available": True,
            "file": "live_gate_report.2026-11-26.json",
            "date": "2026-11-26",
            "verdict": "PASS",
            "finished_at": "2026-11-26T14:45:00+00:00",
            "hard_failures": 0,
            "soft_failures": 1,
            "checks": 3,
        })
        self.assertTrue(set(body) <= GATE_KEYS)
        self.assertNotIn("gate", body)
        self.assertNotIn("args", body)

    def test_hard_failures_derived_from_checks_when_list_absent(self):
        reports = self.root / "derived"
        reports.mkdir()
        (reports / "live_gate_report.2026-11-26.json").write_text(json.dumps({
            "verdict": "FAIL",
            "checks": [
                {"name": "a", "hard": True, "passed": False},
                {"name": "b", "hard": True, "passed": True},
                {"name": "c", "hard": False, "passed": False},
            ],
        }), encoding="utf-8")
        body = self._gate_json(reports)
        self.assertEqual(body["verdict"], "FAIL")
        self.assertEqual(body["hard_failures"], 1)
        self.assertEqual(body["soft_failures"], 1)
        self.assertIsNone(body.get("finished_at"))

    def test_malformed_report_is_unknown(self):
        reports = self.root / "malformed"
        reports.mkdir()
        (reports / "live_gate_report.2026-11-26.json").write_text("{not json", encoding="utf-8")
        body = self._gate_json(reports)
        self.assertTrue(body["available"])
        self.assertEqual(body["verdict"], "UNKNOWN")
        self.assertEqual(body["error"], "unreadable report")
        self.assertTrue(set(body) <= GATE_KEYS)

    def test_unrecognized_verdict_is_unknown(self):
        reports = self.root / "weird"
        reports.mkdir()
        (reports / "live_gate_report.2026-11-26.json").write_text(
            json.dumps({"verdict": "MAYBE", "checks": []}), encoding="utf-8")
        self.assertEqual(self._gate_json(reports)["verdict"], "UNKNOWN")

    def test_oversized_report_is_unknown(self):
        reports = self.root / "big"
        reports.mkdir()
        (reports / "live_gate_report.2026-11-26.json").write_text(
            json.dumps({"verdict": "PASS", "pad": "x" * 1_100_000}), encoding="utf-8")
        body = self._gate_json(reports)
        self.assertEqual(body["verdict"], "UNKNOWN")
        self.assertEqual(body["error"], "unreadable report")

    def test_deeply_nested_report_is_unknown(self):
        reports = self.root / "deep"
        reports.mkdir()
        (reports / "live_gate_report.2026-11-26.json").write_text("[" * 200000, encoding="utf-8")
        self.assertEqual(self._gate_json(reports)["verdict"], "UNKNOWN")

    def test_non_dated_files_ignored(self):
        reports = self.root / "ignored"
        reports.mkdir()
        (reports / "live_gate_report.json").write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
        self.assertEqual(self._gate_json(reports), {"available": False})


if __name__ == "__main__":
    unittest.main()
