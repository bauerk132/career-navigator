"""Applications board: events, fold, matching and export (Phase 3c WS4).

The board shows every row of Kyle's tracker CSV (read by ``tracker_adapter``,
never written) in four columns, plus jobs he chose to track from the
shortlist. What he changes on the board is saved as events in
``applications.jsonl`` in the state folder, one JSON object per line, never in
the tracker:

  move        set the card's status (one of the tracker's statuses)
  next_step   set or clear the next step text
  follow_up   set or clear the follow-up date
  note        add a dated note
  unlink      "this tracker row is not that posting": drop the automatic match
  track       create a Ready entry for a shortlist posting
  undo        names one earlier event of the same card; that event stops applying
  export      written after "Export to tracker" saved a new dated CSV copy

"Export to tracker" writes a NEW file (``job_tracker.YYYY-MM-DD.csv``) in the
state folder's ``tracker-exports`` folder: the tracker as read, with these
events applied. The original is never opened for writing. If Kyle later puts
an export in place of his tracker, the events already in that file are
recognised (by the export's SHA-256) and not applied twice.

Nothing here sends mail, submits a form or calls an outside service. Apply
links are only links; resume and cover-letter paths are only text.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .sqlite_log import SqliteLog
from .state_store import StateError
from .tracker_adapter import (
    COLUMNS, STATUSES, TrackerImport, file_signature, load_tracker, parse_date, today_iso,
)
from .urls import canonical_url

APPLICATIONS_FILENAME = "applications.jsonl"
EXPORT_DIRNAME = "tracker-exports"
EVENT_VERSION = 1
TEXT_MAX = {"next_step": 300, "note": 1000}
CHANGE_TYPES = ("move", "next_step", "follow_up", "note", "unlink", "track")
EVENT_TYPES = CHANGE_TYPES + ("undo", "export")
EXPORTED_BY = "career-navigator"

BOARD_COLUMNS = (
    {"key": "to_apply", "label": "To apply", "statuses": ("Ready", "In Progress")},
    {"key": "applied", "label": "Applied, waiting", "statuses": ("Applied",)},
    {"key": "heard_back", "label": "Heard back", "statuses": ("Heard Back", "Interview")},
    {"key": "closed", "label": "Closed", "statuses": ("Rejected", "Closed", "Passed")},
)
_COLUMN_OF = {s: c["key"] for c in BOARD_COLUMNS for s in c["statuses"]}
UNKNOWN_COLUMN = {"key": "unknown_status", "label": "Status not recognised", "statuses": ()}

_CATEGORY_NAMES = {"food": "Food", "office": "Office", "it": "IT", "office_it": "Office/IT",
                   "office/it": "Office/IT"}
_KEY_RE = re.compile(r"^(tracker:.+|tracker-row:[0-9a-f]{16}(:\d+)?|job:\d+)$")
_EXPORT_RE = re.compile(r"^job_tracker\.\d{4}-\d{2}-\d{2}(-\d+)?\.csv$")
_CN_ROW_ID = re.compile(r"^cn-(\d+)$")
_LINKEDIN_NUMBER = re.compile(r"^\d{8,12}$")

BASIS_LABELS = {
    "same_link": "Same job link",
    "linkedin_job_number": "Same LinkedIn job number",
    "indeed_job_key": "Same Indeed job key",
    "tracker_job_id": "Tracker job id is the LinkedIn job number",
    "tracked_from_shortlist": "Tracked from the shortlist",
}


class ApplicationError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def card_id(app_key: str) -> str:
    return hashlib.sha256(app_key.encode("utf-8")).hexdigest()[:12]


def category_label(raw) -> str:
    text = str(raw or "").strip()
    if not text:
        return "Uncategorized"
    return _CATEGORY_NAMES.get(text.casefold(), text)


def row_app_key(row) -> str:
    """A tracker row's event key. A row exported from a tracked shortlist job
    (job id ``cn-<JobFinder id>``) keeps that job's key, so its history follows it."""
    match = _CN_ROW_ID.match(row.values.get("job_id", "").strip())
    return f"job:{match.group(1)}" if match else row.key


