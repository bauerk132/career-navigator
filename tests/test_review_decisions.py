"""Phase 3c WS7: eligibility review decisions (store, fold, overlay, write route).

Uses a small fixture database with known review reasons (ids are JobFinder ids):

  1  driving review only        $25/hr, Pittsburgh, "Driving is preferred"
  2  pay review only            no pay text
  3  location review only       Remote, no Pennsylvania statement
  4  pay + location + driving   Remote, "Driving is preferred", no pay
  5  eligible (shortlist)       control row, never reviewed
  6  location + driving         Remote, "Driving is preferred", $30/hr

Every app here gets its own temporary state folder, so no test can read or
write Kyle's real decisions.
"""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from career_navigator import review_decisions as rd
from career_navigator.app import create_app, job_id

from test_live_wiring import _row, _write_db

WRITE = {"X-CN-Write": "1"}
ORIGIN = {"Origin": "http://127.0.0.1:5173"}


def _fixture_rows():
    def url(n):
        return f"https://www.indeed.com/viewjob?jk=rv{n:03d}"
    return [
        _row(1, url(1), "Help Desk Technician", "Acme", "Pittsburgh, PA",
             "Support Windows. Driving is preferred.", "$25/hr"),
        _row(2, url(2), "Help Desk Technician", "Beta", "Pittsburgh, PA",
             "Support Windows systems.", ""),
        _row(3, url(3), "IT Support Specialist", "Gamma", "Remote",
             "Support Windows systems remotely.", "$30/hr"),
        _row(4, url(4), "Desktop Support Analyst", "Delta", "Remote",
             "Support Windows. Driving is preferred.", ""),
        _row(5, url(5), "Help Desk Technician", "Epsilon", "Pittsburgh, PA",
             "Support Windows systems.", "$28/hr"),
        _row(6, url(6), "Network Technician", "Zeta", "Remote",
             "Support networks. Driving is preferred.", "$30/hr"),
    ]


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _api_id(row_id):
    return job_id({"url": f"https://www.indeed.com/viewjob?jk=rv{row_id:03d}"})


