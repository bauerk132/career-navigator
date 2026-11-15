"""Deterministic eligibility engine (C6/C9; A2: CN decides, upstream is a source record).

Verified mandatory driving/license excludes; negation passes; contradiction or
ambiguity goes to review; no mention stays unknown. Restaurant *operations*
roles exclude, but technical roles (including restaurant-domain hybrids such as
POS/IT) are not excluded by domain alone. Missing location is review, never a
confident out-of-area rejection. Missing salary goes to Needs Review (Kyle
2026-11-15); the value stays unknown. Remote geography follows explicit
Pennsylvania evidence: open => pass, excludes => exclude, unclear/unstated =>
review; nationwide US scope includes Pennsylvania.
"""
import re

from .models import Decision, Posting, Reason
from .pay import annual_low, floor_check, parse_pay
from .policy import Policy
from .text import clean_text

_MANDATORY = (
    re.compile(r"\b(?:must have|must hold|requires?|required|needs?)\b[^.;]{0,60}\bdriver'?s? license\b"),
    re.compile(r"\bdriver'?s? license\b[^.;]{0,30}\b(?:required|necessary|a must)\b"),
    re.compile(r"\bmust\b[^.;]{0,25}\bdrive\b"),
    re.compile(r"\bcdl\b[^.;]{0,30}\brequired\b|\brequired\b[^.;]{0,30}\bcdl\b"),
    re.compile(r"\bdelivery driver\b"),
)
_NEGATED = (
    re.compile(r"\bno\b[^.;]{0,30}\bdriving\b"),
    re.compile(r"\bno\b[^.;]{0,30}\bdriver'?s? license\b"),
    re.compile(r"\b(?:does not|do not|never)\b[^.;]{0,30}\b(?:require[ds]?|need(?:ed)?)\b[^.;]{0,30}\bdriv"),
)
_PREFERRED = re.compile(r"\bdriv(?:ing|e)\b[^.;]{0,20}\bpreferred\b|\bpreferred\b[^.;]{0,20}\bdriv(?:ing|e)\b")

_TECHNICAL = (
    "technician", "engineer", "developer", "software", "help desk", "pos", "analyst",
    "systems", "network", "support", "data", "cybersecurity", "programmer", "administrator",
)
_OPERATIONS = (
    "server", "cook", "chef", "kitchen", "bartender", "barista", "dishwasher", "host",
    "restaurant", "general manager", "shift leader", "food service", "dining", "waiter",
    "waitress",
)
_RESTRICTION = re.compile(r"\b(?:reside in|live in|residents? of)\s+([a-z][a-z ]*)")
_ONLY = re.compile(r"\b([a-z][a-z ]*?)\s+only\b")
_REMOTE_SUFFIX = re.compile(r"\bremote\b\s*[-–—(,:]\s*([a-z][a-z ,.&]*?)\s*\)?\s*$")
_PA = re.compile(r"\bpennsylvania\b|\bpa\b")
_PA_EXCLUDED = (
    re.compile(r"\b(?:not|no|excluding|except|excludes?|outside)\b[^.;]{0,40}\b(?:pennsylvania|pa)\b"),
    re.compile(r"\b(?:pennsylvania|pa)\b[^.;]{0,30}\b(?:excluded|ineligible|not eligible|not available|not accepted)\b"),
)
_NATIONWIDE = re.compile(
    r"\b(?:united states|nationwide|any (?:us )?state|all (?:50 )?states)\b|\bu\.s\.|\bus\s+only\b"
)
_US_STATE_NAMES = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "rhode island", "south carolina", "south dakota", "tennessee",
    "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
})
_US_STATE_ABBRS = frozenset({
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi",
    "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi",
    "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc",
    "nd", "oh", "ok", "or", "ri", "sc", "sd", "tn", "tx", "ut", "vt",
    "va", "wa", "wv", "wi", "wy", "dc",
})


def _has_technical(text: str) -> list[str]:
    hits = [kw for kw in _TECHNICAL if kw in text]
    if re.search(r"\bit\b", text):
        hits.append("it")
    return hits


def _role_reason(title: str) -> Reason:
    text = title.casefold().replace("-", " ")
    tech = _has_technical(text)
    ops = [kw for kw in _OPERATIONS if kw in text]
    if tech and ops:
        return Reason("role", "pass", f"technical precedence over domain signals: {', '.join(tech)} vs {', '.join(ops)}")
    if ops:
        return Reason("role", "exclude", f"restaurant operations role: {', '.join(ops)}")
    if tech:
        return Reason("role", "pass", f"technical role: {', '.join(tech)}")
    return Reason("role", "info", "no restaurant-operations or technical signals in title")


def _driving_reason(text: str) -> Reason:
    mandatory = [rx.pattern for rx in _MANDATORY if rx.search(text)]
    negated = [rx.pattern for rx in _NEGATED if rx.search(text)]
    if mandatory and negated:
        return Reason("driving", "review", "contradictory driving/license evidence; ambiguous, kept for review")
    if mandatory:
        return Reason("driving", "exclude", "verified mandatory driving/license requirement")
    if negated:
        return Reason("driving", "pass", "driving/license requirement explicitly negated")
    if _PREFERRED.search(text):
        return Reason("driving", "review", "driving preference is ambiguous; kept for review")
    return Reason("driving", "info", "no driving/license mention; unknown (not verified no-driving)")


