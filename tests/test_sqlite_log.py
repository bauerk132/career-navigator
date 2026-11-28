import json
import multiprocessing
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from career_navigator.sqlite_log import DB_FILENAME, SqliteLog
from career_navigator.state_store import StateError


def _proc_append(folder, n, tag):
    log = SqliteLog(Path(folder), "applications", "applications.jsonl")
    for i in range(n):
        log.append({"t": tag, "i": i})


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def names(self):
        return sorted(p.name for p in self.dir.iterdir())

    def test_read_no_db(self):
        self.assertEqual(SqliteLog(self.dir, "applications", "applications.jsonl").read(), ([], 0, None))
        self.assertEqual(self.names(), [])

    def test_round_trip_and_sidecars_gone(self):
        log = SqliteLog(self.dir, "applications")
        recs = [{"a": 1}, {"note": "café \U0001F600"}, {"z": [1, 2]}]
        for r in recs:
            log.append(r)
        self.assertEqual(log.read()[0], recs)
        self.assertEqual(self.names(), [DB_FILENAME])

    def test_signature(self):
        log = SqliteLog(self.dir, "applications")
        self.assertIsNone(log.signature())
        log.append({"a": 1})
        s1 = log.signature()
        log.append({"a": 2})
        self.assertNotEqual(s1, log.signature())

    def test_oversized(self):
        with self.assertRaises(StateError):
            SqliteLog(self.dir, "applications").append({"big": "x" * 20000})
        self.assertEqual(self.names(), [])

    def test_triggers(self):
        SqliteLog(self.dir, "applications").append({"a": 1})
        conn = sqlite3.connect(self.dir / DB_FILENAME)
        try:
            for sql in ("UPDATE events SET record='{}'", "DELETE FROM events"):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql)
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(SqliteLog(self.dir, "applications").read()[0], [{"a": 1}])

    def test_corrupt(self):
        p = self.dir / DB_FILENAME
        p.write_bytes(b"not a database")
        log = SqliteLog(self.dir, "applications")
        with self.assertRaises(StateError):
            log.read()
        with self.assertRaises(StateError):
            log.append({"a": 1})
        with self.assertRaises(StateError):
            log.signature()
        self.assertEqual(p.read_bytes(), b"not a database")
        self.assertEqual(self.names(), [DB_FILENAME])

    def test_threads(self):
        def work(t):
            log = SqliteLog(self.dir, "applications")
            for i in range(25):
                log.append({"t": t, "i": i})
        ts = [threading.Thread(target=work, args=(t,)) for t in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(SqliteLog(self.dir, "applications").read()[0]), 100)
        conn = sqlite3.connect(self.dir / DB_FILENAME)
        seqs = [r[0] for r in conn.execute("SELECT seq FROM events ORDER BY rowid")]
        conn.close()
        self.assertEqual(seqs, sorted(set(seqs)))

    def test_processes(self):
        ctx = multiprocessing.get_context("spawn")
        ps = [ctx.Process(target=_proc_append, args=(str(self.dir), 25, t)) for t in range(4)]
        [p.start() for p in ps]
        [p.join(60) for p in ps]
        self.assertEqual([p.exitcode for p in ps], [0, 0, 0, 0])
        self.assertEqual(len(SqliteLog(self.dir, "applications").read()[0]), 100)

    def test_legacy_import_torn(self):
        legacy = self.dir / "review_decisions.jsonl"
        legacy.write_bytes(b'{"a":1}\n[1,2]\n"str"\n{"broken":\n{"b":2}')
        before = legacy.read_bytes()
        log = SqliteLog(self.dir, "review_decisions", "review_decisions.jsonl")
        recs, skipped, _ = log.read()
        self.assertEqual((recs, skipped), ([{"a": 1}, {"b": 2}], 3))
        self.assertEqual(SqliteLog(self.dir, "review_decisions", "review_decisions.jsonl").read()[:2],
                         ([{"a": 1}, {"b": 2}], 3))
        log.append({"c": 3})
        self.assertEqual(log.read()[:2], ([{"a": 1}, {"b": 2}, {"c": 3}], 3))
        self.assertEqual(legacy.read_bytes(), before)

    def test_legacy_after_first_append_ignored(self):
        log = SqliteLog(self.dir, "applications", "applications.jsonl")
        log.append({"a": 1})
        (self.dir / "applications.jsonl").write_text('{"old":1}\n', encoding="utf-8")
        self.assertEqual(SqliteLog(self.dir, "applications", "applications.jsonl").read()[:2], ([{"a": 1}], 0))

    def test_concurrent_first_use_imports_once(self):
        (self.dir / "applications.jsonl").write_text("".join(json.dumps({"i": i}) + "\n" for i in range(5)))
        out = []
        def work():
            out.append(len(SqliteLog(self.dir, "applications", "applications.jsonl").read()[0]))
        ts = [threading.Thread(target=work) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(out, [5, 5, 5, 5])

    def test_import_per_log_and_no_db_on_read(self):
        (self.dir / "review_decisions.jsonl").write_text('{"r":1}\n')
        self.assertEqual(SqliteLog(self.dir, "applications", "applications.jsonl").read(), ([], 0, None))
        self.assertFalse((self.dir / DB_FILENAME).exists())
        self.assertEqual(SqliteLog(self.dir, "review_decisions", "review_decisions.jsonl").read()[0], [{"r": 1}])
        self.assertEqual(SqliteLog(self.dir, "applications", "applications.jsonl").read()[:2], ([], 0))


if __name__ == "__main__":
    unittest.main()
