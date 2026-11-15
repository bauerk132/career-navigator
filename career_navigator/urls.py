"""CN-owned URL identity handling (contract A1; replaces defective JF-15/45 helpers).

Hosts are validated with exact or dot-boundary checks only; lookalike domains
are never trusted or rewritten. Provider ID precedence is deterministic
(indeed: vjk before jk, independent of query order). Original URLs are always
retained as provenance; untrusted/unknown hosts are preserved verbatim.
"""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from .models import UrlIdentity

_PROVIDERS = ("indeed.com", "linkedin.com")
_LINKEDIN_JOB = re.compile(r"^/jobs/view/(\d+)/?$")


def _trusted_provider(host: str) -> str | None:
    for domain in _PROVIDERS:
        if host == domain or host.endswith("." + domain):
            return domain.split(".")[0]
    return None


def canonical_url(url: str) -> UrlIdentity:
    if not url or not url.strip():
        raise ValueError("url must be a non-empty string")
    original = url
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    provider = _trusted_provider(host)
    if provider is None:
        return UrlIdentity(original, original, "other", None, False, False)

    scheme = parts.scheme.lower() or "https"
    path = parts.path
    if provider == "indeed":
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        provider_id = params.get("vjk") or params.get("jk")
        query = urlencode({"vjk": provider_id}) if "vjk" in params and provider_id else (
            urlencode({"jk": provider_id}) if provider_id else ""
        )
        canonical = f"{scheme}://{host}{path}" + (f"?{query}" if query else "")
    else:
        match = _LINKEDIN_JOB.match(path)
        provider_id = match.group(1) if match else None
        canonical = f"{scheme}://{host}{path}"
    return UrlIdentity(original, canonical, provider, provider_id, True, canonical != original)