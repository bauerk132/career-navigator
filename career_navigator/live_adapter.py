"""Read-only scaffolding for importing a JobFinder SQLite snapshot.

This module is intentionally separate from :mod:`adapters`. It never imports
JobFinder code, opens a database except in SQLite read-only mode, or persists
Career Navigator state. Upstream classification fields are retained only as
provenance; Career Navigator remains the decision authority.
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .dedup import group_duplicates
from .duplicate_resolutions import group_fingerprint
from .eligibility import evaluate
from .models import Decision, Posting, Reason
from .pay import parse_pay
from .pipeline import Evaluation
from .policy import Policy
from .relevance import score
from .text import clean_text, make_excerpt
from .urls import canonical_url

EXPECTED_USER_VERSION = 0
EXPECTED_JOBS_COLUMNS = frozenset(
    "id url source title company location details family city remote pay_text "
    "pay_top salary_unknown driving verdict reasons status posted first_seen "
    "last_seen dupkey dup_of picked_on pick_city needs_details".split()
)
EXPECTED_SOURCE_RUNS_COLUMNS = frozenset(
    "name ok count error ran_at".split()
)
EXPECTED_STATE_COLUMNS = frozenset(
    "key value".split()
)
_JOB_SELECT = "SELECT " + ", ".join(sorted(EXPECTED_JOBS_COLUMNS)) + " FROM jobs ORDER BY id"
_UPSTREAM_COLUMNS = (
    "id", "posted", "first_seen", "last_seen", "dupkey", "dup_of", "picked_on",
    "pick_city", "reasons", "driving", "family", "city", "salary_unknown",
    "pay_top", "remote", "needs_details",
)
_TEXT_COLUMNS = frozenset(
    {"posted", "first_seen", "last_seen", "dupkey", "picked_on", "pick_city",
     "reasons", "driving", "family", "city"}
)
_INT_COLUMNS = frozenset({"id", "dup_of", "salary_unknown", "remote", "needs_details"})


class LiveAdapterError(ValueError):
    """Base class for unsafe or incompatible upstream snapshots."""


class SchemaVersionError(LiveAdapterError):
    """The upstream SQLite user_version is not the version this adapter expects."""


class SchemaDriftError(LiveAdapterError):
    """The upstream jobs table differs from the audited schema."""


class MalformedRowError(LiveAdapterError):
    """A database row contains invalid text or an unsafe posting URL."""


@dataclass(frozen=True)
class ImportedRow:
    posting: Posting
    upstream_records: dict
    duplicate_confidence: str
    duplicate_reason: str
    # Group metadata retained at load time (F-04): exact_duplicate_count is
    # the number of raw rows this row represents in the served presentation —
    # len(group.members) for the representative of an exact group, 1 for
    # singles and uncertains, 0 for non-representative exact siblings.
    # is_representative is False only for non-representative exact siblings;
    # the bridge serves exactly the representative rows. Summed over all
    # imported rows, exact_duplicate_count equals the raw row count.
    exact_duplicate_count: int = 1
    is_representative: bool = True


@dataclass(frozen=True)
class LiveImport:
    rows: tuple[ImportedRow, ...]
    health: dict


def _decode_text(value, column: str, row_id: int, *, table: str = "jobs", default: str = "") -> str:
    if value is None:
        return default
    prefix = f"{table} row {row_id}" if row_id >= 0 else table
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise MalformedRowError(
                f"{prefix} column {column!r} is not valid UTF-8"
            ) from exc
    if not isinstance(value, str):
        raise MalformedRowError(
            f"{prefix} column {column!r} must be text, got {type(value).__name__}"
        )
    if "\x00" in value:
        raise MalformedRowError(f"{prefix} column {column!r} contains a null byte")
    return value


def _parse_int(value, column: str, row_id: int, *, table: str = "jobs", default: int | None = 0) -> int | None:
    if value is None:
        return default
    prefix = f"{table} row {row_id}" if row_id >= 0 else table
    if isinstance(value, float):
        raise MalformedRowError(f"{prefix} column {column!r} must be an integer, got float {value!r}")
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise MalformedRowError(f"{prefix} column {column!r} is not valid UTF-8") from exc
    try:
        return int(value)
    except (ValueError, TypeError) as exc:
        raise MalformedRowError(f"{prefix} column {column!r} must be an integer, got {value!r}") from exc


def _parse_optional_float(value, column: str, row_id: int, *, table: str = "jobs") -> float | None:
    if value is None:
        return None
    prefix = f"{table} row {row_id}" if row_id >= 0 else table
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise MalformedRowError(f"{prefix} column {column!r} is not valid UTF-8") from exc
    try:
        parsed = float(value)
    except (ValueError, TypeError) as exc:
        raise MalformedRowError(f"{prefix} column {column!r} must be a float, got {value!r}") from exc
    if not math.isfinite(parsed):
        raise MalformedRowError(f"{prefix} column {column!r} has non-finite float: {value!r}")
    return parsed


def _posting_url(value, row_id: int):
    url = _decode_text(value, "url", row_id).strip()
    try:
        parts = urlsplit(url)
        port = parts.port  # Access validates malformed/out-of-range ports.
        valid = (
            parts.scheme.lower() in ("http", "https")
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and port != 0
            and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)
        )
    except ValueError:
        valid = False
    if not valid:
        raise MalformedRowError(f"jobs row {row_id} has an unsafe or malformed URL")
    identity = canonical_url(url)
    return url, identity


def _validate_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != EXPECTED_USER_VERSION:
        raise SchemaVersionError(
            f"unsupported JobFinder user_version {version}; expected {EXPECTED_USER_VERSION}"
        )
    for table, expected_cols in (
        ("jobs", EXPECTED_JOBS_COLUMNS),
        ("source_runs", EXPECTED_SOURCE_RUNS_COLUMNS),
        ("state", EXPECTED_STATE_COLUMNS),
    ):
        try:
            actual = frozenset(
                row[1].decode("utf-8", errors="strict") if isinstance(row[1], bytes) else row[1]
                for row in conn.execute(f"PRAGMA table_info('{table}')")
            )
        except (sqlite3.DatabaseError, UnicodeDecodeError) as exc:
            raise SchemaDriftError(f"cannot inspect JobFinder {table} schema: {exc}") from exc
        if actual != expected_cols:
            missing = sorted(expected_cols - actual)
            added = sorted(actual - expected_cols)
            raise SchemaDriftError(
                f"JobFinder {table} schema drift; missing={missing}, added={added}"
            )


def _health(conn: sqlite3.Connection, row_count: int) -> dict:
    sources = [
        {
            "name": _decode_text(row[0], "name", -1, table="source_runs"),
            "ok": bool(_parse_int(row[1], "ok", -1, table="source_runs", default=0)),
            "count": _parse_int(row[2], "count", -1, table="source_runs", default=0),
            "error": _decode_text(row[3], "error", -1, table="source_runs"),
            "ran_at": _decode_text(row[4], "ran_at", -1, table="source_runs") or None,
        }
        for row in conn.execute(
            "SELECT name, ok, count, error, ran_at FROM source_runs ORDER BY name"
        )
    ]
    latest_value = conn.execute("SELECT MAX(last_seen) FROM jobs").fetchone()[0]
    latest = _decode_text(latest_value, "last_seen", -1, table="jobs") or None
    last_updated_row = conn.execute(
        "SELECT value FROM state WHERE key = ?", ("last_updated",)
    ).fetchone()
    last_updated = (
        _decode_text(last_updated_row[0], "value", -1, table="state") or None
        if last_updated_row and last_updated_row[0] is not None
        else None
    )
    all_failed = bool(sources) and not any(source["ok"] for source in sources)
    if row_count:
        empty_reason = None
    elif all_failed:
        empty_reason = "no jobs returned; every recorded source run failed"
    elif not sources:
        empty_reason = "no jobs returned and no source-run health records exist"
    elif any(source["error"] for source in sources):
        empty_reason = "no jobs returned; one or more source runs reported errors"
    else:
        empty_reason = "no jobs returned by the recorded source runs"
    return {
        "jobs_count": row_count,
        "sources": sources,
        "max_last_seen": latest,
        "last_updated": last_updated,
        "staleness_basis": (
            "Compare max_last_seen with state.last_updated; this scaffold does not impose "
            "a freshness threshold."
        ),
        "empty": row_count == 0,
        "empty_reason": empty_reason,
    }


def load_live_database(path: str | Path, policy: Policy | None = None) -> LiveImport:
    """Read a JobFinder snapshot without modifying it and return stable mapped rows.

    Health includes each source run and the maximum ``jobs.last_seen`` value;
    consumers can apply their own freshness threshold without hiding its source
    timestamp. JobFinder's tracker CSV is deliberately not opened here.
    """
    db_path = Path(path).expanduser().resolve()
    if not db_path.is_file():
        raise LiveAdapterError(f"JobFinder database not found or not a regular file: {db_path}")
    uri = db_path.as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise LiveAdapterError(f"cannot open JobFinder database read-only: {exc}") from exc
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.row_factory = sqlite3.Row
        conn.text_factory = bytes
        conn.execute("BEGIN DEFERRED")
        _validate_schema(conn)
        records = conn.execute(_JOB_SELECT).fetchall()
        health = _health(conn, len(records))
        conn.commit()
    except (UnicodeDecodeError, MalformedRowError):
        raise
    except SchemaDriftError:
        raise
    except SchemaVersionError:
        raise
    except sqlite3.Error as exc:
        raise LiveAdapterError(f"cannot read JobFinder database: {exc}") from exc
    finally:
        conn.close()

    active_policy = policy or Policy()
    raw_rows = []
    for record in records:
        if record["id"] is None:
            raise MalformedRowError("jobs row has missing or null id")
        row_id = _parse_int(record["id"], "id", -1, table="jobs", default=None)
        if row_id is None:
            raise MalformedRowError("jobs row has missing or null id")
        url, identity = _posting_url(record["url"], row_id)
        title = clean_text(_decode_text(record["title"], "title", row_id)).strip()
        company = clean_text(_decode_text(record["company"], "company", row_id)).strip()
        location = clean_text(_decode_text(record["location"], "location", row_id)).strip()
        details = clean_text(_decode_text(record["details"], "details", row_id)).strip()
        source = clean_text(_decode_text(record["source"], "source", row_id)).strip() or "jobfinder"
        canonical_source = source if source in {"indeed_email", "linkedin_email", "manual"} else "jobfinder"
        pay_text = clean_text(_decode_text(record["pay_text"], "pay_text", row_id)).strip()
        # Parse with CN's parser as a validation/re-derivation step. Preserve sanitized
        # pay text in Posting; upstream pay_top remains provenance only.
        pay_facts = parse_pay(pay_text)
        upstream = {}
        for name in _UPSTREAM_COLUMNS:
            val = record[name]
            if name in _TEXT_COLUMNS:
                upstream[name] = _decode_text(val, name, row_id)
            elif name in _INT_COLUMNS:
                upstream[name] = _parse_int(val, name, row_id, default=None) if val is not None else None
            elif name == "pay_top":
                upstream[name] = _parse_optional_float(val, name, row_id)

        upstream.update({
            "source": canonical_source,
            "details_excerpt": make_excerpt(details),
            "url": url,
            "canonical_url": identity.url,
            "provider": identity.provider,
            "provider_id": identity.provider_id,
            "pay_facts": {
                "status": pay_facts.status, "low": pay_facts.low,
                "high": pay_facts.high, "unit": pay_facts.unit,
                "open_ended": pay_facts.open_ended,
            },
        })
        posting = Posting(
            url=url, title=title, company=company, location=location,
            details=details, source=source, pay_text=pay_text,
            provider=identity.provider, provider_id=identity.provider_id,
            upstream_verdict=clean_text(_decode_text(record["verdict"], "verdict", row_id)).strip() or None,
            upstream_status=clean_text(_decode_text(record["status"], "status", row_id)).strip() or None,
        )
        decision = evaluate(posting, active_policy)
        upstream["cn_assessment"] = {
            # Phase 3c WS7: a hash of the posting text the location/driving
            # rules read, so a saved review decision can tell when the posting
            # text changed. Provenance only; no rule reads it.
            "details_fp": hashlib.sha256(
                " ".join(details.split()).casefold().encode("utf-8")).hexdigest()[:16],
            "status": decision.status,
            "reasons": tuple((reason.factor, reason.outcome, reason.evidence)
                             for reason in decision.reasons),
        }
        raw_rows.append((posting, upstream))
    groups = group_duplicates(posting for posting, _ in raw_rows)
    upstream_by_posting = {posting: upstream for posting, upstream in raw_rows}
    group_info: dict[Posting, tuple[str, str, int, bool]] = {}
    for group in groups:
        if group.confidence == "exact":
            representative = min(
                group.members,
                key=lambda member: _representative_key(member, upstream_by_posting[member]),
            )
            for member in group.members:
                group_info[member] = (
                    group.confidence,
                    group.reason,
                    len(group.members) if member == representative else 0,
                    member == representative,
                )
        else:
            if group.confidence == "uncertain":
                # Expose the group, don't change it (Phase 3c WS5): each member
                # carries the group fingerprint and every member's JobFinder id
                # so a decision can be recorded against exactly this group.
                member_ids = [upstream_by_posting[m].get("id") for m in group.members]
                if all(isinstance(i, int) for i in member_ids):
                    dup_group = {
                        "fingerprint": group_fingerprint(member_ids),
                        "member_ids": sorted(member_ids),
                    }
                    for member in group.members:
                        upstream_by_posting[member]["dup_group"] = dup_group
            for member in group.members:
                group_info[member] = (group.confidence, group.reason, 1, True)
    imported = tuple(
        ImportedRow(posting, upstream, *group_info[posting])
        for posting, upstream in sorted(raw_rows, key=lambda pair: (pair[0].url, pair[1]["id"]))
    )
    return LiveImport(imported, health)


def _representative_key(posting: Posting, upstream: dict) -> tuple:
    """Deterministic exact-group representative tiebreak (F-05), highest
    precedence first: longest details, then known pay, then lowest
    JobFinder id. Returned as a sort key for min()."""
    pay_known = upstream.get("pay_facts", {}).get("status") != "missing"
    job_id = upstream.get("id")
    return (
        -len(posting.details),
        0 if pay_known else 1,
        job_id if isinstance(job_id, int) else 0,
    )


def _decision_from_assessment(assessment: dict) -> Decision:
    """Rebuild the Decision pre-computed by load_live_database from the
    cn_assessment record (no re-evaluation)."""
    return Decision(
        status=assessment["status"],
        reasons=tuple(
            Reason(factor, outcome, evidence)
            for factor, outcome, evidence in assessment["reasons"]
        ),
    )


def evaluate_live_import(live_import: LiveImport, policy: Policy | None = None) -> tuple[Evaluation, ...]:
    """Map a live import to pipeline Evaluations for the served presentation.

    Reuses the pre-computed CN assessment stored in each row's
    upstream_records (evaluate() is never re-run here); relevance is scored
    fresh against the given policy. Exact-duplicate siblings are folded:
    only the representative row of each exact group is served, carrying
    exact_duplicate_count = group size; singles and uncertains are served
    1:1 with count 1. The complete upstream_records dict (all 21 provenance
    fields plus cn_assessment) survives intact into each Evaluation, so the
    result contract keeps jobs.id and every live provenance field.
    Evaluations are returned in relevance-rank order, mirroring
    pipeline.evaluate_postings.
    """
    active_policy = policy or Policy()
    evaluations = []
    for row in live_import.rows:
        if not row.is_representative:
            continue
        upstream = dict(row.upstream_records)
        upstream["exact_duplicate_count"] = row.exact_duplicate_count
        # Mirror pipeline.evaluate_postings: the contract's upstream_records
        # carries the posting's upstream verdict/status alongside the 21
        # adapter provenance fields, so S2's upstream_verdict resolves.
        upstream["verdict"] = row.posting.upstream_verdict
        upstream["status"] = row.posting.upstream_status
        decision = _decision_from_assessment(row.upstream_records["cn_assessment"])
        relevance = score(row.posting, decision, active_policy)
        evaluations.append(
            Evaluation(
                posting=row.posting,
                identity=canonical_url(row.posting.url),
                decision=decision,
                relevance=relevance,
                duplicate_confidence=row.duplicate_confidence,
                duplicate_reason=row.duplicate_reason,
                upstream_records=upstream,
            )
        )
    evaluations.sort(
        key=lambda ev: (-ev.relevance.value, ev.posting.title, ev.posting.company, ev.posting.url)
    )
    return tuple(evaluations)


def ingestion_summary(live_import: LiveImport) -> dict:
    """Raw ingestion-tier metrics over ALL imported rows (pre-folding).

    Totals: rows ingested, CN decision distribution, duplicate-confidence
    distribution, and the upstream-verdict vs CN-decision contingency
    matrix. Every raw row is counted exactly once.
    """
    by_decision = {"eligible": 0, "review": 0, "rejected": 0}
    by_duplicate = {"single": 0, "exact": 0, "uncertain": 0}
    matrix: dict[str, dict[str, int]] = {}
    for row in live_import.rows:
        status = row.upstream_records["cn_assessment"]["status"]
        by_decision[status] = by_decision.get(status, 0) + 1
        by_duplicate[row.duplicate_confidence] = by_duplicate.get(row.duplicate_confidence, 0) + 1
        verdict = row.posting.upstream_verdict or "unknown"
        cell = matrix.setdefault(verdict, {"eligible": 0, "review": 0, "rejected": 0})
        cell[status] = cell.get(status, 0) + 1
    return {
        "total": len(live_import.rows),
        "by_decision": by_decision,
        "by_duplicate_confidence": by_duplicate,
        "matrix": matrix,
    }
