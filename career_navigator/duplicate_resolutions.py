"""Uncertain-duplicate resolutions: store, fold and serving overlay (Phase 3c WS5).

The V2 dedup policy is untouched. ``dedup.py`` still decides which postings are
"uncertain duplicates" and ``live_adapter.py`` still serves them one-for-one.
This module records what Kyle decided about each uncertain group and applies
those decisions as an overlay AFTER ranking, in the serving layer only:

  keep     "these are different jobs": the group stops being flagged; every
           posting stays served as its own row.
  merge    "these are one job": Kyle picks the posting to keep; the others
           are folded under it the way exact duplicates fold today (the kept
           row's exact_duplicate_count grows, so the reconciliation
           sum(exact_duplicate_count) == raw rows still holds).
  dismiss  "not worth deciding": the group leaves the queue; every posting
           stays visible where it was and stays marked uncertain.

Decisions live in ``duplicate_resolutions.jsonl`` (append-only, one JSON object
per line) in the state folder, never in jobfinder.db. Undo appends an "undo"
line. A decision is bound to the group fingerprint (a hash of the sorted
JobFinder ids in the group); when a group gains or loses a member its
fingerprint changes, the old decision is set aside, and the group returns to
the queue marked "changed since you decided".
"""
from __future__ import annotations

import hashlib
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .ranking import RankedResults
from .sqlite_log import SqliteLog
from .state_store import StateError

RESOLUTIONS_FILENAME = "duplicate_resolutions.jsonl"
EVENT_VERSION = 1
ACTIONS = ("keep", "merge", "dismiss")
NOTE_MAX_CHARS = 500
# Order of the queue payload: what needs Kyle first.
_STATE_ORDER = {"changed": 0, "open": 1, "keep": 2, "merge": 3, "dismiss": 4}


