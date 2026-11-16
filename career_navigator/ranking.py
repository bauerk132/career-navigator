"""Blueprint-weighted ranking engine (CN-005; Slice B).

Turns a pipeline Evaluation (via the Slice A contract) into a fit score and
a final dashboard classification, with plain-language reasons and a gap
analysis. Weights are the blueprint's (page 4172b8e7-2a08-81ea-a736-d826b3b3f545):
skills 30% / career growth 30% / hiring requirements 20% / commute+schedule
10% / comp 10%.

Rules:
- The fit score is computed from posting-side evidence only — not an
  interview probability and not verified candidate fit (same honesty rule as
  relevance.py). It never claims candidate proficiency.
- Ranking never overrides a hard decision: review stays Needs Review and
  rejected stays Excluded. Only eligible postings are re-classified by fit:
  fit >= strong threshold -> Strong Fit, >= realistic threshold -> Realistic,
  below realistic -> Stretch (eligible but a reach on the blueprint factors).
- Thresholds are Muse defaults (70 / 45), tunable in Policy; they are not a
  Kyle decision. The weights are the blueprint's.
- A gap is listed for every fit factor below 0.5, in plain language, with
  what is missing — never a claim about the candidate.
- stdlib only. No I/O, no persistence.
"""
import re
from dataclasses import dataclass

from .contracts import ResultContract, build_contract
from .eligibility import _TECHNICAL
from .pipeline import Evaluation
from .policy import Policy

FIT_WEIGHTS = {
    "skills": 0.30,
    "career_growth": 0.30,
    "hiring_requirements": 0.20,
    "commute_schedule": 0.10,
    "comp": 0.10,
}

_GROWTH_TERMS = (
    "training", "certification", "certifications", "advancement",
    "career growth", "career path", "professional development",
    "mentorship", "mentor", "entry-level", "entry level", "junior",
    "growth opportunity", "promot",
)
_YEARS_OF_EXPERIENCE = (
    re.compile(r"(\d+)\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp)\b"),
    re.compile(r"(?:experience|exp)\b[^.;]{0,30}?(\d+)\s*\+?\s*(?:years?|yrs?)\b"),
    re.compile(r"(?:minimum|min\.?|at least)\s+(\d+)\s*\+?\s*(?:years?|yrs?)\b"),
)
_DEGREE_REQUIRED = (
    re.compile(r"\bbachelor'?s?\b[^.;]{0,40}\b(?:required|must have|requires?|mandatory)\b"),
    re.compile(r"\b(?:required|must have)\b[^.;]{0,40}\bbachelor'?s?\b"),
    re.compile(r"\bdegree\b[^.;]{0,20}\brequired\b"),
)
_DEGREE_PREFERRED = re.compile(
    r"\bbachelor'?s?\b[^.;]{0,40}\bpreferred\b|\bpreferred\b[^.;]{0,40}\bbachelor'?s?\b"
)
_CERT_REQUIRED = (
    re.compile(r"\b(?:comptia|network\+|security\+|ccna|itil)\b[^.;]{0,30}\brequired\b"),
    re.compile(r"\b(?:required|must have)\b[^.;]{0,30}\b(?:comptia|network\+|security\+|ccna|itil)\b"),
    re.compile(r"\ba\+[^.;]{0,30}\brequired\b"),
)


@dataclass(frozen=True)
class FitFactor:
    name: str
    weight: float
    raw: float
    contribution: float
    evidence: str


@dataclass(frozen=True)
class RankedResult:
    contract: ResultContract
    fit_score: float
    fit_factors: tuple[FitFactor, ...]
    gaps: tuple[str, ...]
    final_classification: str
    shortlist_rank: int | None


@dataclass(frozen=True)
class RankedResults:
    shortlist: tuple[RankedResult, ...]
    review: tuple[RankedResult, ...]
    excluded: tuple[RankedResult, ...]


def _factor(name: str, raw: float, evidence: str) -> FitFactor:
    weight = FIT_WEIGHTS[name]
    return FitFactor(name, weight, raw, weight * raw, evidence)


