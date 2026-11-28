# Career Navigator: Phase 4 scope plan (connector job sources)

- **Status:** PLAN ONLY. Approving this plan is not approval to build. Nothing here has been coded, committed or pushed.
- **Written:** Nov 28, 2026, after Kyle connected the Indeed and ZipRecruiter connectors in Claude.
- **Code read for this plan:** `bauerk132/career-navigator` at `726e8f0`, `bauerk132/career-navigator-dashboard` at `0b20ceb`, and the JobFinder folder on the laptop (`C:\Users\kbaue\Projects\JobFinder`), which has no commits yet.
- **How to read the marks:** a claim with a file name next to it was checked in the code. **[unchecked]** means it could not be confirmed from the code or a test call and needs a look before building.

## The short version

Phase 4 adds two new places to find jobs: the Indeed and ZipRecruiter connectors. Today every job comes from Gmail alert emails or links Kyle pastes in. The connectors can search the job boards directly and return structured pay, but only Claude can call them. JobFinder is plain Python and can't.

So the plan is a hand-off through a file:

1. A scheduled Claude task runs at 8:30 AM ET, searches both connectors and writes one file of jobs.
2. JobFinder's 9 AM run reads that file as a new source, filters it with the same rules as the email alerts, and stores the jobs in `jobfinder.db`.
3. Career Navigator and the dashboard read the jobs the same way they do now. They only need to learn the two new source names.

| Step | Workstreams (WS), in build order | What Kyle gets |
|---|---|---|
| 4a: a safety net | 1 JobFinder under git | JobFinder changes have a history and can be undone |
| 4b: the pull | 2 Daily connector pull, 3 JobFinder connector source | Connector jobs show up in the 9 AM digest next to the email jobs, with pay filled in |
| 4c: the dashboard | 4 Career Navigator and dashboard accept the new sources | The dashboard shows where each connector job came from instead of a generic "jobfinder" label |
| 4d: suburbs | 5 Philadelphia suburbs | Jobs in close Philadelphia suburbs in PA pass, and border towns land in Needs Review |
| 4e: the check | 6 Two-week source comparison | A count of what the connectors found that the email alerts missed |

WS4's code lands before WS3's source is switched on in the 9 AM run. That way no connector row ever reaches Career Navigator under a name it doesn't know.

Hard rules that carry over and apply to every workstream:

1. Nothing applies to a job, sends a message or contacts anyone automatically. The scheduled task only searches and writes one file.
2. Career Navigator still opens `jobfinder.db` read-only (`live_adapter.py`). Only JobFinder writes to it.
3. `career_navigator/contracts.py` and `career_navigator/adapters.py` stay untouched. No source names live there (checked by search).
4. The `jobs` table keeps its exact columns. `live_adapter.py:189` raises `SchemaDriftError` if a column is added or removed, so any schema change would take the dashboard down.
5. Localhost only. Nothing public.

**This plan changes JobFinder's code.** Phase 3 ruled that out (`career-navigator-dashboard/docs/phase3-scope-plan.md`, Out of scope). On Nov 1 Kyle also limited sources to alert emails and pasted links, before the connectors were available. On Nov 28 Kyle said yes to both (Q1 and Q2). The email alerts stay on next to the connectors.

---

## What exists today (the starting line)

**JobFinder (`C:\Users\kbaue\Projects\JobFinder`)**
- No git history. `git log` says the `master` branch has no commits. Every file is untracked. `.gitignore` already leaves out `config.json`, `jobfinder.db`, `output/`, `*.log`, `.env` and `manual_jobs.txt`.
- Three sources, built in `jobfinder/sources/__init__.py` `build_sources`: Indeed email, LinkedIn email and the manual file. Each source has a `name` and a `fetch()` that returns `RawJob` rows. `run_source` turns a crash into a failed health row so the other sources still run.
- `RawJob` (`models.py:10`) holds title, company, location, url, source, details, posted and needs_details. Pay is parsed out of `details` by `parse_pay`.
- `manual.py` is the closest pattern for a file-based source. A missing file means no jobs, not an error.
- The dedup key is the URL. `storage.py:10` has `url TEXT NOT NULL UNIQUE`. `canonical_url` (`normalize.py:108`) keys Indeed links by `jk=` and LinkedIn links by job id. Any other link keeps its path and its non-tracking query values.
- `save_job` (`storage.py:56`) updates the row when the URL is already there and inserts a new row when it isn't. A new row whose company, title and city match an older one gets `dup_of` set (`dup_key`, `normalize.py:133`).
- JobFinder's own dashboard maps source names to labels in `SOURCE_LABEL` (`dashboard.py:17`). It knows only the three current names.
- City matching (`normalize.py:82`) only accepts Pittsburgh and Philadelphia in PA. Both `nearby` lists in `config.json` are empty, so suburbs and New Jersey are rejected.

