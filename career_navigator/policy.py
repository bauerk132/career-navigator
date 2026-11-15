"""Configurable policy for the synthetic core (conservative defaults; C9).

Salary-floor treatment of open-ended low pay and unknown-pay ranking are open
policy decisions for Kyle (combined plan); defaults here are conservative and
configurable, not final policy.
"""
from dataclasses import dataclass, field

DEFAULT_WEIGHTS = {
    "technical_alignment": 0.4,
    "location_fit": 0.25,
    "pay_clarity": 0.2,
    "driving_compatibility": 0.15,
}


@dataclass(frozen=True)
class Policy:
    excluded_employers: tuple[str, ...] = ()
    primary_cities: tuple[str, ...] = ("pittsburgh",)
    optional_cities: tuple[str, ...] = ("philadelphia",)
    allow_remote: bool = True
    remote_compatible_regions: tuple[str, ...] = ("pennsylvania", "pa")
    salary_floor_annual: float | None = None
    hours_per_week: float = 40.0
    weeks_per_year: float = 52.0
    priority_threshold: float = 60.0
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))