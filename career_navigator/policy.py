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

# Blueprint fit weights (skills 30% / career growth 30% / hiring
# requirements 20% / commute+schedule 10% / comp 10%). The weights are the
# blueprint's; the thresholds are Muse defaults.
DEFAULT_FIT_WEIGHTS = {
    "skills": 0.30,
    "career_growth": 0.30,
    "hiring_requirements": 0.20,
    "commute_schedule": 0.10,
    "comp": 0.10,
}


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
    # Fit-score bands for the ranking engine (CN-005). Muse defaults, tunable;
    # not a Kyle decision. The 30/30/20/10/10 weights are the blueprint's.
    fit_strong_threshold: float = 70.0
    fit_realistic_threshold: float = 45.0
    # Blueprint fit weights, configurable per policy (B-G1-01). Validated in
    # __post_init__: exactly the five keys, each 0..1, summing to 1.0.
    fit_weights: dict = field(default_factory=lambda: dict(DEFAULT_FIT_WEIGHTS))
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    def __post_init__(self):
        weights = self.fit_weights
        if set(weights) != set(DEFAULT_FIT_WEIGHTS):
            raise ValueError(
                f"fit_weights must have exactly the keys {sorted(DEFAULT_FIT_WEIGHTS)}"
            )
        for name, value in weights.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"fit_weights[{name!r}] must be a number in [0, 1]")
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"fit_weights[{name!r}] must be a number in [0, 1]")
        if abs(sum(weights.values()) - 1.0) > 1e-6:
            raise ValueError("fit_weights must sum to 1.0")
        object.__setattr__(self, "fit_weights", dict(weights))
