"""FastAPI backend (CN-004; Slice C; Phase 2 V2 live wiring).

Serves the Slice A contract + Slice B ranking over the synthetic adapter
by default, or over the live JobFinder adapter when a database is configured
(create_app(db_path=...) or the JOBFINDER_DB_PATH env var). Stateless and
read-only: every load and refresh re-reads the source and recomputes through
the pipeline. No persistence in this slice (the app SQLite is V3 scope).
No auth: local-first, localhost only. CORS allows localhost origins only,
so the laptop-local dashboard (Vite, default :5173) can call it.

Failure protocol (S5): a live load failure at startup fails closed (the
lifespan raises LiveAdapterError and startup halts). A live load failure on
POST /api/refresh keeps the last good snapshot, marks health degraded, and
returns HTTP 503; /api/jobs keeps serving the cached snapshot with
stale: true in the envelope.

Run: uvicorn career_navigator.app:app --host 127.0.0.1 --port 8000
"""
import hashlib
import json
import os
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import freshness
from .adapters import default_source_path, load_postings
from .contracts import CURRENT_PAY_HOURLY
from .duplicate_resolutions import (
    DuplicateResolutions,
    ResolutionError,
    ResolutionState,
    build_served_view,
    group_lookup,
)
from .freshness import parse_eastern
from .review_decisions import (
    REASONS as REVIEW_REASONS,
    ReviewDecisions,
    ReviewError,
    ReviewState,
    build_review_view,
)
from .live_adapter import (
    LiveAdapterError,
    evaluate_live_import,
    ingestion_summary,
    load_live_database,
)
from .pipeline import evaluate_postings
from .policy import SALARY_FLOOR_HOURLY, Policy
from .ranking import RankedResult, rank_evaluations
from .state_store import StateError, resolve_state_dir

_LOCAL_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)

# Staleness threshold for /api/stats (S3): a refresh older than this is stale.
_STALE_THRESHOLD_HOURS = 26.0
# Keys of the /api/gate response. The report body is never returned.
_GATE_RESPONSE_KEYS = frozenset({
    "available", "file", "date", "verdict", "finished_at",
    "hard_failures", "soft_failures", "checks", "error",
})
# Consecutive failed watcher probes before the freshness block reports degraded.
_PROBE_FAILURE_LIMIT = 3
# A real gate report is a few KB; refuse anything bigger rather than load it.
_GATE_REPORT_MAX_BYTES = 1_000_000
_GATE_REPORT_RE = re.compile(r"^live_gate_report\.(\d{4}-\d{2}-\d{2})\.json$")


def job_id(source: dict) -> str:
    """Stable, deterministic id for a posting: first 12 hex chars of the
    SHA-256 of its ORIGINAL URL (C-G2-01). Two distinct postings whose
    originals canonicalize to the same URL must not share an id — hashing
    the canonical URL collapsed them and made one unreachable via detail.
    Falls back to the canonical URL when no original is recorded."""
    url = source.get("original_url") or source["url"]
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _ranked_result_to_dict(result: RankedResult) -> dict:
    contract = result.contract.to_dict()
    upstream = contract.get("upstream_records") or {}
    duplicates = contract.get("duplicates") or {}
    return {
        "id": job_id(contract["source"]),
        "contract": contract,
        "fit_score": result.fit_score,
        "fit_factors": [
            {
                "name": f.name,
                "weight": f.weight,
                "raw": f.raw,
                "contribution": f.contribution,
                "evidence": f.evidence,
            }
            for f in result.fit_factors
        ],
        "gaps": list(result.gaps),
        "final_classification": result.final_classification,
        "shortlist_rank": result.shortlist_rank,
        # Live-mode convenience fields (S2); None / 1 in synthetic mode.
        "jobfinder_id": upstream.get("id"),
        "cn_status": contract["decision_status"],
        "duplicate_confidence": duplicates.get("confidence"),
        "duplicate_reason": duplicates.get("reason", ""),
        "exact_duplicate_count": upstream.get("exact_duplicate_count", 1),
        "upstream_verdict": upstream.get("verdict"),
        # Phase 3c WS5: the decision in force for this posting's uncertain
        # group (keep / merge / dismiss), or None. Raw duplicate_confidence
        # above is never changed by a decision.
        "duplicate_resolution": upstream.get("duplicate_resolution"),
        # Phase 3c WS7: on a Needs Review posting, the per-reason state of
        # Kyle's review decisions (open / pass / reject / changed) and the
        # status the posting is served under after them. cn_status above is
        # always the raw policy status. None for rows that were never in review.
        "review_decision": upstream.get("review_decision"),
    }


