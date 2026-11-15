"""Manually invoked stdout demo over synthetic fixtures only (C10; no live data)."""
from .models import Posting
from .pipeline import evaluate_postings
from .policy import Policy
from .relevance import WORDING

_FIXTURES = (
    Posting(url="https://www.indeed.com/viewjob?jk=TECH1", title="IT Support Technician",
            company="Acme Tech", location="Pittsburgh, PA",
            details="Support Windows systems and networks.", pay_text="$25 an hour",
            provider="indeed", provider_id="TECH1", upstream_verdict="ok", upstream_status="new"),
    Posting(url="https://www.indeed.com/viewjob?jk=REST1", title="Restaurant General Manager",
            company="Diner Corp", location="Pittsburgh, PA", pay_text="$55,000 a year",
            provider="indeed", provider_id="REST1"),
    Posting(url="https://www.indeed.com/viewjob?jk=POS1", title="POS Support Technician",
            company="Eat'n Park Restaurant Group", location="Philadelphia, PA",
            details="Support point-of-sale systems in restaurants.", pay_text="$22 an hour",
            provider="indeed", provider_id="POS1"),
    Posting(url="https://www.indeed.com/viewjob?jk=DRV1", title="Delivery Driver",
            company="QuickShip", location="Pittsburgh, PA", details="CDL required.",
            pay_text="$18 an hour", provider="indeed", provider_id="DRV1",
            upstream_verdict="ok"),
    Posting(url="https://www.indeed.com/viewjob?jk=MISS1", title="Data Analyst",
            company="Bridge Analytics", location="", details="Analyze data pipelines.",
            pay_text="", provider="indeed", provider_id="MISS1"),
    Posting(url="https://www.indeed.com/viewjob?jk=DUP1", title="Cafe Manager",
            company="Morning Brew", location="Pittsburgh, PA", pay_text="$45,000 a year",
            provider="indeed", provider_id="DUP1"),
    Posting(url="https://www.indeed.com/viewjob?vjk=DUP1&jk=OTHER", title="Café Supervisor",
            company="Morning Brew", location="Pittsburgh, PA", pay_text="$45,000 a year",
            provider="indeed", provider_id="DUP1"),
    Posting(url="https://www.indeed.com/viewjob?jk=REM1", title="Remote IT Analyst",
            company="Commonwealth Systems", location="Remote",
            details="California residents only.", pay_text="From $20 an hour",
            provider="indeed", provider_id="REM1"),
    Posting(url="https://indeed.com.evil.example/viewjob?jk=FAKE", title="IT Help Desk",
            company="Lookalike LLC", location="Pittsburgh, PA", pay_text="$20 an hour"),
)


def render_report() -> str:
    lines = [
        "Career Navigator — synthetic core demo (stdout only; synthetic fixtures, no live data)",
        WORDING,
        "",
    ]
    for ev in evaluate_postings(_FIXTURES, Policy(salary_floor_annual=41600.0)):
        p = ev.posting
        lines.append(f"[{ev.decision.status.upper()}] {p.title} — {p.company} ({p.location or 'location missing'})")
        lines.append(f"  url: {ev.identity.url} (trusted_host={ev.identity.trusted_host}, provider={ev.identity.provider})")
        if ev.identity.original_url != ev.identity.url:
            lines.append(f"  original: {ev.identity.original_url}")
        priority = ev.relevance.priority if ev.relevance.priority is not None else "not ranked (review/rejected)"
        lines.append(f"  relevance: {ev.relevance.value:.2f}/100  priority: {priority}")
        for f in ev.relevance.factors:
            lines.append(f"    - {f.name}: weight={f.weight:.2f} raw={f.raw:.2f} contribution={f.contribution:.3f} | {f.evidence}")
        for r in ev.decision.reasons:
            lines.append(f"    * {r.factor} [{r.outcome}]: {r.evidence}")
        lines.append(f"  duplicates: {ev.duplicate_confidence} {ev.duplicate_reason}".rstrip())
        lines.append(f"  upstream source records (not truth): {ev.upstream_records}")
        lines.append("")
    return "\n".join(lines)