**Career Navigator backend**
- `live_adapter.py:291` keeps the source name only for `indeed_email`, `linkedin_email` and `manual`. Anything else is shown as `jobfinder`.
- `app.py:165` uses the same three names when it reports source freshness.
- `urls.py:13` knows two job boards, `indeed.com` and `linkedin.com`. A ZipRecruiter link counts as "other".
- `eligibility.py:53` excludes food roles (restaurant, general manager, shift leader and similar). JobFinder keeps them as the `food` family.

**Dashboard repo**
- `live_gate.py:838` `WS6_FEEDS` lists the allowed source names. The name comes from Phase 3's WS6, not this plan's. A new name fails the gate check at `live_gate.py:883`.
- `mock_api.py` uses only the email source names in its sample jobs and its freshness stamps (`mock_api.py:98`).

**What the connectors returned in test searches (Nov 28)**
- Indeed gives a new `to.indeed.com` short link and a new `JOBSEARCH_…` id on every call. One Bancroft "IT Support Analyst" posting came back with three different links in three searches. An older id still worked in `get_job_details`, which returns the full posting text.
- ZipRecruiter gives a `listing_key` that stays the same across searches. Its `job_redirect_url` carries a `match_token` that changes every time.
- ZipRecruiter returns 5 results per call when no account is linked and 10 once one is. Kyle linked his account on Nov 28. A Philadelphia "IT support" search then returned 10 of 702 matches, and `offset` pages through the rest. Half of those 10 were suburbs, one of them in New Jersey.
- The linked ZipRecruiter account has an apply permission. The pull never uses it.
- Both give structured pay. Many results were suburbs or New Jersey, which JobFinder would reject today.

---

## Workstream 1: JobFinder under git

Every later workstream edits JobFinder. Right now a bad edit can't be undone.

### Scope
1. Make a first commit of JobFinder as it is today, on a `main` branch, with no code changes.
2. Before committing, check that no secret or personal file is staged. `.gitignore` already covers the known ones. Add `.agents/` and `.playwright/` to it, since Career Navigator's baseline (`baseline/BASELINE.md:23`) treats them as private. Look through `docs/reviews/` and the legacy `job_finder/` folder before they go in.
3. Decide with Kyle whether it gets a private GitHub repo or stays local only (Q3).

### Acceptance criteria
- `git log` shows one commit. `git status` is clean.
- The commit has no `config.json`, `jobfinder.db`, log file, `.env` or `manual_jobs.txt`.
- The existing tests pass before and after (`tests/`).

### Dependencies and order
First. Nothing else starts until it is done.

## Workstream 2: Daily connector pull

### Scope
1. A local scheduled Claude task at **8:30 AM ET** every day, made with the scheduled-tasks tool. It runs only while the Claude app is open on the laptop.
2. It runs a fixed list of searches. The default list is each target role family from JobFinder's `config.json` (`families`) for Pittsburgh, PA and Philadelphia, PA, on both connectors. The list lives in `C:\Users\kbaue\Projects\JobFinder\connector_searches.json` so Kyle can change it without editing the task.
3. It writes one file, `C:\Users\kbaue\Projects\JobFinder\connector_jobs.json`, replacing yesterday's. It writes to a temp file first and then renames it, so JobFinder never reads a half-written file. The file is added to `.gitignore`.
4. The file has a fixed shape: `schema_version`, `written_at` (ISO 8601 with the UTC offset), a status for each connector (`ok` or `failed`, with its result count), then one entry per job with `source` (`indeed_connector` or `ziprecruiter_connector`), title, company, location, url, a pay line, posted date and the stable id when there is one (`listing_key` for ZipRecruiter).
5. The task writes only that file. It doesn't apply, save, message or open anything. The linked ZipRecruiter account can apply to jobs, so the task is given search tools only. Posting text is treated as data. If a posting contains instructions, they are ignored.
6. It keeps a short run log next to the file: searches run, jobs written, any connector errors.