def _totals(snapshot: dict, ranked=None) -> dict:
    # Takes the snapshot bound once by the endpoint (C-N4) so a /api/refresh
    # interleaving mid-request can never mix generations inside one response.
    # ``ranked`` is the served view (decisions applied); default is the raw ranking.
    ranked = ranked if ranked is not None else snapshot["ranked"]
    return {
        "shortlist": len(ranked.shortlist),
        "review": len(ranked.review),
        "excluded": len(ranked.excluded),
        "total": len(ranked.shortlist) + len(ranked.review) + len(ranked.excluded),
    }


def _index(ranked) -> dict:
    return {
        job_id(result.contract.to_dict()["source"]): result
        for group in (ranked.shortlist, ranked.review, ranked.excluded)
        for result in group
    }


def _exact_duplicate_count_sum(snapshot: dict, ranked=None) -> int:
    """Reconciliation invariant (S2): summed over all served postings, the
    exact_duplicate_count values must equal the raw ingested row count.
    Merged duplicates add their folded rows to the kept row's count, so the
    invariant holds with decisions applied too."""
    ranked = ranked if ranked is not None else snapshot["ranked"]
    total = 0
    for group in (ranked.shortlist, ranked.review, ranked.excluded):
        for result in group:
            upstream = result.contract.upstream_records or {}
            total += upstream.get("exact_duplicate_count", 1)
    return total


# JobFinder timestamps are naive US Eastern local time (S3/F-03). The parser
# lives in freshness.py; the old name stays importable from here.
_parse_eastern_timestamp = parse_eastern


def _staleness(health: dict) -> dict:
    """Staleness block for /api/stats (S3/F-03). Hours since refresh are
    computed from state.last_updated parsed as US Eastern time, falling
    back to max_last_seen; when neither parses, freshness is unknown and
    the snapshot is reported stale (loud, never hidden)."""
    basis = None
    basis_name = None
    for name in ("last_updated", "max_last_seen"):
        parsed = _parse_eastern_timestamp(health.get(name))
        if parsed is not None:
            basis = parsed
            basis_name = name
            break
    now = datetime.now(timezone.utc)
    hours = (now - basis).total_seconds() / 3600.0 if basis is not None else None
    return {
        "last_updated": health.get("last_updated"),
        "max_last_seen": health.get("max_last_seen"),
        "hours_since_refresh": round(hours, 2) if hours is not None else None,
        "is_stale": True if hours is None else hours > _STALE_THRESHOLD_HOURS,
        "stale_threshold_hours": _STALE_THRESHOLD_HOURS,
        "basis": basis_name,
        "sources": health.get("sources"),
    }


