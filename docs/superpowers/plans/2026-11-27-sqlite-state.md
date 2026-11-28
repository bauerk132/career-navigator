# Career Navigator V3: Local SQLite State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three append-only JSONL logs (applications, duplicate resolutions, review decisions) with one local SQLite file, with one-time import, daily backup and JSONL export.

**Architecture:** The three stores already talk to one class, `JsonlLog` (`read()`, `append()`, `signature()`). Add `SqliteLog` with the same interface in a new `career_navigator/sqlite_log.py`, switch the three store constructors to it, and keep every fold/view function untouched. Backup and export live in a small `state_tools.py`.

**Tech Stack:** Python 3.12 stdlib `sqlite3` (SQLite 3.49.1), existing FastAPI app, `unittest`. No new dependencies.

**Spec:** `C:\Users\kbaue\kepler\tasks\42396d90-b677-45b1-a3ab-32808a23dc66\v3-sqlite-state-design.md` (approved by Kyle, 2026-11-27).

## Global Constraints

- Repo: `C:\Users\kbaue\Projects\career-navigator`. Work on a new branch `cn/v3-sqlite-state` off `main` (`9d0fe37`). Never commit to `main`; never push or open a PR without Kyle's say-so.
- Test command (the system Python has no fastapi): `PYTHONDONTWRITEBYTECODE=1 "C:\Users\kbaue\Documents\Qoder\2026-11-23\b3539718\career-navigator-v2-gate\.venv\Scripts\python.exe" -m unittest discover -s tests`. Baseline: 589 tests, OK. Call that interpreter `$PY` below.
- Git work goes through the GitKraken tools (`git_checkout`, `git_add`, `git_commit`); say so if falling back to `git`. Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Do not touch `jobfinder.db`, `contracts.py`, `contracts.schema.json`, `adapters.py`, eligibility, ranking, dedup rules, or anything in the dashboard repo.
- No new third-party dependency. Database file: `career-navigator.db` in the state folder (`resolve_state_dir`). Log names are exactly `applications`, `duplicate_resolutions`, `review_decisions`.
- Append-only: the database itself must reject UPDATE and DELETE on `events`. Max record size stays `MAX_RECORD_BYTES` (16,384) from `state_store.py`.
- Durability: WAL, `synchronous=FULL`, busy timeout 5 s, one short-lived connection per operation.
- Synthetic mode (no state folder) never creates a database. Reading with no database and no legacy file creates nothing.
- A damaged database raises `StateError`; never fall back to empty.
- Backups: `backups/career-navigator.YYYY-MM-DD.db` in the state folder, keep the last 14. Export: `python -m career_navigator export-state <folder>` writes the three logs as JSONL in today's format.

## Review Focus

1. A non-SQLite or corrupt `career-navigator.db` must raise `StateError` on read and write, and the file must not be overwritten (Task 1).
2. Two processes or threads appending at once must lose nothing and give distinct, ordered `seq` values (Task 1).
3. A legacy `.jsonl` with a torn or non-object line imports the good lines, reports the skipped count, and importing twice adds nothing (Task 2).
4. No state folder, or a state folder with nothing in it, creates no files and reads empty (Tasks 1 and 3).
5. Export into the state folder itself, backup with no database, and pruning must never touch the live database or legacy `.jsonl` files (Task 4).

---

## File Structure

- Create `career_navigator/sqlite_log.py`: `SqliteLog`, schema, triggers, legacy import.
- Create `career_navigator/state_tools.py`: `backup_if_due`, `export_state`.
- Modify `career_navigator/applications.py`, `duplicate_resolutions.py`, `review_decisions.py`: swap `JsonlLog(...)` for `SqliteLog(...)`.
- Modify `career_navigator/app.py`: call `backup_if_due` at live startup; update the module docstring that says persistence is V3 scope.
- Modify `career_navigator/__main__.py`: add the `export-state` command, keep today's no-argument behaviour.
- Modify `career_navigator/state_store.py`: docstring only (points to `sqlite_log.py`); `JsonlLog` stays (used by the importer and its own tests).
- Create `tests/state_helpers.py`, `tests/test_sqlite_log.py`, `tests/test_state_tools.py`; modify `tests/test_applications.py`, `tests/test_duplicate_resolutions.py`, `tests/test_review_decisions.py`.

---

### Task 1: `SqliteLog` core

