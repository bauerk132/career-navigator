"""Golden synthetic examples for the result contract (CN-002; Slice A).

Eight postings, one per contract case. Synthetic only, no live data. Each
example pins the expected dashboard classification; the contract tests run
the real pipeline on these postings and assert the contract output matches.

Cases: eligible, missing-pay -> review, PA-excluded, driving -> excluded,
Philadelphia included, remote-PA-unknown -> review, pay-straddle -> review,
A09 remote-across-US -> eligible (Kyle decision Nov 16, 2026: remote across
the US/USA is nationwide scope and good for jobs, not review).
"""
from dataclasses import dataclass

from .models import Posting


@dataclass(frozen=True)
class GoldenExample:
    name: str
    posting: Posting
    expected_classification: str
    note: str


def _posting(url, title, company, location, details="", pay_text=""):
    return Posting(
        url=url, title=title, company=company, location=location,
        details=details, pay_text=pay_text, provider="indeed",
        provider_id=url.rsplit("jk=", 1)[-1],
    )


GOLDEN_EXAMPLES: tuple[GoldenExample, ...] = (
    GoldenExample(
        name="eligible-pittsburgh",
        posting=_posting(
            "https://www.indeed.com/viewjob?jk=GOLD1",
            "IT Support Technician", "Acme Tech", "Pittsburgh, PA",
            "Support Windows systems and networks.", "$25 an hour",
        ),
        expected_classification="Strong Fit",
        note="Eligible, high relevance priority; pay above Kyle's $21/hr.",
    ),
    GoldenExample(
        name="missing-pay-review",
        posting=_posting(
            "https://www.indeed.com/viewjob?jk=GOLD2",
            "Data Analyst", "Bridge Analytics", "Pittsburgh, PA",
            "Analyze data pipelines.", "",
        ),
        expected_classification="Needs Review",
        note="Missing pay -> Needs Review (Kyle decision 2026-11-15); pay unknown flag set.",
    ),
    GoldenExample(
        name="pa-excluded-remote",
        posting=_posting(
            "https://www.indeed.com/viewjob?jk=GOLD3",
            "Remote IT Analyst", "Commonwealth Systems", "Remote",
            "California residents only.", "$20 an hour",
        ),
        expected_classification="Excluded",
        note="Remote explicitly restricted to California; Pennsylvania not included.",
    ),
    GoldenExample(
        name="driving-excluded",
        posting=_posting(
            "https://www.indeed.com/viewjob?jk=GOLD4",
            "Delivery Driver", "QuickShip", "Pittsburgh, PA",
            "CDL required.", "$18 an hour",
        ),
        expected_classification="Excluded",
        note="Verified mandatory driving/license requirement (and pay below floor).",
    ),
    GoldenExample(
        name="philadelphia-included",
        posting=_posting(
            "https://www.indeed.com/viewjob?jk=GOLD5",
            "POS Support Technician", "Eat'n Park Restaurant Group",
            "Philadelphia, PA",
            "Support point-of-sale systems in restaurants.", "$22 an hour",
        ),
        expected_classification="Strong Fit",
        note="Philadelphia is a regular primary city alongside Pittsburgh.",
    ),
    GoldenExample(
        name="remote-pa-unknown-review",
        posting=_posting(
            "https://www.indeed.com/viewjob?jk=GOLD6",
            "Remote Help Desk Analyst", "Keystone Support", "Remote",
            "Answer support tickets.", "$21 an hour",
        ),
        expected_classification="Needs Review",
        note="Remote with no Pennsylvania evidence -> review; pay exactly at $21/hr.",
    ),
    GoldenExample(
        name="pay-straddle-review",
        posting=_posting(
            "https://www.indeed.com/viewjob?jk=GOLD7",
            "IT Support Technician", "Acme Tech", "Pittsburgh, PA",
            "Support Windows systems and networks.", "$18 to $25 an hour",
        ),
        expected_classification="Needs Review",
        note="Bounded range straddles the $19/hr floor (low below, high above) -> review.",
    ),
    GoldenExample(
        name="a09-remote-across-us",
        posting=_posting(
            "https://www.indeed.com/viewjob?jk=GOLD8",
            "Remote Support Analyst", "Keystone Support", "Remote",
            "Remote across the US.", "$22 an hour",
        ),
        expected_classification="Strong Fit",
        note="A09, Kyle decision Nov 16, 2026: 'Remote across the US' is "
        "nationwide scope and eligible, not review.",
    ),
)


def golden_postings() -> tuple[Posting, ...]:
    return tuple(example.posting for example in GOLDEN_EXAMPLES)