def _file_mtime(path) -> float | None:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _reload(app: FastAPI) -> None:
    policy = app.state.policy
    if app.state.db_path is not None:
        # mtime is taken before the read so a write during the load is seen
        # as a change by the next watcher tick.
        db_mtime = _file_mtime(app.state.db_path)
        live_import = load_live_database(app.state.db_path, policy)
        ranked = rank_evaluations(evaluate_live_import(live_import, policy), policy)
        health = dict(live_import.health)
        health["degraded"] = False
        health["db_mtime"] = db_mtime
        # One snapshot object, swapped in a single assignment (C-N2): a
        # concurrent refresh+read can never mix generations.
        app.state.snapshot = {
            "ranked": ranked,
            "index": _index(ranked),
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "source": "live",
            "health": health,
            "ingestion": ingestion_summary(live_import),
        }
        return
    postings = load_postings(app.state.source_path)
    ranked = rank_evaluations(evaluate_postings(postings, policy), policy)
    app.state.snapshot = {
        "ranked": ranked,
        "index": _index(ranked),
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "source": "synthetic",
        "health": None,
        "ingestion": None,
    }


def _ensure_loaded(app: FastAPI) -> None:
    if app.state.snapshot is None:
        _reload(app)


def _served_view(app: FastAPI, snapshot: dict):
    """The snapshot with Kyle's duplicate decisions applied (Phase 3c WS5).

    The raw snapshot is never modified. The overlay is rebuilt only when the
    snapshot object or the decisions file changes. A damaged or unreadable
    decisions file is reported (store.error) and the raw ranking is served,
    never silently hidden: the dashboard shows the error."""
    resolutions = app.state.resolutions
    store = {"enabled": resolutions.enabled, "events": 0, "skipped_lines": 0, "error": None}
    try:
        state = resolutions.state()
    except StateError as exc:
        store["error"] = str(exc)
        state = None
    reviews = app.state.reviews
    review_store = {"enabled": reviews.enabled, "events": 0, "skipped_lines": 0, "error": None}
    try:
        review_state = reviews.state()
    except StateError as exc:
        review_store["error"] = str(exc)
        review_state = None
    signature = (
        (state.signature, state.event_count) if state is not None else ("error", store["error"]),
        (review_state.signature, review_state.event_count) if review_state is not None
        else ("error", review_store["error"]),
    )
    cached = app.state.view_cache
    if cached is not None and cached[0] is snapshot and cached[1] == signature:
        return cached[2]
    if state is None:
        state = ResolutionState({}, 0, 0, None)
    else:
        store["events"] = state.event_count
        store["skipped_lines"] = state.skipped
    if review_state is None:
        review_state = ReviewState({}, 0, 0, None)
    else:
        review_store["events"] = review_state.event_count
        review_store["skipped_lines"] = review_state.skipped
    view = build_served_view(
        snapshot["ranked"], state, lambda source: job_id(source), store_info=store,
    )
    # Review decisions go on top of the duplicate overlay (WS7). A row folded
    # away by a duplicate merge is not in these lists, so it is never reviewed.
    review_view = build_review_view(
        view.ranked, review_state, app.state.policy, lambda source: job_id(source),
        store_info=review_store,
    )
    view = replace(view, ranked=review_view.ranked, index=review_view.index, review=review_view)
    app.state.view_cache = (snapshot, signature, view)
    return view


def _is_degraded(snapshot: dict) -> bool:
    return bool((snapshot.get("health") or {}).get("degraded"))


def _safe_reload(app: FastAPI) -> tuple[bool, str | None]:
    """Reload under app.state.reload_lock, keeping the last good snapshot.

    Live mode with a snapshot in place: a LiveAdapterError keeps the old
    snapshot, marks its health degraded, and returns (False, message).
    Otherwise the reload runs directly and any error propagates (fail closed
    at startup, when there is nothing to keep).
    """
    with app.state.reload_lock:
        if app.state.db_path is not None and app.state.snapshot is not None:
            try:
                _reload(app)
            except LiveAdapterError as exc:
                snapshot = app.state.snapshot
                health = snapshot.get("health") or {}
                health["degraded"] = True
                health["last_error"] = str(exc)
                health["error_time"] = datetime.now(timezone.utc).isoformat()
                snapshot["health"] = health
                return False, str(exc)
            return True, None
        _reload(app)
        return True, None


