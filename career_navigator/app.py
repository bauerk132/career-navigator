"""FastAPI backend (CN-004; Slice C).

Serves the Slice A contract + Slice B ranking over the synthetic adapter.
Stateless: every load and refresh re-reads the synthetic source and
recomputes through the pipeline. No persistence in this slice (the separate
app SQLite lands with the integration slice, CN-007). No auth: local-first,
localhost only. CORS allows localhost origins only, so the laptop-local
dashboard (Vite, default :5173) can call it.

Run: uvicorn career_navigator.app:app --host 127.0.0.1 --port 8000
"""
import hashlib
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .adapters import default_source_path, load_postings
from .contracts import CURRENT_PAY_HOURLY
from .pipeline import evaluate_postings
from .policy import SALARY_FLOOR_HOURLY, Policy
from .ranking import RankedResult, rank_evaluations

_LOCAL_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def job_id_for_url(canonical_url: str) -> str:
    """Stable, deterministic id for a posting: first 12 hex chars of the
    SHA-256 of its canonical URL."""
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:12]


def _ranked_result_to_dict(result: RankedResult) -> dict:
    contract = result.contract.to_dict()
    return {
        "id": job_id_for_url(contract["source"]["url"]),
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
    }


def _totals(app: FastAPI) -> dict:
    ranked = app.state.ranked
    return {
        "shortlist": len(ranked.shortlist),
        "review": len(ranked.review),
        "excluded": len(ranked.excluded),
        "total": len(ranked.shortlist) + len(ranked.review) + len(ranked.excluded),
    }


def _reload(app: FastAPI) -> None:
    policy = app.state.policy
    postings = load_postings(app.state.source_path)
    ranked = rank_evaluations(evaluate_postings(postings, policy), policy)
    app.state.ranked = ranked
    app.state.index = {
        job_id_for_url(result.contract.to_dict()["source"]["url"]): result
        for group in (ranked.shortlist, ranked.review, ranked.excluded)
        for result in group
    }
    app.state.refreshed_at = datetime.now(timezone.utc).isoformat()


def _ensure_loaded(app: FastAPI) -> None:
    if getattr(app.state, "ranked", None) is None:
        _reload(app)


def create_app(source_path=None, policy=None) -> FastAPI:
    app = FastAPI(title="Career Navigator API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_LOCAL_ORIGINS),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.state.policy = policy or Policy()
    app.state.source_path = source_path or default_source_path()
    app.state.ranked = None
    app.state.index = {}
    app.state.refreshed_at = None

    @app.on_event("startup")
    def _startup() -> None:
        _reload(app)

    @app.get("/health")
    def health() -> dict:
        _ensure_loaded(app)
        return {
            "status": "ok",
            "source": "synthetic",
            "postings": _totals(app)["total"],
            "refreshed_at": app.state.refreshed_at,
        }

    @app.get("/api/jobs")
    def jobs() -> dict:
        _ensure_loaded(app)
        ranked = app.state.ranked
        return {
            "shortlist": [_ranked_result_to_dict(r) for r in ranked.shortlist],
            "review": [_ranked_result_to_dict(r) for r in ranked.review],
            "excluded": [_ranked_result_to_dict(r) for r in ranked.excluded],
            "total": _totals(app)["total"],
            "refreshed_at": app.state.refreshed_at,
        }

    @app.get("/api/jobs/shortlist")
    def shortlist() -> dict:
        _ensure_loaded(app)
        ranked = app.state.ranked
        return {
            "shortlist": [_ranked_result_to_dict(r) for r in ranked.shortlist],
            "total": len(ranked.shortlist),
            "refreshed_at": app.state.refreshed_at,
        }

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str) -> dict:
        _ensure_loaded(app)
        result = app.state.index.get(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _ranked_result_to_dict(result)

    @app.get("/api/stats")
    def stats() -> dict:
        _ensure_loaded(app)
        policy = app.state.policy
        return {
            "totals": _totals(app),
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
            "refreshed_at": app.state.refreshed_at,
        }

    @app.post("/api/refresh")
    def refresh() -> dict:
        _reload(app)
        return {"refreshed_at": app.state.refreshed_at, "totals": _totals(app)}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("career_navigator.app:app", host="127.0.0.1", port=8000)