def safe_cell(text: str) -> str:
    """Text Career Navigator writes into an exported cell. A leading = + @ or
    tab would make Excel read the cell as a formula, so it is quoted."""
    return "'" + text if text[:1] in ("=", "+", "@", "\t", "\r") else text


# ---------------------------------------------------------------------------
# Events and fold
# ---------------------------------------------------------------------------

def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _short_text(value, limit) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit


def _parse_event(record: dict) -> dict | None:
    if record.get("v") != EVENT_VERSION:
        return None
    if not isinstance(record.get("event_id"), str) or not record["event_id"]:
        return None
    kind = record.get("type")
    if kind not in EVENT_TYPES:
        return None
    if kind == "export":
        ok = (isinstance(record.get("file"), str) and _EXPORT_RE.match(record["file"])
              and isinstance(record.get("sha256"), str) and len(record["sha256"]) == 64)
        return record if ok else None
    key = record.get("app_key")
    if not isinstance(key, str) or not _KEY_RE.match(key):
        return None
    if kind == "move":
        ok = record.get("status") in STATUSES
    elif kind == "next_step":
        ok = record.get("text") is None or _short_text(record["text"], TEXT_MAX["next_step"])
    elif kind == "follow_up":
        ok = record.get("date") is None or parse_date(record["date"]) == (record["date"], True)
    elif kind == "note":
        ok = _short_text(record.get("text"), TEXT_MAX["note"])
    elif kind == "unlink":
        ok = _is_int(record.get("jobfinder_id"))
    elif kind == "track":
        ok = (_is_int(record.get("jobfinder_id")) and key == f"job:{record['jobfinder_id']}"
              and isinstance(record.get("title"), str))
    else:  # undo
        ok = isinstance(record.get("undoes"), str)
    return record if ok else None


@dataclass
class CardEvents:
    """What the log says about one card, events in force only."""
    changes: list = field(default_factory=list)   # (seq, event) applied, in order
    last_change_seq: int = -1
    undos: list = field(default_factory=list)     # (undo seq, seq of the change it undid)


@dataclass(frozen=True)
class ApplicationState:
    cards: dict            # app_key -> CardEvents
    exports: tuple         # (seq, export event), in order
    event_count: int
    skipped: int
    signature: object


def fold_events(records: list[dict], skipped_lines: int = 0, signature=None) -> ApplicationState:
    """Fold the log in file order. An undo removes the one event it names (an
    earlier change of the same card); an undo itself, and an export, cannot be
    undone. An undo counts as a change at its own position, so a card whose
    exported change was undone shows as not exported again."""
    events, skipped = [], skipped_lines
    for record in records:
        event = _parse_event(record)
        if event is None:
            skipped += 1
        else:
            events.append(event)
    by_id = {e["event_id"]: (i, e) for i, e in enumerate(events)}
    undone, undone_by = set(), {}   # undone_by: undo event id -> seq of the change it undid
    for seq, event in enumerate(events):
        if event["type"] != "undo":
            continue
        target = by_id.get(event["undoes"])
        if (target is None or target[0] >= seq or target[1]["type"] not in CHANGE_TYPES
                or target[1]["app_key"] != event["app_key"] or event["undoes"] in undone):
            skipped += 1
            continue
        undone.add(event["undoes"])
        undone_by[event["event_id"]] = target[0]
    cards: dict[str, CardEvents] = {}
    exports = []
    for seq, event in enumerate(events):
        if event["type"] == "export":
            exports.append((seq, event))
            continue
        card = cards.setdefault(event["app_key"], CardEvents())
        if event["type"] == "undo":
            if undone_by.get(event["event_id"]) is not None:
                card.undos.append((seq, undone_by[event["event_id"]]))
                card.last_change_seq = max(card.last_change_seq, seq)
            continue
        if event["event_id"] in undone:
            continue
        card.changes.append((seq, event))
        card.last_change_seq = max(card.last_change_seq, seq)
    return ApplicationState(cards, tuple(exports), len(events), skipped, signature)


