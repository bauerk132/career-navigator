"""Configurable policy for the synthetic core (C9).

Defaults encode Kyle's 2026-11-15 decisions (hof_01M7FPD05XTSBPK4T7): a $19/hour
salary floor, Philadelphia primary alongside Pittsburgh, and remote geographic
eligibility decided from explicit Pennsylvania evidence. Still configurable.
"""
from dataclasses import dataclass, field

DEFAULT_WEIGHTS = {
    "technical_alignment": 0.4,
    "location_fit": 0.25,
    "pay_clarity": 0.2,
    "driving_compatibility": 0.15,
}

SALARY_FLOOR_HOURLY = 19.0


@dataclass(frozen=True)
class Policy:
    excluded_employers: tuple[str, ...] = ()
    primary_cities: tuple[str, ...] = ("pittsburgh", "philadelphia")
    optional_cities: tuple[str, ...] = ()
    allow_remote: bool = True
    remote_compatible_regions: tuple[str, ...] = ("pennsylvania", "pa")
    # $19/hr x 40h x 52wk (Kyle decision 2026-11-15); None disables the floor.
    salary_floor_annual: float | None = SALARY_FLOOR_HOURLY * 40.0 * 52.0
    hours_per_week: float = 40.0
    weeks_per_year: float = 52.0
    priority_threshold: float = 60.0
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))