**Files:**
- Create: `career_navigator/sqlite_log.py`, `tests/test_sqlite_log.py`
- Also: create branch `cn/v3-sqlite-state` off `main`; copy the design and this plan into `docs/superpowers/specs/2026-11-27-sqlite-state-design.md` and `docs/superpowers/plans/2026-11-27-sqlite-state.md` and commit them first.

**Interfaces:**
- Consumes: `StateError`, `MAX_RECORD_BYTES`, `JsonlLog` from `career_navigator/state_store.py`.
- Produces:
  ```python
  DB_FILENAME = "career-navigator.db"
  LOG_NAMES = ("applications", "duplicate_resolutions", "review_decisions")
  class SqliteLog:
      def __init__(self, state_dir: Path, name: str, legacy_filename: str | None = None)
      db_path: Path        # state_dir / DB_FILENAME
      def signature(self) -> tuple[int, int] | None   # (row count, max seq) for this log; None if the db file does not exist
      def read(self) -> tuple[list[dict], int, object]   # (records in seq order, skipped_count, signature); same shape as JsonlLog.read
      def append(self, record: dict) -> None             # raises StateError
  ```
  `name` not in `LOG_NAMES` raises `ValueError`.

- [ ] **Step 1: Branch and docs.** Check out `cn/v3-sqlite-state` from `main`, add the two docs, commit `docs: V3 SQLite state design and plan`.
- [ ] **Step 2: Write failing tests** in `tests/test_sqlite_log.py` (temp folder per test):
  - `test_read_with_no_db_returns_empty_and_creates_nothing`: `read()` returns `([], 0, None)`; folder still empty.
  - `test_append_then_read_round_trips_in_order`: three records, including `{"note": "caf\u00e9 \U0001F600"}`, read back equal and in order.
  - `test_logs_are_separate`: append to `applications` and `review_decisions`; each `read()` sees only its own.
  - `test_signature_changes_on_append`: signature before/after differs; `None` before the db exists.
  - `test_oversized_record_refused`: `{"big": "x" * 20000}` raises `StateError`; nothing stored.
  - `test_update_and_delete_are_rejected`: open the file with raw `sqlite3`, `UPDATE events SET record='{}'` and `DELETE FROM events` each raise `sqlite3.DatabaseError`; rows unchanged.
  - `test_unknown_log_name_raises_value_error`.
  - `test_corrupt_db_raises_state_error_and_is_not_overwritten`: write `b"not a database"` to `career-navigator.db`; `read()` and `append()` raise `StateError`; file bytes unchanged.
  - `test_concurrent_appends_lose_nothing`: 4 threads x 25 appends on separate `SqliteLog` instances over the same folder; `read()` returns 100 records and the raw `seq` values are strictly increasing.
- [ ] **Step 3: Run** `$PY -m unittest tests.test_sqlite_log -v`. Expected: all fail (module missing).
- [ ] **Step 4: Implement `SqliteLog`.** Schema (`CREATE ... IF NOT EXISTS`): `events(seq INTEGER PRIMARY KEY AUTOINCREMENT, log TEXT NOT NULL, record TEXT NOT NULL, created_utc TEXT NOT NULL)`, an index on `(log, seq)`, `meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)`, and `BEFORE UPDATE` / `BEFORE DELETE` triggers on `events` that `RAISE(ABORT, 'events are append-only')`. Each operation opens `sqlite3.connect(db_path, timeout=5)`, sets `PRAGMA journal_mode=WAL` and `synchronous=FULL`, and closes afterwards. Serialise records with `json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True)` (same as `JsonlLog`). Every `sqlite3.Error` becomes `StateError` with the file name in the message. `read()` returns early with `([], 0, None)` when the db file does not exist; `append()` creates the folder and schema. Keep a per-instance lock and a read cache keyed on `signature()`. Skipped count is 0 for now (Task 2 fills it in).
- [ ] **Step 5: Run** the same command. Expected: all pass. Run the full suite once to confirm 589 + new tests still OK.
- [ ] **Step 6: Commit** `feat: SqliteLog append-only store`.

### Task 2: One-time legacy JSONL import

**Files:**
- Modify: `career_navigator/sqlite_log.py`, `tests/test_sqlite_log.py`

**Interfaces:**
- Consumes: Task 1 `SqliteLog`; `JsonlLog(path).read() -> (records, skipped, signature)`.
- Produces: `SqliteLog(state_dir, name, legacy_filename)` imports `state_dir / legacy_filename` once per log the first time the log is read or appended. `read()` returns, as its skipped count, the number of legacy lines that were not importable. Marker stored in `meta` under key `import:<name>` as JSON `{"imported": int, "skipped": int, "at": "<UTC ISO>"}`.

