"""Append-only decision logs in one local SQLite file (V3).

Kyle's saved decisions (applications board events, duplicate resolutions,
review decisions) live in ``career-navigator.db`` in the state folder, one
row per event in a single ``events`` table tagged by log name. The record
text is exactly the JSON line the old ``.jsonl`` files held. Triggers make
the table append-only inside the database itself: a change of mind is a new
event (an "undo"), never an edit.

Each operation opens its own short-lived connection (WAL, synchronous=FULL,
5 s busy timeout) and closes it, so the folder holds only the .db file when
idle. Reading never creates anything: with no database and no legacy
``.jsonl`` file a read returns empty. A legacy file is imported once per
log, in the same BEGIN IMMEDIATE transaction that records the
``import:<log>`` marker in ``meta``; the first append records the marker
too, so a legacy file that appears later is never merged into newer
history. Lines that were not JSON objects are counted in the marker and
reported as skipped. The legacy file is never modified. A damaged or
foreign database raises StateError; it is never replaced or read as empty.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .state_store import MAX_RECORD_BYTES, JsonlLog, StateError

DB_FILENAME = "career-navigator.db"
LOG_NAMES = ("applications", "duplicate_resolutions", "review_decisions")
LEGACY_FILENAMES = {
    "applications": "applications.jsonl",
    "duplicate_resolutions": "duplicate_resolutions.jsonl",
    "review_decisions": "review_decisions.jsonl",
}
SCHEMA_VERSION = "1"
BUSY_TIMEOUT_SECONDS = 5.0

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS events ("
    " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " log TEXT NOT NULL,"
    " record TEXT NOT NULL,"
    " created_utc TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS events_log_seq ON events (log, seq)",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events"
    " BEGIN SELECT RAISE(ABORT, 'events are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events"
    " BEGIN SELECT RAISE(ABORT, 'events are append-only'); END",
    "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '" + SCHEMA_VERSION + "')",
)
_INSERT = "INSERT INTO events (log, record, created_utc) VALUES (?, ?, ?)"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def encode_record(record: dict) -> str:
    """The exact text the old JSONL files held for one record (no newline)."""
    return json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def db_file_present(db_path: Path) -> bool:
    """A missing or zero-byte file is 'no database yet' (SQLite treats a
    zero-byte file as an empty database)."""
    try:
        return Path(db_path).stat().st_size > 0
    except FileNotFoundError:
        return False


def _set_wal(conn: sqlite3.Connection) -> None:
    """PRAGMA journal_mode=WAL returns SQLITE_BUSY at once, without using the
    busy timeout, while another connection is creating the file, so retry it
    until the timeout. A damaged or foreign file fails here with 'file is not
    a database' before anything is written to it."""
    deadline = time.monotonic() + BUSY_TIMEOUT_SECONDS
    while True:
        try:
            conn.execute("PRAGMA journal_mode = WAL").fetchone()
            return
        except sqlite3.OperationalError as exc:
            busy = (getattr(exc, "sqlite_errorcode", 0) & 0xFF) in (5, 6)  # SQLITE_BUSY, SQLITE_LOCKED
            if not busy or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def open_db(db_path: Path, create: bool) -> sqlite3.Connection:
    """One connection with explicit transactions (isolation_level=None: we
    issue BEGIN/COMMIT ourselves). create=False never makes a file: mode=rw
    fails when it is missing. Raises sqlite3.Error. Caller must close()."""
    conn = sqlite3.connect(
        Path(db_path).absolute().as_uri() + ("?mode=rwc" if create else "?mode=rw"),
        uri=True,
        timeout=BUSY_TIMEOUT_SECONDS,
        isolation_level=None,
    )
    try:
        _set_wal(conn)
        conn.execute("PRAGMA synchronous = FULL")
    except BaseException:
        conn.close()
        raise
    return conn


def schema_is_empty(conn: sqlite3.Connection) -> bool:
    """True for a file with no tables yet (being created by another
    connection, or a zero-byte file): nothing has been committed."""
    return conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0


