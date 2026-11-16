"""Deterministic eligibility engine (C6/C9; A2: CN decides, upstream is a source record).

Verified mandatory driving/license excludes; negation passes; contradiction or
ambiguity goes to review; no mention stays unknown. Restaurant *operations*
roles exclude, but technical roles (including restaurant-domain hybrids such as
POS/IT) are not excluded by domain alone. Missing location is review, never a
confident out-of-area rejection. Missing salary goes to Needs Review (Kyle
2026-11-15); the value stays unknown. Bounded pay ranges straddling the floor
go to Needs Review (Kyle 2026-11-15). Remote geography follows explicit
Pennsylvania evidence: open => pass, excludes => exclude, unclear/unstated =>
review; nationwide US scope includes Pennsylvania. Geographic "only" phrases
must be genuinely geographic; incidental state mentions never restrict.
Explicit PA applicant exclusions (cannot employ / rejects / residents cannot
apply) beat nationwide or PA remote text. Labeled or soft geographic
qualifiers (Region: Canada, Location: NY, NY required) that conflict with the
remote scope go to review; alone they never hard-reject (Fix Round 2).
"""
import re

from .models import Decision, Posting, Reason
from .pay import annual_high, annual_low, floor_check, parse_pay
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
_RESIDENTS_ONLY = re.compile(r"\b([a-z][a-z ]*?)\s+residents?\s+only\b")
_GEO_ONLY = re.compile(r"\b([a-z][a-z ,.&]*?)\s+only\b")
_REMOTE_SUFFIX = re.compile(r"\bremote\b\s*[-–—(,:]\s*([a-z][a-z ,.&]*?)\s*\)?\s*$")
_PA = re.compile(r"\bpennsylvania\b|\bpa\b")
_PA_EXCLUDED = (
    re.compile(r"\b(?:not|no|excluding|except|excludes?|outside)\b[^.;]{0,40}\b(?:pennsylvania|pa)\b"),
    re.compile(r"\b(?:pennsylvania|pa)\b[^.;]{0,30}\b(?:excluded|ineligible|not eligible|not available|not accepted)\b"),
    re.compile(r"\b(?:cannot|can not|can't|won't|will not|unable to|rejects?|refuses?|declines?|prohibits?)\b[^.;]{0,30}\b(?:pennsylvania|pa)\b\s+(?:residents?|applicants?|candidates?)\b"),
    re.compile(r"\b(?:pennsylvania|pa)\b\s+(?:residents?|applicants?|candidates?)\b[^.;]{0,20}\b(?:cannot|can not|can't|won't|will not|may not|are not|is not|do not|does not|need not|not)\b[^.;]{0,20}\b(?:apply|qualify|considered|eligible|hired|employed)\b"),
    re.compile(r"\b(?:cannot|can not|can't|may not|must not|will not|unable to)\b\s+(?:reside|live|be based|be located)\b[^.;]{0,20}\b(?:pennsylvania|pa)\b"),
)
_NATIONWIDE = re.compile(
    r"\b(?:united states|nationwide|any (?:us )?state|all (?:50 )?states)\b|\bu\.s\.|\bus\s+only\b"
)
# Full-text fallback: pronoun "us only" ("contact us only by email") is not
# nationwide scope. Genuine "US only" clauses are captured as regions instead.
_NATIONWIDE_TEXT = re.compile(
    r"\b(?:united states|nationwide|any (?:us )?state|all (?:50 )?states)\b|\bu\.s\."
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
# Abbreviations that collide with ordinary English words. An "only" phrase made
# solely of these is ambiguous, never a verified geographic restriction.
_COLLISION_ABBRS = frozenset({"in", "or", "me", "id", "oh", "ok", "hi"})
_GEO_VOCAB = (
    frozenset(w for name in _US_STATE_NAMES for w in name.split())
    | _US_STATE_ABBRS
    | {"us", "usa", "u.s.", "u.s", "nationwide", "united", "states", "america"}
)
_GEO_SAFE = _GEO_VOCAB - _COLLISION_ABBRS
# Candidate-scope connector words allowed inside a geographic "only" phrase
# ("Applicants from California only"); content words still must be geographic.
_CANDIDATE_CONNECTORS = frozenset({
    "applicants", "candidates", "workers", "employees", "from", "in", "to", "of",
    "open", "available", "hiring", "based", "located", "the", "and",
})
_INCIDENTAL_SUBJECT = re.compile(
    r"\b(?:customers?|clients?|users?|company|corporation|incorporated|headquartered|headquarters|offices?)\b"
)
_CANDIDATE_CONTEXT = re.compile(
    r"\b(?:applicants?|candidates?|employees?|workers?|you|must|reside|residents?)\b"
)
# Labeled geographic fields are explicit location claims; their values are
# consumed without a vocabulary guard. Soft phrases need a strong geographic
# token so "Python required" / "CDL required" never invent a region.
_GEO_LABEL = re.compile(
    r"\b(?:geographic\s+region|location|region|territory|area|country|state|province)\s*:\s*([a-z][a-z ,.&-]*?)(?=\s*[.;]|\s*$)"
)
_GEO_SOFT = re.compile(r"\b([a-z][a-z ,&-]*?)\s+(?:required|preferred|mandatory)\b")
_GEO_WITHIN = re.compile(r"\bwithin\s+([a-z][a-z ,&-]*?)(?=\s*[.;]|\s*$)")
_GEO_LIMITED = re.compile(r"\blimited to\s+([a-z][a-z ,&-]*?)(?=\s*[.;]|\s*$)")
_GEO_WORK_FROM = re.compile(r"\bwork from\s+([a-z][a-z ,&-]*?)(?=\s*[.;]|\s*$)")
_GEO_BASED = re.compile(r"\b([a-z][a-z-]*?)-based\b")
_FOREIGN_GEO = frozenset({
    "canada", "ontario", "quebec", "alberta", "manitoba", "saskatchewan",
    "british columbia", "newfoundland", "nova scotia", "new brunswick",
    "germany", "france", "mexico", "india", "australia", "japan", "china",
    "brazil", "spain", "italy", "netherlands", "ireland", "new zealand",
    "uk", "united kingdom", "england", "scotland", "wales",
})
_STRONG_GEO = _GEO_SAFE | _FOREIGN_GEO


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
    tokens = set(re.findall(r"[a-z.]+", region))
    if (region in {"us", "u.s.", "u.s", "usa"} or tokens & {"us", "usa", "u.s.", "u.s"}
            or _NATIONWIDE.search(region)):
        return "nationwide"
    if tokens & _US_STATE_ABBRS or any(name in region for name in _US_STATE_NAMES):
        return "other"
    return "unclear"


def _is_geo_only(region: str) -> bool:
    tokens = re.findall(r"[a-z.]+", region)
    allowed = _GEO_VOCAB | _CANDIDATE_CONNECTORS
    return bool(tokens) and all(t in allowed for t in tokens) and any(t in _GEO_SAFE for t in tokens)


def _incidental_subject(text: str, start: int) -> bool:
    sentence_start = max(text.rfind(p, 0, start) for p in ".;!?\n") + 1
    prefix = text[sentence_start:start]
    return bool(_INCIDENTAL_SUBJECT.search(prefix)) and not _CANDIDATE_CONTEXT.search(prefix)


def _has_strong_geo(phrase: str) -> bool:
    return any(t in _STRONG_GEO for t in re.findall(r"[a-z.]+", phrase))


def _geo_qualifiers(loc: str, details: str) -> list[str]:
    values = []
    for field_text in (loc, details):
        values.extend(m.group(1).strip() for m in _GEO_LABEL.finditer(field_text))
        for rx in (_GEO_SOFT, _GEO_WITHIN, _GEO_LIMITED, _GEO_WORK_FROM, _GEO_BASED):
            for m in rx.finditer(field_text):
                phrase = m.group(1).strip()
                if _has_strong_geo(phrase):
                    values.append(phrase)
    return [v for v in values if v]


def _remote_location_reason(loc: str, details: str, policy: Policy) -> Reason:
    if not policy.allow_remote:
        return Reason("location", "exclude", "remote not allowed by policy")
    text = f"{loc} {details}"
    regions = []
    for field_text in (loc, details):
        for m in _RESTRICTION.finditer(field_text):
            if _incidental_subject(field_text, m.start()):
                continue
            regions.append(m.group(1).strip())
        for m in _RESIDENTS_ONLY.finditer(field_text):
            regions.append(m.group(1).strip())
        for m in _GEO_ONLY.finditer(field_text):
            region = m.group(1).strip()
            if _is_geo_only(region):
                regions.append(region)
    suffix = _REMOTE_SUFFIX.search(loc)
    if suffix:
        regions.append(suffix.group(1).strip())
    regions = [r for r in regions if r]
    kinds = {_classify_region(r) for r in regions}
    if any(rx.search(text) for rx in _PA_EXCLUDED):
        return Reason("location", "exclude", "remote explicitly excludes Pennsylvania (posting text)")
    pa_mentioned = bool(_PA.search(text))
    qualifiers = _geo_qualifiers(loc, details)
    if qualifiers:
        qkinds = {_classify_region(q) for q in qualifiers}
        incompatible = qkinds - {"pa", "nationwide"}
        if incompatible:
            if kinds or pa_mentioned or _NATIONWIDE_TEXT.search(text):
                return Reason("location", "review",
                              f"conflicting remote geographic qualifier: {', '.join(qualifiers)} "
                              f"vs {', '.join(regions) or 'remote scope'}; review")
            return Reason("location", "review",
                          f"remote geographic qualifier ambiguous: {', '.join(qualifiers)}; "
                          "Needs Review (not inferred)")
        if "other" in kinds:
            return Reason("location", "review",
                          f"conflicting remote geographic qualifier: {', '.join(qualifiers)} "
                          f"vs {', '.join(regions)}; review")
        regions.extend(qualifiers)
        kinds |= qkinds
    if pa_mentioned and "other" in kinds:
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
    if _NATIONWIDE_TEXT.search(text):
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
        high = annual_high(pay, hours_per_week=policy.hours_per_week, weeks_per_year=policy.weeks_per_year)
        if pay.status == "bounded" and high is not None and high >= policy.salary_floor_annual:
            return Reason("pay", "review",
                          f"bounded pay range straddles floor (annualized low {low:.2f} < {policy.salary_floor_annual:.2f} "
                          f"<= high {high:.2f}); Needs Review (Kyle decision 2026-11-15)")
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