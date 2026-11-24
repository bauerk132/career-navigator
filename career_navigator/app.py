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
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .adapters import default_source_path, load_postings
from .contracts import CURRENT_PAY_HOURLY
from .live_adapter import (
    LiveAdapterError,
    evaluate_live_import,
    ingestion_summary,
    load_live_database,
)
from .pipeline import evaluate_postings
from .policy import SALARY_FLOOR_HOURLY, Policy
from .ranking import RankedResult, rank_evaluations

_LOCAL_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)

# Staleness threshold for /api/stats (S3): a refresh older than this is stale.
_STALE_THRESHOLD_HOURS = 26.0
# JobFinder timestamps are naive US Eastern local time (S3/F-03).
_EASTERN = ZoneInfo("America/New_York")


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
    }


def _totals(snapshot: dict) -> dict:
    # Takes the snapshot bound once by the endpoint (C-N4) so a /api/refresh
    # interleaving mid-request can never mix generations inside one response.
    ranked = snapshot["ranked"]
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


def _exact_duplicate_count_sum(snapshot: dict) -> int:
    """Reconciliation invariant (S2): summed over all served postings, the
    exact_duplicate_count values must equal the raw ingested row count."""
    ranked = snapshot["ranked"]
    total = 0
    for group in (ranked.shortlist, ranked.review, ranked.excluded):
        for result in group:
            upstream = result.contract.upstream_records or {}
            total += upstream.get("exact_duplicate_count", 1)
    return total


def _parse_eastern_timestamp(value):
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


def _reload(app: FastAPI) -> None:
    policy = app.state.policy
    if app.state.db_path is not None:
        live_import = load_live_database(app.state.db_path, policy)
        ranked = rank_evaluations(evaluate_live_import(live_import, policy), policy)
        health = dict(live_import.health)
        health["degraded"] = False
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


def _is_degraded(snapshot: dict) -> bool:
    return bool((snapshot.get("health") or {}).get("degraded"))


def create_app(source_path=None, policy=None, db_path=None) -> FastAPI:
    """Build the API. Live mode when db_path is given, else when the
    JOBFINDER_DB_PATH env var is set and non-empty; otherwise synthetic
    mode. An explicit db_path wins over the env var."""
    resolved_db = db_path or os.environ.get("JOBFINDER_DB_PATH") or None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup fails closed in live mode: a bad database halts startup
        # instead of serving an empty or degraded snapshot (S5).
        _reload(app)
        yield

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
        return body

    @app.get("/api/jobs")
    def jobs() -> dict:
        _ensure_loaded(app)
        snapshot = app.state.snapshot
        ranked = snapshot["ranked"]
        return {
            "shortlist": [_ranked_result_to_dict(r) for r in ranked.shortlist],
            "review": [_ranked_result_to_dict(r) for r in ranked.review],
            "excluded": [_ranked_result_to_dict(r) for r in ranked.excluded],
            "total": _totals(snapshot)["total"],
            "refreshed_at": snapshot["refreshed_at"],
            "stale": _is_degraded(snapshot),
        }

    @app.get("/api/jobs/shortlist")
    def shortlist() -> dict:
        _ensure_loaded(app)
        snapshot = app.state.snapshot
        ranked = snapshot["ranked"]
        return {
            "shortlist": [_ranked_result_to_dict(r) for r in ranked.shortlist],
            "total": len(ranked.shortlist),
            "refreshed_at": snapshot["refreshed_at"],
        }

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str) -> dict:
        _ensure_loaded(app)
        snapshot = app.state.snapshot
        result = snapshot["index"].get(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _ranked_result_to_dict(result)

    @app.get("/api/stats")
    def stats() -> dict:
        _ensure_loaded(app)
        snapshot = app.state.snapshot
        policy = app.state.policy
        body = {
            "totals": _totals(snapshot),
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
                **_totals(snapshot),
                "exact_duplicate_count_sum": _exact_duplicate_count_sum(snapshot),
            }
            body["staleness"] = _staleness(snapshot.get("health") or {})
        return body

    @app.post("/api/refresh")
    def refresh():
        # Runtime refresh failure (S5): keep the last good snapshot, mark
        # health degraded, and answer 503. A refresh with no snapshot yet
        # has nothing to retain, so the load error propagates (fail closed).
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
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "degraded",
                        "error": str(exc),
                        "snapshot_refreshed_at": snapshot["refreshed_at"],
                    },
                )
        else:
            _reload(app)
        snapshot = app.state.snapshot
        return {"refreshed_at": snapshot["refreshed_at"], "totals": _totals(snapshot)}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("career_navigator.app:app", host="127.0.0.1", port=8000)
