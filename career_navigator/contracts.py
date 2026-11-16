"""Typed eligibility result contract (CN-002; Slice A).

One result shape for every consumer (dashboard, API, ranking): the core's
decision + relevance, translated into a dashboard classification with explicit
unknown flags and a pay comparison against Kyle's current $21/hour.

Rules (Nov 15 decisions + Fix Round 3, locked):
- Decision status maps 1:1: eligible -> Strong Fit / Realistic (by relevance
  priority), review -> Needs Review, rejected -> Excluded.
- "Stretch" is a valid classification but the contract never emits it: it is
  reserved for the ranking layer (CN-005), which assigns it from fit-score
  gap analysis. The contract reports posting-side evidence only.
- Unknowns are explicit booleans, never inferred: pay missing/unparseable,
  location missing, driving unmentioned, remote PA eligibility unestablished.
- Pay is compared against Kyle's current $21/hour, annualized on the policy's
  hours/weeks, using the guaranteed low (annual_low). The $19/hour floor
  decision stays in the core's pay reason; the contract only reports it.
- stdlib only. The only I/O is loading the sibling JSON schema.
"""
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .pay import PayFacts, annual_high, annual_low, parse_pay
from .pipeline import Evaluation
from .policy import Policy

CLASSIFICATIONS = ("Strong Fit", "Realistic", "Stretch", "Needs Review", "Excluded")
DECISION_STATUSES = ("eligible", "review", "rejected")
VS_CURRENT = ("above", "below", "at", "unknown")
DUPLICATE_CONFIDENCES = ("exact", "uncertain", "single")
REASON_OUTCOMES = ("exclude", "review", "pass", "info")
PAY_STATUSES = ("bounded", "open_ended", "missing", "unparseable")

# Kyle's current pay: $21/hour at Wheelhouse (Senior Cook, Oct 22, 2026).
CURRENT_PAY_HOURLY = 21.0

_SCHEMA_CACHE: dict | None = None


@dataclass(frozen=True)
class PayComparison:
    status: str  # bounded | open_ended | missing | unparseable
    raw: str
    unit: str | None
    annual_low: float | None
    annual_high: float | None
    # True when the core's pay reason passes the floor, False when it excludes
    # for wholly-below-floor pay, None when unknown (missing, unparseable,
    # straddle, or open-ended low below the floor).
    meets_floor: bool | None
    vs_current: str  # above | below | at | unknown (guaranteed low vs $21/hr)
    current_annual: float
    delta_annual: float | None  # annual_low - current_annual, None when unknown


@dataclass(frozen=True)
class UnknownFlags:
    pay: bool
    location_missing: bool
    driving: bool
    remote_pa: bool

    def as_list(self) -> list[str]:
        names = []
        if self.pay:
            names.append("pay")
        if self.location_missing:
            names.append("location")
        if self.driving:
            names.append("driving")
        if self.remote_pa:
            names.append("remote_pa")
        return names


@dataclass(frozen=True)
class ResultContract:
    classification: str
    decision_status: str
    relevance_value: float
    relevance_priority: str | None
    relevance_wording: str
    factors: tuple[dict, ...]
    reasons: tuple[dict, ...]
    unknowns: UnknownFlags
    pay: PayComparison
    source: dict
    posting: dict
    duplicates: dict
    upstream_records: dict

    def to_dict(self) -> dict:
        data = asdict(self)
        data["unknowns"] = {
            "pay": self.unknowns.pay,
            "location_missing": self.unknowns.location_missing,
            "driving": self.unknowns.driving,
            "remote_pa": self.unknowns.remote_pa,
            "list": self.unknowns.as_list(),
        }
        data["relevance"] = {
            "value": self.relevance_value,
            "priority": self.relevance_priority,
            "wording": self.relevance_wording,
            "factors": [dict(f) for f in self.factors],
        }
        del data["relevance_value"]
        del data["relevance_priority"]
        del data["relevance_wording"]
        del data["factors"]
        data["reasons"] = [dict(r) for r in self.reasons]
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def classify(decision, relevance) -> str:
    """Dashboard classification for a core decision + relevance pair.

    rejected -> Excluded; review -> Needs Review; eligible + high priority ->
    Strong Fit; eligible + standard priority -> Realistic. "Stretch" is never
    returned here (reserved for the ranking layer, CN-005).
    """
    if decision.status == "rejected":
        return "Excluded"
    if decision.status == "review":
        return "Needs Review"
    if decision.status == "eligible":
        if relevance.priority == "high":
            return "Strong Fit"
        if relevance.priority == "standard":
            return "Realistic"
        raise ValueError(
            f"eligible decision with unexpected relevance priority: {relevance.priority!r}"
        )
    raise ValueError(f"unknown decision status: {decision.status!r}")