class Applications:
    """Reads and appends ``applications.jsonl``. ``state_dir`` None means
    persistence is off: reads are empty and every write is refused."""

    def __init__(self, state_dir):
        self.enabled = state_dir is not None
        self.state_dir = Path(state_dir) if self.enabled else None
        self._log = SqliteLog(self.state_dir, "applications", APPLICATIONS_FILENAME) if self.enabled else None
        self.write_lock = threading.Lock()

    @property
    def export_dir(self) -> Path | None:
        return self.state_dir / EXPORT_DIRNAME if self.enabled else None

    def state(self) -> ApplicationState:
        if not self.enabled:
            return ApplicationState({}, (), 0, 0, None)
        records, skipped, signature = self._log.read()
        return fold_events(records, skipped, signature)

    def append(self, event: dict) -> dict:
        if not self.enabled:
            raise ApplicationError("changes are not saved in this mode (no state folder)", status=503)
        full = {"v": EVENT_VERSION, "event_id": uuid.uuid4().hex, "ts": _utc_now(),
                "on": today_iso(), **event}
        if _parse_event(full) is None:
            raise ApplicationError("that change is not valid")
        try:
            self._log.append(full)
        except StateError as exc:
            raise ApplicationError(str(exc), status=503) from exc
        return full


# ---------------------------------------------------------------------------
# Matching tracker rows to postings
# ---------------------------------------------------------------------------

def _identity(url: str):
    try:
        return canonical_url(url)
    except ValueError:
        return None


def build_posting_index(results) -> dict:
    """Lookup maps over served postings: canonical link and provider job id."""
    by_url, by_provider = {}, {}
    for result in results:
        source = result.contract.source
        for url in (source.get("url"), source.get("original_url")):
            ident = _identity(url or "")
            if ident is None:
                continue
            by_url.setdefault(ident.url, result)
            if ident.provider in ("linkedin", "indeed") and ident.provider_id:
                by_provider.setdefault((ident.provider, ident.provider_id), result)
    return {"url": by_url, "provider": by_provider}


def match_candidates(values: dict, index: dict) -> list[tuple[str, object]]:
    """Possible postings for a tracker row, strongest basis first."""
    found, seen = [], set()

    def add(basis, result):
        if result is not None and id(result) not in seen:
            seen.add(id(result))
            found.append((basis, result))

    idents = [i for i in (_identity(values.get(k, "")) for k in ("linkedin_url", "apply_url")) if i]
    for ident in idents:
        add("same_link", index["url"].get(ident.url))
    for ident in idents:
        if ident.provider_id and ident.provider in ("linkedin", "indeed"):
            basis = "linkedin_job_number" if ident.provider == "linkedin" else "indeed_job_key"
            add(basis, index["provider"].get((ident.provider, ident.provider_id)))
    job_id = values.get("job_id", "").strip()
    if _LINKEDIN_NUMBER.match(job_id):
        add("tracker_job_id", index["provider"].get(("linkedin", job_id)))
    return found


# ---------------------------------------------------------------------------
# Board view
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BoardView:
    cards: tuple
    by_id: dict
    columns: tuple
    next_steps: tuple
    tracked_job_ids: dict    # posting api id -> card id
    summary: dict
    baseline_export: dict | None


def _effective(base: dict, changes: list) -> tuple[dict, list]:
    """Apply a card's events (in order) to its tracker values. Returns the new
    values and the CN notes."""
    values = dict(base)
    notes = []
    for _seq, event in changes:
        kind, on = event["type"], event.get("on") or event["ts"][:10]
        if kind == "move":
            values["status"] = event["status"]
            if event["status"] == "Applied" and not values.get("date_applied", "").strip():
                values["date_applied"] = on
            if event["status"] in ("Heard Back", "Interview"):
                values["heard_back"] = "Y"
                if not values.get("heard_back_date", "").strip():
                    values["heard_back_date"] = on
        elif kind == "next_step":
            values["next_step"] = event.get("text") or ""
        elif kind == "follow_up":
            values["follow_up_date"] = event.get("date") or ""
        elif kind == "note":
            notes.append({"event_id": event["event_id"], "on": on, "text": event["text"]})
    return values, notes