- [ ] **Step 1: Write failing tests:**
  - `test_legacy_file_is_imported_on_first_read`: legacy file with 3 valid lines; `read()` returns those 3 in order, skipped 0; legacy file bytes unchanged afterwards.
  - `test_import_runs_once`: create `SqliteLog` twice over the same folder, read each; still 3 records; marker present.
  - `test_torn_and_non_object_lines_are_skipped_and_counted`: legacy content is the four lines `{"a":1}`, `[1,2]`, `"str"`, `{"broken":` followed by `{"b":2}` with no trailing newline; records are `[{"a":1},{"b":2}]`, skipped `3`; a later `read()` still reports 3.
  - `test_append_after_import_keeps_order`: legacy has 2 lines; `append({"c":3})`; read gives a, b, c.
  - `test_import_is_per_log`: two logs with different legacy files each import only their own.
  - `test_no_legacy_file_means_no_marker_and_no_db_on_read`.
  - `test_concurrent_first_use_imports_once`: 4 threads, separate instances, legacy with 5 lines; every read sees exactly 5.
- [ ] **Step 2: Run** `$PY -m unittest tests.test_sqlite_log -v`. Expected: new tests fail.
- [ ] **Step 3: Implement** the import in the private `_prepare()` step used by `read()` and `append()`: if the legacy file exists, inside one `BEGIN IMMEDIATE` transaction check for the marker, and if absent insert all valid records (via `JsonlLog(legacy).read()`, which already skips bad lines and enforces the 20 MB limit) in file order plus the marker. Never modify or delete the legacy file. `read()` takes its skipped count from the marker.
- [ ] **Step 4: Run** the module, then the full suite. Expected: all pass.
- [ ] **Step 5: Commit** `feat: import legacy JSONL history into SqliteLog once`.

### Task 3: Switch the three stores and their tests

**Files:**
- Modify: `career_navigator/applications.py:224`, `career_navigator/duplicate_resolutions.py:152`, `career_navigator/review_decisions.py:210`, `career_navigator/state_store.py` (docstring), `career_navigator/app.py` (module docstring, line 7)
- Create: `tests/state_helpers.py`
- Modify: `tests/test_applications.py`, `tests/test_duplicate_resolutions.py`, `tests/test_review_decisions.py`

**Interfaces:**
- Consumes: Task 2 `SqliteLog(state_dir, name, legacy_filename)`.
- Produces: `tests/state_helpers.py` with `read_events(state_dir: Path, name: str) -> list[dict]` (raw `sqlite3` read of one log in seq order; `[]` when the db file does not exist) and `db_exists(state_dir: Path) -> bool`.

- [ ] **Step 1: Write the helper** and rewrite the tests' file helpers to use it. In each store test module, replace `log_path` reads (`test_applications.py:204-207`, `test_duplicate_resolutions.py:133-139`, `test_review_decisions.py:128-134`) with `read_events`, replace `assertFalse(self.log_path.exists())` (no-write cases) with `assertFalse(db_exists(self.state_dir))`, and change the directory-listing assertions (`test_duplicate_resolutions.py:373`, `test_review_decisions.py:314`) to expect `{"career-navigator.db"}`. Tests that deliberately write a damaged `.jsonl` (`test_duplicate_resolutions.py:403`, `test_review_decisions.py:440`) keep writing the legacy file first and now assert the same skipped-line behaviour through the import. Add `test_legacy_jsonl_is_imported_at_app_start` to `test_review_decisions.py`: legacy file with one valid decision, app started, `GET` shows the decision, legacy file untouched.
- [ ] **Step 2: Run** the three modules. Expected: they fail because the stores still use `JsonlLog`.
- [ ] **Step 3: Swap the constructors** to `SqliteLog(state_dir, "<name>", <existing FILENAME constant>)`, using the module's existing `APPLICATIONS_FILENAME`, `RESOLUTIONS_FILENAME`, `REVIEW_FILENAME` as the legacy filename. Update the docstrings that say "Reads and appends `X.jsonl`".
- [ ] **Step 4: Run** the three modules, then the full suite. Expected: OK, count = 589 + Task 1 and 2 tests + the one new test. `test_provenance.py` and `test_live_wiring.py` must pass unchanged.
- [ ] **Step 5: Commit** `feat: stores save to career-navigator.db`.