class FoldTests(unittest.TestCase):
    def _decide(self, jf=1, reason="pay", verdict="pass", event_id="e1", fp="a" * 16):
        return {"v": 1, "event_id": event_id, "ts": "2026-11-27T10:00:00+00:00", "type": "decide",
                "jobfinder_id": jf, "api_id": "0123456789ab", "reason": reason, "verdict": verdict,
                "field_fp": fp, "note": None}

    def _undo(self, undoes, jf=1, reason="pay", event_id="u1"):
        return {"v": 1, "event_id": event_id, "ts": "2026-11-27T10:05:00+00:00", "type": "undo",
                "jobfinder_id": jf, "reason": reason, "undoes": undoes}

    def test_decide_then_undo_clears(self):
        state = rd.fold_events([self._decide(), self._undo("e1")])
        self.assertEqual(state.active, {})
        self.assertEqual(state.event_count, 2)

    def test_later_decision_supersedes_and_keys_are_per_reason(self):
        state = rd.fold_events([
            self._decide(verdict="pass", event_id="e1"),
            self._decide(verdict="reject", event_id="e2"),
            self._decide(reason="driving", verdict="pass", event_id="e3"),
        ])
        self.assertEqual(state.active[(1, "pay")]["verdict"], "reject")
        self.assertEqual(state.active[(1, "driving")]["verdict"], "pass")

    def test_undo_naming_something_else_is_ignored(self):
        state = rd.fold_events([self._decide(), self._undo("nope")])
        self.assertIn((1, "pay"), state.active)
        self.assertEqual(state.skipped, 1)

    def test_malformed_events_are_skipped_not_half_applied(self):
        bad = [
            self._decide(reason="role"),                 # not a reviewable reason
            self._decide(verdict="maybe", event_id="e2"),
            self._decide(fp="short", event_id="e3"),
            dict(self._decide(event_id="e4"), v=2),       # future version
            dict(self._decide(event_id="e5"), jobfinder_id="1"),
            dict(self._decide(event_id="e6"), note="x" * 501),
            dict(self._decide(event_id="e7"), api_id="not-hex"),
        ]
        state = rd.fold_events(bad)
        self.assertEqual(state.active, {})
        self.assertEqual(state.skipped, len(bad))


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "jobfinder.db"
        self.state_dir = self.root / "state"
        _write_db(self.db, _fixture_rows())
        self.db_digest = _digest(self.db)
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.__exit__(None, None, None)
        self.temp.cleanup()

    def client(self):
        c = TestClient(create_app(db_path=str(self.db), reload_interval=0, state_dir=self.state_dir))
        c.__enter__()
        self.clients.append(c)
        return c

    @property
    def log_path(self):
        return self.state_dir / "review_decisions.jsonl"

    def log_lines(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line]

    def post(self, client, row_id, body, headers=None):
        return client.post(f"/api/jobs/{_api_id(row_id)}/review", json=body,
                           headers={**WRITE, **ORIGIN, **(headers or {})})

    def decide(self, client, row_id, reason, verdict, note=None):
        body = {"type": "decide", "reason": reason, "verdict": verdict}
        if note is not None:
            body["note"] = note
        r = self.post(client, row_id, body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def lists(self, client):
        data = client.get("/api/jobs").json()
        return {name: {r["jobfinder_id"]: r for r in data[name]} for name in ("shortlist", "review", "excluded")}

    def where(self, client, row_id):
        for name, rows in self.lists(client).items():
            if row_id in rows:
                return name, rows[row_id]
        raise AssertionError(f"row {row_id} is not served")


class QueueTests(ApiBase):
    def test_fixture_review_reasons_are_what_these_tests_assume(self):
        client = self.client()
        lists = self.lists(client)
        self.assertEqual(set(lists["review"]), {1, 2, 3, 4, 6})
        self.assertEqual(set(lists["shortlist"]), {5})
        reasons = {
            jf: sorted(r["reason"] for r in row["review_decision"]["reasons"])
            for jf, row in lists["review"].items()
        }
        self.assertEqual(reasons, {
            1: ["driving"], 2: ["pay"], 3: ["location"],
            4: ["driving", "location", "pay"], 6: ["driving", "location"],
        })

    def test_no_decisions_changes_nothing(self):
        client = self.client()
        stats = client.get("/api/stats").json()
        self.assertEqual(stats["totals"], {"shortlist": 1, "review": 5, "excluded": 0, "total": 6})
        self.assertEqual(stats["review_decisions"]["open"], 5)
        self.assertEqual(stats["review_decisions"]["decisions_in_force"], 0)
        self.assertFalse(self.log_path.exists())
        self.assertIsNone(self.lists(client)["shortlist"][5]["review_decision"])
        self.assertEqual(self.db_digest, _digest(self.db))


class DecisionTests(ApiBase):
    def test_pass_moves_to_shortlist_with_the_real_backend_score(self):
        client = self.client()
        _, before = self.where(client, 2)
        body = self.decide(client, 2, "pay", "pass", note="called the recruiter")
        lines = self.log_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["type"], "decide")
        self.assertEqual(lines[0]["jobfinder_id"], 2)
        self.assertEqual(lines[0]["api_id"], _api_id(2))
        self.assertEqual(lines[0]["reason"], "pay")
        self.assertEqual(lines[0]["verdict"], "pass")
        self.assertEqual(lines[0]["note"], "called the recruiter")
        self.assertRegex(lines[0]["field_fp"], r"^[0-9a-f]{16}$")
        where, row = self.where(client, 2)
        self.assertEqual(where, "shortlist")
        self.assertEqual(row["fit_score"], before["fit_score"])      # the score the backend computed, nothing invented
        self.assertEqual(row["fit_factors"], before["fit_factors"])
        self.assertIn(row["final_classification"], ("Strong Fit", "Realistic", "Stretch"))
        self.assertEqual(row["cn_status"], "review")                  # raw policy status never rewritten
        self.assertEqual(row["review_decision"]["state"], "passed")
        self.assertEqual(row["review_decision"]["effective_status"], "eligible")
        self.assertEqual(row["review_decision"]["reasons"][0]["badge"], "Confirmed by you: pay floor")
        self.assertIsNotNone(row["shortlist_rank"])
        self.assertEqual(body["job"]["id"], _api_id(2))
        ranks = [r["shortlist_rank"] for r in client.get("/api/jobs/shortlist").json()["shortlist"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    def test_reject_moves_to_excluded_with_a_reason(self):
        client = self.client()
        self.decide(client, 1, "driving", "reject")
        where, row = self.where(client, 1)
        self.assertEqual(where, "excluded")
        self.assertEqual(row["final_classification"], "Excluded")
        self.assertIsNone(row["shortlist_rank"])
        self.assertEqual(row["review_decision"]["state"], "rejected")
        self.assertEqual(row["review_decision"]["reasons"][0]["badge"], "Rejected by you: mandatory driving")

    def test_undo_puts_it_back_in_needs_review_and_keeps_history(self):
        client = self.client()
        self.decide(client, 2, "pay", "pass")
        r = self.post(client, 2, {"type": "undo", "reason": "pay"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.where(client, 2)[0], "review")
        lines = self.log_lines()
        self.assertEqual([l["type"] for l in lines], ["decide", "undo"])
        self.assertEqual(lines[1]["undoes"], lines[0]["event_id"])
        # Undo again: nothing in force.
        self.assertEqual(self.post(client, 2, {"type": "undo", "reason": "pay"}).status_code, 409)

    def test_undo_works_after_a_reject_moved_the_posting_out_of_review(self):
        client = self.client()
        self.decide(client, 3, "location", "reject")
        self.assertEqual(self.where(client, 3)[0], "excluded")
        self.assertEqual(self.post(client, 3, {"type": "undo", "reason": "location"}).status_code, 200)
        self.assertEqual(self.where(client, 3)[0], "review")

    def test_changing_your_mind_without_undo_supersedes(self):
        client = self.client()
        self.decide(client, 2, "pay", "pass")
        self.decide(client, 2, "pay", "reject")
        self.assertEqual(self.where(client, 2)[0], "excluded")
        self.assertEqual(len(self.log_lines()), 2)

    def test_two_reasons_stay_in_review_until_both_are_answered(self):
        client = self.client()
        self.decide(client, 6, "driving", "pass")
        where, row = self.where(client, 6)
        self.assertEqual(where, "review")
        self.assertEqual(row["review_decision"]["state"], "partial")
        statuses = {r["reason"]: r["status"] for r in row["review_decision"]["reasons"]}
        self.assertEqual(statuses, {"driving": "pass", "location": "open"})
        self.decide(client, 6, "location", "pass")
        where, row = self.where(client, 6)
        self.assertEqual(where, "shortlist")
        self.assertEqual(row["review_decision"]["state"], "passed")

    def test_three_reasons_need_all_three(self):
        client = self.client()
        for reason in ("pay", "location"):
            self.decide(client, 4, reason, "pass")
            self.assertEqual(self.where(client, 4)[0], "review")
        self.decide(client, 4, "driving", "pass")
        self.assertEqual(self.where(client, 4)[0], "shortlist")

    def test_one_reject_excludes_even_with_other_reasons_unanswered(self):
        client = self.client()
        self.decide(client, 4, "pay", "reject")
        self.assertEqual(self.where(client, 4)[0], "excluded")

    def test_decisions_survive_a_restart(self):
        client = self.client()
        self.decide(client, 2, "pay", "pass")
        self.decide(client, 1, "driving", "reject")
        again = self.client()   # a second app on the same state folder = a backend restart
        self.assertEqual(self.where(again, 2)[0], "shortlist")
        self.assertEqual(self.where(again, 1)[0], "excluded")

    def test_counts_after_decisions_sit_next_to_the_raw_numbers(self):
        client = self.client()
        raw = client.get("/api/stats").json()
        self.decide(client, 2, "pay", "pass")
        self.decide(client, 1, "driving", "reject")
        stats = client.get("/api/stats").json()
        self.assertEqual(stats["ingestion"], raw["ingestion"])              # raw by_decision untouched
        self.assertEqual(stats["ingestion"]["by_decision"]["review"], 5)
        self.assertEqual(stats["totals"], {"shortlist": 2, "review": 3, "excluded": 1, "total": 6})
        block = stats["review_decisions"]
        self.assertEqual((block["passed"], block["rejected"], block["open"]), (1, 1, 3))
        self.assertEqual(block["served_before_review_decisions"],
                         {"shortlist": 1, "review": 5, "excluded": 0, "total": 6})
        self.assertEqual(stats["served"]["exact_duplicate_count_sum"], 6)
        self.assertEqual(set(stats["served"]), set(raw["served"]))          # key set unchanged

    def test_audit_endpoint_lists_decisions(self):
        client = self.client()
        self.decide(client, 1, "driving", "reject", note="drives to client sites")
        audit = client.get("/api/review").json()
        self.assertTrue(audit["enabled"])
        self.assertEqual(len(audit["decisions"]), 1)
        entry = audit["decisions"][0]
        self.assertEqual((entry["jobfinder_id"], entry["reason"], entry["status"]), (1, "driving", "reject"))
        self.assertEqual(entry["note"], "drives to client sites")
        self.assertEqual(entry["badge"], "Rejected by you: mandatory driving")

    def test_database_and_other_state_untouched(self):
        client = self.client()
        self.decide(client, 2, "pay", "pass")
        self.assertEqual(self.db_digest, _digest(self.db))
        self.assertEqual({p.name for p in self.state_dir.iterdir()}, {"review_decisions.jsonl"})


class ChangedPostingTests(ApiBase):
    def _edit(self, row_id, **columns):
        conn = sqlite3.connect(self.db)
        sets = ", ".join(f"{name} = ?" for name in columns)
        conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*columns.values(), row_id))
        conn.commit()
        conn.close()

    def test_new_pay_text_sets_the_pass_aside_and_returns_it_to_review(self):
        client = self.client()
        self.decide(client, 2, "pay", "pass")
        self.assertEqual(self.where(client, 2)[0], "shortlist")
        # The posting is re-scraped with a pay range that straddles the floor:
        # still a pay review, but on different pay text.
        self._edit(2, pay_text="$15 - $30 an hour")
        self.assertEqual(client.post("/api/refresh").status_code, 200)
        where, row = self.where(client, 2)
        self.assertEqual(where, "review")
        meta = row["review_decision"]
        self.assertEqual(meta["state"], "changed")
        self.assertEqual(meta["reasons"][0]["status"], "changed")
        self.assertEqual(meta["reasons"][0]["verdict"], "pass")   # what Kyle had decided before
        self.assertEqual(client.get("/api/stats").json()["review_decisions"]["changed"], 1)
        self.assertEqual(len(self.log_lines()), 1)                # nothing rewritten or deleted

    def test_unchanged_posting_keeps_its_decision_across_a_refresh(self):
        client = self.client()
        self.decide(client, 2, "pay", "pass")
        self._edit(2, last_seen="2026-11-20 08:00:00")             # only the seen date moves
        self.assertEqual(client.post("/api/refresh").status_code, 200)
        self.assertEqual(self.where(client, 2)[0], "shortlist")

    def test_decision_for_a_posting_that_left_review_is_inert(self):
        client = self.client()
        self.decide(client, 2, "pay", "reject")
        self._edit(2, pay_text="$40/hr")                           # now plainly eligible
        self.assertEqual(client.post("/api/refresh").status_code, 200)
        self.assertEqual(self.where(client, 2)[0], "shortlist")
        self.assertEqual(client.get("/api/stats").json()["review_decisions"]["decisions_without_a_review_row"], 1)

    def test_driving_decision_follows_the_posting_text(self):
        client = self.client()
        self.decide(client, 1, "driving", "pass")
        self.assertEqual(self.where(client, 1)[0], "shortlist")
        self._edit(1, details="Support Windows. Driving is preferred. Occasional site visits.")
        self.assertEqual(client.post("/api/refresh").status_code, 200)
        where, row = self.where(client, 1)
        self.assertEqual(where, "review")
        self.assertEqual(row["review_decision"]["state"], "changed")


class GuardTests(ApiBase):
    def test_missing_write_header_is_403(self):
        client = self.client()
        r = client.post(f"/api/jobs/{_api_id(2)}/review", json={"type": "decide", "reason": "pay", "verdict": "pass"},
                        headers=ORIGIN)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.log_path.exists())

    def test_foreign_origin_is_403(self):
        client = self.client()
        r = self.post(client, 2, {"type": "decide", "reason": "pay", "verdict": "pass"},
                      headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.log_path.exists())

    def test_non_json_is_415(self):
        client = self.client()
        r = client.post(f"/api/jobs/{_api_id(2)}/review", content="type=decide",
                        headers={**WRITE, **ORIGIN, "Content-Type": "text/plain"})
        self.assertEqual(r.status_code, 415)

    def test_extra_or_bad_fields_are_422(self):
        client = self.client()
        for body in (
            {"type": "decide", "reason": "pay", "verdict": "pass", "field_fp": "0" * 16},
            {"type": "decide", "reason": "pay", "verdict": "pass", "jobfinder_id": 1},
            {"type": "decide", "reason": "role", "verdict": "pass"},
            {"type": "decide", "reason": "pay", "verdict": "maybe"},
            {"type": "delete", "reason": "pay"},
            {"type": "decide", "reason": "pay", "verdict": "pass", "note": "x" * 501},
        ):
            self.assertEqual(self.post(client, 2, body).status_code, 422, body)
        self.assertFalse(self.log_path.exists())

    def test_decide_without_verdict_and_undo_with_verdict_are_400(self):
        client = self.client()
        self.assertEqual(self.post(client, 2, {"type": "decide", "reason": "pay"}).status_code, 400)
        self.decide(client, 2, "pay", "pass")
        self.assertEqual(self.post(client, 2, {"type": "undo", "reason": "pay", "verdict": "pass"}).status_code, 400)

    def test_unknown_or_malformed_job_is_404(self):
        client = self.client()
        body = {"type": "decide", "reason": "pay", "verdict": "pass"}
        r = client.post("/api/jobs/ffffffffffff/review", json=body, headers={**WRITE, **ORIGIN})
        self.assertEqual(r.status_code, 404)
        r = client.post("/api/jobs/not-an-id/review", json=body, headers={**WRITE, **ORIGIN})
        self.assertEqual(r.status_code, 404)

    def test_reason_the_posting_is_not_waiting_on_is_409(self):
        client = self.client()
        self.assertEqual(self.post(client, 2, {"type": "decide", "reason": "driving", "verdict": "pass"}).status_code, 409)
        # The eligible control row has no review reasons at all.
        self.assertEqual(self.post(client, 5, {"type": "decide", "reason": "pay", "verdict": "pass"}).status_code, 409)
        self.assertFalse(self.log_path.exists())

    def test_no_state_folder_means_503_and_nothing_written(self):
        # Synthetic backend: no db, no state folder, so it can never write.
        with mock.patch.dict(os.environ):
            os.environ.pop("CN_STATE_DIR", None)
            os.environ.pop("JOBFINDER_DB_PATH", None)
            app = create_app(reload_interval=0)
        client = TestClient(app)
        client.__enter__()
        self.clients.append(client)
        r = client.post(f"/api/jobs/{_api_id(2)}/review",
                        json={"type": "decide", "reason": "pay", "verdict": "pass"},
                        headers={**WRITE, **ORIGIN})
        self.assertEqual(r.status_code, 503)
        self.assertFalse(client.get("/api/review").json()["enabled"])

    def test_damaged_log_lines_are_counted_never_fatal(self):
        self.state_dir.mkdir(parents=True)
        self.log_path.write_bytes(b'{"broken":\nnot json\n')
        client = self.client()
        stats = client.get("/api/stats").json()
        self.assertEqual(stats["review_decisions"]["store"]["skipped_lines"], 2)
        self.assertEqual(stats["totals"]["review"], 5)
        self.decide(client, 2, "pay", "pass")                      # and writing still works
        self.assertEqual(self.where(client, 2)[0], "shortlist")


if __name__ == "__main__":
    unittest.main()