def _change_summary(event: dict) -> str:
    kind = event["type"]
    if kind == "move":
        return f"Moved to {event['status']}"
    if kind == "next_step":
        return "Set next step" if event.get("text") else "Cleared next step"
    if kind == "follow_up":
        return f"Follow up on {event['date']}" if event.get("date") else "Cleared follow-up date"
    if kind == "note":
        return "Added a note"
    if kind == "unlink":
        return "Unlinked the matched posting"
    return "Tracked from the shortlist"


def _unexported(events: CardEvents, mark: int) -> bool:
    return (any(seq > mark for seq, _e in events.changes)
            or any(undo_seq > mark and target_seq <= mark for undo_seq, target_seq in events.undos))


def _match_block(basis: str, result, api_id_of) -> dict:
    upstream = result.contract.upstream_records or {}
    return {
        "jobfinder_id": upstream.get("id"),
        "api_id": api_id_of(result.contract.source),
        "basis": basis,
        "basis_label": BASIS_LABELS[basis],
        "title": result.contract.posting.get("title", ""),
        "company": result.contract.posting.get("company", ""),
        "fit_score": result.fit_score,
        "final_classification": result.final_classification,
        "url": result.contract.source.get("url"),
    }


def build_board(tracker: TrackerImport | None, state: ApplicationState, postings,
                api_id_of, today: str | None = None) -> BoardView:
    """The board: every tracker row, plus shortlist jobs Kyle tracked, with his
    events applied. ``postings`` are the served postings used for matching."""
    today = today or today_iso()
    index = build_posting_index(postings)
    by_jobfinder_id = {}
    for result in postings:
        jf = (result.contract.upstream_records or {}).get("id")
        if _is_int(jf):
            by_jobfinder_id.setdefault(jf, result)

    # Events already inside the tracker file (it is one of our exports).
    baseline = None
    if tracker is not None:
        for seq, export in state.exports:
            if export["sha256"] == tracker.sha256:
                baseline = (seq, export)
    baseline_seq = baseline[0] if baseline else -1
    last_export_seq = state.exports[-1][0] if state.exports else -1

    def pending(changes):
        return [(s, e) for s, e in changes if s > baseline_seq]

    entries = []   # (app_key, base values, row or None, card events)
    tracker_keys = set()
    if tracker is not None:
        for row in tracker.rows:
            key = row_app_key(row)
            tracker_keys.add(key)
            entries.append((key, row.values, row, state.cards.get(key, CardEvents())))
    for key, events in state.cards.items():
        if key in tracker_keys or not key.startswith("job:"):
            continue
        track = next((e for s, e in events.changes if e["type"] == "track"), None)
        if track is None:
            continue  # its track event was undone: the entry is gone
        base = {c: "" for c in COLUMNS}
        base.update({
            "job_id": f"cn-{track['jobfinder_id']}", "title": track.get("title", ""),
            "company": track.get("company", ""), "location": track.get("location", ""),
            "pay": track.get("pay", ""), "category": track.get("category", ""),
            "apply_url": track.get("url", ""), "status": "Ready",
            "date_found": track.get("on", ""), "assigned_agent": EXPORTED_BY,
        })
        entries.append((key, base, None, events))

    cards, tracked = [], {}
    counts = {c["key"]: 0 for c in BOARD_COLUMNS}
    counts[UNKNOWN_COLUMN["key"]] = 0
    matched = 0
    for key, base, row, events in entries:
        applying = pending(events.changes)
        values, cn_notes = _effective(base, applying)
        status = values.get("status", "").strip()
        status = next((s for s in STATUSES if s.casefold() == status.casefold()), None)
        column = _COLUMN_OF.get(status, UNKNOWN_COLUMN["key"])
        counts[column] += 1
        unlinked = {e["jobfinder_id"] for _s, e in events.changes if e["type"] == "unlink"}
        match = None
        if key.startswith("job:") and row is None:
            jf = int(key.split(":", 1)[1])
            if jf in by_jobfinder_id:
                match = _match_block("tracked_from_shortlist", by_jobfinder_id[jf], api_id_of)
        else:
            for basis, result in match_candidates(values, index):
                if (result.contract.upstream_records or {}).get("id") in unlinked:
                    continue
                match = _match_block(basis, result, api_id_of)
                break
        if match is not None:
            matched += 1
        dates = {c: parse_date(values.get(c))[0] for c in
                 ("date_found", "date_applied", "heard_back_date", "follow_up_date", "last_updated")}
        cid = card_id(key)
        card = {
            "id": cid,
            "app_key": key,
            "source": "tracker" if row is not None else "tracked",
            "row_number": row.row_number if row is not None else None,
            "job_id": values.get("job_id", "").strip(),
            "title": values.get("title", ""),
            "company": values.get("company", ""),
            "location": values.get("location", ""),
            "work_type": values.get("work_type", ""),
            "pay": values.get("pay", ""),
            "category": category_label(values.get("category")),
            "status": status or values.get("status", ""),
            "column": column,
            "heard_back": values.get("heard_back", ""),
            "response_type": values.get("response_type", ""),
            "next_step": values.get("next_step", ""),
            "dates": dates,
            "follow_up_due": bool(dates["follow_up_date"] and dates["follow_up_date"] <= today),
            "apply_url": values.get("apply_url", ""),
            "linkedin_url": values.get("linkedin_url", ""),
            "apply_platform": values.get("apply_platform", ""),
            "description_summary": values.get("description_summary", ""),
            "fit_notes": values.get("fit_notes", ""),
            "resume_file": values.get("resume_file", ""),
            "cover_letter_file": values.get("cover_letter_file", ""),
            # Private: served to the local dashboard only (the API strips them otherwise).
            "confirmation_number": values.get("confirmation_number", ""),
            "notes": values.get("notes", ""),
            "cn_notes": cn_notes,
            "match": match,
            "unlinked_jobfinder_ids": sorted(unlinked),
            "changes": [{"event_id": e["event_id"], "type": e["type"], "on": e.get("on"),
                         "summary": _change_summary(e)}
                        for s, e in applying if e["type"] != "track" or row is None],
            # Not exported yet: a change in force made after the last export, or an
            # undo after it of a change that export already wrote. A change made and
            # undone with no export between them leaves nothing to export.
            "unexported": _unexported(events, max(last_export_seq, baseline_seq)),
            "problems": [{"column": c, "issue": i} for c, i in (row.problems if row else ())],
            # WS3B hook: provenance of the matched posting goes here once it lands.
            "provenance": None,
        }
        cards.append(card)
        if match is not None and match.get("api_id"):
            tracked.setdefault(match["api_id"], cid)

    def strip_key(card):
        due = card["dates"]["follow_up_date"]
        return (0 if due else 1, due or "", card["title"].casefold())

    next_steps = tuple(c["id"] for c in sorted(
        (c for c in cards if c["column"] != "closed"
         and (c["next_step"].strip() or c["follow_up_due"])), key=strip_key))
    columns = [dict(c, statuses=list(c["statuses"]), count=counts[c["key"]]) for c in BOARD_COLUMNS]
    if counts[UNKNOWN_COLUMN["key"]]:
        columns.append(dict(UNKNOWN_COLUMN, statuses=[], count=counts[UNKNOWN_COLUMN["key"]]))
    categories = {}
    for card in cards:
        categories[card["category"]] = categories.get(card["category"], 0) + 1
    summary = {
        "cards": len(cards),
        "tracker_rows": len(tracker.rows) if tracker is not None else 0,
        "tracked_from_shortlist": sum(1 for c in cards if c["source"] == "tracked"),
        "matched": matched,
        "unmatched": len(cards) - matched,
        "by_column": counts,
        "categories": dict(sorted(categories.items())),
        "unexported": sum(1 for c in cards if c["unexported"]),
        "next_steps": len(next_steps),
    }
    baseline_info = ({"file": baseline[1]["file"], "exported_at": baseline[1]["ts"]}
                     if baseline else None)
    return BoardView(tuple(cards), {c["id"]: c for c in cards}, tuple(columns), next_steps,
                     tracked, summary, baseline_info)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

