"""Phase 3c WS4: tracker adapter, application events, board and export.

Uses the small WS7 fixture database (Indeed postings rv001..rv006; row 5 is on
the shortlist) and a synthetic tracker CSV written into a temporary folder.
Every app gets its own temporary state folder and tracker copy, so no test can
read or write Kyle's real tracker or decisions.
"""
import csv
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from career_navigator import applications as ap
from career_navigator import tracker_adapter as ta
from career_navigator.app import create_app, job_id

from test_live_wiring import _write_db
from test_review_decisions import _fixture_rows

WRITE = {"X-CN-Write": "1", "Origin": "http://127.0.0.1:5173"}
LOCAL = ("127.0.0.1", 50000)


def _api_id(row_id):
    return job_id({"url": f"https://www.indeed.com/viewjob?jk=rv{row_id:03d}"})


def _tracker_rows():
    base = {c: "" for c in ta.COLUMNS}

    def row(**values):
        return {**base, **values}
    return [
        row(job_id="T1", title="Help Desk Technician", company="Epsilon", category="IT",
            apply_url="https://www.indeed.com/viewjob?jk=rv005&from=serp", status="Ready",
            date_found="10/2/2026", last_updated="10/2/2026"),
        row(job_id="T2", title="Help Desk Technician", company="Acme", category="Office",
            apply_url="https://www.indeed.com/m/viewjob?vjk=rv001", status="In Progress",
            date_found="2026-10-16"),
        row(job_id="T3", title="Records Clerk", company="County", category="Office", status="Applied",
            date_applied="10/5/2026", confirmation_number="CONF-SECRET-1", notes="private note text",
            next_step="Call the office", follow_up_date="11/20/2026"),
        row(job_id="T4", title="Line Cook", company="Diner", category="Food", status="Heard Back",
            heard_back="Y", response_type="Interview request", heard_back_date="2026-10-20"),
        row(job_id="T5", title="Prep Cook", company="Bistro", category="Food", status="Passed",
            next_step="nothing to do"),
        row(job_id="", title="Mystery Job", company="Unknown", status="Weird", date_found="soon"),
        row(job_id="T3", title="Records Clerk (again)", company="County", status="Interview",
            next_step="Prepare answers"),
        row(job_id="T8", title="Barista", company="Cafe", category="Food", status="Rejected",
            apply_url="https://to.indeed.com/abc123", response_type="Rejection"),
    ]


def _write_tracker(path, rows, bom=False):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(ta.COLUMNS), lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    data = buffer.getvalue().encode("utf-8")
    Path(path).write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class TrackerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "job_tracker.csv"
        _write_tracker(self.path, _tracker_rows())

    def tearDown(self):
        self.temp.cleanup()

    def test_both_date_styles_normalize(self):
        self.assertEqual(ta.parse_date("10/2/2026"), ("2026-10-02", True))
        self.assertEqual(ta.parse_date("2026-10-16"), ("2026-10-16", True))
        self.assertEqual(ta.parse_date("2026-10-16T09:30:00"), ("2026-10-16", True))
        self.assertEqual(ta.parse_date(""), (None, True))
        self.assertEqual(ta.parse_date("soon"), (None, False))
        self.assertEqual(ta.parse_date("2/30/2026"), (None, False))

    def test_every_row_is_kept_and_problems_are_reported(self):
        tracker = ta.load_tracker(self.path)
        self.assertEqual(len(tracker.rows), 8)
        issues = {(p["row_number"], p["column"], p["issue"]) for p in tracker.problems}
        self.assertIn((6, "date_found", "date could not be read"), issues)
        self.assertIn((6, "status", "not a known status"), issues)
        self.assertIn((6, "job_id", "blank job id"), issues)
        self.assertIn((7, "job_id", "job id repeats an earlier row"), issues)
        self.assertEqual(len({r.key for r in tracker.rows}), 8, "every row gets its own key")

    def test_problem_list_never_carries_cell_text(self):
        tracker = ta.load_tracker(self.path)
        text = json.dumps(list(tracker.problems))
        self.assertNotIn("soon", text)
        self.assertNotIn("Weird", text)

    def test_reading_never_changes_the_file(self):
        before = (_sha(self.path), os.stat(self.path).st_mtime_ns)
        ta.load_tracker(self.path)
        self.assertEqual(before, (_sha(self.path), os.stat(self.path).st_mtime_ns))

    def test_bom_and_missing_columns(self):
        _write_tracker(self.path, _tracker_rows(), bom=True)
        self.assertEqual(ta.load_tracker(self.path).header[0], "job_id")
        self.path.write_text("title,company\nA,B\n", encoding="utf-8")
        with self.assertRaises(ta.TrackerError):
            ta.load_tracker(self.path)

    def test_repo_fixture_still_loads(self):
        fixture = Path(__file__).parent / "fixtures" / "job_tracker.csv"
        tracker = ta.load_tracker(fixture)
        self.assertEqual(len(tracker.rows), 1)
        self.assertEqual(tracker.problems, ())