### Task 4: Daily backup, export command, startup hook

**Files:**
- Create: `career_navigator/state_tools.py`, `tests/test_state_tools.py`
- Modify: `career_navigator/__main__.py`, `career_navigator/app.py` (startup, live mode only)

**Interfaces:**
- Consumes: `DB_FILENAME`, `LOG_NAMES`, `StateError`, `resolve_state_dir`.
- Produces:
  ```python
  def backup_if_due(state_dir: Path, today: datetime.date | None = None, keep: int = 14) -> Path | None
  def export_state(state_dir: Path, out_dir: Path) -> dict[str, int]   # {log_name: record_count}
  ```
  CLI: `python -m career_navigator export-state <folder> [--state-dir <folder>]`. With no arguments the module behaves exactly as today (prints the demo report).

- [ ] **Step 1: Write failing tests** in `tests/test_state_tools.py`:
  - `test_backup_none_when_no_db`: returns `None`, creates no `backups/` folder.
  - `test_backup_creates_dated_copy_once_per_day`: first call returns `backups/career-navigator.2026-11-28.db`; second call the same day returns `None`; the copy opens and holds the same events.
  - `test_backup_prunes_to_keep_newest`: pre-create 16 dated files plus `notes.txt`; after a call with `keep=14` only the 14 newest `career-navigator.*.db` remain and `notes.txt` is untouched.
  - `test_export_round_trip`: append records to all three logs, export, then each exported file's lines parse to the same records in order, written with today's JSON settings (sorted keys, compact separators).
  - `test_export_refuses_state_folder_itself`: `out_dir == state_dir` raises `StateError`; legacy `.jsonl` files unchanged.
  - `test_export_with_no_db_raises_state_error`.
  - `test_cli_export_state`: run `__main__.main(["export-state", out, "--state-dir", state])`, exit code 0, files exist; running with no arguments still prints the demo report.
  - `test_app_startup_takes_backup_in_live_mode_only`: live app over a state folder that already has a db creates one backup; the synthetic app creates none.
- [ ] **Step 2: Run** `$PY -m unittest tests.test_state_tools -v`. Expected: fail.
- [ ] **Step 3: Implement.** `backup_if_due` uses `sqlite3.Connection.backup` into a temp name, then renames, so a half-written backup never has a final name. `export_state` writes each log to a temp file then renames. Wrap `__main__` in a `main(argv=None)` function; keep the demo print as the no-argument path. In `create_app`, after the stores are built and only when the state dir is not `None`, call `backup_if_due(state_dir)` inside a `try/except StateError` that records the error but does not stop startup.
- [ ] **Step 4: Run** the module, then the full suite. Expected: OK.
- [ ] **Step 5: Commit** `feat: daily backup and export-state command`.

### Task 5: End-to-end verification

**Files:** none changed unless a check fails.

- [ ] **Step 1:** Full suite with `$PY`. Expected: OK, no failures.
- [ ] **Step 2: Real-data rehearsal on a copy.** Copy `C:\Users\kbaue\career-navigator-state` to a temp folder, start the app against it with `CN_STATE_DIR` pointing at the copy and `JOBFINDER_DB_PATH` at the real `jobfinder.db` (read-only), then `GET /api/applications` and `GET /health`. Expected: the 2 existing application events present, `career-navigator.db` created in the copy only, a backup taken on the second start, and `export-state` output matching the original `applications.jsonl` content. The real state folder must be unchanged (compare file list and sizes before and after).
- [ ] **Step 3: Live gate.** From `C:\Users\kbaue\Projects\career-navigator-dashboard`, run the daily gate the way WS6 did (`python live_gate.py --daily`, own port 8010) against the branch checkout. Expected: PASS, `jobfinder.db` byte-identical, `contracts.py` pin unchanged. Do not commit the gate's output files.
- [ ] **Step 4:** Report results to Kyle with the branch name and test count. Do not push, open a PR, or switch Kyle's running backend to the branch without his approval.

---

## Self-review notes

- Spec coverage: schema, triggers, WAL/FULL, signature, import, backup, export, failure behaviour, synthetic-mode rule, tests and live gate each map to a task above. The "health shows db size and counts" item is left to the builder and is intentionally not planned.
- Types: `SqliteLog.read()` returns the same 3-tuple as `JsonlLog.read()`, so the stores' `fold_events(records, skipped, signature)` calls are unchanged.