EXPORTED_COLUMNS = ("status", "next_step", "follow_up_date", "date_applied",
                    "heard_back", "heard_back_date", "notes")


def export_rows(tracker: TrackerImport, board: BoardView) -> tuple[list[str], list[list[str]], list[dict]]:
    """(header, rows, changes) for an export: the tracker as read, in its own
    column order, with board changes applied; tracked jobs appended."""
    header = list(tracker.header)
    for column in COLUMNS:
        if column not in header:
            header.append(column)
    by_row = {c["row_number"]: c for c in board.cards if c["row_number"] is not None}
    changes, out = [], []

    def merged(base: dict, card: dict | None) -> tuple[dict, list]:
        values = dict(base)
        diffs = []
        if card is None or not card["changes"]:
            return values, diffs
        after = {
            "status": card["status"],
            "next_step": card["next_step"],
            "follow_up_date": card["dates"]["follow_up_date"] or "",
            "date_applied": base.get("date_applied", "") or (card["dates"]["date_applied"] or ""),
            "heard_back": card["heard_back"],
            "heard_back_date": base.get("heard_back_date", "") or (card["dates"]["heard_back_date"] or ""),
        }
        # A follow-up date the tracker already had, in its own style, stays as written.
        if parse_date(base.get("follow_up_date"))[0] == after["follow_up_date"] and after["follow_up_date"]:
            after["follow_up_date"] = base.get("follow_up_date", "")
        if card["cn_notes"]:
            added = " | ".join(f"{n['on']}: {n['text']}" for n in card["cn_notes"])
            existing = base.get("notes", "")
            after["notes"] = (existing + " | " if existing else "") + added
        for column, value in after.items():
            if value != values.get(column, ""):
                written = safe_cell(value) if column in ("next_step", "notes") else value
                diffs.append({"column": column, "before": values.get(column, ""), "after": written,
                              "private": column == "notes"})
                values[column] = written
        if diffs:
            values["last_updated"] = today_iso()
            values["last_updated_by"] = EXPORTED_BY
        return values, diffs

    for row in tracker.rows:
        card = by_row.get(row.row_number)
        values, diffs = merged(row.values, card)
        out.append([values.get(h, "") for h in header])
        if diffs:
            changes.append({"id": card["id"], "row_number": row.row_number, "title": card["title"],
                            "company": card["company"], "columns": diffs, "new_row": False})
    for card in board.cards:
        if card["source"] != "tracked":
            continue
        base = {h: "" for h in header}
        base.update({
            "job_id": card["job_id"], "date_found": card["dates"]["date_found"] or "",
            "assigned_agent": EXPORTED_BY, "category": card["category"],
            "title": safe_cell(card["title"]), "company": safe_cell(card["company"]),
            "location": safe_cell(card["location"]), "pay": safe_cell(card["pay"]),
            "apply_url": card["apply_url"], "status": "Ready",
        })
        values, _ = merged(base, card)
        values["last_updated"] = today_iso()
        values["last_updated_by"] = EXPORTED_BY
        out.append([values.get(h, "") for h in header])
        changes.append({"id": card["id"], "row_number": None, "title": card["title"],
                        "company": card["company"], "columns": [], "new_row": True})
    return header, out, changes