class FoldTests(unittest.TestCase):
    def ev(self, event_id, kind, key="tracker:T1", **fields):
        return {"v": 1, "event_id": event_id, "ts": "2026-11-27T10:00:00+00:00", "on": "2026-11-27",
                "type": kind, "app_key": key, **fields}

    def test_undo_removes_only_the_named_event(self):
        state = ap.fold_events([
            self.ev("a", "move", status="Applied"),
            self.ev("b", "note", text="hi"),
            self.ev("u", "undo", undoes="a"),
        ])
        kinds = [e["type"] for _s, e in state.cards["tracker:T1"].changes]
        self.assertEqual(kinds, ["note"])

    def test_undo_must_name_an_earlier_event_of_the_same_card(self):
        state = ap.fold_events([
            self.ev("a", "move", status="Applied"),
            self.ev("u", "undo", key="tracker:T2", undoes="a"),
            self.ev("u2", "undo", undoes="missing"),
        ])
        self.assertEqual(len(state.cards["tracker:T1"].changes), 1)
        self.assertEqual(state.skipped, 2)

    def test_bad_lines_are_skipped(self):
        state = ap.fold_events([
            self.ev("a", "move", status="Hired"),
            self.ev("b", "follow_up", date="11/20/2026"),
            {"v": 2, "event_id": "c", "type": "move"},
            self.ev("d", "move", status="Applied"),
        ], skipped_lines=1)
        self.assertEqual(state.event_count, 1)
        self.assertEqual(state.skipped, 4)


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "jobfinder.db"
        self.state_dir = self.root / "state"
        self.tracker = self.root / "job_tracker.csv"
        _write_db(self.db, _fixture_rows())
        _write_tracker(self.tracker, _tracker_rows())
        self.tracker_sha = _sha(self.tracker)
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.__exit__(None, None, None)
        self.temp.cleanup()

    def client(self, local=True, state=True):
        app = create_app(db_path=str(self.db), reload_interval=0,
                         state_dir=self.state_dir if state else None, tracker_path=self.tracker)
        if not state:
            app.state.applications = ap.Applications(None)
        c = TestClient(app, client=LOCAL) if local else TestClient(app)
        c.__enter__()
        self.clients.append(c)
        return c

    def board(self, c):
        r = c.get("/api/applications")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def card(self, c, job_id_value, title=None):
        for card in self.board(c)["cards"]:
            if card["job_id"] == job_id_value and (title is None or card["title"] == title):
                return card
        raise AssertionError(f"no card {job_id_value}")

    def event(self, c, card, body, status=200):
        r = c.post(f"/api/applications/{card['id']}/events", json=body, headers=WRITE)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def log_lines(self):
        path = self.state_dir / ap.APPLICATIONS_FILENAME
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


