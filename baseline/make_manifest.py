"""Hash JobFinder source files from its checkout or an implementation worktree."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(r"C:/Users/kbaue/Projects/JobFinder")
OUT = Path(__file__).resolve().parent / "jobfinder-source-manifest.json"

INCLUDE_DIRS = ("jobfinder", "job_finder", "tests", "docs")
INCLUDE_ROOT_FILES = (
    ".gitignore",
    "README.md",
    "config.example.json",
    "connector_searches.json",
    "requirements.txt",
    "requirements-dev.txt",
    "run_jobfinder.bat",
)
EXCLUDE_NAMES = {
    "config.json",
    "connector_jobs.json",
    "connector_jobs.json.tmp",
    "jobfinder.db",
    "jobfinder.log",
    "test.db",
    "manual_jobs.txt",
    ".env",
}
PUBLIC_FIXTURES = {"tests/fixtures/connector_jobs.json", "tests/fixtures/manual_jobs.txt"}
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    ".agents",
    ".playwright",
    "output",
    "__pycache__",
    ".pytest_cache",
}


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], encoding="utf-8"
    )


def iter_files(root):
    paths = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    for name in sorted(set(paths.split("\0")) - {""}):
        rel = Path(name)
        if name not in INCLUDE_ROOT_FILES and rel.parts[0] not in INCLUDE_DIRS:
            continue
        if any(part in EXCLUDE_DIRS for part in rel.parts) or rel.suffix in {".db", ".log"}:
            continue
        if rel.name in EXCLUDE_NAMES and name not in PUBLIC_FIXTURES:
            continue
        p = root / rel
        if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root):
            yield p


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="JobFinder checkout or worktree")
    root = parser.parse_args().root.resolve()
    entries = []
    for p in iter_files(root):
        data = p.read_bytes()
        entries.append(
            {
                "path": p.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    manifest = {
        "repo": str(root),
        "git_branch": git(root, "branch", "--show-current").strip(),
        "git_head": git(root, "rev-parse", "HEAD").strip(),
        "git_commits": int(git(root, "rev-list", "--count", "HEAD")),
        "git_state": "local changes included" if git(root, "status", "--porcelain") else "clean",
        "note": (
            "Snapshot of JobFinder source files. "
            "Private configuration, runtime data, logs and ignored files are excluded."
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
