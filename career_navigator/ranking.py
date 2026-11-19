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
# Requirement anchors, split by how far they may reach (B4). Predicate
# anchors (required/must have/requires/mandatory/preferred) are requirement
# predicates: they may reach across commas within a sentence (B3-R1 cert
# lists; B4-R1 "Training, required for all new hires"). "minimum" is
# usually about pay/hours, not the growth term: it only counts within the
# term's own clause (B3-N2). "need/needs/necessary/essential" are
# deliberately NOT anchors: they also describe offered growth ("the
# training you need to succeed"). "must" + verb is a predicate (B5-N4:
# "You must give certification details." is a requirement, not growth) —
# but NOT "must have" in the must-verb list: "must have" is already its
# own anchor, and the B4-R2 offer clause ("Must have 2 years of experience
# and we offer paid training.") must stay offerable. The must+verb pattern
# tolerates up to two adverbs between "must" and the verb (B6-N1: "You
# must quickly provide certification details." is a requirement, not
# growth). The gap is capped at two words so a distant "must" cannot reach
# across a clause ("Must have 2 years of experience and we offer paid
# training." must stay offerable).
_REQUIREMENT_PREDICATE = re.compile(r"\b(?:required|must have|requires?|mandatory|preferred|must\s+(?:[a-z]+\s+){0,2}(?:give|provide|include|hold|submit|present|show|complete|possess|maintain|obtain|offer))\b")
_REQUIREMENT_MINIMUM = re.compile(r"\bminimum\b")
# Offer verbs for the B4-R2 truncation: BASE forms plus progressive -ing
# forms (B6-R1). B5-N2 correctly excluded the -ed participles, but it also
# dropped the progressives, so an earlier requirement anchor suppressed
# genuinely offered growth stated in progressive tense ("We require 3 years
# of experience and are providing training." is offered growth, not a
# requirement). A participle (offered/provided/included) immediately
# before the term's noun phrase is still an adjective, not an offer verb
# ("Must have certification from our offered training program." is a
# requirement — the truncation must not discard the "must have"). "give"
# is deliberately NOT an offer verb — it also appears in requirements
# ("You must give certification details.").
# "including" is a list-continuation marker, not an independent offer
# (B7-R1): when it extends a requirement list ("We require 3 years of
# experience, including certification."), the requirement scope covers the
# rest of the sentence fragment — see _requirement_governed_including.
# It still counts as an offer verb when it continues an offer list
# ("We offer training, including certification.").
_OFFER_VERB = re.compile(r"\b(?:offer|offers|provide|provides|include|includes|providing|offering|including)\b")
# Progressive -ing offer forms (B7-R3): "must have" governs these across a
# longer, digit-containing requirement tail.
_OFFER_ING = frozenset({"providing", "offering", "including"})
# Clause break that ends "must have" governance of an -ing form (B7-R3):
# a conjunction plus a finite auxiliary or pronoun starts a new clause
# ("and are providing", "and we offer"), so the -ing form is a fresh
# offer, not the requirement's gerund.
_CLAUSE_BREAK = re.compile(r"\band\s+(?:are|is|was|were|we|they|you|he|she|it|our|their)\b")
# Scheduling/logistics nouns (B5-N1): when a requirement predicate is
# directly followed by one of these, it modifies THAT noun, not the growth
# term ("required start date" is about the start date). A narrow set on
# purpose: a broad noun list would break compound requirements ("Must have
# experience and certification" — "experience" is a real requirement
# object, not a scheduling noun).
_SCHEDULING_NOUN = frozenset({
    "start", "date", "time", "deadline", "shift", "schedule",
    "location", "interview", "application",
})
# Imperative instruction verbs (B5-N3): a sentence starting with one of
# these (optionally after "please") is an instruction to the applicant
# ("Include certification in your application."), not offered growth.
# Base forms only — the participles are noun modifiers ("offered
# training" IS offered growth). The verb may be followed by its object
# (B6-R2): a determiner, adjective, or noun between the verb and the term
# ("Include your certification in your application." / "Provide relevant
# certification details at interview.") is still an instruction — the B5
# rule's bare-verb adjacency missed it. A finite verb form ANYWHERE in
# the remainder means the verb is a noun subject instead (B7-R2): the
# B6 guard only checked the immediate next word, so an intervening
# adverb turned a noun-subject offer into a false imperative ("Offer now
# includes training." IS offered growth — the adverb sits between the
# subject and its finite verb). Past-tense finite forms (offered/
# provided/included) count too ("Offer provided training." is an offer,
# not an instruction).
_IMPERATIVE_VERB = re.compile(
    r"^\s*(?:please\s+)?(?:offer|include|provide)\b"
    r"(?![\s\S]*\b(?:offers|provides|includes|offered|provided|included)\b)"
    r"(?:\s+\S+)*\s*$"
)
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