def _skills_factor(evaluation: Evaluation) -> FitFactor:
    relevance = evaluation.relevance
    factor = next((f for f in relevance.factors if f.name == "technical_alignment"), None)
    if factor is not None:
        return _factor("skills", factor.raw, factor.evidence)
    text = f"{evaluation.posting.title} {evaluation.posting.details}".casefold()
    hits = [kw for kw in _TECHNICAL if kw in text]
    raw = min(1.0, len(hits) / 3.0)
    evidence = (
        f"posting mentions: {', '.join(sorted(set(hits)))}" if hits
        else "posting mentions no target technical terms"
    )
    return _factor("skills", raw, evidence)


def _growth_factor(evaluation: Evaluation) -> FitFactor:
    text = f"{evaluation.posting.title} {evaluation.posting.details}".casefold()
    hits = sorted({term for term in _GROWTH_TERMS if term in text})
    raw = min(1.0, len(hits) / 2.0)
    evidence = (
        f"growth signals: {', '.join(hits)}" if hits
        else "no career-growth language in posting (no training, certification, or advancement mention)"
    )
    return _factor("career_growth", raw, evidence)


def _required_years(text: str) -> int | None:
    years = []
    for rx in _YEARS_OF_EXPERIENCE:
        years.extend(int(m.group(1)) for m in rx.finditer(text))
    return max(years) if years else None


def _requirements_factor(evaluation: Evaluation) -> FitFactor:
    text = f"{evaluation.posting.title} {evaluation.posting.details}".casefold()
    years = _required_years(text)
    if years is None:
        experience_raw = 1.0
        experience_note = "no experience requirement stated"
    else:
        experience_raw = max(0.0, 1.0 - years / 6.0)
        experience_note = f"requires {years}+ years of experience"
    if any(rx.search(text) for rx in _DEGREE_REQUIRED):
        degree_raw = 0.5
        degree_note = "bachelor's degree required"
    elif _DEGREE_PREFERRED.search(text):
        degree_raw = 0.75
        degree_note = "bachelor's degree preferred"
    else:
        degree_raw = 1.0
        degree_note = "no degree requirement stated"
    if any(rx.search(text) for rx in _CERT_REQUIRED):
        cert_raw = 0.7
        cert_note = "certification required"
    else:
        cert_raw = 1.0
        cert_note = "no certification requirement stated"
    raw = min(experience_raw, degree_raw, cert_raw)
    binding = []
    if experience_raw == raw:
        binding.append(experience_note)
    if degree_raw == raw:
        binding.append(degree_note)
    if cert_raw == raw:
        binding.append(cert_note)
    return _factor("hiring_requirements", raw, "; ".join(binding))


def _commute_factor(evaluation: Evaluation) -> FitFactor:
    decision = evaluation.decision
    reason = next(r for r in decision.reasons if r.factor == "location")
    base = {"pass": 1.0, "review": 0.5, "exclude": 0.0}.get(reason.outcome, 0.5)
    text = f"{evaluation.posting.title} {evaluation.posting.details}".casefold()
    perk = "flexible" in text or "hybrid" in text
    raw = min(1.0, base + 0.1) if perk and base > 0.0 else base
    evidence = reason.evidence
    if perk and base > 0.0:
        evidence += "; flexible/hybrid scheduling mentioned"
    return _factor("commute_schedule", raw, evidence)


