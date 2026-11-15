"""CN-owned pay parsing (contract A1; JF-08/40/41 semantics).

Distinguishes missing / present-but-unparseable / bounded / open-ended.
Units: hour, week, month, year (USD only). Values are never rounded before
floor comparison; weekly hours and weeks per year are explicit, caller-supplied
assumptions (defaults 40/52) and are never silently invented per posting.
"""
import re
from dataclasses import dataclass

_AMOUNT = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
_UNITS = (
    ("hour", re.compile(r"\b(?:hour|hr|hourly)\b")),
    ("week", re.compile(r"\b(?:week|wk|weekly)\b")),
    ("month", re.compile(r"\b(?:month|mo|monthly)\b")),
    ("year", re.compile(r"\b(?:year|yr|annual|annually)\b")),
)


@dataclass(frozen=True)
class PayFacts:
    status: str  # bounded | open_ended | missing | unparseable
    low: float | None
    high: float | None
    unit: str | None
    open_ended: bool
    raw: str


def _amounts(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in _AMOUNT.findall(text)]


def parse_pay(text: str | None) -> PayFacts:
    if text is None or not text.strip():
        return PayFacts("missing", None, None, None, False, "" if text is None else text)
    raw = text.strip()
    lowered = raw.lower()
    unit = next((name for name, rx in _UNITS if rx.search(lowered)), None)
    amounts = _amounts(raw)
    if unit is None or not amounts:
        return PayFacts("unparseable", None, None, unit, False, raw)
    if re.search(r"\bup\s+to\b", lowered):
        return PayFacts("open_ended", None, amounts[0], unit, True, raw)
    if re.search(r"\b(?:from|starting\s+at)\b", lowered):
        return PayFacts("open_ended", amounts[0], None, unit, True, raw)
    if len(amounts) >= 2:
        low, high = sorted(amounts[:2])
        return PayFacts("bounded", low, high, unit, False, raw)
    return PayFacts("bounded", amounts[0], amounts[0], unit, False, raw)


def annual_low(
    pay: PayFacts, *, hours_per_week: float = 40.0, weeks_per_year: float = 52.0
) -> float | None:
    """Annualized guaranteed-low pay, or None when unknown. Unrounded."""
    if pay.low is None or pay.status in ("missing", "unparseable"):
        return None
    if pay.unit == "hour":
        return pay.low * hours_per_week * weeks_per_year
    if pay.unit == "week":
        return pay.low * weeks_per_year
    if pay.unit == "month":
        return pay.low * 12.0
    if pay.unit == "year":
        return pay.low
    return None


def annual_high(
    pay: PayFacts, *, hours_per_week: float = 40.0, weeks_per_year: float = 52.0
) -> float | None:
    """Annualized high pay, or None when unknown. Unrounded."""
    if pay.high is None or pay.status in ("missing", "unparseable"):
        return None
    if pay.unit == "hour":
        return pay.high * hours_per_week * weeks_per_year
    if pay.unit == "week":
        return pay.high * weeks_per_year
    if pay.unit == "month":
        return pay.high * 12.0
    if pay.unit == "year":
        return pay.high
    return None


def floor_check(
    pay: PayFacts, floor_annual: float, *, hours_per_week: float = 40.0, weeks_per_year: float = 52.0
) -> bool | None:
    """True/False against an annual floor, or None when pay is unknown."""
    value = annual_low(pay, hours_per_week=hours_per_week, weeks_per_year=weeks_per_year)
    if value is None:
        return None
    return value >= floor_annual