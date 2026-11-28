import contextlib
import datetime
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from career_navigator import __main__ as cli
from career_navigator.app import create_app
from career_navigator.sqlite_log import DB_FILENAME, LEGACY_FILENAMES, SqliteLog
from career_navigator.state_store import StateError
from career_navigator.state_tools import backup_if_due, export_state

from test_review_decisions import _fixture_rows
from test_live_wiring import _write_db

DAY = datetime.date(2026, 11, 28)


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def fill(self):
        data = {"applications": [{"a": 1}, {"a": 2}], "duplicate_resolutions": [{"d": "café"}],
                "review_decisions": []}
        for name, recs in data.items():
            log = SqliteLog(self.state, name)
            for r in recs:
                log.append(r)
        return data

    def test_backup_none_when_no_db(self):
        self.assertIsNone(backup_if_due(self.root, today=DAY))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_backup_once_per_day(self):
        self.fill()
        made = backup_if_due(self.state, today=DAY)
        self.assertEqual(made, self.state / "backups" / "career-navigator.2026-11-28.db")
        self.assertIsNone(backup_if_due(self.state, today=DAY))
        conn = sqlite3.connect(made)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)
        finally:
            conn.close()
        self.assertEqual(sorted(p.name for p in (self.state / "backups").iterdir()), [made.name])

    def test_prune(self):
        self.fill()
        b = self.state / "backups"
        b.mkdir()
        for i in range(16):
            (b / f"career-navigator.2026-10-{i + 1:02d}.db").write_bytes(b"x")
        (b / "notes.txt").write_text("keep me")
        backup_if_due(self.state, today=DAY, keep=14)
        dated = sorted(p.name for p in b.iterdir() if p.suffix == ".db")
        self.assertEqual(len(dated), 14)
        self.assertEqual(dated[-1], "career-navigator.2026-11-28.db")
        self.assertEqual(dated[0], "career-navigator.2026-10-04.db")
        self.assertTrue((b / "notes.txt").exists())

    def test_export_round_trip(self):
        data = self.fill()
        out = self.root / "out"
        self.assertEqual(export_state(self.state, out), {k: len(v) for k, v in data.items()})
        for name, recs in data.items():
            raw = (out / LEGACY_FILENAMES[name]).read_bytes()
            self.assertEqual(raw, b"".join(json.dumps(r, ensure_ascii=True, separators=(",", ":"),
                                                      sort_keys=True).encode() + b"\n" for r in recs))

    def test_export_refuses_state_folder(self):
        self.fill()
        legacy = self.state / "applications.jsonl"
        legacy.write_bytes(b'{"old":1}\n')
        with self.assertRaises(StateError):
            export_state(self.state, self.state)
        self.assertEqual(legacy.read_bytes(), b'{"old":1}\n')

    def test_export_no_db(self):
        with self.assertRaises(StateError):
            export_state(self.state, self.root / "out")
        self.assertFalse(self.state.exists())

    def test_cli(self):
        self.fill()
        out = self.root / "out"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["export-state", str(out), "--state-dir", str(self.state)]), 0)
        self.assertTrue((out / "applications.jsonl").exists())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main([]), 0)
        self.assertIn("synthetic core demo", buf.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["export-state", str(self.root / "o2"), "--state-dir", str(self.root / "none")]), 1)

    def test_startup_backup_live_only(self):
        self.fill()
        db = self.root / "jobfinder.db"
        _write_db(db, _fixture_rows())
        with TestClient(create_app(db_path=str(db), reload_interval=0, state_dir=self.state)):
            pass
        self.assertEqual(len(list((self.state / "backups").iterdir())), 1)
        other = self.root / "state2"
        SqliteLog(other, "applications").append({"a": 1})
        with mock.patch.dict(os.environ, {"JOBFINDER_DB_PATH": ""}):
            with TestClient(create_app(reload_interval=0, state_dir=other)):
                pass
        self.assertFalse((other / "backups").exists())


if __name__ == "__main__":
    unittest.main()
