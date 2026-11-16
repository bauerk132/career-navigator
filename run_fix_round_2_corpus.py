"""Reproducibility runner for the Fix Round 2 corpus (synthetic postings only).

Run from the worktree root:
  PYTHONDONTWRITEBYTECODE=1 py -3.12 run_fix_round_2_corpus.py
  PYTHONDONTWRITEBYTECODE=1 py -3.15-arm64 run_fix_round_2_corpus.py

Emits JSON with per-case actual status/priority/location evidence for the 40
recovered Hermes counterexamples and the 7 named controls, plus group counts.
Mirrors the format of Hermes's cn_triage_433 triage for direct comparison.
"""
import collections
import json
import sys

from career_navigator.eligibility import evaluate
from career_navigator.models import Posting
from career_navigator.policy import Policy
from career_navigator.relevance import score
from tests.test_fix_round_2 import HERMES_40, HERMES_CONTROLS


def run(loc, details):
    p = Posting(url="https://example.invalid/synthetic", title="IT Support Technician",
                company="Synthetic", location=loc, details=details, pay_text="$25 an hour")
    d = evaluate(p, Policy())
    r = score(p, d, Policy())
    return {"actual": d.status, "priority": r.priority,
            "location_evidence": next(x.evidence for x in d.reasons if x.factor == "location")}


def main():
    rows = []
    for cid, group, loc, details, allowed in HERMES_40:
        result = run(loc, details)
        rows.append({"id": cid, "group": group, "location": loc, "details": details,
                     "allowed": list(allowed), **result,
                     "fixed": result["actual"] in allowed})
    controls = []
    for cid, loc, details, allowed in HERMES_CONTROLS:
        result = run(loc, details)
        controls.append({"id": cid, "location": loc, "details": details,
                         "allowed": list(allowed), **result,
                         "fixed": result["actual"] in allowed})
    groups = sorted({r["group"] for r in rows})
    print(json.dumps({
        "python": sys.version,
        "original_40_count": len(rows),
        "original_fixed": sum(r["fixed"] for r in rows),
        "original_still_failing": sum(not r["fixed"] for r in rows),
        "group_counts": {
            g: dict(collections.Counter("fixed" if r["fixed"] else "failing"
                                        for r in rows if r["group"] == g))
            for g in groups
        },
        "rows": rows,
        "controls": controls,
        "controls_fixed": sum(c["fixed"] for c in controls),
    }, indent=2))


if __name__ == "__main__":
    main()