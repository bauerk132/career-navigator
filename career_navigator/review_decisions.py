"""Eligibility review decisions: store, fold and serving overlay (Phase 3c WS7).

Postings land in Needs Review when an eligibility reason comes back "review":
pay unstated or ambiguous, remote Pennsylvania eligibility not established, or
driving text unclear. ``eligibility.py`` is untouched and still decides that.
This module records what Kyle decided about each review reason and applies the
decisions as an overlay AFTER ranking (and after the duplicate overlay), in the
serving layer only:

  pass    "this reason is fine": once every review reason of the posting is
          passed, the posting moves to the shortlist with the fit score the
          backend already computed for it (nothing is invented) and carries a
          "Confirmed by you" badge.
  reject  "this reason disqualifies it": the posting moves to Excluded at once
          (one rejected reason is enough) with "Rejected by you: ...".
  undo    appends an undo line; the reason is open again.

A posting with two review reasons stays in Needs Review until both are
answered (a reject on either one excludes it immediately, since no answer to
the other can rescue it).

Decisions live in ``review_decisions.jsonl`` (append-only, one JSON object per
line) in the state folder, never in jobfinder.db. Each decision is bound to a
fingerprint of the posting fields that reason rests on. When those fields
change (new pay text, new location, new posting text) the old decision is set
aside and the reason returns to the queue marked "changed since you decided".
"""
from __future__ import annotations

import hashlib
import re
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .duplicate_resolutions import _annotated
from .ranking import RankedResult, RankedResults, _sort_key, final_classification
from .sqlite_log import SqliteLog
from .state_store import StateError

REVIEW_FILENAME = "review_decisions.jsonl"
EVENT_VERSION = 1
REASONS = ("pay", "location", "driving")  # the only factors that can come back "review"
VERDICTS = ("pass", "reject")
NOTE_MAX_CHARS = 500

# Plain wording per reason, reusing the prototype's button language.
_REASON_TEXT = {
    "pay": {
        "name": "pay floor",
        "pass_label": "Confirm pay floor",
        "reject_label": "Mark below floor",
        "pass_badge": "Confirmed by you: pay floor",
        "reject_badge": "Rejected by you: pay below floor",
    },
    "location": {
        "name": "PA eligibility",
        "pass_label": "Verify PA eligibility",
        "reject_label": "Out of state / ineligible",
        "pass_badge": "Confirmed by you: PA eligibility",
        "reject_badge": "Rejected by you: not eligible from Pennsylvania",
    },
    "driving": {
        "name": "driving",
        "pass_label": "Verify no driving required",
        "reject_label": "Mark mandatory driving",
        "pass_badge": "Confirmed by you: no driving required",
        "reject_badge": "Rejected by you: mandatory driving",
    },
}

_FP_RE = re.compile(r"^[0-9a-f]{16}$")
_API_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_WS_RE = re.compile(r"\s+")


