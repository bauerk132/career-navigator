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
  Kyle decision. The weights are the blueprint's, configurable per Policy
  with validation (B-G1-01).
- Negated requirements do not score as requirements ("no bachelor's degree
  required" is the absence of one; "not flexible" adds no perk bonus,
  B-G2-01). Years count only when framed as a candidate requirement
  (requires/required/minimum/N+ years, B-G2-02). Growth signals count once
  per distinct concept, and requirement-context words ("certification
  required") are not growth signals (B-G2-03).
- A gap is listed for every fit factor below 0.5, in plain language, with
  what is missing — never a claim about the candidate.
- stdlib only. No I/O, no persistence.
"""
import re
from dataclasses import dataclass

from .contracts import ResultContract, build_contract
from .eligibility import _TECHNICAL
from .pipeline import Evaluation
from .policy import DEFAULT_FIT_WEIGHTS, Policy

# Blueprint fit weights (the default; a Policy may override them, B-G1-01).
FIT_WEIGHTS = DEFAULT_FIT_WEIGHTS

# Growth concepts: each counts once (B-G2-03). "mentor" covers mentor/
# mentorship/mentoring; "certification" covers certification/certifications.
# training/certification in a requirement context are NOT growth signals —
# they are requirements, not offered growth. The guard is two-sided within
# the sentence (B2-R1): a requirement anchor BEFORE the term ("Must have
# CompTIA certification.") leaks into growth just as one after it
# ("Certification required.") does, so the check lives in _concept_present,
# not in the regexes.
_GROWTH_CONCEPTS = (
    ("training", re.compile(r"\btraining\b")),
    ("certification", re.compile(r"\bcertifications?\b")),
    ("advancement", re.compile(r"\badvancement\b")),
    ("career growth", re.compile(r"\bcareer growth\b")),
    ("career path", re.compile(r"\bcareer path\b")),
    ("professional development", re.compile(r"\bprofessional development\b")),
    ("mentor", re.compile(r"\bmentor\w*\b")),
    ("entry-level", re.compile(r"\bentry[- ]level\b")),
    ("junior", re.compile(r"\bjunior\b")),
    ("growth opportunity", re.compile(r"\bgrowth opportunit\w*\b")),
    ("promotion", re.compile(r"\bpromot\w*\b")),
)
_GUARDED_GROWTH_CONCEPTS = frozenset({"training", "certification"})
# "mandatory" is a requirement word (B3-N1: "Mandatory certification." /
# "Certification is mandatory." are requirements, not offered growth).
# "need/needs/necessary/essential" are deliberately NOT anchors: they also
# describe offered growth ("the training you need to succeed"), so adding
# them would suppress genuine growth signals.
_REQUIREMENT_ANCHOR = re.compile(r"\b(?:required|must have|requires?|minimum|preferred|mandatory)\b")
# Years are extracted only when framed as a requirement for the candidate
# (B-G2-02): a requirement anchor before the number, a "+" minimum marker,
# or required/preferred right after. Employer-history boilerplate
# ("10 years of experience in the industry") has none of these and is not
# extracted.
_YEARS_OF_EXPERIENCE = (
    re.compile(r"(?:requires?|required|minimum|min\.?|at least|preferred)\b[^.;]{0,40}?(\d+)\s*\+?\s*(?:years?|yrs?)\b"),
    re.compile(r"(\d+)\+\s*(?:years?|yrs?)\b"),
    re.compile(r"(\d+)\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp)\b[^.;]{0,20}?\b(?:required|preferred)\b"),
)
# Negated requirements must not score as requirements (B-G2-01): "no/not X
# required" is the absence of a requirement, not a requirement.
_DEGREE_NEGATED = (
    re.compile(r"\bno\b[^.;]{0,40}\bbachelor'?s?\b[^.;]{0,20}\brequired\b"),
    re.compile(r"\bbachelor'?s?\b[^.;]{0,20}\bnot required\b"),
    re.compile(r"\bno\b[^.;]{0,20}\bdegree\b[^.;]{0,20}\brequired\b"),
)
_CERT_NEGATED = (
    re.compile(r"\bno\b[^.;]{0,40}\b(?:comptia|network\+|security\+|ccna|itil)(?![a-z0-9])[^.;]{0,20}\brequired\b"),
    re.compile(r"\b(?:comptia|network\+|security\+|ccna|itil)(?![a-z0-9])[^.;]{0,20}\bnot required\b"),
    re.compile(r"\bno\b[^.;]{0,20}\bcertification\b[^.;]{0,20}\brequired\b"),
    re.compile(r"\ba\+[^.;]{0,20}\bnot required\b"),
)
# Negated perks must not add the bonus or the evidence (B-G2-01). The guard
# allows a short gap so "not very flexible" / "isn't flexible" / "never
# hybrid" are caught too (B2-N1); the gap excludes commas (B3-N3) so a
# negation cannot reach across a clause ("This is not remote, flexible
# schedule offered." keeps the flexible perk).
_NEGATED_FLEXIBLE = re.compile(r"\b(?:not|no|never|isn't|is not|aren't|are not)\b[^.;,]{0,12}?\bflexible\b")
_NEGATED_HYBRID = re.compile(r"\b(?:not|no|never|isn't|is not|aren't|are not)\b[^.;,]{0,12}?\bhybrid\b")
_DEGREE_REQUIRED = (
    re.compile(r"\bbachelor'?s?\b[^.;]{0,40}\b(?:required|must have|requires?|mandatory)\b"),
    re.compile(r"\b(?:required|must have)\b[^.;]{0,40}\bbachelor'?s?\b"),
    re.compile(r"\bdegree\b[^.;]{0,20}\brequired\b"),
)
_DEGREE_PREFERRED = re.compile(
    r"\bbachelor'?s?\b[^.;]{0,40}\bpreferred\b|\bpreferred\b[^.;]{0,40}\bbachelor'?s?\b"
)
_CERT_REQUIRED = (
    re.compile(r"\b(?:comptia|network\+|security\+|ccna|itil)(?![a-z0-9])[^.;]{0,30}\brequired\b"),
    re.compile(r"\b(?:required|must have)\b[^.;]{0,30}\b(?:comptia|network\+|security\+|ccna|itil)(?![a-z0-9])"),
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


def _factor(name: str, raw: float, evidence: str, weights: dict) -> FitFactor:
    weight = weights[name]
    return FitFactor(name, weight, raw, weight * raw, evidence)


def _skills_factor(evaluation: Evaluation, weights: dict) -> FitFactor:
    relevance = evaluation.relevance
    factor = next((f for f in relevance.factors if f.name == "technical_alignment"), None)
    if factor is not None:
        return _factor("skills", factor.raw, factor.evidence, weights)
    text = f"{evaluation.posting.title} {evaluation.posting.details}".casefold()
    hits = [kw for kw in _TECHNICAL if kw in text]
    raw = min(1.0, len(hits) / 3.0)
    evidence = (
        f"posting mentions: {', '.join(sorted(set(hits)))}" if hits
        else "posting mentions no target technical terms"
    )
    return _factor("skills", raw, evidence, weights)


def _concept_present(text: str, name: str, pattern) -> bool:
    """True when a growth concept appears as offered growth, not as a
    requirement. Training/certification are guarded on BOTH sides of the
    term within the sentence (B2-R1): a requirement anchor before the term
    ("Must have CompTIA certification.") leaks into growth just as one
    after it ("Certification required.") does. The before-search covers
    the FULL preceding sentence fragment (B3-R1): a 30-char window cuts
    off anchors before a cert list ("Must have CompTIA A+, Network+, or
    Security+ certification."). The after-search keeps its 30-char window
    and is clause-aware (B3-N2): a "minimum" about pay/hours in a later
    clause ("We provide training, minimum starting pay $25/hour.") must
    not suppress genuinely offered training."""
    for match in pattern.finditer(text):
        if name in _GUARDED_GROWTH_CONCEPTS:
            before = re.split(r"[.;!?]", text[:match.start()])[-1]
            if _REQUIREMENT_ANCHOR.search(before):
                continue
            after = re.split(r"[.;!?,]", text[match.end():match.end() + 30])[0]
            if _REQUIREMENT_ANCHOR.search(after):
                continue
        return True
    return False


def _growth_factor(evaluation: Evaluation, weights: dict) -> FitFactor:
    text = f"{evaluation.posting.title} {evaluation.posting.details}".casefold()
    hits = sorted({name for name, rx in _GROWTH_CONCEPTS if _concept_present(text, name, rx)})
    raw = min(1.0, len(hits) / 2.0)
    evidence = (
        f"growth signals: {', '.join(hits)}" if hits
        else "no career-growth language in posting (no training, certification, or advancement mention)"
    )
    return _factor("career_growth", raw, evidence, weights)


def _required_years(text: str) -> int | None:
    years = []
    for rx in _YEARS_OF_EXPERIENCE:
        years.extend(int(m.group(1)) for m in rx.finditer(text))
    return max(years) if years else None


def _sentence_kinds(text: str, positive_patterns, negated_patterns):
    """(has_positive, has_negated) per sentence. A sentence counts as
    positive only if it matches a positive pattern and NO negated pattern
    (B2-N2): the broad positive patterns also match negated sentences
    ("No bachelor's degree required." matches "bachelor's ... required"),
    so the negation must win within one sentence — but a positive
    requirement in ANOTHER sentence (multi-track) is conservative and wins
    overall. The [^.;] windows never cross a sentence boundary, so
    splitting first changes nothing for single-sentence text. Clauses
    count separately (B3-N4): in comma-joined contradictory text
    ("Security+ certification required, no certification required for
    interns.") the explicit positive requirement wins."""
    sentences = [s for s in re.split(r"[.;!?,]", text) if s.strip()]
    has_positive = has_negated = False
    for sentence in sentences:
        if any(rx.search(sentence) for rx in negated_patterns):
            has_negated = True
        elif any(rx.search(sentence) for rx in positive_patterns):
            has_positive = True
    return has_positive, has_negated


def _requirements_factor(evaluation: Evaluation, weights: dict) -> FitFactor:
    text = f"{evaluation.posting.title} {evaluation.posting.details}".casefold()
    years = _required_years(text)
    if years is None:
        experience_raw = 1.0
        experience_note = "no experience requirement stated"
    else:
        experience_raw = max(0.0, 1.0 - years / 6.0)
        experience_note = f"requires {years}+ years of experience"
    degree_positive, degree_negated = _sentence_kinds(text, _DEGREE_REQUIRED, _DEGREE_NEGATED)
    if degree_positive:
        degree_raw = 0.5
        degree_note = "bachelor's degree required"
    elif degree_negated:
        degree_raw = 0.75 if _DEGREE_PREFERRED.search(text) else 1.0
        degree_note = "bachelor's degree not required" + (
            " (preferred)" if _DEGREE_PREFERRED.search(text) else ""
        )
    elif _DEGREE_PREFERRED.search(text):
        degree_raw = 0.75
        degree_note = "bachelor's degree preferred"
    else:
        degree_raw = 1.0
        degree_note = "no degree requirement stated"
    cert_positive, cert_negated = _sentence_kinds(text, _CERT_REQUIRED, _CERT_NEGATED)
    if cert_positive:
        cert_raw = 0.7
        cert_note = "certification required"
    elif cert_negated:
        cert_raw = 1.0
        cert_note = "no certification requirement stated"
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
    return _factor("hiring_requirements", raw, "; ".join(binding), weights)


def _commute_factor(evaluation: Evaluation, weights: dict) -> FitFactor:
    decision = evaluation.decision
    reason = next(r for r in decision.reasons if r.factor == "location")
    base = {"pass": 1.0, "review": 0.5, "exclude": 0.0}.get(reason.outcome, 0.5)
    text = f"{evaluation.posting.title} {evaluation.posting.details}".casefold()
    perk = (
        ("flexible" in text and not _NEGATED_FLEXIBLE.search(text))
        or ("hybrid" in text and not _NEGATED_HYBRID.search(text))
    )
    raw = min(1.0, base + 0.1) if perk and base > 0.0 else base
    evidence = reason.evidence
    if perk and base > 0.0:
        evidence += "; flexible/hybrid scheduling mentioned"
    return _factor("commute_schedule", raw, evidence, weights)


def _comp_factor(contract: ResultContract, policy: Policy) -> FitFactor:
    pay = contract.pay
    floor = policy.salary_floor_annual
    current = pay.current_annual
    low = pay.annual_low
    high = pay.annual_high
    weights = policy.fit_weights
    if low is not None:
        if low >= current:
            return _factor("comp", 1.0, f"pay above current $21/hr (annualized low {low:.2f})", weights)
        if floor is not None and low >= floor:
            return _factor("comp", 0.75, f"pay meets the $19/hr floor but below current $21/hr (annualized low {low:.2f})", weights)
        if high is not None and floor is not None and high >= floor:
            return _factor("comp", 0.5, f"pay range straddles the $19/hr floor (low {low:.2f} below, high {high:.2f} above)", weights)
        return _factor("comp", 0.0, f"pay below the $19/hr floor (annualized low {low:.2f})", weights)
    if high is not None:
        if floor is not None and high >= floor:
            return _factor("comp", 0.5, f"pay stated as an upper bound only (high {high:.2f}); no guaranteed low", weights)
        return _factor("comp", 0.0, f"pay upper bound below the $19/hr floor (high {high:.2f})", weights)
    return _factor("comp", 0.0, "pay unknown; remains unknown (not fabricated)", weights)


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
    weights = policy.fit_weights
    return (
        _skills_factor(evaluation, weights),
        _growth_factor(evaluation, weights),
        _requirements_factor(evaluation, weights),
        _commute_factor(evaluation, weights),
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