def _reason(decision, factor: str):
    return next(r for r in decision.reasons if r.factor == factor)


def _pay_comparison(
    pay: PayFacts, pay_reason_outcome: str, policy: Policy
) -> PayComparison:
    low = annual_low(
        pay, hours_per_week=policy.hours_per_week, weeks_per_year=policy.weeks_per_year
    )
    high = annual_high(
        pay, hours_per_week=policy.hours_per_week, weeks_per_year=policy.weeks_per_year
    )
    if pay_reason_outcome == "pass":
        meets_floor: bool | None = True
    elif pay_reason_outcome == "exclude":
        meets_floor = False
    else:
        meets_floor = None
    current_annual = CURRENT_PAY_HOURLY * policy.hours_per_week * policy.weeks_per_year
    if low is None:
        vs_current = "unknown"
        delta: float | None = None
    elif low > current_annual:
        vs_current = "above"
        delta = low - current_annual
    elif low < current_annual:
        vs_current = "below"
        delta = low - current_annual
    else:
        vs_current = "at"
        delta = 0.0
    return PayComparison(
        status=pay.status,
        raw=pay.raw,
        unit=pay.unit,
        annual_low=low,
        annual_high=high,
        meets_floor=meets_floor,
        vs_current=vs_current,
        current_annual=current_annual,
        delta_annual=delta,
    )


def build_contract(evaluation: Evaluation, policy: Policy) -> ResultContract:
    """Translate one pipeline Evaluation into the typed result contract."""
    posting = evaluation.posting
    decision = evaluation.decision
    relevance = evaluation.relevance
    pay = parse_pay(posting.pay_text)
    pay_reason = _reason(decision, "pay")
    location_reason = _reason(decision, "location")
    driving_reason = _reason(decision, "driving")
    loc = (posting.location or "").casefold()
    unknowns = UnknownFlags(
        pay=pay.status in ("missing", "unparseable"),
        location_missing=not (posting.location or "").strip(),
        driving=driving_reason.outcome == "info",
        # A remote posting whose location reason is review has no established
        # Pennsylvania eligibility (unstated, conditional, or conflicting).
        remote_pa=location_reason.outcome == "review" and "remote" in loc,
    )
    return ResultContract(
        classification=classify(decision, relevance),
        decision_status=decision.status,
        relevance_value=relevance.value,
        relevance_priority=relevance.priority,
        relevance_wording=relevance.wording,
        factors=tuple(
            {
                "name": f.name,
                "weight": f.weight,
                "raw": f.raw,
                "contribution": f.contribution,
                "evidence": f.evidence,
            }
            for f in relevance.factors
        ),
        reasons=tuple(
            {"factor": r.factor, "outcome": r.outcome, "evidence": r.evidence}
            for r in decision.reasons
        ),
        unknowns=unknowns,
        pay=_pay_comparison(pay, pay_reason.outcome, policy),
        source={
            "url": evaluation.identity.url,
            "original_url": evaluation.identity.original_url,
            "provider": evaluation.identity.provider,
            "provider_id": evaluation.identity.provider_id,
            "trusted_host": evaluation.identity.trusted_host,
            "rewritten": evaluation.identity.rewritten,
        },
        posting={
            "title": posting.title,
            "company": posting.company,
            "location": posting.location,
        },
        duplicates={
            "confidence": evaluation.duplicate_confidence,
            "reason": evaluation.duplicate_reason,
        },
        upstream_records=dict(evaluation.upstream_records),
    )