def _must_governed_offer(prefix: str, verb: str) -> bool:
    """True when an offer verb at the end of `prefix` is governed by
    "must", so it is a requirement, not an offer (B5-N4/B6-N1/B7-R3).
    Direct must+verb ("You must quickly provide certification details.")
    is governed for any verb. For -ing forms only, "must have" also
    governs across a longer, digit-containing requirement tail ("Must
    have 3 years of experience providing training.") — but not across a
    new clause ("... and are providing training." stays an offer, X4/X5).
    Base forms keep the short gap on purpose: "Must have 2 years of
    experience and we offer paid training." (S5) must stay offered."""
    if re.search(r"\bmust\s+(?:[a-z]+\s+){0,2}$", prefix):
        return True
    if verb in _OFFER_ING:
        m = re.search(r"\bmust\s+have\b", prefix)
        if m is not None:
            tail = prefix[m.end():]
            if ";" not in tail and not _CLAUSE_BREAK.search(tail):
                return True
    return False


def _requirement_governed_including(before: str) -> bool:
    """True when `before` contains an "including" that extends a
    requirement list (B7-R1). "including" is a list-continuation marker:
    it takes the governance of the nearest preceding anchor in the
    fragment — a requirement predicate ("We require 3 years of
    experience, including certification.") or a free offer verb ("We
    offer training, including certification."). A must-governed offer
    verb ("You must provide training, including certification.") is
    itself a requirement anchor, not a free offer. When the "including"
    is requirement-governed, the requirement scope covers the rest of
    the sentence fragment, so a later term in the same fragment is a
    requirement item too ("... including certification, and are
    providing training.")."""
    for m in re.finditer(r"\bincluding\b", before):
        head = before[:m.start()]
        req = list(_REQUIREMENT_PREDICATE.finditer(head))
        off = [
            o for o in _OFFER_VERB.finditer(head)
            if o.group(0) != "including"
            and not _must_governed_offer(head[:o.start()], o.group(0))
        ]
        if req and (not off or req[-1].start() > off[-1].start()):
            return True
    return False


def _predicate_attaches(text: str) -> bool:
    """True when a requirement predicate in `text` attaches to the growth
    term. A predicate directly followed by a scheduling noun modifies THAT
    noun, not the term (B5-N1: 'required start date' is about the start
    date). A comma/period/end or any other following word means it
    attaches (the predicate governs the term or a list ending in it)."""
    for m in _REQUIREMENT_PREDICATE.finditer(text):
        rest = text[m.end():]
        nxt = re.match(r"\s*([a-z]+)", rest)
        if nxt is None or nxt.group(1) not in _SCHEDULING_NOUN:
            return True
    return False