### Acceptance criteria
- After one manual run, the file exists, has today's `written_at`, and every entry has the fixed fields.
- A connector that is down gives a log line, a `failed` status and an empty list for that connector, not a missing file. An empty list with an `ok` status means the searches found nothing.
- No other file changes and nothing is sent.

### Dependencies and order
After WS1, and after both connectors' terms are checked (see Risks). Can be built at the same time as WS3, since the file shape is fixed here.

## Workstream 3: JobFinder connector source

### Scope
1. A new `jobfinder/sources/connector_file.py`, modeled on `manual.py`. It gives two sources, `indeed_connector` and `ziprecruiter_connector`. Both read `connector_jobs.json` and return `RawJob` rows for their own connector, so each gets its own health row.
2. The pay line goes into `details` so the existing `parse_pay` and minimum-pay rule work unchanged.
3. Every link goes through `safe_href`. An entry with a bad link, a missing title or a missing company is skipped and counted.
4. **File checks.** If `written_at` is more than 20 hours old, the source reports failed with "connector file is stale" and returns no jobs. A missing file, a file that isn't valid JSON or an unknown `schema_version` is reported the same way. A connector marked `failed` in the file makes that source report failed. All of these show in `source_runs`, so the health row is honest.
5. **Stable identity.** Indeed's link changes every call, so the URL alone would add a new row every morning for the same job. For the two connector sources only, `save_job` first looks for an existing row with the same `source` and the same `dup_key` (company, title and city). If one exists, it updates that row and refreshes the link instead of inserting a new one. No column is added.
6. **Across sources nothing changes.** An email copy and a connector copy of the same job are still two rows, linked by `dup_of` as today.
7. For ZipRecruiter the link is kept whole, `match_token` included. Checked Nov 28: the only link the connector gives is `job-redirect?match_token=...&tsid=...`, and the job is identified only inside `match_token`. The token holds the `listing_key`, an id made fresh for each search and, for jobs on an employer's own site, that site's URL. So a link without it can't point at any job, and the token changes every day like Indeed's links. Rows still match on source plus `dup_key` (step 5), and the newest link wins. **[unchecked: whether a link from an earlier day still opens the posting. ZipRecruiter shows a Cloudflare check to the browser, which Kyle has to pass by hand]**.
8. Add the two sources to `build_sources` so the 9 AM run picks them up. This step waits until WS4 has landed.
9. Add labels for the two names to `SOURCE_LABEL` (`dashboard.py:17`), so JobFinder's own digest and health table don't show raw ids.
10. Regenerate Career Navigator's JobFinder baseline with `baseline/make_manifest.py`, since `connector_file.py` is new and `sources/__init__.py` changes.
11. The same filters, ranking and daily quotas apply. Connector jobs compete with email jobs for the same picks.

### Acceptance criteria
- Tests with a sample `connector_jobs.json`: good entries are stored, bad links are skipped, and a stale, missing or broken file reports failed with no crash.
- Running the same sample twice with changed Indeed links leaves one row per job, with the newest link.
- An email-alert job and a connector job for the same posting end up linked by `dup_of`, as they would today for two email copies.
- `jobs` table columns are unchanged, so Career Navigator still loads.

### Dependencies and order
After WS1. Needs the file shape from WS2.

## Workstream 4: Career Navigator and dashboard accept the new sources

### Scope
1. Add `indeed_connector` and `ziprecruiter_connector` to the source names at `live_adapter.py:291` and `app.py:165`.
2. Add them to `WS6_FEEDS` at `live_gate.py:838`.
3. Add `ziprecruiter.com` to `urls.py:13` providers, keyed by `listing_key` if the link carries it.
4. Add one connector job to `mock_api.py` sample data so the demo shows the new label. Add both names to its freshness stamps at `mock_api.py:98` too.
5. The dashboard shows a plain label for each source (for example "Indeed search" and "ZipRecruiter search") next to the existing "Indeed email" and "LinkedIn email".