def _watch_once(app: FastAPI) -> str:
    """One watcher tick. Returns a short status string (see tests).

    Probes jobfinder.db read-only. A probe error is not degradation (the DB
    may be locked mid-run). A change in mtime or state.last_updated triggers
    a reload unless the scheduled task is running, in which case the reload
    waits for the next tick.
    """
    if app.state.db_path is None:
        return "synthetic"
    probe = freshness.probe_db(app.state.db_path)
    app.state.last_probe = probe
    if probe["error"]:
        # One failure is silent (the DB may be locked mid-run); a run of them
        # is surfaced by _freshness_block as degraded.
        app.state.consecutive_probe_failures += 1
        return "probe-error"
    app.state.consecutive_probe_failures = 0
    health = (app.state.snapshot or {}).get("health") or {}
    changed = (
        probe["mtime"] != health.get("db_mtime")
        or probe["last_updated"] != health.get("last_updated")
    )
    if not changed:
        return "unchanged"
    if app.state.task_check() is True:
        return "task-running"
    ok, error = _safe_reload(app)
    if ok:
        app.state.auto_reload_error = None
        app.state.last_auto_reload = datetime.now(timezone.utc).isoformat()
        return "reloaded"
    app.state.auto_reload_error = error
    return "reload-failed"


def _watch_loop(app: FastAPI, interval: float, stop: threading.Event) -> None:
    while not stop.wait(interval):
        try:
            _watch_once(app)
        except Exception as exc:  # the watcher must never die silently
            app.state.auto_reload_error = f"{type(exc).__name__}: {exc}"


def _freshness_block(app: FastAPI, snapshot: dict) -> dict:
    health = snapshot.get("health") or {}
    snapshot_degraded = _is_degraded(snapshot)
    probe_degraded = app.state.consecutive_probe_failures >= _PROBE_FAILURE_LIMIT
    probe_error = None
    if probe_degraded:
        probe_error = str((app.state.last_probe or {}).get("error") or "unknown error")[:200]
    block = freshness.classify(
        health.get("last_updated"),
        sources=health.get("sources"),
        db_last_updated=(app.state.last_probe or {}).get("last_updated"),
        degraded=snapshot_degraded or probe_degraded,
    )
    if probe_degraded:
        if not snapshot_degraded:
            # Drop classify's generic "failed reload" reason; it is not what happened.
            block["reasons"] = [r for r in block["reasons"]
                                if not r.startswith("live backend is serving the last good snapshot")]
        block["reasons"].append(f"cannot read jobfinder.db: {probe_error}")
    block["probe_error"] = probe_error
    block["loaded_at"] = snapshot["refreshed_at"]
    block["rows"] = health.get("jobs_count")
    block["watch_interval_seconds"] = app.state.reload_interval
    return block