def check_schema(conn: sqlite3.Connection) -> None:
    """Raise StateError unless this is our schema. A foreign SQLite file
    (no meta table) raises sqlite3.OperationalError, which callers map."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None or row[0] != SCHEMA_VERSION:
        raise StateError(f"{DB_FILENAME} has an unknown schema version {row[0] if row else None!r}")


class SqliteLog:
    """One named append-only log inside career-navigator.db. Same interface
    as JsonlLog: read() -> (records, skipped, signature), append(record)."""

    def __init__(self, state_dir, name: str, legacy_filename: str | None = None):
        if name not in LOG_NAMES:
            raise ValueError(f"unknown log name {name!r}")
        self.state_dir = Path(state_dir)
        self.name = name
        self.db_path = self.state_dir / DB_FILENAME
        self.legacy_path = self.state_dir / legacy_filename if legacy_filename else None
        self._marker_key = f"import:{name}"
        self._lock = threading.Lock()
        self._cache_signature = None
        self._cache_value: tuple[list[dict], int] = ([], 0)

    def _legacy_present(self) -> bool:
        return self.legacy_path is not None and self.legacy_path.is_file()

    def _marker(self, conn) -> dict | None:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (self._marker_key,)).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except ValueError:
            value = None
        if not isinstance(value, dict):
            raise StateError(f"{DB_FILENAME} has a damaged import marker for {self.name}")
        return value

    def _write(self, line: str | None) -> None:
        """One BEGIN IMMEDIATE transaction: create the schema if needed,
        import the legacy file if this log has no marker yet (re-checked
        under the write lock, so two writers never import twice), record the
        marker, then insert ``line`` when given. All or nothing."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        conn = open_db(self.db_path, create=True)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _SCHEMA:
                    conn.execute(statement)
                check_schema(conn)
                if self._marker(conn) is None:
                    imported, skipped = 0, 0
                    if self._legacy_present():
                        records, skipped, _ = JsonlLog(self.legacy_path).read()
                        now = _utc_now()
                        conn.executemany(_INSERT, [(self.name, encode_record(r), now) for r in records])
                        imported = len(records)
                    marker = {"imported": imported, "skipped": skipped, "at": _utc_now()}
                    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                                 (self._marker_key, json.dumps(marker, sort_keys=True)))
                if line is not None:
                    conn.execute(_INSERT, (self.name, line, _utc_now()))
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def _open_existing(self):
        """A connection to a database that holds our schema, or None when
        there is none yet. Raises StateError / sqlite3.Error when damaged."""
        if not db_file_present(self.db_path):
            return None
        conn = open_db(self.db_path, create=False)
        try:
            if schema_is_empty(conn):
                conn.close()
                return None
            check_schema(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    def _signature(self, conn) -> tuple[int, int, int]:
        count, max_seq = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(seq), 0) FROM events WHERE log = ?", (self.name,)
        ).fetchone()
        marker = self._marker(conn) or {}
        return (count, max_seq, int(marker.get("skipped") or 0))

    def signature(self):
        """(event count, max seq, legacy lines skipped) for this log, or None
        when there is no database yet."""
        with self._lock:
            try:
                conn = self._open_existing()
                if conn is None:
                    return None
                try:
                    return self._signature(conn)
                finally:
                    conn.close()
            except (sqlite3.Error, OSError) as exc:
                raise StateError(f"cannot read {DB_FILENAME}: {exc}") from exc

    def read(self) -> tuple[list[dict], int, object]:
        """Return (records in seq order, skipped count, signature). Cached
        until the signature changes."""
        with self._lock:
            try:
                conn = self._open_existing()
                try:
                    if self._legacy_present() and (conn is None or self._marker(conn) is None):
                        if conn is not None:
                            conn.close()
                            conn = None
                        self._write(None)  # first use with a legacy file: import it once
                        conn = self._open_existing()
                    if conn is None:
                        self._cache_signature = None
                        self._cache_value = ([], 0)
                        return [], 0, None
                    conn.execute("BEGIN")  # one consistent snapshot
                    signature = self._signature(conn)
                    if signature == self._cache_signature:
                        records, skipped = self._cache_value
                        return list(records), skipped, signature
                    texts = [row[0] for row in conn.execute(
                        "SELECT record FROM events WHERE log = ? ORDER BY seq", (self.name,))]
                    conn.execute("COMMIT")
                finally:
                    if conn is not None:
                        conn.close()
            except (sqlite3.Error, OSError) as exc:
                raise StateError(f"cannot read {DB_FILENAME}: {exc}") from exc
            records: list[dict] = []
            skipped = signature[2]
            for text in texts:
                try:
                    value = json.loads(text)
                except (ValueError, RecursionError):
                    value = None
                if isinstance(value, dict):
                    records.append(value)
                else:
                    skipped += 1
            self._cache_signature = signature
            self._cache_value = (records, skipped)
            return list(records), skipped, signature

    def append(self, record: dict) -> None:
        """Append one record durably. Raises StateError on any failure."""
        line = encode_record(record)
        if len(line.encode("utf-8")) > MAX_RECORD_BYTES:
            raise StateError("record is too large to store")
        with self._lock:
            self._cache_signature = None
            try:
                self._write(line)
            except (sqlite3.Error, OSError) as exc:
                raise StateError(f"cannot write {DB_FILENAME}: {exc}") from exc