### Acceptance criteria
- With connector rows in `jobfinder.db`, the dashboard shows their real source, not `jobfinder`.
- The live gate passes with connector rows present.
- Source freshness shows a stamp for each connector source.
- Existing tests pass, plus a new test for each new name. The tests that list source names today need the new ones too: `test_freshness.py`, `test_live_adapter.py`, `test_live_wiring.py` and `test_provenance.py` in Career Navigator, and `tests_provenance_gate.py` in the dashboard repo.

### Dependencies and order
After WS2, since the source names are fixed there. It must land before WS3 switches on the new sources (WS3 item 8). Its checks use the sample connector rows from WS3's tests, and then the first real rows.

## Workstream 5: Philadelphia suburbs

Kyle's rule (Q4): a Philadelphia suburb in PA counts if it is under 30 minutes by transit to Center City. Towns near the line go to Needs Review. New Jersey towns are left out (Q9). Pittsburgh is unchanged.

### Scope
1. **One town list, built once.** Each town gets a weekday morning transit time from its main station (SEPTA Regional Rail, the Market-Frankford Line, the Norristown High Speed Line or a trolley) to Suburban Station or City Hall, taken from the published schedules. No map service is called at run time.
2. **Three bands.** Under 25 minutes passes. 25 to 35 minutes is the border and goes to Needs Review. Over 35 minutes stays out of area. Kyle confirmed on Nov 28 that a town at 25 to 30 minutes goes to Needs Review.
3. **Kyle signs off on the list** before it goes into any code or config. Every time in it is marked **[unchecked]** until then.
4. **JobFinder keeps the jobs.** Towns in the pass and border bands are added to the Philadelphia `nearby` list in `config.json`, so `match_city` (`normalize.py:82`) keeps them instead of rejecting them. For PA towns this is a config change, not a code change.
5. **New Jersey stays out (Q9).** The list has PA towns only. `match_city` already rejects any state other than PA, so New Jersey jobs stay out of area with no code change.
6. **Career Navigator sorts them.** `_location_reason` (`eligibility.py:320`) passes a pass-band town with the reason "Philadelphia suburb, under 25 minutes by transit". A border town gets `review` with the reason "Philadelphia suburb near the 30 minute line". The two lists live in `policy.py` next to `primary_cities` (`policy.py:33`). Town names match as whole words, so a town like Media doesn't match "social media".
7. JobFinder's own digest shows border towns as normal matches. Only Career Navigator puts them in Needs Review.

### Acceptance criteria
- Tests: a pass-band town passes, a border town lands in Needs Review, a town over 35 minutes is out of area, and a word that only contains a town name doesn't match.
- Tests: a New Jersey town, such as Camden, NJ, is still rejected.
- Pittsburgh results are unchanged.
- Existing tests pass.

### Dependencies and order
After WS1. Can be built alongside WS3 and WS4. The list waits on Kyle's sign-off.

## Workstream 6: Two-week source comparison

### Scope
1. Two weeks after the connector sources switch on, count jobs by where they were found: only by a connector, only by an email alert, or by both (linked by `dup_of`).
2. Also count how many of each source's jobs passed the filters and made the picks.
3. Write the counts up in a short note for Kyle. It answers one question: should the Indeed email alerts stay on?
4. The counts come from read-only queries on `jobfinder.db`. Nothing is changed.

### Acceptance criteria
- The note has the three counts for Indeed, and the connector-only count for ZipRecruiter.
- Kyle decides whether to keep the Indeed alerts. The LinkedIn alerts stay either way, since no LinkedIn connector exists.

### Dependencies and order
Last. Starts two weeks after WS3 item 8.

---

## Out of scope for Phase 4

- LinkedIn. No LinkedIn job search connector exists.
- Upwork. Kyle has a reminder to decide on it (Todoist, Nov 30).
- JobsPipe, unless Kyle chooses it in Q8.
- Any change to the `jobs` table columns, `contracts.py` or `adapters.py`.
- Changing the filter rules, the ranking weights or the eligibility rules, apart from what Kyle decides on cities (Q4) and food roles (Q5).
- Fetching the full posting text for every connector job. `get_job_details` can do it, but it is one call per job and is left for later.
- Applying, saving or messaging from either connector.
- Running the pull in the cloud or on a server.

## Risks

