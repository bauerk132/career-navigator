"""Untrusted-text handling (C7, JF-38): suppress script/style, strip tags, escape."""
import html
import re

_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_UNCLOSED = re.compile(r"<(script|style)\b[^>]*>.*$", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")


def clean_text(text: str) -> str:
    text = _SCRIPT_STYLE.sub(" ", text)
    text = _UNCLOSED.sub(" ", text)
    return _TAGS.sub(" ", text)


def escape_html(text: str) -> str:
    return html.escape(text, quote=True)


def make_excerpt(text: str, limit: int = 300) -> str:
    """Collapse whitespace and return a bounded display excerpt."""
    if limit < 1:
        return ""
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    # `limit` bounds the excerpt text; the trailing ellipsis is an extra marker.
    cut = value[:limit]
    if cut and not cut[-1].isspace() and len(value) > limit and not value[limit].isspace():
        boundary = cut.rfind(" ")
        if boundary > 0:
            cut = cut[:boundary]
    return cut.rstrip() + "…"
