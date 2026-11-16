"""Synthetic read-only adapters (CN-004; Slice C).

Loads postings from a JSON seed file into the pipeline's Posting shape.
The source is synthetic-only and READ-ONLY: this module never writes to
the source file, the filesystem, or any live data store.

Live JobFinder integration (read-only import from the JobFinder SQLite
database and the canonical CSV) is separately gated (JF-14/21/08/40/25/
38/09/39) and NOT part of this slice. A live adapter must be a separate
module behind Kyle's explicit approval; it must never be bolted onto this
synthetic loader.
"""
import json
from pathlib import Path

from .models import Posting

_REQUIRED_KEYS = ("url", "title", "company", "location", "details", "pay_text")


def default_source_path() -> Path:
    return Path(__file__).parent / "synthetic_postings.json"


def load_postings(path=None) -> list[Posting]:
    """Read a JSON seed file and return its postings.

    The file holds {"postings": [{url, title, company, location, details,
    pay_text}, ...]}. Missing keys raise ValueError naming the row and key;
    a missing file raises FileNotFoundError with the path. The file is only
    ever read, never written.
    """
    source = Path(path) if path is not None else default_source_path()
    if not source.exists():
        raise FileNotFoundError(f"synthetic source not found: {source}")
    data = json.loads(source.read_text(encoding="utf-8"))
    rows = data.get("postings") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"synthetic source {source} must hold a 'postings' list")
    postings = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"synthetic source {source}: row {i} is not an object")
        for key in _REQUIRED_KEYS:
            if key not in row:
                raise ValueError(f"synthetic source {source}: row {i} missing key '{key}'")
            if not isinstance(row[key], str):
                raise ValueError(f"synthetic source {source}: row {i} key '{key}' must be a string")
        if not row["url"]:
            raise ValueError(f"synthetic source {source}: row {i} has an empty url")
        postings.append(Posting(**{key: row[key] for key in _REQUIRED_KEYS}))
    return postings