def export_token(tracker: TrackerImport, state: ApplicationState) -> str:
    """Ties a preview to the exact tracker bytes and event log it was built on."""
    basis = f"{tracker.sha256}|{state.event_count}|{state.signature}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def next_export_path(export_dir: Path, on: str) -> Path:
    candidate = export_dir / f"job_tracker.{on}.csv"
    n = 2
    while candidate.exists():
        candidate = export_dir / f"job_tracker.{on}-{n}.csv"
        n += 1
    return candidate


def write_export(tracker_path: Path, export_dir: Path, header, rows) -> dict:
    """Write a new CSV (never overwriting anything) and check the original
    tracker is byte-identical afterwards. UTF-8 with a BOM and CRLF line ends
    so Excel opens it cleanly."""
    before = load_tracker(tracker_path).sha256
    export_dir.mkdir(parents=True, exist_ok=True)
    path = next_export_path(export_dir, today_iso())
    try:
        with open(path, "x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(header)
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ApplicationError(f"cannot write the export: {exc}", status=503) from exc
    after = load_tracker(tracker_path).sha256
    if before != after:
        raise ApplicationError("the tracker changed while exporting; nothing recorded", status=409)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"file": path.name, "path": str(path), "sha256": sha, "rows": len(rows),
            "original_sha256": before, "original_signature": file_signature(tracker_path)}
