"""Daily backup and JSONL export of career-navigator.db (V3).

backup_if_due copies the live database with SQLite's online backup API into
``backups/career-navigator.YYYY-MM-DD.db`` (at most one per day, newest
``keep`` kept). export_state writes each log back out as JSONL, byte for
byte the format the old ``.jsonl`` files used. Neither ever writes to the
live database, the legacy ``.jsonl`` files, or creates a database.
"""
from __future__ import annotations

import datetime
import os
import re
import sqlite3
from pathlib import Path

from .sqlite_log import (
    DB_FILENAME, LEGACY_FILENAMES, LOG_NAMES, check_schema, db_file_present, open_db, schema_is_empty,
)
from .state_store import StateError

BACKUP_DIRNAME = "backups"
_BACKUP_NAME = re.compile(r"^career-navigator\.\d{4}-\d{2}-\d{2}\.db$")


def backup_if_due(state_dir, today: datetime.date | None = None, keep: int = 14) -> Path | None:
    """Make today's backup if it does not exist yet; return its path, or None
    when there is no database or today's backup already exists."""
    state_dir = Path(state_dir)
    db_path = state_dir / DB_FILENAME
    if not db_file_present(db_path):
        return None
    today = today or datetime.date.today()
    backup_dir = state_dir / BACKUP_DIRNAME
    final = backup_dir / f"career-navigator.{today.isoformat()}.db"
    if final.exists():
        return None
    tmp = final.with_name(final.name + ".tmp")
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        if tmp.exists():
            tmp.unlink()  # left over from an interrupted backup
        src = open_db(db_path, create=False)
        try:
            if schema_is_empty(src):
                return None  # another connection is still creating it
            check_schema(src)
            dst = sqlite3.connect(tmp, isolation_level=None)
            try:
                src.backup(dst)
                dst.execute("PRAGMA journal_mode = DELETE")  # self-contained copy, no -wal
            finally:
                dst.close()
        finally:
            src.close()
        os.replace(tmp, final)
    except (sqlite3.Error, OSError) as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise StateError(f"cannot back up {DB_FILENAME}: {exc}") from exc
    _prune(backup_dir, keep)
    return final


def _prune(backup_dir: Path, keep: int) -> None:
    dated = sorted(p for p in backup_dir.iterdir() if p.is_file() and _BACKUP_NAME.match(p.name))
    for old in dated[:-keep] if keep > 0 else []:
        try:
            old.unlink()
        except OSError:
            pass  # a locked old backup is retried on the next day's run


def export_state(state_dir, out_dir) -> dict[str, int]:
    """Write <log>.jsonl for each log into out_dir. Returns {log: count}."""
    state_dir, out_dir = Path(state_dir), Path(out_dir)
    if out_dir.resolve() == state_dir.resolve():
        raise StateError("export folder must not be the state folder")
    db_path = state_dir / DB_FILENAME
    if not db_file_present(db_path):
        raise StateError(f"no {DB_FILENAME} in {state_dir}")
    try:
        conn = open_db(db_path, create=False)
        try:
            if schema_is_empty(conn):
                raise StateError(f"{DB_FILENAME} in {state_dir} holds no saved state yet")
            check_schema(conn)
            conn.execute("BEGIN")  # one snapshot for all three logs
            texts = {
                name: [row[0] for row in conn.execute(
                    "SELECT record FROM events WHERE log = ? ORDER BY seq", (name,))]
                for name in LOG_NAMES
            }
            conn.execute("COMMIT")
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        raise StateError(f"cannot read {DB_FILENAME}: {exc}") from exc
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in LOG_NAMES:
            final = out_dir / LEGACY_FILENAMES[name]
            tmp = final.with_name(final.name + ".tmp")
            with open(tmp, "wb") as handle:
                handle.write("".join(text + "\n" for text in texts[name]).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, final)
    except OSError as exc:
        raise StateError(f"cannot write the export: {exc}") from exc
    return {name: len(texts[name]) for name in LOG_NAMES}
