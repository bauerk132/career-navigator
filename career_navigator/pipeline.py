"""End-to-end synthetic pipeline: identity -> eligibility -> relevance -> ranking.

Upstream verdict/status are carried as source records only (A2); CN decisions
are independent. Uncertain duplicate matches remain visible, never suppressed.
Deterministic ordering; no I/O, no persistence.
"""
from dataclasses import dataclass

from .dedup import group_duplicates
from .eligibility import evaluate
from .models import Decision, Posting, UrlIdentity
from .policy import Policy
from .relevance import RelevanceResult, rank, score
from .urls import canonical_url


@dataclass(frozen=True)
class Evaluation:
    posting: Posting
    identity: UrlIdentity
    decision: Decision
    relevance: RelevanceResult
    duplicate_confidence: str
    duplicate_reason: str
    upstream_records: dict


def evaluate_postings(postings, policy: Policy) -> tuple[Evaluation, ...]:
    postings = tuple(postings)
    identities = {p.url: canonical_url(p.url) for p in postings}
    decisions = {p.url: evaluate(p, policy) for p in postings}
    relevances = {p.url: score(p, decisions[p.url], policy) for p in postings}
    dup_info = {}
    for group in group_duplicates(postings):
        for member in group.members:
            dup_info[member.url] = (group.confidence, group.reason)
    evaluations = []
    for result in rank(relevances.values()):
        p = result.posting
        confidence, reason = dup_info[p.url]
        evaluations.append(
            Evaluation(
                p, identities[p.url], decisions[p.url], result, confidence, reason,
                {"verdict": p.upstream_verdict, "status": p.upstream_status},
            )
        )
    return tuple(evaluations)