def _read_gate_summary(reports_dir: str | None) -> dict:
    """Summarize the newest dated live gate report, for /api/gate.

    Report keys mapped (live_gate.py finalize() writes these; the report has
    no soft_failures key, so it is derived):
      verdict        <- report["verdict"]; anything other than PASS/FAIL is UNKNOWN
      finished_at    <- report["finished_at"] (ISO string, or null)
      hard_failures  <- len(report["hard_failures"]) (a list of check names);
                        when absent, count checks with hard=True and passed=False
      soft_failures  <- count of checks with hard=False and passed=False
      checks         <- len(report["checks"])
      date           <- the YYYY-MM-DD in the file name (ET date of the run)
    Only the keys in _GATE_RESPONSE_KEYS are returned; the body is never
    returned. An unreadable or malformed report yields verdict UNKNOWN with
    an error message.
    """
    if not reports_dir or not Path(reports_dir).is_dir():
        return {"available": False}
    dated = []
    try:
        for entry in Path(reports_dir).iterdir():
            match = _GATE_REPORT_RE.match(entry.name)
            if match and entry.is_file():
                dated.append((match.group(1), entry.name))
    except OSError:
        return {"available": False}
    if not dated:
        return {"available": False}
    date, name = max(dated)
    try:
        path = Path(reports_dir) / name
        if path.stat().st_size > _GATE_REPORT_MAX_BYTES:
            raise ValueError("report too large")
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("report is not an object")
    except (OSError, ValueError, RecursionError):
        return {"available": True, "file": name, "date": date,
                "verdict": "UNKNOWN", "error": "unreadable report"}
    raw_checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    checks = [c for c in raw_checks if isinstance(c, dict)]
    hard_list = report.get("hard_failures")
    if isinstance(hard_list, list):
        hard_count = len(hard_list)
    else:
        hard_count = sum(1 for c in checks if c.get("hard") and c.get("passed") is False)
    soft_count = sum(1 for c in checks if not c.get("hard") and c.get("passed") is False)
    verdict = report.get("verdict")
    finished = report.get("finished_at")
    summary = {
        "available": True,
        "file": name,
        "date": date,
        "verdict": verdict if verdict in ("PASS", "FAIL") else "UNKNOWN",
        "finished_at": finished if isinstance(finished, str) else None,
        "hard_failures": hard_count,
        "soft_failures": soft_count,
        "checks": len(raw_checks),
    }
    return {k: v for k, v in summary.items() if k in _GATE_RESPONSE_KEYS}


_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")


class DuplicateEventBody(BaseModel):
    """One write to duplicate_resolutions.jsonl. Only these fields, only
    these event types; anything else is refused (422)."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["resolve", "undo"]
    action: Literal["keep", "merge", "dismiss"] | None = None
    kept_jobfinder_id: int | None = None
    note: str | None = Field(default=None, max_length=500)


_API_ID_RE = re.compile(r"^[0-9a-f]{12}$")


class ReviewEventBody(BaseModel):
    """One write to review_decisions.jsonl. Only these fields, only these event
    types; anything else is refused (422)."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["decide", "undo"]
    reason: Literal["pay", "location", "driving"]
    verdict: Literal["pass", "reject"] | None = None
    note: str | None = Field(default=None, max_length=500)