class BoardTests(ApiBase):
    def test_every_row_lands_in_its_column(self):
        board = self.board(self.client())
        self.assertTrue(board["available"])
        self.assertEqual(board["summary"]["cards"], 8)
        columns = {c["key"]: c["count"] for c in board["columns"]}
        self.assertEqual(columns, {"to_apply": 2, "applied": 1, "heard_back": 2, "closed": 2,
                                   "unknown_status": 1})
        self.assertEqual(sum(columns.values()), 8)
        heard = self.card(self.client(), "T4")
        self.assertEqual((heard["column"], heard["response_type"]), ("heard_back", "Interview request"))

    def test_matches_show_their_basis(self):
        c = self.client()
        t1, t2, t8 = self.card(c, "T1"), self.card(c, "T2"), self.card(c, "T8")
        self.assertEqual(t1["match"]["basis"], "same_link")
        self.assertEqual(t1["match"]["api_id"], _api_id(5))
        self.assertEqual(t2["match"]["basis"], "indeed_job_key")
        self.assertEqual(t2["match"]["jobfinder_id"], 1)
        self.assertIsNone(t8["match"], "a to.indeed.com short link is never followed")
        self.assertIsNone(self.card(c, "T3", "Records Clerk")["match"])
        self.assertEqual(self.board(c)["tracked_job_ids"][_api_id(5)], t1["id"])

    def test_categories_are_all_shown(self):
        board = self.board(self.client())
        self.assertEqual(board["summary"]["categories"],
                         {"Food": 3, "IT": 1, "Office": 2, "Uncategorized": 2})

    def test_dates_are_normalized_and_follow_up_due(self):
        t3 = self.card(self.client(), "T3", "Records Clerk")
        self.assertEqual(t3["dates"]["date_applied"], "2026-10-05")
        self.assertEqual(t3["dates"]["follow_up_date"], "2026-11-20")
        self.assertTrue(t3["follow_up_due"])

    def test_next_step_strip_is_soonest_first_and_skips_closed(self):
        c = self.client()
        board = self.board(c)
        titles = [board_card["title"] for cid in board["next_steps"]
                  for board_card in board["cards"] if board_card["id"] == cid]
        self.assertEqual(titles, ["Records Clerk", "Records Clerk (again)"])

    def test_private_fields_only_for_local_clients(self):
        local = self.card(self.client(), "T3", "Records Clerk")
        self.assertEqual(local["confirmation_number"], "CONF-SECRET-1")
        self.assertEqual(local["notes"], "private note text")
        remote = self.board(self.client(local=False))
        self.assertFalse(remote["private_fields_served"])
        self.assertNotIn("CONF-SECRET-1", json.dumps(remote))
        self.assertNotIn("private note text", json.dumps(remote))

    def test_no_tracker_configured(self):
        app = create_app(db_path=str(self.db), reload_interval=0, state_dir=self.state_dir)
        app.state.tracker_path = None
        with TestClient(app, client=LOCAL) as c:
            board = c.get("/api/applications").json()
        self.assertFalse(board["available"])
        self.assertEqual(board["cards"], [])


