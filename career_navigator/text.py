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