| Risk | Why it matters | Mitigation in the plan |
|---|---|---|
| The Claude app is closed at 8:30 AM | No connector jobs that day | Stale and missing file checks report it in `source_runs`, and the email sources still run (WS3) |
| The pull runs late, after 9 AM | That day's run misses it, and by the next run the file is stale | Kyle can run JobFinder by hand after a late pull |
| Indeed links change every call | The same job piles up as new rows each day | Match on source plus `dup_key` for connector rows (WS3) |
| Two real postings share a company, title and city | They merge into one row | Accepted for connector rows. It is rare, and the newest link wins |
| A posting carries instructions aimed at Claude | The task does something it shouldn't | The task writes one file only, with a fixed shape, and JobFinder checks every field (WS2, WS3) |
| A new source name or column reaches Career Navigator unannounced | The dashboard shows "jobfinder" or fails to load | WS4 lands before WS3 switches on the new sources, and no column changes |
| Connector jobs crowd out email jobs | Kyle's picks shift toward whatever the connectors return | Same quotas for all sources. The two-week counts show it (WS6) |
| Many results are suburbs or New Jersey | Lots of rejected rows and few real gains | Philadelphia suburbs in PA under 30 minutes by transit now count (WS5). New Jersey jobs are still rejected (Q9) |
| A transit time in the town list is wrong | A job is kept or dropped by mistake | Kyle signs off on the list before it goes in, and towns near the line go to Needs Review (WS5) |
| ZipRecruiter limit of 10 results per call | Thin results from ZipRecruiter | Account linked Nov 28 (Q6). The pull pages with `offset` up to a set number of pages per search |
| A connector's terms limit automated use | The pull breaks a site's rules | Check both connectors' terms before WS2 starts **[unchecked]** (Q7) |
| JobFinder has no history today | A bad change can't be rolled back | WS1 first |

## Decisions for Kyle

| # | Question | Default if Kyle agrees | Where it lands |
|---|---|---|---|
| Q1 | On Nov 1 you limited sources to alert emails and pasted links. Is it OK to add the two connectors? | **Decided Nov 28: yes, both connectors, next to the email alerts** | Whole plan |
| Q2 | Phase 3 ruled out changes to JobFinder's code, config or schedule. Phase 4 changes its code and `.gitignore` and adds a second scheduled task. Is that OK? | **Decided Nov 28: yes, limited to WS1, WS2 and WS3** | WS1, WS2, WS3 |
| Q3 | Should JobFinder get a private GitHub repo, or stay local with git only? | **Decided Nov 28: private GitHub repo** | WS1 |
| Q4 | Should suburbs and New Jersey count? Today only Pittsburgh and Philadelphia in PA pass. | **Decided Nov 28: Philadelphia suburbs in PA count if they are under 30 minutes by transit to Center City. Border towns, 25 to 35 minutes, go to Needs Review. Pittsburgh stays as is** | WS5 |
| Q5 | Career Navigator excludes food roles but JobFinder keeps them. Should the pull search food roles too? | **Decided Nov 28: yes.** The JobFinder digest stays the same as today, and Career Navigator still hides them | WS2 |
| Q6 | Should you link your ZipRecruiter account to get more than 5 results per search? | **Decided Nov 28: yes. Linked the same day, and searches now return 10 per call** | WS2 |
| Q7 | Should we follow Indeed's short links to get the real `jk=` job key? This may run against Indeed's terms. | **Decided Nov 28: no. Nothing in Phase 4 goes against Indeed's terms.** Use source plus `dup_key` instead | WS3 |
| Q8 | JobsPipe is also connected and searches 30+ job boards, but each match costs a credit. Add it now? | **Decided Nov 28: no, not now** | Out of scope |
| Q9 | Several New Jersey towns, such as Camden and Collingswood, are close to Center City on PATCO. Do they count under the same 30 minute rule? | **Decided Nov 28: no. New Jersey jobs stay out of area, as today** | WS5 |

### Smaller defaults the build can settle (no decision needed from Kyle)
- Pull time: 8:30 AM ET, 30 minutes before JobFinder's 9 AM run.
- Stale file limit: 20 hours.
- File location: `C:\Users\kbaue\Projects\JobFinder\connector_jobs.json`, left out of git.
- Source names: `indeed_connector` and `ziprecruiter_connector`.

---

*Checked against the code before delivery: every file reference and line number above was looked up in the three folders on Nov 28.*
