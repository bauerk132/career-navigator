"""Local state folder and append-only JSONL logs (Phase 3c, Workstream 5).

Kyle's own decisions (duplicate resolutions now; eligibility reviews and
application events in later workstreams) are recorded as one JSON object per
line in files under a state folder that lives outside both repositories and
outside jobfinder.db. Nothing here ever rewrites or deletes a line: a change
of mind is a new line (an "undo" event), so the file is a full history.

The state folder is chosen, in order, by the explicit ``state_dir`` argument,
the CN_STATE_DIR environment variable, or, in live mode only, the default
``~/career-navigator-state``. The synthetic backend has no state folder, so
it can never write anything.

Reads tolerate damage: a line that is not valid JSON (for example a torn
write after a power cut) is counted and skipped, never fatal. Appends write
whole lines under a lock, flush and fsync before returning, and start a new
line first if the file does not already end in one, so a damaged tail can
never swallow the next record.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

STATE_DIR_ENV = "CN_STATE_DIR"
DEFAULT_STATE_DIRNAME = "career-navigator-state"

# A real log is a few hundred KB at most. Refuse to load anything absurd
# rather than read it into memory, and refuse to write a record that large.
MAX_FILE_BYTES = 20_000_000
MAX_RECORD_BYTES = 16_384


class StateError(RuntimeError):
    """The state folder or one of its logs cannot be used safely."""


def default_state_dir() -> Path:
    return Path.home() / DEFAULT_STATE_DIRNAME


def resolve_state_dir(explicit=None, live: bool = False) -> Path | None:
    """Pick the state folder, or None when this backend must not persist."""
    if explicit:
        return Path(explicit)
    from_env = os.environ.get(STATE_DIR_ENV, "").strip()
    if from_env:
        return Path(from_env)
    return default_state_dir() if live else None


class JsonlLog:
    """One append-only JSON-lines file with a signature-keyed read cache."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._cache_signature = None
        self._cache_value: tuple[list[dict], int] = ([], 0)

    def signature(self):
        """(mtime_ns, size) of the file, or None when it does not exist."""
        try:
            stat = os.stat(self.path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StateError(f"cannot read {self.path.name}: {exc}") from exc
        return (stat.st_mtime_ns, stat.st_size)

    def read(self) -> tuple[list[dict], int, object]:
        """Return (records, skipped_line_count, signature). Records are the
        JSON objects found, in file order. Cached until the file changes."""
        with self._lock:
            signature = self.signature()
            if signature is None:
                self._cache_signature = None
                self._cache_value = ([], 0)
                return [], 0, None
            if signature == self._cache_signature:
                records, skipped = self._cache_value
                return list(records), skipped, signature
            if signature[1] > MAX_FILE_BYTES:
                raise StateError(f"{self.path.name} is larger than {MAX_FILE_BYTES} bytes; refusing to load it")
            try:
                raw = self.path.read_bytes()
            except OSError as exc:
                raise StateError(f"cannot read {self.path.name}: {exc}") from exc
            records: list[dict] = []
            skipped = 0
            for line in raw.split(b"\n"):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line.decode("utf-8"))
                except (ValueError, RecursionError):
                    skipped += 1
                    continue
                if isinstance(value, dict):
                    records.append(value)
                else:
                    skipped += 1
            self._cache_signature = signature
            self._cache_value = (records, skipped)
            return list(records), skipped, signature

    def append(self, record: dict) -> None:
        """Append one record as one line, durably. Raises StateError on failure."""
        line = json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(line) > MAX_RECORD_BYTES:
            raise StateError("record is too large to store")
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                prefix = b""
                if self.path.exists() and self.path.stat().st_size > 0:
                    with open(self.path, "rb") as probe:
                        probe.seek(-1, os.SEEK_END)
                        if probe.read(1) != b"\n":
                            prefix = b"\n"  # damaged tail: never glue onto it
                with open(self.path, "ab") as handle:
                    handle.write(prefix + line + b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise StateError(f"cannot write {self.path.name}: {exc}") from exc
            self._cache_signature = None