def _comp_factor(contract: ResultContract, policy: Policy) -> FitFactor:
    pay = contract.pay
    floor = policy.salary_floor_annual
    current = pay.current_annual
    low = pay.annual_low
    high = pay.annual_high
    if low is not None:
        if low >= current:
            return _factor("comp", 1.0, f"pay above current $21/hr (annualized low {low:.2f})")
        if floor is not None and low >= floor:
            return _factor("comp", 0.75, f"pay meets the $19/hr floor but below current $21/hr (annualized low {low:.2f})")
        if high is not None and floor is not None and high >= floor:
            return _factor("comp", 0.5, f"pay range straddles the $19/hr floor (low {low:.2f} below, high {high:.2f} above)")
        return _factor("comp", 0.0, f"pay below the $19/hr floor (annualized low {low:.2f})")
    if high is not None:
        if floor is not None and high >= floor:
            return _factor("comp", 0.5, f"pay stated as an upper bound only (high {high:.2f}); no guaranteed low")
        return _factor("comp", 0.0, f"pay upper bound below the $19/hr floor (high {high:.2f})")
    return _factor("comp", 0.0, "pay unknown; remains unknown (not fabricated)")


def _gap_for(factor: FitFactor) -> str | None:
    if factor.raw >= 0.5:
        return None
    if factor.name == "skills":
        return f"skills: {factor.evidence} — highlight transferable support experience when applying"
    if factor.name == "career_growth":
        return "career growth: no growth language in the posting — ask about training and advancement at interview"
    if factor.name == "hiring_requirements":
        return f"hiring requirements: {factor.evidence} — a reach on stated requirements"
    if factor.name == "commute_schedule":
        return f"commute/schedule: {factor.evidence}"
    return f"comp: {factor.evidence}"


def fit_factors(evaluation: Evaluation, contract: ResultContract, policy: Policy) -> tuple[FitFactor, ...]:
    return (
        _skills_factor(evaluation),
        _growth_factor(evaluation),
        _requirements_factor(evaluation),
        _commute_factor(evaluation),
        _comp_factor(contract, policy),
    )


def fit_score(factors) -> float:
    return round(100.0 * sum(f.contribution for f in factors), 2)


def final_classification(contract: ResultContract, score_value: float, policy: Policy) -> str:
    if contract.decision_status == "rejected":
        return "Excluded"
    if contract.decision_status == "review":
        return "Needs Review"
    if score_value >= policy.fit_strong_threshold:
        return "Strong Fit"
    if score_value >= policy.fit_realistic_threshold:
        return "Realistic"
    return "Stretch"


def rank_evaluation(evaluation: Evaluation, policy: Policy) -> RankedResult:
    contract = build_contract(evaluation, policy)
    factors = fit_factors(evaluation, contract, policy)
    value = fit_score(factors)
    gaps = tuple(g for f in factors if (g := _gap_for(f)) is not None)
    return RankedResult(
        contract=contract,
        fit_score=value,
        fit_factors=factors,
        gaps=gaps,
        final_classification=final_classification(contract, value, policy),
        shortlist_rank=None,
    )


def _sort_key(result: RankedResult):
    posting = result.contract.posting
    return (
        -result.fit_score,
        -result.contract.relevance_value,
        posting["title"],
        posting["company"],
        result.contract.source["url"],
    )


def rank_evaluations(evaluations, policy: Policy) -> RankedResults:
    ranked = [rank_evaluation(ev, policy) for ev in evaluations]
    shortlist = sorted(
        (r for r in ranked if r.final_classification in ("Strong Fit", "Realistic", "Stretch")),
        key=_sort_key,
    )
    shortlist = tuple(
        RankedResult(
            contract=r.contract,
            fit_score=r.fit_score,
            fit_factors=r.fit_factors,
            gaps=r.gaps,
            final_classification=r.final_classification,
            shortlist_rank=i + 1,
        )
        for i, r in enumerate(shortlist)
    )
    review = tuple(sorted(
        (r for r in ranked if r.final_classification == "Needs Review"),
        key=lambda r: (
            -r.contract.relevance_value,
            r.contract.posting["title"],
            r.contract.posting["company"],
            r.contract.source["url"],
        ),
    ))
    excluded = tuple(sorted(
        (r for r in ranked if r.final_classification == "Excluded"),
        key=lambda r: (
            -r.contract.relevance_value,
            r.contract.posting["title"],
            r.contract.posting["company"],
            r.contract.source["url"],
        ),
    ))
    return RankedResults(shortlist=shortlist, review=review, excluded=excluded)