def _concept_present(text: str, name: str, pattern, title: str = "") -> bool:
    """True when a growth concept appears as offered growth, not as a
    requirement. Training/certification are guarded on BOTH sides of the
    term within the sentence (B2-R1). The before-search covers the FULL
    preceding sentence fragment (B3-R1: a 30-char window cuts off anchors
    before a cert list), but it is offer-aware (B4-R2): the search stops
    after an offer/provide/include verb, so an anchor in an earlier clause
    cannot suppress a term a later clause explicitly offers. Two guards
    on that truncation (B5): a must-governed offer verb is a requirement,
    not an offer ("You must provide certification details." — the
    truncation must not discard the "must"), and a participle is an
    adjective, not an offer verb (B5-N2). The must-governed guard
    tolerates up to two adverbs between "must" and the verb (B6-N1:
    "You must quickly provide certification details."). For -ing forms
    only, "must have" also governs across a longer, digit-containing
    requirement tail (B7-R3: "Must have 3 years of experience providing
    training."), but not across a new clause. A requirement-governed
    "including" extends the requirement scope to the rest of the
    sentence fragment (B7-R1). A sentence
    starting with an imperative offer/include/provide verb is an
    instruction, not offered growth (B5-N3: "Include certification in
    your application.") — the posting title is stripped from the fragment
    first, since the text is "title + details" and the title is not part
    of the sentence's grammar. The imperative guard looks for a finite
    verb form anywhere in the remainder (B7-R2: "Offer now includes
    training." is a noun subject, not an instruction). The after-search is 30 chars,
    sentence-bounded: predicate anchors may reach across commas (B4-R1:
    "Training, required for all new hires"), while "minimum" only counts
    within the term's own clause (B3-N2: "minimum starting pay" must not
    suppress offered training). A predicate directly followed by a
    scheduling noun does not attach to the term (B5-N1: "required start
    date")."""
    for match in pattern.finditer(text):
        if name in _GUARDED_GROWTH_CONCEPTS:
            before = re.split(r"[.;!?]", text[:match.start()])[-1]
            fragment = before
            if title:
                title_folded = title.casefold()
                if fragment.startswith(title_folded):
                    fragment = fragment[len(title_folded):]
            if _IMPERATIVE_VERB.match(fragment):
                continue
            if _requirement_governed_including(fragment):
                continue
            offer_matches = [
                m for m in _OFFER_VERB.finditer(before)
                if not _must_governed_offer(before[:m.start()], m.group(0))
            ]
            if offer_matches:
                before = before[offer_matches[-1].end():]
            if _predicate_attaches(before):
                continue
            if _REQUIREMENT_MINIMUM.search(before.rsplit(",", 1)[-1]):
                continue
            after = re.split(r"[.;!?]", text[match.end():match.end() + 30])[0]
            if _predicate_attaches(after):
                continue
            if _REQUIREMENT_MINIMUM.search(after.split(",", 1)[0]):
                continue
        return True
    return False


def _growth_factor(evaluation: Evaluation, weights: dict) -> FitFactor:
    text = f"{evaluation.posting.title} {evaluation.posting.details}".casefold()
    hits = sorted({name for name, rx in _GROWTH_CONCEPTS if _concept_present(text, name, rx, title=evaluation.posting.title)})
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
    """(has_positive, has_negated). Positive patterns are matched at the
    SENTENCE level ([.;!?]) so their comma-spanning windows work (B4-R1:
    "Bachelor's degree, required for all candidates." — splitting on
    commas first destroys the match). Clause analysis ([.;!?,]) only
    decides the contradiction case: an explicit positive clause beats a
    negated clause (B3-N4: "Security+ certification required, no
    certification required for interns."), and a sentence whose positive
    match sits entirely inside a negated clause counts as negated (B2-N2:
    "No bachelor's degree required.")."""
    has_positive = has_negated = False
    for sentence in re.split(r"[.;!?]", text):
        if not sentence.strip():
            continue
        sentence_positive = any(rx.search(sentence) for rx in positive_patterns)
        sentence_negated = any(rx.search(sentence) for rx in negated_patterns)
        clause_positive = clause_negated = False
        for clause in re.split(r",", sentence):
            if not clause.strip():
                continue
            if any(rx.search(clause) for rx in negated_patterns):
                clause_negated = True
            elif any(rx.search(clause) for rx in positive_patterns):
                clause_positive = True
        if clause_positive or (sentence_positive and not clause_negated):
            has_positive = True
        elif clause_negated or sentence_negated:
            has_negated = True
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