class EventTests(ApiBase):
    def test_move_writes_one_line_survives_reload_and_leaves_csv_alone(self):
        c = self.client()
        t1 = self.card(c, "T1")
        out = self.event(c, t1, {"type": "move", "status": "Applied"})
        self.assertEqual(out["card"]["column"], "applied")
        self.assertTrue(out["card"]["unexported"])
        self.assertEqual(out["card"]["dates"]["date_applied"], ap.today_iso())
        self.assertEqual(len(self.log_lines()), 1)
        again = self.card(self.client(), "T1")
        self.assertEqual((again["status"], again["column"]), ("Applied", "applied"))
        self.assertEqual(_sha(self.tracker), self.tracker_sha)

    def test_next_step_follow_up_and_note(self):
        c = self.client()
        t2 = self.card(c, "T2")
        self.event(c, t2, {"type": "next_step", "text": "Email the manager"})
        self.event(c, t2, {"type": "follow_up", "date": "2026-11-01"})
        self.event(c, t2, {"type": "note", "text": "Spoke to HR"})
        card = self.card(c, "T2")
        self.assertEqual(card["next_step"], "Email the manager")
        self.assertTrue(card["follow_up_due"])
        self.assertEqual([n["text"] for n in card["cn_notes"]], ["Spoke to HR"])
        self.assertIn(card["id"], self.board(c)["next_steps"])
        self.event(c, card, {"type": "follow_up", "date": "11/01/2026"}, status=400)
        self.event(c, card, {"type": "follow_up", "date": "2026-02-30"}, status=400)
        self.event(c, card, {"type": "note", "text": "   "}, status=400)
        self.event(c, card, {"type": "move", "status": "Hired"}, status=400)
        self.event(c, card, {"type": "move", "status": "In Progress"}, status=409)
        self.event(c, card, {"type": "note", "text": "x", "status": "Applied"}, status=400)

    def test_undo(self):
        c = self.client()
        t1 = self.card(c, "T1")
        moved = self.event(c, t1, {"type": "move", "status": "Applied"})
        self.event(c, t1, {"type": "undo", "event_id": moved["event"]["event_id"]})
        card = self.card(c, "T1")
        self.assertEqual(card["status"], "Ready")
        self.assertEqual(card["changes"], [])
        self.event(c, card, {"type": "undo", "event_id": moved["event"]["event_id"]}, status=409)

    def test_unlink_a_wrong_match(self):
        c = self.client()
        t2 = self.card(c, "T2")
        self.event(c, t2, {"type": "unlink"})
        card = self.card(self.client(), "T2")
        self.assertIsNone(card["match"])
        self.assertEqual(card["unlinked_jobfinder_ids"], [1])
        self.event(c, card, {"type": "unlink"}, status=409)

    def test_track_a_shortlist_job(self):
        c = self.client()
        # Row 5 is matched by T1; unlink it first so it can be tracked on its own.
        self.event(c, self.card(c, "T1"), {"type": "unlink"})
        r = c.post(f"/api/jobs/{_api_id(5)}/track", json={}, headers=WRITE)
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual((card["source"], card["status"], card["column"]), ("tracked", "Ready", "to_apply"))
        self.assertEqual(card["match"]["basis"], "tracked_from_shortlist")
        self.assertEqual(c.post(f"/api/jobs/{_api_id(5)}/track", json={}, headers=WRITE).status_code, 409)
        self.assertEqual(self.board(c)["summary"]["cards"], 9)

    def test_tracking_an_already_matched_job_is_refused(self):
        c = self.client()
        self.assertEqual(c.post(f"/api/jobs/{_api_id(5)}/track", json={}, headers=WRITE).status_code, 409)

    def test_guards(self):
        c = self.client()
        t1 = self.card(c, "T1")
        url = f"/api/applications/{t1['id']}/events"
        self.assertEqual(c.post(url, json={"type": "note", "text": "x"}).status_code, 403)
        self.assertEqual(c.post(url, json={"type": "note", "text": "x"},
                                headers={**WRITE, "Origin": "http://evil.example"}).status_code, 403)
        self.assertEqual(c.post(url, json={"type": "note", "text": "x", "extra": 1},
                                headers=WRITE).status_code, 422)
        self.assertEqual(c.post("/api/applications/ffffffffffff/events", json={"type": "note", "text": "x"},
                                headers=WRITE).status_code, 404)
        self.assertEqual(self.log_lines(), [])

    def test_no_state_folder_refuses_writes(self):
        c = self.client(state=False)
        t1 = self.card(c, "T1")
        self.event(c, t1, {"type": "note", "text": "x"}, status=503)


