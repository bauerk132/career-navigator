"""Explainable job-relevance index (C9; combined plan #5).

This is a *job relevance index* computed from posting-side evidence only — not
an interview probability and not verified candidate fit. Factor evidence never
claims candidate proficiency. Weights are configurable and must sum to 1.
Review/rejected records receive no priority band. Ties break deterministically.
"""
from dataclasses import dataclass

from .eligibility import _TECHNICAL
from .models import Decision, Posting, Reason
from .pay import parse_pay
from .policy import DEFAULT_WEIGHTS, Policy
from .text import clean_text

WORDING = "job relevance index (posting-side evidence only; not verified candidate fit)"
_FACTOR_NAMES = tuple(DEFAULT_WEIGHTS)


@dataclass(frozen=True)
class FactorScore:
    name: str
    weight: float
    raw: float
    contribution: float
    evidence: str


@dataclass(frozen=True)
class RelevanceResult:
    posting: Posting
    value: float
    factors: tuple[FactorScore, ...]
    priority: str | None
    wording: str


def _validate_weights(weights: dict) -> None:
    if set(weights) != set(_FACTOR_NAMES):
        raise ValueError(f"weights must cover exactly {_FACTOR_NAMES}")
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError("weights must sum to 1.0")


def _technical_factor(posting: Posting) -> tuple[float, str]:
    text = clean_text(f"{posting.title} {posting.details}").casefold()
    hits = [kw for kw in _TECHNICAL if kw in text]
    if "it" in text.split():
        hits.append("it")
    raw = min(1.0, len(hits) / 3.0)
    evidence = (
        f"posting mentions: {', '.join(sorted(set(hits)))}" if hits
        else "posting mentions no target technical terms"
    )
    return raw, evidence


def _location_factor(posting: Posting, policy: Policy) -> tuple[float, str]:
    loc = clean_text(posting.location).casefold()
    if not loc.strip():
        return 0.0, "location missing; unknown"
    if "remote" in loc:
        return 0.8, "remote"
    for city in policy.primary_cities:
        if city in loc:
            return 1.0, f"{city} (primary)"
    for city in policy.optional_cities:
        if city in loc:
            return 0.6, f"{city} (optional)"
    return 0.0, f"out of area: {posting.location.strip()}"


def _pay_factor(posting: Posting) -> tuple[float, str]:
    pay = parse_pay(posting.pay_text)
    if pay.status == "bounded":
        return 1.0, f"bounded pay stated: {pay.raw!r}"
    if pay.status == "open_ended":
        return 0.5, f"open-ended pay stated: {pay.raw!r}"
    return 0.0, "salary unknown; remains unknown (not fabricated)"


def _driving_factor(decision: Decision) -> tuple[float, str]:
    reason = next(r for r in decision.reasons if r.factor == "driving")
    raw = {"pass": 1.0, "info": 0.5, "review": 0.25, "exclude": 0.0}[reason.outcome]
    return raw, reason.evidence


def score(posting: Posting, decision: Decision, policy: Policy) -> RelevanceResult:
    _validate_weights(policy.weights)
    raws = {
        "technical_alignment": _technical_factor(posting),
        "location_fit": _location_factor(posting, policy),
        "pay_clarity": _pay_factor(posting),
        "driving_compatibility": _driving_factor(decision),
    }
    factors = tuple(
        FactorScore(name, policy.weights[name], raw, policy.weights[name] * raw, evidence)
        for name, (raw, evidence) in raws.items()
    )
    value = 100.0 * sum(f.contribution for f in factors)
    if decision.status != "eligible":
        priority = None
    elif value >= policy.priority_threshold:
        priority = "high"
    else:
        priority = "standard"
    return RelevanceResult(posting, value, factors, priority, WORDING)


def rank(results) -> tuple[RelevanceResult, ...]:
    return tuple(
        sorted(results, key=lambda r: (-r.value, r.posting.title, r.posting.company, r.posting.url))
    )