class ReviewError(ValueError):
    """A review request that cannot be honoured. ``status`` is the HTTP status
    the API answers with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _normal(text) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip().casefold()


def review_reasons(result) -> list[dict]:
    """The reasons that put this posting in Needs Review, in contract order.
    Empty for a posting that is not in review."""
    if result.contract.decision_status != "review":
        return []
    return [r for r in result.contract.reasons
            if r.get("outcome") == "review" and r.get("factor") in REASONS]


def reason_fingerprint(result, reason: dict) -> str:
    """Hash of the posting fields one review reason rests on:

      pay       the posted pay text and the rule's evidence
      location  the posted location and the rule's evidence (and, for a remote
                posting, the posting text the remote-scope rule reads)
      driving   the posting text the driving rule reads, and the evidence

    The reason's own evidence string is included for all three, so a change of
    posting that changes why the rule is unsure also changes the fingerprint."""
    contract = result.contract
    upstream = contract.upstream_records or {}
    factor = reason["factor"]
    evidence = _normal(reason.get("evidence"))
    # live_adapter stores a hash of the posting text next to the assessment
    # (the text itself is not carried into the contract).
    details = str((upstream.get("cn_assessment") or {}).get("details_fp") or "")
    if factor == "pay":
        return _digest("pay", _normal(contract.pay.raw), evidence)
    location = _normal((contract.posting or {}).get("location"))
    if factor == "location":
        text = details if "remote" in location else ""
        return _digest("location", location, evidence, text)
    return _digest("driving", details, evidence)


# ---------------------------------------------------------------------------
# Store and fold
# ---------------------------------------------------------------------------

def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_event(record: dict) -> dict | None:
    """Return the record when it is a well-formed event, else None (counted as
    skipped, never half-applied)."""
    if record.get("v") != EVENT_VERSION:
        return None
    event_id = record.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return None
    if not _is_int(record.get("jobfinder_id")) or record.get("reason") not in REASONS:
        return None
    kind = record.get("type")
    if kind == "undo":
        return record if isinstance(record.get("undoes"), str) else None
    if kind != "decide":
        return None
    if record.get("verdict") not in VERDICTS:
        return None
    if not isinstance(record.get("field_fp"), str) or not _FP_RE.match(record["field_fp"]):
        return None
    api_id = record.get("api_id")
    if api_id is not None and not (isinstance(api_id, str) and _API_ID_RE.match(api_id)):
        return None
    note = record.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > NOTE_MAX_CHARS):
        return None
    return record


@dataclass(frozen=True)
class ReviewState:
    active: dict          # (jobfinder_id, reason) -> the decide event in force
    event_count: int      # valid events read
    skipped: int          # lines that were not usable events
    signature: object     # file signature this state was folded from


def fold_events(records: list[dict], skipped_lines: int = 0, signature=None) -> ReviewState:
    """Fold the log, in file order, into the decisions currently in force. A
    later decision for the same (posting, reason) supersedes an earlier one; an
    undo clears the decision it names (and only that one)."""
    active: dict[tuple, dict] = {}
    valid = 0
    skipped = skipped_lines
    for record in records:
        event = _parse_event(record)
        if event is None:
            skipped += 1
            continue
        valid += 1
        key = (event["jobfinder_id"], event["reason"])
        if event["type"] == "decide":
            active.pop(key, None)
            active[key] = event
        else:
            current = active.get(key)
            if current is not None and current["event_id"] == event["undoes"]:
                del active[key]
            else:
                skipped += 1  # an undo that names nothing in force is ignored
    return ReviewState(active, valid, skipped, signature)


class ReviewDecisions:
    """Reads and appends ``review_decisions.jsonl``. ``state_dir`` None means
    persistence is off (synthetic backend): reads are empty and every write is
    refused."""

    def __init__(self, state_dir):
        self.enabled = state_dir is not None
        self._log = SqliteLog(state_dir, "review_decisions", REVIEW_FILENAME) if self.enabled else None
        self.write_lock = threading.Lock()

    def state(self) -> ReviewState:
        if not self.enabled:
            return ReviewState({}, 0, 0, None)
        records, skipped, signature = self._log.read()
        return fold_events(records, skipped, signature)

    def record_decide(self, result, api_id: str, reason: str, verdict: str, note) -> dict:
        """Validate and append one decision for a CURRENT review posting. The
        posting's JobFinder id and the fingerprint come from this backend's own
        data, never from the client."""
        if reason not in REASONS:
            raise ReviewError(f"reason must be one of {', '.join(REASONS)}")
        if verdict not in VERDICTS:
            raise ReviewError(f"verdict must be one of {', '.join(VERDICTS)}")
        match = next((r for r in review_reasons(result) if r["factor"] == reason), None)
        if match is None:
            raise ReviewError(f"this posting is not waiting on a {reason} review", status=409)
        jobfinder_id = (result.contract.upstream_records or {}).get("id")
        if not _is_int(jobfinder_id):
            raise ReviewError("this posting has no JobFinder id, so a decision cannot be saved", status=409)
        if note is not None:
            if not isinstance(note, str) or len(note) > NOTE_MAX_CHARS:
                raise ReviewError(f"note must be text of at most {NOTE_MAX_CHARS} characters")
            note = note.strip() or None
        event = {
            "v": EVENT_VERSION,
            "event_id": uuid.uuid4().hex,
            "ts": _utc_now(),
            "type": "decide",
            "jobfinder_id": jobfinder_id,
            "api_id": api_id,
            "reason": reason,
            "verdict": verdict,
            "field_fp": reason_fingerprint(result, match),
            "note": note,
        }
        self._append(event)
        return event

    def record_undo(self, result, reason: str) -> dict:
        jobfinder_id = (result.contract.upstream_records or {}).get("id")
        current = self.state().active.get((jobfinder_id, reason))
        if current is None:
            raise ReviewError("there is no decision in force for this reason to undo", status=409)
        event = {
            "v": EVENT_VERSION,
            "event_id": uuid.uuid4().hex,
            "ts": _utc_now(),
            "type": "undo",
            "jobfinder_id": jobfinder_id,
            "reason": reason,
            "undoes": current["event_id"],
        }
        self._append(event)
        return event

    def _append(self, event: dict) -> None:
        if not self.enabled:
            raise ReviewError("decisions are not saved in this mode (no state folder)", status=503)
        try:
            self._log.append(event)
        except StateError as exc:
            raise ReviewError(str(exc), status=503) from exc


# ---------------------------------------------------------------------------
# Serving overlay
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReviewView:
    """Ranked lists with review decisions applied, plus the payloads the API
    reports. ``summary`` holds the resolved counts reported next to the raw
    ingestion numbers; ``audit`` lists every decision in force or set aside."""
    ranked: RankedResults
    index: dict
    summary: dict
    audit: tuple
    store: dict


def _reason_entry(reason: dict, decision, current_fp: str) -> dict:
    text = _REASON_TEXT[reason["factor"]]
    if decision is None:
        status = "open"
    elif decision["field_fp"] == current_fp:
        status = decision["verdict"]
    else:
        status = "changed"
    entry = {
        "reason": reason["factor"],
        "name": text["name"],
        "evidence": reason.get("evidence", ""),
        "status": status,                      # open | pass | reject | changed
        "pass_label": text["pass_label"],
        "reject_label": text["reject_label"],
        "event_id": decision["event_id"] if decision else None,
        "verdict": decision["verdict"] if decision else None,
        "decided_at": decision["ts"] if decision else None,
        "note": decision.get("note") if decision else None,
        "badge": None,
    }
    if status == "pass":
        entry["badge"] = text["pass_badge"]
    elif status == "reject":
        entry["badge"] = text["reject_badge"]
    return entry


def _decision_summary(reasons: list[dict]) -> tuple[str, str]:
    """(state, effective_status) for a review posting from its reason entries."""
    if any(r["status"] == "reject" for r in reasons):
        return "rejected", "rejected"
    if all(r["status"] == "pass" for r in reasons):
        return "passed", "eligible"
    if any(r["status"] == "pass" for r in reasons):
        return "partial", "review"
    if any(r["status"] == "changed" for r in reasons):
        return "changed", "review"
    return "open", "review"


def build_review_view(raw: RankedResults, state: ReviewState, policy, job_id_of,
                      store_info=None) -> ReviewView:
    """Apply review decisions to the (duplicate-overlaid) ranked results.
    ``raw`` is never modified. Every Needs Review row is annotated with its
    per-reason status so the dashboard can show the right buttons; rows move
    list only when a decision says so."""
    counts = {"open": 0, "partial": 0, "changed": 0, "passed": 0, "rejected": 0}
    audit = []
    moved: dict[str, RankedResult] = {}       # job id -> replacement row (new list)
    matched_keys = set()

    def annotate(result):
        reasons = review_reasons(result)
        if not reasons:
            return result, None
        upstream = result.contract.upstream_records or {}
        jobfinder_id = upstream.get("id")
        entries = []
        for reason in reasons:
            key = (jobfinder_id, reason["factor"])
            decision = state.active.get(key)
            if decision is not None:
                matched_keys.add(key)
            entries.append(_reason_entry(reason, decision, reason_fingerprint(result, reason)))
        group_state, effective = _decision_summary(entries)
        counts[group_state] += 1
        meta = {"state": group_state, "effective_status": effective, "reasons": entries}
        for entry in entries:
            if entry["status"] in ("pass", "reject", "changed"):
                audit.append({
                    "id": job_id_of(result.contract.source),
                    "jobfinder_id": jobfinder_id,
                    "title": result.contract.posting.get("title", ""),
                    "company": result.contract.posting.get("company", ""),
                    **{k: entry[k] for k in ("reason", "name", "status", "verdict", "decided_at",
                                             "note", "event_id", "badge")},
                    "posting_state": group_state,
                })
        return _annotated(result, review_decision=meta), meta

    new_review, new_shortlist, new_excluded = [], [], []

    for result in raw.shortlist:
        new_shortlist.append(result)
    for result in raw.excluded:
        new_excluded.append(result)
    for result in raw.review:
        annotated, meta = annotate(result)
        if meta is None or meta["effective_status"] == "review":
            new_review.append(annotated)
        elif meta["effective_status"] == "rejected":
            new_excluded.append(replace(annotated, final_classification="Excluded", shortlist_rank=None))
        else:  # passed: the real backend score, classified as an eligible posting would be
            as_eligible = replace(annotated.contract, decision_status="eligible")
            label = final_classification(as_eligible, annotated.fit_score, policy)
            new_shortlist.append(replace(annotated, final_classification=label))

    changed_lists = len(new_review) != len(raw.review)
    if changed_lists:
        new_shortlist.sort(key=_sort_key)
        new_excluded.sort(key=lambda r: (
            -r.contract.relevance_value, r.contract.posting["title"],
            r.contract.posting["company"], r.contract.source["url"]))
    shortlist = tuple(replace(r, shortlist_rank=i + 1) for i, r in enumerate(new_shortlist))
    ranked = RankedResults(shortlist=shortlist, review=tuple(new_review), excluded=tuple(new_excluded))
    index = {
        job_id_of(r.contract.source): r
        for group in (ranked.shortlist, ranked.review, ranked.excluded)
        for r in group
    }
    unmatched = [k for k in state.active if k not in matched_keys]
    audit.sort(key=lambda a: (a["decided_at"] or "", a["id"], a["reason"]), reverse=True)
    summary = {
        "review_rows": len(raw.review),
        "open": counts["open"],
        "partial": counts["partial"],
        "changed": counts["changed"],
        "passed": counts["passed"],
        "rejected": counts["rejected"],
        "still_in_review": len(new_review),
        "decisions_in_force": len(state.active),
        "decisions_without_a_review_row": len(unmatched),
    }
    return ReviewView(ranked, index, summary, tuple(audit), store_info or {})
