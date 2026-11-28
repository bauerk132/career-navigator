# Career Navigator V3: local SQLite database for saved decisions

Status: DRAFT for Kyle's approval, 2026-11-27. Nothing is built yet.
Repo affected: career-navigator (backend) only. The dashboard does not change.

## Intent and why
Kyle wants a real database on his laptop for the things he saves in Career Navigator, the way Hivemind keeps its data on the machine. Today those saves are three append-only JSON-lines files in `~/career-navigator-state`. They work, but they are fragile (a torn line is skipped), hard to query, and a poor base for later features. A single local database fixes that without sending private job-search data anywhere.

## Goals
- One SQLite file, `career-navigator.db`, in the existing state folder, holding the same three logs: applications, duplicate resolutions, review decisions.
- Behave exactly as today from the outside: same API, same append-only history, same undo events, same 503 when no state folder.
- Durable writes, no torn records, safe with the backend's threads.
- Nothing is lost: a one-time import of any existing `.jsonl` history, a daily backup, and an export back to JSONL.

## Non-goals
- No job history, snapshots or analytics tables yet (later phase, once this is in use).
- No change to what the dashboard shows or sends.
- No hosting, sync, accounts or cloud.

## Anti-goals (failure even if it "works")
- Writing anything to `jobfinder.db`, or changing `contracts.py`, `adapters.py`, eligibility rules, ranking weights or dedup rules.
- Silently starting from an empty database when the file is damaged or unreadable.
- Creating a database in synthetic mode.
- Any new third-party dependency (ARM64 Surface): use Python's built-in `sqlite3` (3.49.1 here).

## Constraints
Localhost only. `jobfinder.db` stays read-only. Phase 3 hard rules still apply. The 589-test backend suite and the live gate must still pass unchanged.

## Approaches considered
- **A. One local SQLite file (chosen by Kyle).** Single file, built in, transactional.
- B. Readable per-record files plus a search index. Rejected: more moving parts, and the files and index can disagree.
- Firebase / cloud database. Rejected: puts private data on Google's servers and breaks the localhost-only rule.

## Design (decided)
- **Swap point.** All three stores already use one class, `JsonlLog` in `state_store.py` (`read()`, `append()`, `signature()`). Add `SqliteLog` with the same interface and switch the three constructors to it (`applications.py`, `duplicate_resolutions.py`, `review_decisions.py`). Their folding and view logic is untouched.
- **Schema.** One table `events(seq INTEGER PRIMARY KEY AUTOINCREMENT, log TEXT, record TEXT, created_utc TEXT)` where `log` is one of the three names and `record` is the same JSON text written today. A `meta` table holds schema version and import status. Triggers reject UPDATE and DELETE on `events`, so append-only is enforced by the database.
- **Durability.** WAL mode, `synchronous=FULL`, busy timeout, existing per-log lock kept, existing 16 KB record limit kept.
- **Read cache.** `signature()` becomes (row count, max seq) for that log, so the existing change-detection keeps working.
- **Import.** On first live start, if `meta` has no import marker and `.jsonl` files exist, import every valid line in file order inside one transaction, then record the marker. Safe to re-run. Original `.jsonl` files are left in place and no longer read. Invalid lines are counted and reported, not imported.
- **Backup and export.** A once-a-day copy using SQLite's backup API into `backups/` in the state folder (keep the last 14). A command `python -m career_navigator export-state <folder>` writes the three logs back out as JSONL in today's format. The existing `tracker-exports` CSV folder is unchanged.
- **Failure behaviour.** A damaged or unreadable database raises `StateError` as today (writes return 503, reads report degraded). Never fall back to empty. No database file is created when there is no state folder.
- **Tests.** Run the existing store tests against the SQLite backend; add tests for append-only triggers, concurrent appends, idempotent import, torn-line import, backup, and export round-trip. Live gate: `jobfinder.db` byte-identical, contracts pin unchanged.

## Calls I made (Kyle can overrule)
1. One `events` table with a `log` column, not three tables.
2. Keep the old `.jsonl` files after import instead of deleting or renaming them.
3. Daily backup, keep 14.
4. Include the export command in this first version.
5. First version stores only today's three logs; job history comes later.

## Left to the builder
Exact column types and indexes, file and function names, how the daily backup is triggered (on startup and first write of the day is fine), whether health reports database size and event counts.

## Facts found
Real data today is tiny: `applications.jsonl` has 2 lines; no duplicate or review decisions saved yet. Migration risk is low and this is the cheapest moment to switch.