def _require_local_write(request: Request) -> None:
    """Guard for every route that writes to the state folder.

    A browser page on another site can reach 127.0.0.1, so a write needs more
    than being on localhost: the request must be JSON, must carry the custom
    X-CN-Write header (which forces a CORS preflight that only our local
    dashboard origins pass), and, when the browser sends an Origin, it must be
    one of the local dashboard origins."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in _LOCAL_ORIGINS:
        raise HTTPException(status_code=403, detail="request origin is not allowed")
    if request.headers.get("x-cn-write") != "1":
        raise HTTPException(status_code=403, detail="missing X-CN-Write header")
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="content type must be application/json")


def create_app(source_path=None, policy=None, db_path=None,
               reload_interval=None, task_check=None, state_dir=None) -> FastAPI:
    """Build the API. Live mode when db_path is given, else when the
    JOBFINDER_DB_PATH env var is set and non-empty; otherwise synthetic
    mode. An explicit db_path wins over the env var.

    reload_interval: seconds between database watcher ticks (live mode only;
    0 disables the watcher). Defaults to CN_RELOAD_INTERVAL_SECONDS or 300.
    task_check: callable returning True/False/None for "is the JobFinder
    scheduled task running"; defaults to freshness.task_running.
    state_dir: folder for Kyle's local decision logs (duplicate_resolutions.jsonl
    and later workstreams'). Defaults to CN_STATE_DIR, then ~/career-navigator-state
    in live mode; the synthetic backend has none and cannot write.
    """
    resolved_db = db_path or os.environ.get("JOBFINDER_DB_PATH") or None
    if reload_interval is None:
        try:
            reload_interval = float(os.environ.get("CN_RELOAD_INTERVAL_SECONDS", "300"))
        except ValueError:
            reload_interval = 300.0

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup fails closed in live mode: a bad database halts startup
        # instead of serving an empty or degraded snapshot (S5).
        _reload(app)
        stop = None
        if app.state.db_path is not None and app.state.reload_interval > 0:
            stop = threading.Event()
            thread = threading.Thread(
                target=_watch_loop,
                args=(app, app.state.reload_interval, stop),
                name="cn-db-watch",
                daemon=True,
            )
            app.state.watch_thread = thread
            thread.start()
        try:
            yield
        finally:
            if stop is not None:
                stop.set()
                app.state.watch_thread.join(timeout=2)

    app = FastAPI(title="Career Navigator API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_LOCAL_ORIGINS),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.state.policy = policy or Policy()
    app.state.source_path = source_path or default_source_path()
    app.state.db_path = resolved_db
    app.state.snapshot = None
    app.state.reload_interval = float(reload_interval)
    app.state.task_check = task_check or freshness.task_running
    app.state.reload_lock = threading.Lock()
    app.state.last_probe = None
    app.state.consecutive_probe_failures = 0
    app.state.last_auto_reload = None
    app.state.auto_reload_error = None
    app.state.watch_thread = None
    app.state.resolutions = DuplicateResolutions(
        resolve_state_dir(state_dir, live=resolved_db is not None)
    )
    app.state.reviews = ReviewDecisions(
        resolve_state_dir(state_dir, live=resolved_db is not None)
    )
    app.state.view_cache = None

    @app.get("/health")
    def health() -> dict:
        _ensure_loaded(app)
        snapshot = app.state.snapshot
        live_health = snapshot.get("health") or {}
        degraded = _is_degraded(snapshot)
        body = {
            "status": "degraded" if degraded else "ok",
            "source": snapshot.get("source", "synthetic"),
            "postings": (
                live_health.get("jobs_count")
                if snapshot.get("source") == "live"
                else _totals(snapshot)["total"]
            ),
            "refreshed_at": snapshot["refreshed_at"],
        }
        if snapshot.get("source") == "live":
            body["jobs_count"] = live_health.get("jobs_count")
            body["max_last_seen"] = live_health.get("max_last_seen")
            body["last_updated"] = live_health.get("last_updated")
            body["sources"] = live_health.get("sources")
            if degraded:
                body["degraded"] = True
                body["last_error"] = live_health.get("last_error")
            body["freshness"] = _freshness_block(app, snapshot)
        return body

    @app.get("/api/jobs")
    def jobs() -> dict:
        _ensure_loaded(app)
        snapshot = app.state.snapshot
        ranked = _served_view(app, snapshot).ranked
        return {
            "shortlist": [_ranked_result_to_dict(r) for r in ranked.shortlist],
            "review": [_ranked_result_to_dict(r) for r in ranked.review],
            "excluded": [_ranked_result_to_dict(r) for r in ranked.excluded],
            "total": _totals(snapshot, ranked)["total"],
            "refreshed_at": snapshot["refreshed_at"],
            "stale": _is_degraded(snapshot),
        }

    @app.get("/api/jobs/shortlist")
    def shortlist() -> dict:
        _ensure_loaded(app)
        snapshot = app.state.snapshot
        ranked = _served_view(app, snapshot).ranked
        return {
            "shortlist": [_ranked_result_to_dict(r) for r in ranked.shortlist],
            "total": len(ranked.shortlist),
            "refreshed_at": snapshot["refreshed_at"],
        }

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str) -> dict:
        _ensure_loaded(app)
        snapshot = app.state.snapshot
        view = _served_view(app, snapshot)
        # A posting folded away by a merge still resolves (its detail carries
        # the duplicate_resolution that explains where it went).
        result = view.index.get(job_id) or view.folded.get(job_id) or snapshot["index"].get(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _ranked_result_to_dict(result)

    @app.get("/api/stats")
    def stats() -> dict:
        _ensure_loaded(app)
        snapshot = app.state.snapshot
        policy = app.state.policy
        view = _served_view(app, snapshot)
        body = {
            "totals": _totals(snapshot, view.ranked),
            "policy": {
                "salary_floor_hourly": SALARY_FLOOR_HOURLY,
                "salary_floor_annual": policy.salary_floor_annual,
                "current_pay_hourly": CURRENT_PAY_HOURLY,
                "current_pay_annual": CURRENT_PAY_HOURLY * policy.hours_per_week * policy.weeks_per_year,
                "primary_cities": sorted(policy.primary_cities),
                "allow_remote": policy.allow_remote,
                "priority_threshold": policy.priority_threshold,
                "fit_strong_threshold": policy.fit_strong_threshold,
                "fit_realistic_threshold": policy.fit_realistic_threshold,
            },
            "refreshed_at": snapshot["refreshed_at"],
        }
        ingestion = snapshot.get("ingestion")
        if ingestion is not None:
            body["ingestion"] = {
                "total": ingestion["total"],
                "by_decision": ingestion["by_decision"],
                "by_duplicate_confidence": ingestion["by_duplicate_confidence"],
            }
            body["matrix"] = ingestion["matrix"]
            body["served"] = {
                **_totals(snapshot, view.ranked),
                "exact_duplicate_count_sum": _exact_duplicate_count_sum(snapshot, view.ranked),
            }
            # Resolved counts sit next to (never inside) the raw ingestion
            # numbers above. served_before_decisions is what would be served
            # with the overlay off.
            body["duplicates"] = {
                **view.summary,
                "served_before_decisions": _totals(snapshot),
                "store": view.store,
            }
            # Phase 3c WS7: same idea for eligibility review decisions. The raw
            # by_decision counts in "ingestion" never change; these are the
            # counts after Kyle's passes and rejects.
            review = view.review
            moved_up = review.summary["passed"]
            moved_out = review.summary["rejected"]
            served = _totals(snapshot, view.ranked)
            body["review_decisions"] = {
                **review.summary,
                "served_before_review_decisions": {
                    "shortlist": served["shortlist"] - moved_up,
                    "review": served["review"] + moved_up + moved_out,
                    "excluded": served["excluded"] - moved_out,
                    "total": served["total"],
                },
                "store": review.store,
            }
            body["staleness"] = _staleness(snapshot.get("health") or {})
            body["freshness"] = _freshness_block(app, snapshot)
        return body

    @app.post("/api/refresh")
    def refresh():
        # Runtime refresh failure (S5): keep the last good snapshot, mark
        # health degraded, and answer 503. A refresh with no snapshot yet
        # has nothing to retain, so the load error propagates (fail closed).
        ok, error = _safe_reload(app)
        if not ok:
            snapshot = app.state.snapshot
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "error": error,
                    "snapshot_refreshed_at": snapshot["refreshed_at"],
                },
            )
        snapshot = app.state.snapshot
        return {
            "refreshed_at": snapshot["refreshed_at"],
            "totals": _totals(snapshot, _served_view(app, snapshot).ranked),
        }

    @app.get("/api/duplicates")
    def duplicates() -> dict:
        _ensure_loaded(app)
        snapshot = app.state.snapshot
        view = _served_view(app, snapshot)
        return {
            "enabled": app.state.resolutions.enabled,
            "groups": list(view.groups),
            "summary": view.summary,
            "store": view.store,
            "refreshed_at": snapshot["refreshed_at"],
            "stale": _is_degraded(snapshot),
        }

    # The guard is a route dependency so it runs before the body is validated:
    # a cross-site or non-JSON request is refused (403/415), never parsed.
    @app.post("/api/duplicates/{fingerprint}/events", dependencies=[Depends(_require_local_write)])
    def duplicate_event(fingerprint: str, body: DuplicateEventBody) -> dict:
        resolutions = app.state.resolutions
        if not resolutions.enabled:
            raise HTTPException(status_code=503, detail="decisions are not saved in this mode (no state folder)")
        if not _FINGERPRINT_RE.match(fingerprint):
            raise HTTPException(status_code=404, detail="no such duplicate group")
        _ensure_loaded(app)
        # One writer at a time, and the group is looked up against the CURRENT
        # snapshot under the lock: a decision is only ever recorded for a group
        # that exists right now, with the member ids this backend computed
        # (never ids supplied by the client).
        with resolutions.write_lock:
            snapshot = app.state.snapshot
            view = _served_view(app, snapshot)
            group = group_lookup(view).get(fingerprint)
            if group is None:
                raise HTTPException(
                    status_code=404,
                    detail="no such duplicate group in the current data (it may have changed; reload)",
                )
            try:
                if body.type == "undo":
                    if body.action is not None or body.kept_jobfinder_id is not None or body.note is not None:
                        raise ResolutionError("undo takes no action, kept_jobfinder_id or note")
                    event = resolutions.record_undo(fingerprint)
                else:
                    if body.action is None:
                        raise ResolutionError("resolve needs an action: keep, merge or dismiss")
                    event = resolutions.record_resolve(group, body.action, body.kept_jobfinder_id, body.note)
            except ResolutionError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc))
            view = _served_view(app, snapshot)
        return {
            "event": event,
            "group": next((g for g in view.groups if g["fingerprint"] == fingerprint), None),
            "summary": view.summary,
            "totals": _totals(snapshot, view.ranked),
        }

    @app.get("/api/review")
    def review_log() -> dict:
        """Review decisions in force or set aside, newest first (Audit tab)."""
        _ensure_loaded(app)
        snapshot = app.state.snapshot
        view = _served_view(app, snapshot)
        return {
            "enabled": app.state.reviews.enabled,
            "decisions": list(view.review.audit),
            "summary": view.review.summary,
            "store": view.review.store,
            "refreshed_at": snapshot["refreshed_at"],
            "stale": _is_degraded(snapshot),
        }

    @app.post("/api/jobs/{job_id}/review", dependencies=[Depends(_require_local_write)])
    def review_event(job_id: str, body: ReviewEventBody) -> dict:
        reviews = app.state.reviews
        if not reviews.enabled:
            raise HTTPException(status_code=503, detail="decisions are not saved in this mode (no state folder)")
        if not _API_ID_RE.match(job_id):
            raise HTTPException(status_code=404, detail="job not found")
        _ensure_loaded(app)
        # One writer at a time; the posting is looked up in the CURRENT served
        # data under the lock, so a decision is only recorded for a posting that
        # is in the queue right now, with ids and fingerprint this backend
        # computed (never values supplied by the client).
        with reviews.write_lock:
            snapshot = app.state.snapshot
            view = _served_view(app, snapshot)
            result = view.index.get(job_id)
            if result is None:
                raise HTTPException(status_code=404, detail="job not found (it may have changed; reload)")
            try:
                if body.type == "undo":
                    if body.verdict is not None or body.note is not None:
                        raise ReviewError("undo takes no verdict or note")
                    # Undo works on the raw review reasons even when a decision has
                    # moved the posting out of the review list.
                    event = reviews.record_undo(result, body.reason)
                else:
                    if body.verdict is None:
                        raise ReviewError("decide needs a verdict: pass or reject")
                    event = reviews.record_decide(result, job_id, body.reason, body.verdict, body.note)
            except ReviewError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc))
            view = _served_view(app, snapshot)
        served = view.index.get(job_id)
        return {
            "event": event,
            "job": _ranked_result_to_dict(served) if served is not None else None,
            "summary": view.review.summary,
            "totals": _totals(snapshot, view.ranked),
        }

    @app.get("/api/gate")
    def gate() -> dict:
        return _read_gate_summary(os.environ.get("CN_GATE_REPORTS_DIR"))

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("career_navigator.app:app", host="127.0.0.1", port=8000)