class ExportTests(ApiBase):
    def preview(self, c):
        r = c.get("/api/applications/export/preview")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def export(self, c, token, status=200):
        r = c.post("/api/applications/export", json={"token": token}, headers=WRITE)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_preview_then_export_writes_a_new_dated_copy(self):
        c = self.client()
        self.event(c, self.card(c, "T1"), {"type": "move", "status": "Applied"})
        self.event(c, self.card(c, "T3", "Records Clerk"), {"type": "note", "text": "Sent thank-you"})
        preview = self.preview(c)
        self.assertEqual((preview["changed_rows"], preview["new_rows"]), (2, 0))
        out = self.export(c, preview["token"])
        self.assertTrue(out["original_unchanged"])
        self.assertEqual(_sha(self.tracker), self.tracker_sha)
        path = Path(out["path"])
        self.assertEqual(path.parent, self.state_dir / ap.EXPORT_DIRNAME)
        self.assertRegex(path.name, r"^job_tracker\.\d{4}-\d{2}-\d{2}\.csv$")
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM so Excel reads it as UTF-8")
        self.assertIn(b"\r\n", raw)
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
        original = list(csv.DictReader(io.StringIO(self.tracker.read_text(encoding="utf-8"), newline="")))
        self.assertEqual(len(rows), len(original))
        self.assertEqual(rows[0]["status"], "Applied")
        self.assertEqual(rows[0]["last_updated_by"], "career-navigator")
        self.assertEqual(rows[2]["notes"], f"private note text | {ap.today_iso()}: Sent thank-you")
        for i in (1, 3, 4, 5, 6, 7):
            self.assertEqual(rows[i], original[i], f"untouched row {i} must be identical")
        self.assertEqual(self.board(c)["summary"]["unexported"], 0)
        self.assertEqual(self.log_lines()[-1]["type"], "export")

    def test_a_second_export_never_overwrites(self):
        c = self.client()
        first = self.export(c, self.preview(c)["token"])
        second = self.export(c, self.preview(c)["token"])
        self.assertNotEqual(first["file"], second["file"])
        self.assertTrue(Path(first["path"]).exists())

    def test_stale_preview_is_refused(self):
        c = self.client()
        token = self.preview(c)["token"]
        self.event(c, self.card(c, "T1"), {"type": "note", "text": "later"})
        self.export(c, token, status=409)

    def test_export_put_in_place_of_the_tracker_is_not_applied_twice(self):
        c = self.client()
        self.event(c, self.card(c, "T3", "Records Clerk"), {"type": "note", "text": "Once"})
        out = self.export(c, self.preview(c)["token"])
        # Kyle copies the export over his tracker himself.
        self.tracker.write_bytes(Path(out["path"]).read_bytes())
        c2 = self.client()
        card = self.card(c2, "T3", "Records Clerk")
        self.assertEqual(card["cn_notes"], [], "the note is already in the file")
        self.assertEqual(card["notes"].count("Once"), 1)
        self.assertFalse(card["unexported"])
        self.assertIsNotNone(self.board(c2)["tracker"]["baseline_export"])

    def test_not_exported_yet_only_when_there_is_something_to_export(self):
        c = self.client()
        moved = self.event(c, self.card(c, "T1"), {"type": "move", "status": "Applied"})
        undone = self.event(c, self.card(c, "T1"),
                            {"type": "undo", "event_id": moved["event"]["event_id"]})
        self.assertFalse(undone["card"]["unexported"], "made and undone before any export")
        moved = self.event(c, self.card(c, "T1"), {"type": "move", "status": "Applied"})
        self.export(c, self.preview(c)["token"])
        self.assertFalse(self.card(c, "T1")["unexported"])
        undone = self.event(c, self.card(c, "T1"),
                            {"type": "undo", "event_id": moved["event"]["event_id"]})
        self.assertTrue(undone["card"]["unexported"], "the export still has the undone change")

    def test_formula_like_text_is_quoted(self):
        self.assertEqual(ap.safe_cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(ap.safe_cell("- call back"), "- call back")

    def test_tracked_job_becomes_a_new_row(self):
        c = self.client()
        self.event(c, self.card(c, "T1"), {"type": "unlink"})
        c.post(f"/api/jobs/{_api_id(5)}/track", json={}, headers=WRITE)
        preview = self.preview(c)
        self.assertEqual(preview["new_rows"], 1)
        out = self.export(c, preview["token"])
        rows = list(csv.DictReader(io.StringIO(Path(out["path"]).read_text(encoding="utf-8-sig"), newline="")))
        self.assertEqual(rows[-1]["job_id"], "cn-5")
        self.assertEqual(rows[-1]["status"], "Ready")


class NoOutsideCallsTests(unittest.TestCase):
    def test_board_modules_import_no_network_or_mail_code(self):
        root = Path(__file__).resolve().parent.parent / "career_navigator"
        for name in ("applications.py", "tracker_adapter.py"):
            text = (root / name).read_text(encoding="utf-8")
            for banned in ("urllib.request", "http.client", "smtplib", "requests", "socket",
                           "subprocess", "webbrowser", "httpx"):
                self.assertNotIn(f"import {banned}", text, f"{name} imports {banned}")
                self.assertNotIn(f"from {banned}", text, f"{name} imports {banned}")


if __name__ == "__main__":
    unittest.main()