def load_schema() -> dict:
    """The JSON Schema for a result dict (contracts.schema.json, same package)."""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        path = Path(__file__).with_name("contracts.schema.json")
        _SCHEMA_CACHE = json.loads(path.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE


def _check_number(value, field: str, errors: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{field} must be a number, got {value!r}")


def _check_str(value, field: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append(f"{field} must be a string, got {value!r}")


def _check_bool(value, field: str, errors: list[str]) -> None:
    if not isinstance(value, bool):
        errors.append(f"{field} must be a boolean, got {value!r}")


def _check_nullable_number(value, field: str, errors: list[str]) -> None:
    if value is not None:
        _check_number(value, field, errors)


def _check_nullable_str(value, field: str, errors: list[str]) -> None:
    if value is not None:
        _check_str(value, field, errors)


def _check_nullable_bool(value, field: str, errors: list[str]) -> None:
    if value is not None:
        _check_bool(value, field, errors)


def validate_result(data: dict) -> list[str]:
    """Stdlib conformance check mirroring contracts.schema.json.

    Returns a list of error strings; empty means the result dict is valid.
    This checks shape, types, enums, and the classification/status mapping —
    it is not a full JSON Schema validator.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["result must be an object"]
    required = (
        "classification", "decision_status", "relevance", "reasons",
        "unknowns", "pay", "source", "posting", "duplicates", "upstream_records",
    )
    for key in required:
        if key not in data:
            errors.append(f"missing required field: {key}")
    for key in data:
        if key not in required:
            errors.append(f"unexpected field: {key}")
    if errors:
        return errors

    classification = data["classification"]
    if classification not in CLASSIFICATIONS:
        errors.append(f"classification must be one of {CLASSIFICATIONS}, got {classification!r}")
    status = data["decision_status"]
    if status not in DECISION_STATUSES:
        errors.append(f"decision_status must be one of {DECISION_STATUSES}, got {status!r}")
    else:
        expected = {
            "rejected": "Excluded",
            "review": "Needs Review",
        }.get(status)
        if expected is not None and classification != expected:
            errors.append(
                f"classification {classification!r} inconsistent with decision_status {status!r}"
            )
        if status == "eligible" and classification not in ("Strong Fit", "Realistic", "Stretch"):
            errors.append(
                f"classification {classification!r} inconsistent with decision_status 'eligible'"
            )

    relevance = data["relevance"]
    if not isinstance(relevance, dict):
        errors.append("relevance must be an object")
    else:
        for key in ("value", "priority", "wording", "factors"):
            if key not in relevance:
                errors.append(f"missing relevance field: {key}")
        if "value" in relevance:
            _check_number(relevance["value"], "relevance.value", errors)
            if isinstance(relevance["value"], (int, float)) and not isinstance(
                relevance["value"], bool
            ):
                if not 0.0 <= relevance["value"] <= 100.0:
                    errors.append(
                        f"relevance.value must be 0-100, got {relevance['value']!r}"
                    )
        if "priority" in relevance and relevance["priority"] not in (None, "high", "standard"):
            errors.append(
                f"relevance.priority must be high, standard, or null, got {relevance['priority']!r}"
            )
        if "wording" in relevance:
            _check_str(relevance["wording"], "relevance.wording", errors)
        factors = relevance.get("factors")
        if not isinstance(factors, list):
            errors.append("relevance.factors must be an array")
        else:
            for i, factor in enumerate(factors):
                if not isinstance(factor, dict):
                    errors.append(f"relevance.factors[{i}] must be an object")
                    continue
                for key in ("name", "weight", "raw", "contribution", "evidence"):
                    if key not in factor:
                        errors.append(f"missing relevance.factors[{i}] field: {key}")
                _check_str(factor.get("name"), f"relevance.factors[{i}].name", errors)
                _check_number(factor.get("weight"), f"relevance.factors[{i}].weight", errors)
                _check_number(factor.get("raw"), f"relevance.factors[{i}].raw", errors)
                _check_number(
                    factor.get("contribution"), f"relevance.factors[{i}].contribution", errors
                )
                _check_str(factor.get("evidence"), f"relevance.factors[{i}].evidence", errors)

    reasons = data["reasons"]
    if not isinstance(reasons, list):
        errors.append("reasons must be an array")
    else:
        for i, reason in enumerate(reasons):
            if not isinstance(reason, dict):
                errors.append(f"reasons[{i}] must be an object")
                continue
            for key in ("factor", "outcome", "evidence"):
                if key not in reason:
                    errors.append(f"missing reasons[{i}] field: {key}")
            _check_str(reason.get("factor"), f"reasons[{i}].factor", errors)
            if reason.get("outcome") not in REASON_OUTCOMES:
                errors.append(
                    f"reasons[{i}].outcome must be one of {REASON_OUTCOMES}, got {reason.get('outcome')!r}"
                )
            _check_str(reason.get("evidence"), f"reasons[{i}].evidence", errors)

    unknowns = data["unknowns"]
    if not isinstance(unknowns, dict):
        errors.append("unknowns must be an object")
    else:
        for key in ("pay", "location_missing", "driving", "remote_pa", "list"):
            if key not in unknowns:
                errors.append(f"missing unknowns field: {key}")
        for key in ("pay", "location_missing", "driving", "remote_pa"):
            if key in unknowns:
                _check_bool(unknowns[key], f"unknowns.{key}", errors)
        flag_list = unknowns.get("list")
        if not isinstance(flag_list, list) or not all(isinstance(x, str) for x in flag_list):
            errors.append("unknowns.list must be an array of strings")
        elif set(flag_list) != {
            name
            for name, value in (
                ("pay", unknowns.get("pay")),
                ("location", unknowns.get("location_missing")),
                ("driving", unknowns.get("driving")),
                ("remote_pa", unknowns.get("remote_pa")),
            )
            if value
        }:
            errors.append("unknowns.list does not match the unknown flags")

    pay = data["pay"]
    if not isinstance(pay, dict):
        errors.append("pay must be an object")
    else:
        for key in (
            "status", "raw", "unit", "annual_low", "annual_high", "meets_floor",
            "vs_current", "current_annual", "delta_annual",
        ):
            if key not in pay:
                errors.append(f"missing pay field: {key}")
        if pay.get("status") not in PAY_STATUSES:
            errors.append(
                f"pay.status must be one of {PAY_STATUSES}, got {pay.get('status')!r}"
            )
        _check_str(pay.get("raw"), "pay.raw", errors)
        _check_nullable_str(pay.get("unit"), "pay.unit", errors)
        _check_nullable_number(pay.get("annual_low"), "pay.annual_low", errors)
        _check_nullable_number(pay.get("annual_high"), "pay.annual_high", errors)
        _check_nullable_bool(pay.get("meets_floor"), "pay.meets_floor", errors)
        if pay.get("vs_current") not in VS_CURRENT:
            errors.append(
                f"pay.vs_current must be one of {VS_CURRENT}, got {pay.get('vs_current')!r}"
            )
        _check_number(pay.get("current_annual"), "pay.current_annual", errors)
        _check_nullable_number(pay.get("delta_annual"), "pay.delta_annual", errors)

    source = data["source"]
    if not isinstance(source, dict):
        errors.append("source must be an object")
    else:
        for key in ("url", "original_url", "provider", "provider_id", "trusted_host", "rewritten"):
            if key not in source:
                errors.append(f"missing source field: {key}")
        _check_str(source.get("url"), "source.url", errors)
        _check_str(source.get("original_url"), "source.original_url", errors)
        _check_str(source.get("provider"), "source.provider", errors)
        _check_nullable_str(source.get("provider_id"), "source.provider_id", errors)
        _check_bool(source.get("trusted_host"), "source.trusted_host", errors)
        _check_bool(source.get("rewritten"), "source.rewritten", errors)

    posting = data["posting"]
    if not isinstance(posting, dict):
        errors.append("posting must be an object")
    else:
        for key in ("title", "company", "location"):
            if key not in posting:
                errors.append(f"missing posting field: {key}")
            elif not isinstance(posting[key], str):
                errors.append(f"posting.{key} must be a string")

    duplicates = data["duplicates"]
    if not isinstance(duplicates, dict):
        errors.append("duplicates must be an object")
    else:
        if duplicates.get("confidence") not in DUPLICATE_CONFIDENCES:
            errors.append(
                f"duplicates.confidence must be one of {DUPLICATE_CONFIDENCES}, "
                f"got {duplicates.get('confidence')!r}"
            )
        _check_str(duplicates.get("reason"), "duplicates.reason", errors)

    if not isinstance(data["upstream_records"], dict):
        errors.append("upstream_records must be an object")
    return errors
