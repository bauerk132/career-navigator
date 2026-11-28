"""Freshness classification for the live JobFinder snapshot (Phase 3a WS2).

Stdlib only. Nothing here writes to jobfinder.db: every database read goes
through a SQLite read-only URI with ``PRAGMA query_only = ON``.

JobFinder timestamps are naive US Eastern local time. "Ran today" means the
parsed ``state.last_updated`` falls on today's Eastern date. JobFinder runs
daily at 09:00 ET; a run is expected by 09:00 and is considered late until
10:30 ET.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

RUN_TIME = time(9, 0)
GRACE_TIME = time(10, 30)
_EASTERN = ZoneInfo("America/New_York")

# Severity for choosing the single headline state (higher wins).
_SEVERITY = {"degraded": 5, "stale": 4, "behind": 3, "late": 2, "partial": 1, "fresh": 0}
_LEVEL = {
    "degraded": "red",
    "stale": "red",
    "behind": "amber",
    "late": "amber",
    "partial": "amber",
    "fresh": "ok",
}


def parse_eastern(value):
    """Parse a JobFinder timestamp into an aware datetime.

    JobFinder stores naive US Eastern local time ("2026-11-19 09:00" or
    ISO "2026-11-20T09:00:04"); a value that already carries an offset is
    used as-is. Returns None when the value is missing or unparseable.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_EASTERN)
    return parsed


def _raw(value):
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def classify(last_updated, *, now=None, sources=None, db_last_updated=None, degraded=False) -> dict:
    """Classify how fresh the loaded JobFinder snapshot is.

    ``now`` must be an aware datetime (a naive one is taken as UTC); it
    defaults to the current UTC time. Callers in tests pass a fixed ``now``.

    Base rule (evaluated in ET; ``today`` is ET date of ``now``):

    ==========================================================  =========  ======
    condition                                                   state      level
    ==========================================================  =========  ======
    last_updated missing or unparseable                         stale      red
    last_updated date == today                                  fresh      ok
    not today, now < 09:00, last_updated date == yesterday      fresh      ok
    not today, now < 09:00, otherwise                           stale      red
    not today, 09:00 <= now < 10:30, date == yesterday          late       amber
    not today, 09:00 <= now < 10:30, otherwise                  stale      red
    not today, now >= 10:30                                     stale      red
    ==========================================================  =========  ======

    Overlays each add a reason; the headline ``state`` is the most severe
    triggered condition, ordered degraded > stale > behind > late > partial
    > fresh, and ``level`` follows the state:

    * ``degraded=True`` -> degraded (red)
    * ``db_last_updated`` parses and is newer than ``last_updated`` -> behind (amber)
    * any ``sources[i]["ok"] is False`` -> partial (amber)

    Returns a dict with keys state, level, reasons, last_updated, run_date
    (YYYY-MM-DD or None), expected_run, grace_until, checked_at (UTC ISO),
    db_last_updated.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_et = now.astimezone(_EASTERN)
    today = now_et.date()
    yesterday = today - timedelta(days=1)

    parsed = parse_eastern(last_updated)
    run_date = parsed.astimezone(_EASTERN).date() if parsed is not None else None

    triggered = []  # (state, reason)
    if parsed is None:
        triggered.append(("stale", "last_updated missing or unparseable"))
    elif run_date == today:
        triggered.append(("fresh", f"JobFinder ran today at {parsed.astimezone(_EASTERN):%H:%M} ET"))
    elif now_et.time() < RUN_TIME:
        if run_date == yesterday:
            triggered.append(("fresh", "last run was yesterday; today's 9:00 AM ET run is not due yet"))
        else:
            triggered.append(("stale", f"last run {run_date.isoformat()} is older than yesterday's run"))
    elif now_et.time() < GRACE_TIME:
        if run_date == yesterday:
            triggered.append(("late", "today's 9:00 AM ET run has not landed yet (grace until 10:30 AM ET)"))
        else:
            triggered.append(("stale", f"no run today; last run {run_date.isoformat()} is older than yesterday"))
    else:
        triggered.append(("stale", f"no run since the 10:30 AM ET grace period; last run {run_date.isoformat() if run_date else 'unknown'}"))

    if degraded:
        triggered.append(("degraded", "live backend is serving the last good snapshot after a failed reload"))

    db_parsed = parse_eastern(db_last_updated)
    if db_parsed is not None and parsed is not None and db_parsed > parsed:
        triggered.append((
            "behind",
            f"jobfinder.db has newer data ({_raw(db_last_updated)}) than the loaded snapshot ({_raw(last_updated)})",
        ))

    for source in sources or []:
        if isinstance(source, dict) and source.get("ok") is False:
            triggered.append(("partial", f"source {source.get('name')} failed in the last run"))

    state = max((s for s, _ in triggered), key=lambda s: _SEVERITY[s])
    return {
        "state": state,
        "level": _LEVEL[state],
        "reasons": [reason for _, reason in triggered],
        "last_updated": _raw(last_updated),
        "run_date": run_date.isoformat() if run_date is not None else None,
        "expected_run": datetime.combine(today, RUN_TIME, tzinfo=_EASTERN).isoformat(),
        "grace_until": datetime.combine(today, GRACE_TIME, tzinfo=_EASTERN).isoformat(),
        "checked_at": now.astimezone(timezone.utc).isoformat(),
        "db_last_updated": _raw(db_last_updated),
    }


def probe_db(db_path) -> dict:
    """Cheap read-only look at jobfinder.db: file mtime/size and state.last_updated.

    Never raises. Failures are reported in the ``error`` key. The database is
    opened with a SQLite read-only URI and ``PRAGMA query_only = ON``.
    """
    result = {"mtime": None, "size": None, "last_updated": None, "error": None}
    try:
        stat = os.stat(db_path)
    except OSError as exc:
        result["error"] = f"cannot stat database: {exc}"
        return result
    result["mtime"] = stat.st_mtime
    result["size"] = stat.st_size
    conn = None
    try:
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1)
        conn.execute("PRAGMA query_only = ON")
        row = conn.execute("SELECT value FROM state WHERE key = ?", ("last_updated",)).fetchone()
        value = row[0] if row else None
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="strict")
        result["last_updated"] = _raw(value) or None
    except (sqlite3.Error, UnicodeDecodeError, OSError, ValueError) as exc:
        result["error"] = f"cannot read database state: {exc}"
    finally:
        if conn is not None:
            conn.close()
    return result


def task_running(task_name=r"\JobFinder daily refresh") -> bool | None:
    """True when the scheduled task is Running, False when it exists and is not
    running, None when the state cannot be determined (schtasks error, missing
    task, unexpected output). Never raises."""
    try:
        completed = subprocess.run(
            ["schtasks", "/query", "/tn", task_name, "/fo", "LIST"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    for line in (completed.stdout or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("Status:"):
            status = stripped.split(":", 1)[1].strip()
            return status == "Running"
    return None
