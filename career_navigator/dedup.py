"""Non-lossy dedup keys and duplicate grouping with visible uncertainty (JF-44, A2).

Keys casefold and collapse whitespace but never strip accents or otherwise
lose distinguishing characters. Ambiguous matches (same provider ID with
differing content, same title/company with differing location) are grouped as
``uncertain`` and remain visible; they are never silently merged or dropped.
"""
import re
from dataclasses import dataclass

from .models import Posting

_WS = re.compile(r"\s+")


def _norm(field: str) -> str:
    return _WS.sub(" ", field.casefold()).strip()


def dup_key(title: str, company: str, location: str) -> str:
    return "|".join(_norm(f) for f in (title, company, location))


@dataclass(frozen=True)
class DuplicateGroup:
    members: tuple[Posting, ...]
    confidence: str  # exact | uncertain | single
    reason: str


def _key(p: Posting) -> str:
    return dup_key(p.title, p.company, p.location)


def group_duplicates(postings) -> tuple[DuplicateGroup, ...]:
    items = sorted(postings, key=lambda p: p.url)
    n = len(items)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    uncertain_edges = []
    by_key = {}
    by_provider = {}
    by_title_company = {}
    for i, p in enumerate(items):
        by_key.setdefault(_key(p), []).append(i)
        if p.provider_id:
            by_provider.setdefault((p.provider, p.provider_id), []).append(i)
        by_title_company.setdefault((_norm(p.title), _norm(p.company)), []).append(i)

    for indices in by_key.values():
        for j in indices[1:]:
            union(indices[0], j)
    for indices in by_provider.values():
        for j in indices[1:]:
            union(indices[0], j)
            if _key(items[indices[0]]) != _key(items[j]):
                uncertain_edges.append((indices[0], j))
    for indices in by_title_company.values():
        for j in indices[1:]:
            union(indices[0], j)
            if _key(items[indices[0]]) != _key(items[j]):
                uncertain_edges.append((indices[0], j))

    buckets = {}
    for i in range(n):
        buckets.setdefault(find(i), []).append(i)

    groups = []
    for indices in buckets.values():
        members = tuple(items[i] for i in indices)
        if len(members) == 1:
            groups.append(DuplicateGroup(members, "single", ""))
            continue
        roots = {find(i) for i, _ in uncertain_edges}
        used_uncertain = any(find(i) in roots and find(j) in roots for i, j in uncertain_edges)
        if used_uncertain:
            groups.append(
                DuplicateGroup(members, "uncertain", "ambiguous identity match; kept visible for review")
            )
        else:
            groups.append(DuplicateGroup(members, "exact", "identical normalized title/company/location"))
    return tuple(sorted(groups, key=lambda g: g.members[0].url))