class ResolutionError(ValueError):
    """A resolution request that cannot be honoured. ``status`` is the HTTP
    status the API answers with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def group_fingerprint(member_ids) -> str:
    """Stable id of an uncertain group: SHA-256 (first 16 hex chars) of the
    sorted JobFinder ids joined with '|'. Same members, same fingerprint."""
    ids = list(member_ids)
    if all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        ordered = [str(i) for i in sorted(ids)]
    else:
        ordered = sorted(str(i) for i in ids)
    return hashlib.sha256("|".join(ordered).encode("utf-8")).hexdigest()[:16]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_member_ids(value) -> bool:
    return (
        isinstance(value, list)
        and 2 <= len(value) <= 500
        and all(isinstance(i, int) and not isinstance(i, bool) for i in value)
        and len(set(value)) == len(value)
    )


def _parse_event(record: dict) -> dict | None:
    """Return the record when it is a well-formed event, else None. A record
    written by hand or by a future version that fails any check is skipped
    (counted), never half-applied."""
    if record.get("v") != EVENT_VERSION:
        return None
    event_id = record.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return None
    fingerprint = record.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        return None
    kind = record.get("type")
    if kind == "undo":
        return record if isinstance(record.get("undoes"), str) else None
    if kind != "resolve":
        return None
    member_ids = record.get("member_ids")
    if not _valid_member_ids(member_ids) or group_fingerprint(member_ids) != fingerprint:
        return None
    action = record.get("action")
    if action not in ACTIONS:
        return None
    kept_id = record.get("kept_id")
    if action == "merge":
        if kept_id not in member_ids or isinstance(kept_id, bool):
            return None
    elif kept_id is not None:
        return None
    note = record.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > NOTE_MAX_CHARS):
        return None
    return record


@dataclass(frozen=True)
class ResolutionState:
    active: dict          # fingerprint -> the resolve event currently in force
    event_count: int      # valid events read
    skipped: int          # lines that were not usable events
    signature: object     # file signature this state was folded from


def fold_events(records: list[dict], skipped_lines: int = 0, signature=None) -> ResolutionState:
    """Fold the log, in file order, into the decisions currently in force.
    A later resolve for the same group supersedes an earlier one; an undo
    clears the decision it names (and only that one)."""
    active: dict[str, dict] = {}
    valid = 0
    skipped = skipped_lines
    for record in records:
        event = _parse_event(record)
        if event is None:
            skipped += 1
            continue
        valid += 1
        fingerprint = event["fingerprint"]
        if event["type"] == "resolve":
            active.pop(fingerprint, None)  # re-insert so dict order is decision order
            active[fingerprint] = event
        else:  # undo
            current = active.get(fingerprint)
            if current is not None and current["event_id"] == event["undoes"]:
                del active[fingerprint]
            else:
                skipped += 1  # an undo that names nothing in force is ignored
    return ResolutionState(active, valid, skipped, signature)


class DuplicateResolutions:
    """Reads and appends ``duplicate_resolutions.jsonl``. ``state_dir`` None
    means persistence is off (synthetic backend): reads are empty and every
    write is refused."""

    def __init__(self, state_dir):
        self.enabled = state_dir is not None
        self._log = SqliteLog(state_dir, "duplicate_resolutions", RESOLUTIONS_FILENAME) if self.enabled else None
        self.write_lock = threading.Lock()

    def state(self) -> ResolutionState:
        if not self.enabled:
            return ResolutionState({}, 0, 0, None)
        records, skipped, signature = self._log.read()
        return fold_events(records, skipped, signature)

    def record_resolve(self, group: dict, action: str, kept_id, note) -> dict:
        """Validate and append one resolve event for a CURRENT group."""
        if action not in ACTIONS:
            raise ResolutionError(f"action must be one of {', '.join(ACTIONS)}")
        member_ids = list(group["member_ids"])
        if action == "merge":
            if kept_id is None or isinstance(kept_id, bool) or kept_id not in member_ids:
                raise ResolutionError("merge needs kept_jobfinder_id: the posting to keep, one of the group's members")
        elif kept_id is not None:
            raise ResolutionError("kept_jobfinder_id is only used with merge")
        if note is not None:
            if not isinstance(note, str) or len(note) > NOTE_MAX_CHARS:
                raise ResolutionError(f"note must be text of at most {NOTE_MAX_CHARS} characters")
            note = note.strip() or None
        event = {
            "v": EVENT_VERSION,
            "event_id": uuid.uuid4().hex,
            "ts": _utc_now(),
            "type": "resolve",
            "fingerprint": group["fingerprint"],
            "member_ids": member_ids,
            "action": action,
            "kept_id": kept_id,
            "note": note,
        }
        self._append(event)
        return event

    def record_undo(self, fingerprint: str) -> dict:
        current = self.state().active.get(fingerprint)
        if current is None:
            raise ResolutionError("there is no decision in force for this group to undo", status=409)
        event = {
            "v": EVENT_VERSION,
            "event_id": uuid.uuid4().hex,
            "ts": _utc_now(),
            "type": "undo",
            "fingerprint": fingerprint,
            "undoes": current["event_id"],
        }
        self._append(event)
        return event

    def _append(self, event: dict) -> None:
        if not self.enabled:
            raise ResolutionError("decisions are not saved in this mode (no state folder)", status=503)
        try:
            self._log.append(event)
        except StateError as exc:
            raise ResolutionError(str(exc), status=503) from exc


# ---------------------------------------------------------------------------
# Serving overlay
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ServedView:
    """What the API serves once decisions are applied. ``ranked`` has merged
    rows folded away; ``index`` maps job id to each served row; ``folded``
    maps job id to the (annotated) rows that were folded away, so their
    detail pages still resolve; ``groups`` is the queue payload; ``summary``
    holds the resolved counts reported next to the raw ingestion numbers."""
    ranked: RankedResults
    index: dict
    folded: dict
    groups: tuple
    summary: dict
    store: dict
    # Phase 3c WS7: the review-decisions overlay composed on top (a
    # review_decisions.ReviewView), set by app._served_view; None when absent.
    review: object = None


def _result_upstream(result) -> dict:
    return result.contract.upstream_records or {}


def _annotated(result, **extra_upstream):
    upstream = dict(_result_upstream(result))
    upstream.update(extra_upstream)
    return replace(result, contract=replace(result.contract, upstream_records=upstream))


def _member_payload(result, list_name: str, job_id: str) -> dict:
    contract = result.contract.to_dict()
    posting = contract.get("posting") or {}
    pay = contract.get("pay") or {}
    source = contract.get("source") or {}
    upstream = contract.get("upstream_records") or {}
    return {
        "id": job_id,
        "jobfinder_id": upstream.get("id"),
        "title": posting.get("title", ""),
        "company": posting.get("company", ""),
        "location": posting.get("location", ""),
        "pay_text": pay.get("raw", ""),
        "provider": upstream.get("provider"),
        "url": source.get("original_url") or source.get("url") or upstream.get("url") or "",
        "posted": upstream.get("posted"),
        "first_seen": upstream.get("first_seen"),
        "last_seen": upstream.get("last_seen"),
        "list": list_name,
        "cn_status": contract.get("decision_status"),
        "classification": result.final_classification,
        "fit_score": result.fit_score,
    }


def build_served_view(raw: RankedResults, state: ResolutionState, job_id_of, store_info=None) -> ServedView:
    """Apply decisions to freshly ranked results. ``raw`` is never modified.

    With no decisions the returned lists contain the very same rows in the
    same order, so serving is unchanged."""
    lists = (("shortlist", raw.shortlist), ("review", raw.review), ("excluded", raw.excluded))

    # Collect the uncertain groups from the group metadata live_adapter put in
    # each member's upstream_records (dup_group).
    groups: dict[str, dict] = {}
    for list_name, results in lists:
        for result in results:
            meta = _result_upstream(result).get("dup_group")
            if not isinstance(meta, dict) or not meta.get("fingerprint"):
                continue
            entry = groups.setdefault(meta["fingerprint"], {
                "fingerprint": meta["fingerprint"],
                "member_ids": list(meta.get("member_ids") or []),
                "members": [],
                "reason": (result.contract.duplicates or {}).get("reason", ""),
            })
            entry["members"].append((list_name, result))

    # Orphaned decisions (their group no longer exists as it was) explain a
    # group that "changed since you decided".
    orphans = [e for fp, e in state.active.items() if fp not in groups]
    orphan_order = {e["event_id"]: i for i, e in enumerate(state.active.values())}

    payload_groups = []
    replacements: dict[str, object] = {}     # job id -> annotated served row
    folded_ids: dict[str, str] = {}          # job id of folded row -> job id of kept row
    folded_rows: dict[str, object] = {}
    counts = {"keep": 0, "merge": 0, "dismiss": 0, "open": 0, "changed": 0}
    folded_total = 0

    for fingerprint, entry in groups.items():
        decision = state.active.get(fingerprint)
        members = sorted(
            ((name, result, job_id_of(result.contract.source)) for name, result in entry["members"]),
            key=lambda m: (_result_upstream(m[1]).get("id") is None, _result_upstream(m[1]).get("id") or 0),
        )
        member_payloads = [_member_payload(r, name, jid) for name, r, jid in members]
        group_state = "open"
        changed = None
        resolution_meta = None
        if decision is not None:
            group_state = decision["action"]
            counts[group_state] += 1
            resolution_meta = {
                "action": decision["action"],
                "event_id": decision["event_id"],
                "decided_at": decision["ts"],
                "kept_jobfinder_id": decision.get("kept_id"),
                "note": decision.get("note"),
            }
        else:
            counts["open"] += 1
            current_ids = set(entry["member_ids"])
            related = [e for e in orphans if current_ids & set(e["member_ids"])]
            if related:
                previous = max(related, key=lambda e: orphan_order[e["event_id"]])
                previous_ids = set(previous["member_ids"])
                changed = {
                    "previous_action": previous["action"],
                    "decided_at": previous["ts"],
                    "added_jobfinder_ids": sorted(current_ids - previous_ids),
                    "removed_jobfinder_ids": sorted(previous_ids - current_ids),
                }
                group_state = "changed"
                counts["changed"] += 1

        if decision is not None:
            kept_job_id = None
            if decision["action"] == "merge":
                kept_jf = decision["kept_id"]
                kept_job_id = next(jid for _, r, jid in members if _result_upstream(r).get("id") == kept_jf)
            folded_here = []
            for name, result, jid in members:
                jf = _result_upstream(result).get("id")
                base_meta = {
                    "action": decision["action"],
                    "event_id": decision["event_id"],
                    "decided_at": decision["ts"],
                    "group_fingerprint": fingerprint,
                }
                if decision["action"] == "merge" and jid != kept_job_id:
                    folded_ids[jid] = kept_job_id
                    folded_here.append((jid, result))
                    folded_rows[jid] = _annotated(result, duplicate_resolution={
                        **base_meta, "role": "folded", "kept_jobfinder_id": decision["kept_id"],
                    })
                elif decision["action"] == "merge":
                    replacements[jid] = None  # filled in below once folded rows are known
                else:
                    replacements[jid] = _annotated(result, duplicate_resolution=base_meta)
            if decision["action"] == "merge":
                extra = sum(_result_upstream(r).get("exact_duplicate_count", 1) for _, r in folded_here)
                kept_result = next(r for _, r, jid in members if jid == kept_job_id)
                kept_count = _result_upstream(kept_result).get("exact_duplicate_count", 1) + extra
                replacements[kept_job_id] = _annotated(
                    kept_result,
                    exact_duplicate_count=kept_count,
                    duplicate_resolution={
                        "action": "merge", "event_id": decision["event_id"],
                        "decided_at": decision["ts"], "group_fingerprint": fingerprint,
                        "role": "kept", "kept_jobfinder_id": decision["kept_id"],
                        "folded_jobfinder_ids": sorted(
                            _result_upstream(r).get("id") for _, r in folded_here),
                    },
                )
                folded_total += len(folded_here)

        payload_groups.append({
            "fingerprint": fingerprint,
            "size": len(members),
            "reason": entry["reason"],
            "state": group_state,
            "members": member_payloads,
            "resolution": resolution_meta,
            "changed_since_decision": changed,
        })

    payload_groups.sort(key=lambda g: (
        _STATE_ORDER[g["state"]],
        (g["members"][0]["company"] or "").casefold(),
        (g["members"][0]["title"] or "").casefold(),
        g["fingerprint"],
    ))

    def rebuild(results):
        out = []
        for result in results:
            jid = job_id_of(result.contract.source)
            if jid in folded_ids:
                continue
            out.append(replacements.get(jid, result) or result)
        return out

    shortlist = rebuild(raw.shortlist)
    ranked = RankedResults(
        shortlist=tuple(replace(r, shortlist_rank=i + 1) for i, r in enumerate(shortlist)),
        review=tuple(rebuild(raw.review)),
        excluded=tuple(rebuild(raw.excluded)),
    )
    index = {
        job_id_of(r.contract.source): r
        for group in (ranked.shortlist, ranked.review, ranked.excluded)
        for r in group
    }
    summary = {
        "groups": len(payload_groups),
        "open": counts["open"],
        "changed": counts["changed"],
        "keep": counts["keep"],
        "merge": counts["merge"],
        "dismiss": counts["dismiss"],
        "folded_rows": folded_total,
        "uncertain_rows": sum(g["size"] for g in payload_groups),
    }
    return ServedView(ranked, index, folded_rows, tuple(payload_groups), summary, store_info or {})


def group_lookup(view: ServedView) -> dict:
    """fingerprint -> {"fingerprint", "member_ids"} for every current group."""
    return {
        g["fingerprint"]: {
            "fingerprint": g["fingerprint"],
            "member_ids": [m["jobfinder_id"] for m in g["members"]],
        }
        for g in view.groups
    }
