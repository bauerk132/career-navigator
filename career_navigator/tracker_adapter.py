"""Read-only adapter for Kyle's application tracker CSV (Phase 3c WS4).

``job_tracker.csv`` is Kyle's own record of jobs he is applying to. This module
only ever opens it for reading: it never writes, renames or locks the file.
Changes Kyle makes on the dashboard are saved as events in the state folder
(see ``applications.py``) and only reach a CSV through "Export to tracker",
which writes a NEW dated copy.

Every row is kept. A row with a date that cannot be read, an unknown status or
a missing job id is reported in ``problems`` and still shown, never dropped.

Dates in the tracker come in two styles (``10/2/2026`` and ``2026-10-16``);
both are normalized to ISO ``YYYY-MM-DD``. The raw text is kept beside it.

The tracker path is chosen by the explicit argument, then the CN_TRACKER_CSV
environment variable, then (live mode only) ``~/job-search/job_tracker.csv``
when that file exists.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

TRACKER_ENV = "CN_TRACKER_CSV"
DEFAULT_TRACKER = Path("job-search") / "job_tracker.csv"
MAX_TRACKER_BYTES = 10_000_000

# The tracker's own columns, in its own order (export writes them back in it).
COLUMNS = (
    "job_id", "date_found", "assigned_agent", "category", "title", "company",
    "location", "work_type", "pay", "linkedin_url", "apply_url", "apply_platform",
    "description_summary", "key_requirements", "fit_notes", "resume_file",
    "cover_letter_file", "status", "date_applied", "confirmation_number",
    "heard_back", "heard_back_date", "response_type", "next_step",
    "follow_up_date", "notes", "last_updated_by", "last_updated",
)
REQUIRED_COLUMNS = ("job_id", "title", "company", "status")
DATE_COLUMNS = ("date_found", "date_applied", "heard_back_date", "follow_up_date", "last_updated")
# Served to the local dashboard only; never logged or put in a gate report.
PRIVATE_COLUMNS = ("confirmation_number", "notes")

STATUSES = ("Ready", "In Progress", "Applied", "Heard Back", "Interview",
            "Rejected", "Closed", "Passed")
_STATUS_LOOKUP = {s.casefold(): s for s in STATUSES}

_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$")


class TrackerError(RuntimeError):
    """The tracker file cannot be read safely (missing, too big, wrong header)."""


def resolve_tracker_path(explicit=None, live: bool = False) -> Path | None:
    if explicit:
        return Path(explicit)
    from_env = os.environ.get(TRACKER_ENV, "").strip()
    if from_env:
        return Path(from_env)
    if live:
        default = Path.home() / DEFAULT_TRACKER
        if default.is_file():
            return default
    return None


def parse_date(text) -> tuple[str | None, bool]:
    """(ISO date or None, ok). Blank is (None, True); unreadable is (None, False)."""
    value = str(text or "").strip()
    if not value:
        return None, True
    match = _US_DATE.match(value)
    try:
        if match:
            month, day, year = (int(g) for g in match.groups())
            return date(year, month, day).isoformat(), True
        match = _ISO_DATE.match(value)
        if match:
            year, month, day = (int(g) for g in match.groups())
            return date(year, month, day).isoformat(), True
    except ValueError:
        return None, False
    return None, False


def normalize_status(text) -> str | None:
    return _STATUS_LOOKUP.get(str(text or "").strip().casefold())


@dataclass(frozen=True)
class TrackerRow:
    row_number: int        # 1-based data row (header is row 0); spreadsheet row = row_number + 1
    key: str               # stable identity used by application events
    values: dict           # every tracker column, raw text as read
    dates: dict            # column -> ISO date or None
    status: str | None     # normalized status, None when unknown
    problems: tuple        # (column, issue) pairs


@dataclass(frozen=True)
class TrackerImport:
    path: Path
    rows: tuple
    header: tuple
    extra_columns: tuple   # columns in the file that are not tracker columns (kept, exported)
    sha256: str
    signature: tuple       # (mtime_ns, size)
    problems: tuple        # dicts: row_number, job_id, column, issue
    encoding: str


def _row_key(values: dict, row_number: int, seen: set) -> str:
    job_id = values.get("job_id", "").strip()
    if job_id and "tracker:" + job_id not in seen:
        return "tracker:" + job_id
    # No job id, or a repeated one: key on the row's own identity so a
    # decision never lands on the wrong row. Row number is a last resort.
    basis = "\x1f".join(values.get(k, "").strip().casefold()
                        for k in ("title", "company", "linkedin_url", "apply_url"))
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    key = f"tracker-row:{digest}"
    return key if key not in seen else f"{key}:{row_number}"


def file_signature(path: Path) -> tuple | None:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def load_tracker(path) -> TrackerImport:
    """Read the tracker once (bytes, then parse in memory). Never writes."""
    path = Path(path)
    signature = file_signature(path)
    if signature is None:
        raise TrackerError(f"tracker file not found: {path.name}")
    if signature[1] > MAX_TRACKER_BYTES:
        raise TrackerError(f"{path.name} is larger than {MAX_TRACKER_BYTES} bytes; refusing to load it")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise TrackerError(f"cannot read {path.name}: {exc}") from exc
    sha = hashlib.sha256(raw).hexdigest()
    try:
        text, encoding = raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("cp1252", errors="replace"), "cp1252"
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = tuple(h.strip() for h in next(reader))
    except StopIteration:
        raise TrackerError(f"{path.name} is empty") from None
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise TrackerError(f"{path.name} has no {', '.join(missing)} column")
    extra = tuple(h for h in header if h not in COLUMNS)

    rows, problems, seen = [], [], set()
    for number, cells in enumerate(reader, start=1):
        if not any(c.strip() for c in cells):
            continue  # a fully blank line is not a row
        row_problems = []
        if len(cells) != len(header):
            row_problems.append(("*", f"has {len(cells)} cells, header has {len(header)}"))
        values = {h: (cells[i] if i < len(cells) else "") for i, h in enumerate(header)}
        for column in COLUMNS:
            values.setdefault(column, "")
        dates = {}
        for column in DATE_COLUMNS:
            iso, ok = parse_date(values.get(column))
            dates[column] = iso
            if not ok:
                row_problems.append((column, "date could not be read"))
        status = normalize_status(values.get("status"))
        if status is None:
            row_problems.append(("status", "not a known status" if values.get("status", "").strip()
                                 else "blank status"))
        if not values.get("job_id", "").strip():
            row_problems.append(("job_id", "blank job id"))
        elif "tracker:" + values["job_id"].strip() in seen:
            row_problems.append(("job_id", "job id repeats an earlier row"))
        key = _row_key(values, number, seen)
        seen.add(key)
        rows.append(TrackerRow(number, key, values, dates, status, tuple(row_problems)))
        for column, issue in row_problems:
            # The problem list names the column, never the cell's text, so it
            # is safe to show anywhere (private columns are never parsed here).
            problems.append({"row_number": number, "job_id": values.get("job_id", "").strip(),
                             "column": column, "issue": issue})
    return TrackerImport(path, tuple(rows), header, extra, sha, signature, tuple(problems), encoding)


def today_iso() -> str:
    return datetime.now().date().isoformat()
