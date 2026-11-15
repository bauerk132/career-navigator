"""Pure data shapes for the Career Navigator synthetic core (stdlib only)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class UrlIdentity:
    original_url: str
    url: str
    provider: str
    provider_id: str | None
    trusted_host: bool
    rewritten: bool


@dataclass(frozen=True)
class Posting:
    """Synthetic job posting. Upstream verdict/status are source records only (A2)."""

    url: str
    title: str
    company: str
    location: str = ""
    details: str = ""
    source: str = "synthetic"
    pay_text: str = ""
    provider: str = "other"
    provider_id: str | None = None
    upstream_verdict: str | None = None
    upstream_status: str | None = None


@dataclass(frozen=True)
class Reason:
    factor: str
    outcome: str  # exclude | review | pass | info
    evidence: str


@dataclass(frozen=True)
class Decision:
    status: str  # eligible | review | rejected
    reasons: tuple[Reason, ...]