def _classify_region(region: str) -> str:
    if _PA.search(region):
        return "pa"
    if region in {"us", "u.s.", "u.s", "usa"} or _NATIONWIDE.search(region):
        return "nationwide"
    tokens = set(re.findall(r"[a-z.]+", region))
    if tokens & _US_STATE_ABBRS or any(name in region for name in _US_STATE_NAMES):
        return "other"
    return "unclear"


def _remote_location_reason(loc: str, details: str, policy: Policy) -> Reason:
    if not policy.allow_remote:
        return Reason("location", "exclude", "remote not allowed by policy")
    text = f"{loc} {details}"
    regions = [
        m.group(1).strip()
        for field_text in (loc, details)
        for rx in (_RESTRICTION, _ONLY)
        for m in rx.finditer(field_text)
    ]
    suffix = _REMOTE_SUFFIX.search(loc)
    if suffix:
        regions.append(suffix.group(1).strip())
    regions = [r for r in regions if r]
    kinds = {_classify_region(r) for r in regions}
    if any(rx.search(text) for rx in _PA_EXCLUDED):
        return Reason("location", "exclude", "remote explicitly excludes Pennsylvania (posting text)")
    pa_mentioned = bool(_PA.search(text))
    other_mentioned = any(name in text for name in _US_STATE_NAMES)
    if pa_mentioned and (other_mentioned or "other" in kinds):
        return Reason("location", "review",
                      "conflicting remote geographic evidence: Pennsylvania and other-state restriction; review")
    if kinds:
        if "unclear" in kinds:
            return Reason("location", "review",
                          f"remote geographic eligibility unclear: {', '.join(regions)}")
        if "other" in kinds and kinds != {"other"}:
            return Reason("location", "review",
                          f"conflicting remote geographic restrictions: {', '.join(regions)}")
        if "other" in kinds:
            return Reason("location", "exclude",
                          f"remote restricted to {', '.join(regions)}; Pennsylvania not included")
        if "pa" in kinds:
            return Reason("location", "pass",
                          f"remote explicitly open to Pennsylvania: {', '.join(regions)}")
        return Reason("location", "pass", "remote; nationwide US scope includes Pennsylvania")
    if pa_mentioned:
        return Reason("location", "pass", "remote explicitly open to Pennsylvania (posting text)")
    if other_mentioned:
        return Reason("location", "exclude", "remote limited to other state(s); Pennsylvania not included")
    if _NATIONWIDE.search(text):
        return Reason("location", "pass", "remote; nationwide US scope includes Pennsylvania")
    return Reason("location", "review", "remote geographic eligibility unstated; Needs Review (not inferred)")


def _location_reason(posting: Posting, policy: Policy) -> Reason:
    loc = clean_text(posting.location).casefold()
    details = clean_text(posting.details).casefold()
    if not loc.strip():
        return Reason("location", "review", "missing location; review, not confidently out-of-area")
    if "remote" in loc:
        return _remote_location_reason(loc, details, policy)
    for city in policy.primary_cities:
        if city in loc:
            return Reason("location", "pass", f"{city} (primary)")
    for city in policy.optional_cities:
        if city in loc:
            return Reason("location", "pass", f"{city} (optional)")
    return Reason("location", "exclude", f"out of area: {posting.location.strip()}")


def _pay_reason(posting: Posting, policy: Policy) -> Reason:
    pay = parse_pay(posting.pay_text)
    if pay.status == "missing":
        return Reason("pay", "review",
                      "salary not stated; Needs Review (Kyle decision 2026-11-15); value remains unknown, not coerced")
    if pay.status == "unparseable":
        return Reason("pay", "info", f"salary present but unparseable: {pay.raw!r}; remains unknown")
    if policy.salary_floor_annual is None:
        return Reason("pay", "info", f"pay {pay.status}: {pay.raw!r}; no floor configured")
    check = floor_check(
        pay, policy.salary_floor_annual,
        hours_per_week=policy.hours_per_week, weeks_per_year=policy.weeks_per_year,
    )
    low = annual_low(pay, hours_per_week=policy.hours_per_week, weeks_per_year=policy.weeks_per_year)
    if check is False:
        if pay.open_ended:
            return Reason("pay", "review",
                          f"open-ended low pay below floor (annualized low {low:.2f} < {policy.salary_floor_annual:.2f}); "
                          "Needs Review (Kyle decision 2026-11-15)")
        return Reason("pay", "exclude", f"pay below floor (annualized low {low:.2f} < {policy.salary_floor_annual:.2f}, unrounded)")
    if check is None:
        return Reason("pay", "info", f"pay {pay.status}: {pay.raw!r}; floor comparison unknown, remains visible")
    return Reason("pay", "pass", f"pay meets floor (annualized low {low:.2f} >= {policy.salary_floor_annual:.2f}, unrounded)")


def evaluate(posting: Posting, policy: Policy) -> Decision:
    text = clean_text(f"{posting.title}. {posting.details}").casefold()
    reasons = []
    if posting.company.casefold() in {e.casefold() for e in policy.excluded_employers}:
        reasons.append(Reason("employer", "exclude", f"employer on exclusion list: {posting.company}"))
    else:
        reasons.append(Reason("employer", "pass", "employer not on exclusion list"))
    reasons.append(_role_reason(posting.title))
    reasons.append(_driving_reason(text))
    reasons.append(_location_reason(posting, policy))
    reasons.append(_pay_reason(posting, policy))
    outcomes = {r.outcome for r in reasons}
    if "exclude" in outcomes:
        status = "rejected"
    elif "review" in outcomes:
        status = "review"
    else:
        status = "eligible"
    return Decision(status, tuple(reasons))