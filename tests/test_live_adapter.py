"""Synthetic-only tests for the read-only JobFinder adapter scaffold."""
import csv
import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from career_navigator.live_adapter import (
    LiveAdapterError,
    MalformedRowError,
    SchemaDriftError,
    SchemaVersionError,
    load_live_database,
)
from career_navigator.policy import Policy

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA = FIXTURES / "jobfinder_schema.sql"
TRACKER = FIXTURES / "job_tracker.csv"


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LiveAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "jobfinder.db"
        self.csv = self.root / "job_tracker.csv"
        shutil.copyfile(TRACKER, self.csv)
        conn = sqlite3.connect(self.db)
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO state VALUES (?, ?)", ("last_updated", "2026-11-19 09:00"))
        conn.execute(
            "INSERT INTO source_runs VALUES (?, ?, ?, ?, ?)",
            ("indeed_email", 1, 2, "", "2026-11-19 09:00:00"),
        )
        conn.execute(
            "INSERT INTO source_runs VALUES (?, ?, ?, ?, ?)",
            ("linkedin_email", 1, 1, "", "2026-11-19 09:00:00"),
        )
        self._insert(conn, 1, "https://www.indeed.com/viewjob?jk=alpha&utm_source=synthetic",
                     "IT Support Technician", "Example Systems", "Pittsburgh, PA",
                     "<script>steal()</script>Windows support", "$18 per week",
                     family="advisory-family", city="advisory-city", remote=1,
                     driving="yes", verdict="rejected", status="saved", picked_on="2026-11-18")
        self._insert(conn, 2, "https://www.linkedin.com/jobs/view/123456789/",
                     "<style>hidden</style>Help Desk Analyst", "<script>bad()</script>Acme",
                     "Remote in Philadelphia, PA", "<style>ignore</style>Ticket support",
                     "$2,000 monthly", family="office_it", city="philadelphia", remote=0)
        conn.commit()
        conn.close()
        self._snapshot = {self.db: _digest(self.db), self.csv: _digest(self.csv)}

    def tearDown(self):
        for path, expected in self._snapshot.items():
            self.assertEqual(_digest(path), expected, f"read-only input changed: {path.name}")
        self.temp.cleanup()

    @staticmethod
    def _insert(conn, job_id, url, title, company, location, details, pay_text, **overrides):
        values = {
            "id": job_id, "url": url, "source": "indeed_email", "title": title,
            "company": company, "location": location, "details": details,
            "family": "office_it", "city": "pittsburgh", "remote": 0,
            "pay_text": pay_text, "pay_top": 99.0, "salary_unknown": 0,
            "driving": "unknown", "verdict": "ok", "reasons": "synthetic reason",
            "status": "new", "posted": "2026-11-18", "first_seen": "2026-11-18",
            "last_seen": "2026-11-19 08:00:00", "dupkey": "", "dup_of": None,
            "picked_on": None, "pick_city": None, "needs_details": 0,
        }
        values.update(overrides)
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        conn.execute(f"INSERT INTO jobs ({columns}) VALUES ({marks})", tuple(values.values()))

    def _run(self):
        before = (_digest(self.db), _digest(self.csv))
        result = load_live_database(self.db)
        self.assertEqual((_digest(self.db), _digest(self.csv)), before)
        return result

    def test_happy_path_maps_named_fields_and_preserves_advisory_data(self):
        result = self._run()
        self.assertEqual(len(result.rows), 2)
        first = next(row for row in result.rows if row.upstream_records["id"] == 1)
        self.assertEqual(first.posting.title, "IT Support Technician")
        self.assertEqual(first.posting.provider, "indeed")
        self.assertEqual(first.posting.provider_id, "alpha")
        self.assertEqual(first.posting.upstream_verdict, "rejected")
        self.assertEqual(first.posting.upstream_status, "saved")
        self.assertEqual(first.upstream_records["family"], "advisory-family")
        self.assertEqual(first.upstream_records["city"], "advisory-city")
        self.assertEqual(first.upstream_records["picked_on"], "2026-11-18")
        self.assertIn("canonical_url", first.upstream_records)
        driving = [reason for reason in first.upstream_records["cn_assessment"]["reasons"]
                   if reason[0] == "driving"]
        self.assertEqual(driving[0][1], "info")  # upstream says yes; CN re-derives unknown

    def test_added_column_fails_closed(self):
        conn = sqlite3.connect(self.db)
        conn.execute("ALTER TABLE jobs ADD COLUMN unexpected TEXT")
        conn.commit()
        conn.close()
        self._snapshot[self.db] = _digest(self.db)
        with self.assertRaisesRegex(SchemaDriftError, r"added=\['unexpected'\]"):
            self._run()

    def test_missing_column_fails_closed(self):
        conn = sqlite3.connect(self.db)
        conn.execute("ALTER TABLE jobs DROP COLUMN needs_details")
        conn.commit()
        conn.close()
        self._snapshot[self.db] = _digest(self.db)
        with self.assertRaisesRegex(SchemaDriftError, "needs_details"):
            self._run()

    def test_unexpected_user_version_fails_closed(self):
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
        conn.close()
        self._snapshot[self.db] = _digest(self.db)
        with self.assertRaises(SchemaVersionError):
            self._run()

    def test_week_and_month_pay_are_reparsed_by_cn(self):
        result = self._run()
        facts = {row.upstream_records["id"]: row.upstream_records["pay_facts"] for row in result.rows}
        self.assertEqual((facts[1]["unit"], facts[1]["low"]), ("week", 18.0))
        self.assertEqual((facts[2]["unit"], facts[2]["low"]), ("month", 2000.0))
        self.assertEqual(next(r for r in result.rows if r.upstream_records["id"] == 2).posting.pay_text,
                         "$2,000 monthly")

    def test_script_style_content_is_suppressed(self):
        rows = {r.upstream_records["id"]: r for r in self._run().rows}
        self.assertEqual(rows[2].posting.title, "Help Desk Analyst")
        self.assertEqual(rows[2].posting.company, "Acme")
        self.assertEqual(rows[2].posting.details, "Ticket support")
        self.assertEqual(rows[1].posting.details, "Windows support")

    def test_remote_city_text_is_kept_and_upstream_location_flags_stay_advisory(self):
        row = next(row for row in self._run().rows if row.upstream_records["id"] == 2)
        self.assertEqual(row.posting.location, "Remote in Philadelphia, PA")
        self.assertEqual(row.upstream_records["city"], "philadelphia")
        self.assertEqual(row.upstream_records["remote"], 0)
        location = [reason for reason in row.upstream_records["cn_assessment"]["reasons"]
                    if reason[0] == "location"]
        self.assertEqual(location[0][1], "pass")

    def test_idempotent_reimport_has_identical_rows_and_health(self):
        first = self._run()
        second = self._run()
        self.assertEqual(first, second)
        self.assertEqual([r.posting.url for r in first.rows], sorted(r.posting.url for r in first.rows))

    def test_health_present_for_empty_result_with_reason_and_staleness(self):
        conn = sqlite3.connect(self.db)
        conn.execute("DELETE FROM jobs")
        conn.execute("UPDATE source_runs SET ok=0, error='synthetic source failure'")
        conn.commit()
        conn.close()
        self._snapshot[self.db] = _digest(self.db)
        health = self._run().health
        self.assertTrue(health["empty"])
        self.assertIn("every recorded source run failed", health["empty_reason"])
        self.assertIsNone(health["max_last_seen"])
        self.assertEqual(health["last_updated"], "2026-11-19 09:00")
        self.assertIn("does not impose", health["staleness_basis"])
        self.assertEqual(len(health["sources"]), 2)

    def test_invalid_utf8_fails_closed(self):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE jobs SET title=CAST(X'FF' AS TEXT) WHERE id=1")
        conn.commit()
        conn.close()
        self._snapshot[self.db] = _digest(self.db)
        with self.assertRaisesRegex(MalformedRowError, "valid UTF-8"):
            self._run()

    def test_null_byte_fails_closed(self):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE jobs SET company=? WHERE id=1", ("Acme\x00Injected",))
        conn.commit()
        conn.close()
        self._snapshot[self.db] = _digest(self.db)
        with self.assertRaisesRegex(MalformedRowError, "null byte"):
            self._run()

    def test_unsafe_url_fails_closed(self):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE jobs SET url=? WHERE id=1", ("javascript:alert(1)",))
        conn.commit()
        conn.close()
        self._snapshot[self.db] = _digest(self.db)
        with self.assertRaisesRegex(MalformedRowError, "unsafe"):
            self._run()

    def test_duplicate_rows_remain_visible_and_are_labeled(self):
        conn = sqlite3.connect(self.db)
        self._insert(conn, 3, "https://jobs.example.test/another", "Help Desk Analyst",
                     "Acme", "Philadelphia, PA", "Support", "$22 an hour")
        conn.commit()
        conn.close()
        self._snapshot[self.db] = _digest(self.db)
        rows = self._run().rows
        self.assertEqual(len(rows), 3)
        duplicates = [row for row in rows if row.posting.title == "Help Desk Analyst"]
        self.assertTrue(all(row.duplicate_confidence == "uncertain" for row in duplicates))

    def test_exact_duplicates_are_labeled_without_dropping_either(self):
        conn = sqlite3.connect(self.db)
        self._insert(conn, 3, "https://jobs.example.test/exact", "Help Desk Analyst",
                     "Acme", "Remote in Philadelphia, PA", "Support", "$22 an hour")
        conn.commit()
        conn.close()
        self._snapshot[self.db] = _digest(self.db)
        matches = [r for r in self._run().rows if r.posting.title == "Help Desk Analyst"]
        self.assertEqual(len(matches), 2)
        self.assertTrue(all(row.duplicate_confidence == "exact" for row in matches))

    def test_ragged_tracker_csv_is_not_opened_or_changed(self):
        ragged = self.root / "ragged.csv"
        ragged.write_bytes(b"job_id,title\nSYN-002\n")
        self._snapshot[ragged] = _digest(ragged)
        before = _digest(ragged)
        result = load_live_database(self.db)
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(_digest(ragged), before)

    def test_bad_utf8_tracker_csv_is_not_opened_or_changed(self):
        malformed = self.root / "bad-utf8.csv"
        malformed.write_bytes(b"job_id,title\nSYN-003,\xff\n")
        self._snapshot[malformed] = _digest(malformed)
        before = _digest(malformed)
        self.assertEqual(len(load_live_database(self.db).rows), 2)
        self.assertEqual(_digest(malformed), before)

    def test_missing_database_is_not_created(self):
        missing = self.root / "absent.db"
        with self.assertRaises(LiveAdapterError):
            load_live_database(missing)
        self.assertFalse(missing.exists())

    def test_tracker_fixture_has_expected_synthetic_header(self):
        with TRACKER.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(len(rows[0]), 28)
        self.assertEqual(len(rows[1]), 28)
        self.assertIn("synthetic fixture only", rows[1])

    def test_source_runs_schema_drift_fails_closed(self):
        conn = sqlite3.connect(self.db)
        conn.execute("ALTER TABLE source_runs DROP COLUMN ran_at")
        conn.commit()
        conn.close()
        self._snapshot[self.db] = _digest(self.db)
        with self.assertRaisesRegex(SchemaDriftError, r"source_runs.*ran_at"):
            self._run()

    def test_state_schema_drift_fails_closed(self):
        conn = sqlite3.connect(self.db)
        conn.execute("ALTER TABLE state ADD COLUMN extra TEXT")
        conn.commit()
        conn.close()
        self._snapshot[self.db] = _digest(self.db)
        with self.assertRaisesRegex(SchemaDriftError, r"state.*extra"):
            self._run()

    def test_non_numeric_count_fails_closed(self):
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE source_runs SET count='not_an_int' WHERE name='indeed_email'")
            conn.commit()
        finally:
            conn.close()
        self._snapshot[self.db] = _digest(self.db)
        with self.assertRaisesRegex(MalformedRowError, "integer"):
            self._run()

    def test_null_verdict_and_status_normalize_to_none(self):
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE jobs SET verdict=NULL, status=NULL WHERE id=1")
            conn.commit()
        finally:
            conn.close()
        self._snapshot[self.db] = _digest(self.db)
        row = next(r for r in self._run().rows if r.upstream_records["id"] == 1)
        self.assertIsNone(row.posting.upstream_verdict)
        self.assertIsNone(row.posting.upstream_status)

    def test_custom_policy_applied(self):
        custom_policy = Policy(excluded_employers=("Acme",))
        result = load_live_database(self.db, policy=custom_policy)
        row = next(r for r in result.rows if r.upstream_records["id"] == 2)
        employer_reasons = [r for r in row.upstream_records["cn_assessment"]["reasons"] if r[0] == "employer"]
        self.assertEqual(employer_reasons[0][1], "exclude")

    def test_numeric_provenance_types_preserved_and_not_raw_bytes(self):
        result = self._run()
        row = next(r for r in result.rows if r.upstream_records["id"] == 1)
        self.assertIsInstance(row.upstream_records["id"], int)
        self.assertIsInstance(row.upstream_records["remote"], int)
        self.assertIsInstance(row.upstream_records["salary_unknown"], int)
        self.assertIsInstance(row.upstream_records["pay_top"], float)
        self.assertNotIsInstance(row.upstream_records["remote"], bytes)
        self.assertNotIsInstance(row.upstream_records["pay_top"], bytes)

    def test_url_whitespace_difference_does_not_corrupt_duplicate_info(self):
        conn = sqlite3.connect(self.db)
        # Insert a job with same stripped URL but trailing space and distinct content
        self._insert(conn, 10, "https://www.indeed.com/viewjob?jk=alpha&utm_source=synthetic ",
                     "Network Engineer", "Different Org", "Pittsburgh, PA", "Details", "$40 an hour")
        conn.commit()
        conn.close()
        self._snapshot[self.db] = _digest(self.db)
        result = self._run()
        row1 = next(r for r in result.rows if r.upstream_records["id"] == 1)
        row10 = next(r for r in result.rows if r.upstream_records["id"] == 10)
        self.assertEqual(row1.posting.title, "IT Support Technician")
        self.assertEqual(row10.posting.title, "Network Engineer")
        # Ensure each row has duplicate confidence string without key collision error
        self.assertIn(row1.duplicate_confidence, ("exact", "uncertain", "single"))
        self.assertIn(row10.duplicate_confidence, ("exact", "uncertain", "single"))

    def test_source_runs_ok_string_zero_evaluates_false(self):
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE source_runs SET ok='0' WHERE name='indeed_email'")
            conn.commit()
        finally:
            conn.close()
        self._snapshot[self.db] = _digest(self.db)
        health = self._run().health
        indeed = next(s for s in health["sources"] if s["name"] == "indeed_email")
        self.assertFalse(indeed["ok"])

    def test_null_job_id_fails_closed(self):
        # Disable NOT NULL or insert null via SQLite raw query
        conn = sqlite3.connect(self.db)
        try:
            # Create a table without NOT NULL on id to simulate corrupt snapshot
            conn.execute("CREATE TABLE jobs_corrupt AS SELECT * FROM jobs")
            conn.execute("UPDATE jobs_corrupt SET id=NULL WHERE id=1")
            conn.execute("DROP TABLE jobs")
            conn.execute("ALTER TABLE jobs_corrupt RENAME TO jobs")
            conn.commit()
        finally:
            conn.close()
        self._snapshot[self.db] = _digest(self.db)
        with self.assertRaisesRegex(MalformedRowError, "missing or null id"):
            self._run()

    def test_non_finite_float_fails_closed(self):
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE jobs SET pay_top='nan' WHERE id=1")
            conn.commit()
        finally:
            conn.close()
        self._snapshot[self.db] = _digest(self.db)
        with self.assertRaisesRegex(MalformedRowError, "non-finite float"):
            self._run()

    def test_float_in_int_column_fails_closed(self):
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE jobs SET remote=1.5 WHERE id=1")
            conn.commit()
        finally:
            conn.close()
        self._snapshot[self.db] = _digest(self.db)
        with self.assertRaisesRegex(MalformedRowError, "must be an integer"):
            self._run()

    def test_health_empty_reasons_coverage(self):
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("DELETE FROM jobs")
            # Sub-case: some sources ok, one error
            conn.execute("UPDATE source_runs SET ok=1, error='' WHERE name='indeed_email'")
            conn.execute("UPDATE source_runs SET ok=0, error='partial failure' WHERE name='linkedin_email'")
            conn.commit()
        finally:
            conn.close()
        self._snapshot[self.db] = _digest(self.db)
        health = self._run().health
        self.assertTrue(health["empty"])
        self.assertIn("one or more source runs reported errors", health["empty_reason"])

    def test_exact_and_uncertain_duplicates_coexist_without_contamination(self):
        conn = sqlite3.connect(self.db)
        try:
            # Job 1 and Job 10: exact duplicate (identical title/company/location)
            self._insert(conn, 10, "https://jobs.example.test/exact10", "IT Support Technician",
                         "Example Systems", "Pittsburgh, PA", "Windows support", "$18 per week")
            # Job 2 and Job 20: uncertain duplicate (same title/company, different location)
            self._insert(conn, 20, "https://jobs.example.test/unc20", "<style>hidden</style>Help Desk Analyst",
                         "<script>bad()</script>Acme", "Remote in Pittsburgh, PA", "Ticket support",
                         "$2,000 monthly")
            conn.commit()
        finally:
            conn.close()
        self._snapshot[self.db] = _digest(self.db)
        result = self._run()
        row1 = next(r for r in result.rows if r.upstream_records["id"] == 1)
        row10 = next(r for r in result.rows if r.upstream_records["id"] == 10)
        row2 = next(r for r in result.rows if r.upstream_records["id"] == 2)
        row20 = next(r for r in result.rows if r.upstream_records["id"] == 20)
        self.assertEqual(row1.duplicate_confidence, "exact")
        self.assertEqual(row10.duplicate_confidence, "exact")
        self.assertEqual(row2.duplicate_confidence, "uncertain")
        self.assertEqual(row20.duplicate_confidence, "uncertain")


if __name__ == "__main__":
    unittest.main()
