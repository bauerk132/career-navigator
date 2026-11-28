"""Phase 3c WS5: uncertain-duplicate resolutions (store, overlay, write route).

Runs on the 845-row fixture database from test_live_wiring (59 uncertain
pairs = 118 uncertain rows, 688 served, sum(exact_duplicate_count) == 845).
Every app here gets its own temporary state folder, so no test can read or
write Kyle's real decisions.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from career_navigator import duplicate_resolutions as dr
from career_navigator.app import create_app
from career_navigator.state_store import JsonlLog, StateError

from state_helpers import db_exists, read_events

from test_live_wiring import _details, _row, _write_db, build_845_db, build_845_rows

WRITE = {"X-CN-Write": "1"}
ORIGIN = {"Origin": "http://127.0.0.1:5173"}


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FingerprintTests(unittest.TestCase):
    def test_order_independent_and_member_sensitive(self):
        self.assertEqual(dr.group_fingerprint([3, 1, 2]), dr.group_fingerprint([1, 2, 3]))
        self.assertNotEqual(dr.group_fingerprint([1, 2]), dr.group_fingerprint([1, 2, 3]))
        self.assertEqual(len(dr.group_fingerprint([1, 2])), 16)

    def test_numeric_not_lexical_order(self):
        self.assertEqual(dr.group_fingerprint([9, 10]), dr.group_fingerprint([10, 9]))


class FoldTests(unittest.TestCase):
    def _resolve(self, ids, action="keep", event_id="e1", kept=None):
        return {"v": 1, "event_id": event_id, "ts": "2026-11-27T10:00:00+00:00", "type": "resolve",
                "fingerprint": dr.group_fingerprint(ids), "member_ids": ids, "action": action,
                "kept_id": kept, "note": None}

    def _undo(self, ids, undoes, event_id="u1"):
        return {"v": 1, "event_id": event_id, "ts": "2026-11-27T10:05:00+00:00", "type": "undo",
                "fingerprint": dr.group_fingerprint(ids), "undoes": undoes}

    def test_resolve_then_undo_clears(self):
        state = dr.fold_events([self._resolve([1, 2]), self._undo([1, 2], "e1")])
        self.assertEqual(state.active, {})
        self.assertEqual(state.event_count, 2)

    def test_later_resolve_supersedes(self):
        state = dr.fold_events([self._resolve([1, 2], "keep", "e1"), self._resolve([1, 2], "dismiss", "e2")])
        self.assertEqual(state.active[dr.group_fingerprint([1, 2])]["action"], "dismiss")

    def test_undo_naming_something_else_is_ignored(self):
        state = dr.fold_events([self._resolve([1, 2], "keep", "e1"), self._undo([1, 2], "nope")])
        self.assertIn(dr.group_fingerprint([1, 2]), state.active)
        self.assertEqual(state.skipped, 1)

    def test_tampered_or_malformed_events_are_skipped(self):
        bad_fp = self._resolve([1, 2])
        bad_fp["fingerprint"] = "0" * 16
        merge_no_kept = self._resolve([1, 2], "merge", "e3", kept=None)
        merge_outsider = self._resolve([1, 2], "merge", "e4", kept=99)
        keep_with_kept = self._resolve([1, 2], "keep", "e5", kept=1)
        future = dict(self._resolve([1, 2]), v=2)
        state = dr.fold_events([bad_fp, merge_no_kept, merge_outsider, keep_with_kept, future])
        self.assertEqual(state.active, {})
        self.assertEqual(state.skipped, 5)


class JsonlLogTests(unittest.TestCase):
    def test_torn_tail_never_swallows_next_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.jsonl"
            path.write_bytes(b'{"a":1}\n{"b":')   # power cut mid-line
            log = JsonlLog(path)
            log.append({"c": 3})
            records, skipped, _ = log.read()
            self.assertEqual(records, [{"a": 1}, {"c": 3}])
            self.assertEqual(skipped, 1)

    def test_append_only_and_cache_follows_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = JsonlLog(Path(tmp) / "sub" / "x.jsonl")
            self.assertEqual(log.read()[0], [])
            log.append({"n": 1})
            log.append({"n": 2})
            self.assertEqual([r["n"] for r in log.read()[0]], [1, 2])
            self.assertEqual(len(log.path.read_text().splitlines()), 2)

    def test_non_object_lines_are_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.jsonl"
            path.write_text('[1,2]\n"str"\n{"ok":true}\n', encoding="utf-8")
            records, skipped, _ = JsonlLog(path).read()
            self.assertEqual(records, [{"ok": True}])
            self.assertEqual(skipped, 2)

    def test_oversized_record_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(StateError):
                JsonlLog(Path(tmp) / "x.jsonl").append({"big": "x" * 20000})


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "jobfinder.db"
        self.state_dir = self.root / "state"
        build_845_db(self.db)
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
        return self.state_dir / "duplicate_resolutions.jsonl"

    def log_lines(self):
        return read_events(self.state_dir, "duplicate_resolutions")

    def groups(self, client):
        return client.get("/api/duplicates").json()["groups"]

    def post(self, client, fingerprint, body, headers=None):
        return client.post(f"/api/duplicates/{fingerprint}/events", json=body,
                           headers={**WRITE, **ORIGIN, **(headers or {})})

    def served_rows(self, client):
        data = client.get("/api/jobs").json()
        return {lst: data[lst] for lst in ("shortlist", "review", "excluded")}, data


class QueueTests(ApiBase):
    def test_every_uncertain_group_listed_once_with_all_members(self):
        client = self.client()
        body = client.get("/api/duplicates").json()
        self.assertTrue(body["enabled"])
        self.assertEqual(len(body["groups"]), 59)
        self.assertEqual({g["size"] for g in body["groups"]}, {2})
        self.assertEqual(len({g["fingerprint"] for g in body["groups"]}), 59)
        self.assertEqual(body["summary"]["uncertain_rows"], 118)
        self.assertEqual(body["summary"]["open"], 59)
        member_ids = [m["jobfinder_id"] for g in body["groups"] for m in g["members"]]
        self.assertEqual(len(member_ids), len(set(member_ids)))
        for g in body["groups"]:
            for m in g["members"]:
                for key in ("title", "company", "location", "pay_text", "url", "first_seen",
                            "last_seen", "list", "provider"):
                    self.assertIn(key, m)

    def test_upstream_records_carry_group_fingerprint_and_members(self):
        client = self.client()
        rows, _ = self.served_rows(client)
        flagged = [r for lst in rows.values() for r in lst
                   if r["contract"]["upstream_records"].get("dup_group")]
        self.assertEqual(len(flagged), 118)
        meta = flagged[0]["contract"]["upstream_records"]["dup_group"]
        self.assertEqual(meta["fingerprint"], dr.group_fingerprint(meta["member_ids"]))

    def test_no_decisions_means_nothing_changes(self):
        client = self.client()
        stats = client.get("/api/stats").json()
        self.assertEqual(stats["served"]["total"], 688)
        self.assertEqual(stats["served"]["exact_duplicate_count_sum"], 845)
        self.assertEqual(stats["ingestion"]["by_duplicate_confidence"]["uncertain"], 118)
        self.assertEqual(stats["duplicates"]["served_before_decisions"]["total"], 688)
        self.assertEqual(stats["duplicates"]["folded_rows"], 0)
        self.assertFalse(db_exists(self.state_dir))


class DecisionTests(ApiBase):
    def test_keep_writes_one_line_and_stops_flagging_nothing_hidden(self):
        client = self.client()
        group = self.groups(client)[0]
        r = self.post(client, group["fingerprint"], {"type": "resolve", "action": "keep", "note": "different sites"})
        self.assertEqual(r.status_code, 200, r.text)
        lines = self.log_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["action"], "keep")
        self.assertEqual(lines[0]["fingerprint"], group["fingerprint"])
        self.assertEqual(lines[0]["member_ids"], [m["jobfinder_id"] for m in group["members"]])
        self.assertEqual(lines[0]["note"], "different sites")
        rows, data = self.served_rows(client)
        self.assertEqual(data["total"], 688)  # keep hides nothing
        by_jf = {r["jobfinder_id"]: r for lst in rows.values() for r in lst}
        for m in group["members"]:
            row = by_jf[m["jobfinder_id"]]
            self.assertEqual(row["duplicate_resolution"]["action"], "keep")
            self.assertEqual(row["duplicate_confidence"], "uncertain")  # raw fields untouched
        stats = client.get("/api/stats").json()
        self.assertEqual(stats["duplicates"]["keep"], 1)
        self.assertEqual(stats["duplicates"]["open"], 58)
        self.assertEqual(stats["ingestion"]["by_duplicate_confidence"]["uncertain"], 118)

    def test_dismiss_leaves_every_posting_where_it_was_still_uncertain(self):
        client = self.client()
        before, _ = self.served_rows(client)
        group = self.groups(client)[0]
        self.assertEqual(self.post(client, group["fingerprint"], {"type": "resolve", "action": "dismiss"}).status_code, 200)
        after, data = self.served_rows(client)
        self.assertEqual(data["total"], 688)
        for lst in before:
            self.assertEqual([r["id"] for r in before[lst]], [r["id"] for r in after[lst]])
        body = client.get("/api/duplicates").json()
        dismissed = next(g for g in body["groups"] if g["fingerprint"] == group["fingerprint"])
        self.assertEqual(dismissed["state"], "dismiss")
        self.assertEqual(body["summary"]["open"], 58)
        for lst in after.values():
            for row in lst:
                if row["jobfinder_id"] in {m["jobfinder_id"] for m in group["members"]}:
                    self.assertEqual(row["duplicate_confidence"], "uncertain")

    def test_merge_folds_the_others_and_reconciliation_still_holds(self):
        client = self.client()
        before, before_data = self.served_rows(client)
        both_short = [g for g in self.groups(client) if all(m["list"] == "shortlist" for m in g["members"])]
        group = both_short[0] if both_short else self.groups(client)[0]
        kept, folded = group["members"][0], group["members"][1]
        r = self.post(client, group["fingerprint"],
                      {"type": "resolve", "action": "merge", "kept_jobfinder_id": kept["jobfinder_id"]})
        self.assertEqual(r.status_code, 200, r.text)
        rows, data = self.served_rows(client)
        self.assertEqual(data["total"], 687)
        flat = {r["jobfinder_id"]: r for lst in rows.values() for r in lst}
        self.assertNotIn(folded["jobfinder_id"], flat)
        self.assertEqual(flat[kept["jobfinder_id"]]["exact_duplicate_count"], 2)
        self.assertEqual(flat[kept["jobfinder_id"]]["duplicate_resolution"]["role"], "kept")
        stats = client.get("/api/stats").json()
        self.assertEqual(stats["served"]["total"], 687)
        self.assertEqual(stats["served"]["exact_duplicate_count_sum"], 845)  # == raw rows
        self.assertEqual(stats["ingestion"]["total"], 845)
        self.assertEqual(stats["ingestion"]["by_duplicate_confidence"]["uncertain"], 118)
        self.assertEqual(stats["duplicates"]["merge"], 1)
        self.assertEqual(stats["duplicates"]["folded_rows"], 1)
        self.assertEqual(stats["duplicates"]["served_before_decisions"]["total"], 688)
        ranks = [r["shortlist_rank"] for r in rows["shortlist"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
        # the folded posting's detail still resolves and says where it went
        detail = client.get(f"/api/jobs/{[r for lst in before.values() for r in lst if r['jobfinder_id'] == folded['jobfinder_id']][0]['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["duplicate_resolution"]["role"], "folded")

    def test_undo_appends_a_line_and_restores_the_view(self):
        client = self.client()
        group = self.groups(client)[0]
        kept = group["members"][1]["jobfinder_id"]
        self.post(client, group["fingerprint"], {"type": "resolve", "action": "merge", "kept_jobfinder_id": kept})
        self.assertEqual(client.get("/api/jobs").json()["total"], 687)
        r = self.post(client, group["fingerprint"], {"type": "undo"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(client.get("/api/jobs").json()["total"], 688)
        lines = self.log_lines()
        self.assertEqual([l["type"] for l in lines], ["resolve", "undo"])  # nothing deleted
        self.assertEqual(lines[1]["undoes"], lines[0]["event_id"])
        again = self.post(client, group["fingerprint"], {"type": "undo"})
        self.assertEqual(again.status_code, 409)

    def test_decisions_survive_a_restart(self):
        client = self.client()
        group = self.groups(client)[0]
        kept = group["members"][0]["jobfinder_id"]
        self.post(client, group["fingerprint"], {"type": "resolve", "action": "merge", "kept_jobfinder_id": kept})
        client2 = self.client()  # a fresh app on the same state folder
        self.assertEqual(client2.get("/api/jobs").json()["total"], 687)
        self.assertEqual(client2.get("/api/stats").json()["served"]["exact_duplicate_count_sum"], 845)

    def test_decisions_survive_a_refresh_of_unchanged_data(self):
        client = self.client()
        group = self.groups(client)[0]
        self.post(client, group["fingerprint"], {"type": "resolve", "action": "merge",
                                                 "kept_jobfinder_id": group["members"][0]["jobfinder_id"]})
        self.assertEqual(client.post("/api/refresh").status_code, 200)
        self.assertEqual(client.get("/api/jobs").json()["total"], 687)

    def test_changed_group_returns_to_the_queue(self):
        client = self.client()
        group = self.groups(client)[0]
        title, company = group["members"][0]["title"], group["members"][0]["company"]
        self.post(client, group["fingerprint"], {"type": "resolve", "action": "merge",
                                                 "kept_jobfinder_id": group["members"][0]["jobfinder_id"]})
        self.assertEqual(client.get("/api/jobs").json()["total"], 687)
        # tomorrow's run adds a third posting with the same title and company
        rows = build_845_rows()
        rows.append(_row(846, "https://www.indeed.com/viewjob?jk=fx0846", title, company,
                         "Erie, PA", _details(8), "$25 an hour"))
        _write_db(self.db, rows)
        self.assertEqual(client.post("/api/refresh").status_code, 200)
        body = client.get("/api/duplicates").json()
        back = [g for g in body["groups"] if title in g["members"][0]["title"]][0]
        self.assertEqual(back["size"], 3)
        self.assertEqual(back["state"], "changed")
        self.assertNotEqual(back["fingerprint"], group["fingerprint"])
        self.assertEqual(back["changed_since_decision"]["previous_action"], "merge")
        self.assertEqual(back["changed_since_decision"]["added_jobfinder_ids"], [846])
        self.assertEqual(body["summary"]["changed"], 1)
        # the old decision is set aside: nothing stays folded
        self.assertEqual(client.get("/api/jobs").json()["total"], 689)
        stats = client.get("/api/stats").json()
        self.assertEqual(stats["served"]["exact_duplicate_count_sum"], 846)
        # a fresh decision on the changed group works
        r = self.post(client, back["fingerprint"], {"type": "resolve", "action": "keep"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(client.get("/api/duplicates").json()["summary"]["changed"], 0)

    def test_database_file_is_never_touched(self):
        client = self.client()
        group = self.groups(client)[0]
        self.post(client, group["fingerprint"], {"type": "resolve", "action": "merge",
                                                 "kept_jobfinder_id": group["members"][0]["jobfinder_id"]})
        self.post(client, group["fingerprint"], {"type": "undo"})
        client.post("/api/refresh")
        self.assertEqual(_digest(self.db), self.db_digest)


class WriteRouteGuardTests(ApiBase):
    def test_guards_and_validation(self):
        client = self.client()
        group = self.groups(client)[0]
        fp = group["fingerprint"]
        ids = [m["jobfinder_id"] for m in group["members"]]
        body = {"type": "resolve", "action": "keep"}
        url = f"/api/duplicates/{fp}/events"

        no_header = client.post(url, json=body, headers=ORIGIN)
        self.assertEqual(no_header.status_code, 403)
        evil = client.post(url, json=body, headers={**WRITE, "Origin": "http://evil.example"})
        self.assertEqual(evil.status_code, 403)
        wrong_type = client.post(url, content=json.dumps(body), headers={**WRITE, **ORIGIN, "Content-Type": "text/plain"})
        self.assertEqual(wrong_type.status_code, 415)
        self.assertEqual(self.post(client, "0" * 16, body).status_code, 404)
        self.assertEqual(self.post(client, "not-a-fingerprint", body).status_code, 404)
        self.assertEqual(self.post(client, fp, {"type": "resolve"}).status_code, 400)
        self.assertEqual(self.post(client, fp, {"type": "resolve", "action": "merge"}).status_code, 400)
        self.assertEqual(self.post(client, fp, {"type": "resolve", "action": "merge", "kept_jobfinder_id": 999999}).status_code, 400)
        self.assertEqual(self.post(client, fp, {"type": "resolve", "action": "keep", "kept_jobfinder_id": ids[0]}).status_code, 400)
        self.assertEqual(self.post(client, fp, {"type": "resolve", "action": "delete"}).status_code, 422)
        self.assertEqual(self.post(client, fp, {"type": "resolve", "action": "keep", "member_ids": [1]}).status_code, 422)
        self.assertEqual(self.post(client, fp, {"type": "wipe"}).status_code, 422)
        self.assertEqual(self.post(client, fp, {"type": "undo", "action": "keep"}).status_code, 400)
        self.assertEqual(self.post(client, fp, {"type": "resolve", "action": "keep", "note": "x" * 501}).status_code, 422)
        self.assertEqual(self.log_lines(), [])  # none of the refusals wrote anything

    def test_client_cannot_supply_member_ids(self):
        client = self.client()
        fp = self.groups(client)[0]["fingerprint"]
        r = self.post(client, fp, {"type": "resolve", "action": "keep", "member_ids": [1, 2]})
        self.assertEqual(r.status_code, 422)

    def test_writes_land_only_in_the_resolutions_file(self):
        client = self.client()
        group = self.groups(client)[0]
        self.post(client, group["fingerprint"], {"type": "resolve", "action": "dismiss"})
        self.assertEqual([p.name for p in self.state_dir.iterdir()], ["career-navigator.db"])

    def test_synthetic_backend_cannot_write(self):
        with mock.patch.dict("os.environ", {"JOBFINDER_DB_PATH": "", "CN_STATE_DIR": ""}):
            with TestClient(create_app(reload_interval=0)) as client:
                self.assertFalse(client.get("/api/duplicates").json()["enabled"])
                r = client.post("/api/duplicates/" + "0" * 16 + "/events", json={"type": "undo"},
                                headers={**WRITE, **ORIGIN})
                self.assertEqual(r.status_code, 503)

    def test_cors_preflight_allows_the_write_header_from_the_dashboard_only(self):
        client = self.client()
        url = "/api/duplicates/" + "0" * 16 + "/events"
        ok = client.options(url, headers={"Origin": "http://127.0.0.1:5173",
                                          "Access-Control-Request-Method": "POST",
                                          "Access-Control-Request-Headers": "content-type,x-cn-write"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.headers.get("access-control-allow-origin"), "http://127.0.0.1:5173")
        evil = client.options(url, headers={"Origin": "http://evil.example",
                                            "Access-Control-Request-Method": "POST",
                                            "Access-Control-Request-Headers": "content-type,x-cn-write"})
        self.assertNotEqual(evil.headers.get("access-control-allow-origin"), "http://evil.example")


class DamagedFileTests(ApiBase):
    def test_garbage_lines_are_counted_and_the_rest_still_applies(self):
        # A legacy JSONL file with a torn last line is imported on first use:
        # the torn line is counted, never fatal, and later writes still apply.
        self.state_dir.mkdir(parents=True)
        self.log_path.write_bytes(b'{"torn":')
        client = self.client()
        group = self.groups(client)[0]
        self.post(client, group["fingerprint"], {"type": "resolve", "action": "merge",
                                                 "kept_jobfinder_id": group["members"][0]["jobfinder_id"]})
        self.post(client, self.groups(client)[1]["fingerprint"], {"type": "resolve", "action": "keep"})
        self.assertEqual(self.log_path.read_bytes(), b'{"torn":')   # legacy file never modified
        client2 = self.client()
        stats = client2.get("/api/stats").json()
        self.assertEqual(stats["duplicates"]["store"]["skipped_lines"], 1)
        self.assertEqual(stats["duplicates"]["merge"], 1)
        self.assertEqual(stats["duplicates"]["keep"], 1)


if __name__ == "__main__":
    unittest.main()
