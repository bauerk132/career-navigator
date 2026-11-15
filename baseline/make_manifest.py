"""Read-only baseline manifest of approved public JobFinder source files.

Private files (config.json, jobfinder.db, logs, output/, .venv/, .git/,
.agents/) are excluded by design and never opened. The manifest is written
outside the JobFinder working tree. No JobFinder file is modified.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(r"C:/Users/kbaue/Projects/JobFinder")
OUT = Path(__file__).resolve().parent / "jobfinder-source-manifest.json"

INCLUDE_DIRS = ("jobfinder", "job_finder", "tests", "docs")
INCLUDE_ROOT_FILES = (
    ".gitignore",
    "README.md",
    "config.example.json",
    "requirements.txt",
    "requirements-dev.txt",
    "run_jobfinder.bat",
)
EXCLUDE_NAMES = {
    "config.json",
    "jobfinder.db",
    "jobfinder.log",
    "test.db",
    "manual_jobs.txt",
    ".env",
}
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    ".agents",
    ".playwright",
    "output",
    "__pycache__",
    ".pytest_cache",
}


def iter_files():
    for name in INCLUDE_ROOT_FILES:
        p = ROOT / name
        if p.is_file():
            yield p
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            if p.name in EXCLUDE_NAMES:
                continue
            yield p


def main():
    entries = []
    for p in iter_files():
        data = p.read_bytes()
        entries.append(
            {
                "path": p.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    manifest = {
        "repo": str(ROOT),
        "git_branch": "master",
        "git_commits": 0,
        "git_state": "all files untracked; no commits",
        "note": (
            "Read-only snapshot manifest of approved public source files. "
            "Private config/database/log/output files excluded by design."
        ),
        "file_count": len(entries),
        "files": entries,
    }
    OUT.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"manifest: {OUT}")
    print(f"files: {len(entries)}")
    print(f"total bytes: {sum(e['bytes'] for e in entries)}")


if __name__ == "__main__":
    main()