# Career Navigator — Synthetic Core Implementation Report (Phase 1)

- **Date:** 2026-11-15 · **Handoffs:** `hof_01M7EGRMF487KPXQQR` (working), `hof_01M7FJF0409QCW448F` (approval, working)
- **Authority:** Kyle's "Approve" + adjustments A1–A3 (CN-001 contract v3 §0); Sol-reviewed combined plan Phase 1.
- **Scope delivered:** isolated deterministic eligibility + explainable job-relevance core, **synthetic tests only**. **Stopped before live JobFinder integration.**

## Slices A/B/C and fix rounds (2026-11-16)

- **Slice A** (`career_navigator/contracts.py`, fixtures, schema, 32 tests) — commit
  `9c434bf`, **accepted** (Qoder G1 PASS / G2 ACCEPT, GO). Five advisory nits
  G2-N1..N5 (validate_result drift, mutable schema cache, status<->priority
  invariant, unknowns duplicates/order, remote_pa normalization) — queued
  as a follow-up, not yet fixed.
- **A09** (remote across the US is eligible) — commit `9fc09b6`, **accepted**
  (Qoder GO). Two advisory grammar edges G2-A09-N1/N2 — queued as a
  follow-up.
- **Slice B** (`career_navigator/ranking.py`, CN-005) — commit `08a50b8`,
  **NO-GO** (G1 PASS conditional, G2 fix round required): 3 Major scoring
  defects + 1 spec gap. Fix round in this commit: B-G2-01 negate-aware
  requirement/perk detection; B-G2-02 years only in a candidate-requirement
  framing; B-G2-03 distinct growth concepts, no requirement-context growth;
  B-G1-01 fit weights Policy-configurable with validation. Also fixed a
  cert-name boundary bug (`network+`/`security+` never matched, `\b` after
  `+` cannot hold). 19 new regression tests; 52 ranking tests total.
- **Slice C** (`career_navigator/app.py`, adapters, CN-004) — commit
  `f728cd9`, **NO-GO** (G1 PASS conditional, G2 fix round required): 2
  Majors + 4 nits. Fix round in this commit: C-G1-01 `requirements.txt`
  with pinned fastapi 0.163.1 / uvicorn 0.54.0 / httpx 0.28.1; C-G2-01 job
  ids from the original URL (two originals sharing one canonical no longer
  collide); C-N1 `on_event` startup migrated to lifespan; C-N2 state swapped
  as one atomic snapshot. C-N4 (dashboard must escape posting text) is for
  the dashboard slice, not the backend. Evidence artifacts regenerated
  (this file + `test-output.txt`).
- **Verification after both fix rounds:** 303/303 tests, demo byte-identical,
  corpus 40/40, controls 7/7, live uvicorn smoke 9 shortlist / 5 review /
  5 excluded / 19 total, 19 unique job ids, no deprecation warnings.
- **Fix Round B2** — Qoder re-review of Fix Round B (`cc068d9`) was NO-GO:
  1 residual Major B2-R1 (growth-phrase anchor-before leak — the
  requirement guard only looked after the term, so "Must have CompTIA
  certification." scored career_growth 0.50 and flipped the class to
  Strong Fit at 79.00; correct is 64.00 Realistic) + 3 nits. Fix round:
  B2-R1 two-sided requirement guard for training/certification
  (`_concept_present`, sentence-bounded lookbehind + lookahead); B2-N1
  perk-negation guard allows a short gap ("isn't flexible", "not very
  flexible", "never hybrid"); B2-N2 contradictory multi-track text is
  sentence-aware — a negation wins within its own sentence, an explicit
  positive requirement in another sentence wins overall (conservative);
  B2-N3 trailing newline in `policy.py`; C-N4 (from the Fix Round C
  re-review, GO for C's scope) endpoints bind `snapshot =
  app.state.snapshot` once per request so a mid-request refresh cannot mix
  generations. 12 new regression tests (64 ranking tests total).
- **Verification after Fix Round B2:** 315/315 tests, demo byte-identical,
  corpus 40/40, controls 7/7, live uvicorn smoke 9 shortlist / 5 review /
  5 excluded / 19 total, 19 unique job ids, no deprecation warnings.
  Antigravity re-reviewed both Fix Round B (`cc068d9`) and Fix Round C
  (`7cefeccb`) as GO for API/dashboard fit (hof_01M7J3XN8N4FVZDSN0); Qoder
  re-reviewed Fix Round C (`7cefeccb`) as GO for its own scope, gated on
  the B fix round (hof_01M7J4EDZBNHP82HGA).
- **Fix Round B3** — Qoder re-review of Fix Round B2 (`6a2f637`) was NO-GO:
  all 5 claimed fixes verified in core cases, 1 residual Major B3-R1
  (30-char before-window leaks on standard cert-list phrasing — "Must
  have CompTIA A+, Network+, or Security+ certification." scored growth
  0.50 and flipped to Strong Fit 79.00; correct is 64.00 Realistic) + 4
  nits. Fix round: B3-R1 before-guard covers the FULL preceding sentence
  fragment (no 30-char cap); B3-N1 "mandatory" added to the requirement
  anchors ("need/needs/necessary/essential" deliberately NOT added — they
  also describe offered growth, e.g. "the training you need"); B3-N2
  after-guard is clause-aware (a "minimum" about pay/hours in a later
  clause no longer suppresses offered training); B3-N3 perk-negation gap
  excludes commas (a negation cannot reach across a clause); B3-N4
  requirement precedence is clause-aware (in comma-joined contradictory
  text the explicit positive requirement wins). 8 new regression tests
  (72 ranking tests total).
- **Verification after Fix Round B3:** 323/323 tests, demo byte-identical,
  corpus 40/40, controls 7/7, live uvicorn smoke 9 shortlist / 5 review /
  5 excluded / 19 total, 19 unique job ids, no deprecation warnings.
  Antigravity re-reviewed Fix Round B2 (`6a2f637`) as GO for API/dashboard
  fit (hof_01M7J59H49W98NRJVH).
- **Fix Round B4** — Qoder re-review of Fix Round B3 (`5e460fb`) was NO-GO:
  all 5 claimed fixes verified, but the commit introduced 2 NEW Major
  regressions vs parent (hof_01M7J75R4KAP1094E8). B4-R1: comma-splitting
  broke requirement detection — the B3-N4 clause split destroyed the
  positive patterns' own comma-spanning windows, so "Bachelor's degree,
  required for all candidates." scored no requirement (fit 70.00 Strong
  Fit; correct 60.00 Realistic) and "CompTIA Security+ certification,
  required within 6 months." scored req 1.00 AND growth 0.50 (fit 85.00;
  correct 64.00 Realistic). B4-R2: the full-sentence before-guard
  suppressed explicitly offered growth — "We require 3 years of
  experience and offer certification." scored growth 0.00 (fit 60.00
  Realistic; correct 75.00 Strong Fit). Fix round: B4-R1 positive
  patterns are matched at SENTENCE level ([.;!?]) so comma-spanning
  windows work; clause analysis is used ONLY for the contradiction case
  (an explicit positive clause beats a negated clause, B3-N4 kept green);
  the growth after-guard splits anchors — predicates (required/must
  have/requires/mandatory/preferred) may reach across commas
  ("Training, required for all new hires, is provided." is not growth)
  while "minimum" only counts within the term's own clause (B3-N2 kept
  green). B4-R2 the before-guard is offer-aware: the search stops after
  an offer/provide/include verb, so an anchor in an earlier clause
  cannot suppress a term a later clause explicitly offers ("give" is
  deliberately NOT an offer verb — it also appears in requirements).
  The anchor set is now `_REQUIREMENT_PREDICATE` + `_REQUIREMENT_MINIMUM`
  (replacing `_REQUIREMENT_ANCHOR`). 7 new regression tests (79 ranking
  tests total): S1–S5 regression matrix plus two lock-ins (offered
  certification counts when the next sentence states a requirement;
  "minimum" about pay in an earlier clause does not suppress offered
  training). The reviewer's shared-helper suggestion was considered and
  not taken: the boundary rules differ per site (sentence-level positive
  matching vs offer-aware before-guard vs predicate/minimum-split
  after-guard), so they stay as focused helpers guarded by the S1–S5
  matrix. Antigravity re-reviewed Fix Round B3 (`5e460fb`) as GO for
  API/dashboard fit (hof_01M7J6YEYQD8MCJ5TB).
- **Verification after Fix Round B4:** 330/330 tests, demo byte-identical,
  corpus 40/40, controls 7/7, live uvicorn smoke 9 shortlist / 5 review /
  5 excluded / 19 total, 19 unique job ids, detail parity 19/19, no
  deprecation warnings.
- **Fix Round B5** — Kyle chose the hardening round first (Nov 16, after
  BOTH B4 re-reviews landed GO: Qoder hof_01M7JBSG22NZBEEA2Q and
  Antigravity hof_01M7JBP04XAA4M6Q2B, merge gate open for the B/C chain).
  Qoder's B4 verdict left 4 advisory nits (B5-N1..N4, none a merge gate).
  B5-N3 (pre-existing class flip): imperative instruction verbs at clause
  start counted as offered growth — "Include certification in your
  application." scored growth 0.50 (fit 85.00; correct 70.00), and
  combined with a degree requirement "Bachelor's degree required. Include
  certification in your application." flipped to 75.00 Strong Fit (correct
  60.00 Realistic). Fix: a sentence starting with an imperative
  offer/include/provide verb (optionally after "please") is an instruction,
  not offered growth — checked BEFORE the offer truncation, with the
  posting title stripped from the fragment first (the text is "title +
  details", and the title is not part of the sentence's grammar).
  B5-N4 (pre-existing record correction): "You must give certification
  details." was NOT guarded at either SHA (growth 0.50, fit 85.00; correct
  70.00) because the predicate set had "must have" but not bare "must".
  Fix: "must" + verb (give/provide/include/hold/submit/present/show/
  complete/possess/maintain/obtain/offer) is a requirement predicate; the
  B4-R2 truncation skips a must-governed offer verb ("must provide" is a
  requirement, not an offer) so the predicate survives, while an offer
  verb in a later clause still truncates ("You must give references and we
  offer training." stays growth 0.50). B5-N2: a participle immediately
  before the term's noun phrase is an adjective, not an offer verb —
  "Must have certification from our offered training program." scored
  growth 0.50 (fit 85.00; correct 70.00). Fix: the truncation uses BASE
  forms only (offer/offers/provide/provides/include/includes); participles
  and gerunds are excluded, so the "must have" still governs. B5-N1:
  "We offer training, required start date is Monday." scored growth 0.00
  (fit 70.00; correct 85.00, no class flip). Fix: a predicate directly
  followed by a scheduling noun (start/date/time/deadline/shift/schedule/
  location/interview/application) modifies THAT noun, not the term — via
  new `_predicate_attaches()`, used by both guards. The set is narrow on
  purpose: a broad noun list would break compound requirements ("Must have
  experience and certification" — "experience" is a real requirement
  object). Known limitations (documented, not fixed): the general
  "required <noun>" case beyond scheduling nouns; passive "must be
  provided"; compound "must complete the form and provide certification";
  participle-led "Provided certification details at interview." 10 new
  regression tests (89 ranking tests total).
- **Verification after Fix Round B5:** 340/340 tests, demo byte-identical,
  corpus 40/40, controls 7/7, live uvicorn smoke 9 shortlist / 5 review /
  5 excluded / 19 total, 19 unique job ids, detail parity 19/19, no
  deprecation warnings.
- **Fix Round B6** — Qoder's B5 re-review verdict was NO-GO at 2811395
  (hof_01M7JEJXNRDZQVVY1Q, Nov 16, 10:11 AM EST; G1 PASS, G2 2 Majors).
  Antigravity said GO (hof_01M7JE5GT65304V0MV, Nov 16, 10:03 AM EST), but
  the NO-GO closes the merge gate — 2811395 does not merge. Kyle chose
  "do fix round B6" (Nov 18). B6-R1 (NEW class-flipping regression): the
  B5-N2 fix dropped progressive -ing offer forms along with the -ed
  participles, so an earlier requirement anchor suppressed genuinely
  offered growth in progressive tense — "We require 3 years of experience
  and are providing training." scored growth 0.00 (fit 60.00 Realistic;
  correct 75.00 Strong Fit), and "We require experience and are offering
  certification." scored 70.00 (correct 85.00). Fix: restore
  providing|offering|including to the `_OFFER_VERB` truncation set while
  keeping -ed participles (offered/provided/included) excluded, so A7/A8
  ("Must have certification from our offered training program.") stay
  growth 0.00. B6-R2 (incomplete fix of B5-N3): the imperative rule
  required bare-verb adjacency, so a determiner/adjective between the verb
  and the term defeated it on standard phrasing — "Include your
  certification in your application." scored growth 0.50 (fit 85.00;
  correct 70.00), and with a degree requirement "Bachelor's degree
  required. Include your certification in your application." flipped to
  75.00 Strong Fit (correct 60.00 Realistic); "Provide relevant
  certification details at interview." scored 85.00 (correct 70.00). Fix:
  an imperative offer/include/provide verb at sentence start (optionally
  after "please") followed by its object — determiner, adjective, or noun
  — is an instruction, not offered growth; a following finite verb form
  (offers/provides/includes) means the verb is a noun subject instead, so
  A9 ("Offer includes training.") stays growth 0.50. B6-N1: the
  must-governed offer-verb skip tolerated only a fixed adverb list
  (also/always/still/really/just), so "You must quickly provide
  certification details." counted as an offer (growth 0.50, fit 85.00;
  correct 70.00). Fix: the must-governed skip and the must+verb predicate
  both tolerate up to two adverbs between "must" and the verb; the gap is
  capped at two words so a distant "must" cannot reach across a clause
  (S5 "Must have 2 years of experience and we offer paid training."
  stays growth 0.50). Known limitation (documented, not fixed — example
  CORRECTED in Fix Round B7, see below): a progressive -ing form inside
  a requirement truncates the anchor and counts as growth. The example
  given here ("Must have experience providing training.") does NOT
  reproduce — it scores growth 0.00, fit 70.00 at both 2811395 and
  5121d2b, because the 2-word must-guard already covers "have
  experience". The sentence that actually reproduces it is "We require
  3 years of experience providing training." (growth 0.50, fit 75.00;
  correct 0.00/60.00). 6 new regression tests (95 ranking tests total):
  X1–X6.
- **Verification after Fix Round B6:** 346/346 tests, demo byte-identical,
  corpus 40/40, controls 7/7, live uvicorn smoke 9 shortlist / 5 review /
  5 excluded / 19 total, 19 unique job ids, detail parity 19/19.
- **Fix Round B7** — Qoder's B6 re-review verdict was NO-GO at 5121d2b
  (hof_01M7S0BD7D9SWS8PXP, Nov 19, 04:17:02Z; G1 PASS, G2 3 NEW Majors).
  Antigravity said GO (hof_01M7RZJM52MVS3MFRJ, Nov 19, 04:03Z; G1 PASS,
  G2 ACCEPT), but the NO-GO closes the merge gate — 5121d2b does not
  merge. All B5 fixes were verified and every B6 claim was reproduced
  independently. Kyle ordered Fix Round B7 (Nov 19). B7-R1 (G2-B6-01):
  "including" as an unconditional offer verb truncated requirement-list
  anchors — "We require 3 years of experience, including certification."
  scored growth 0.50 (fit 75.00 Strong Fit; correct 0.00/60.00
  Realistic), and "Bachelor's degree required. We require experience,
  including certification." likewise flipped. Fix: "including" is a
  list-continuation marker — it takes the governance of the nearest
  preceding anchor in the fragment, so a requirement-governed
  "including" extends the requirement scope to the rest of the sentence
  fragment (including a later "and are providing training" clause);
  an offer-governed "including" still counts ("We offer training,
  including certification." stays growth 1.00). A must-governed offer
  verb ("You must provide training, including certification.") counts as
  a requirement anchor, not a free offer. B7-R2 (G2-B6-02): the
  imperative guard's finite-verb lookahead only checked the immediate
  next word, so an intervening adverb turned a noun-subject offer into
  a false imperative — "Offer now includes training." scored growth 0.00
  (fit 70.00; correct 0.50/85.00), and "We require 3 years of
  experience. Offer now includes training." flipped to 0.00/60.00
  Realistic (correct 0.50/75.00 Strong Fit). Fix: a finite verb form
  (offers/provides/includes, plus past-tense offered/provided/included)
  ANYWHERE in the remainder means the verb is a noun subject instead.
  B7-R3 (G2-B6-03): a requirement-side progressive gerund counted as
  growth when the must->verb gap exceeded the 2-word [a-z]-only window —
  "Must have 3 years of experience providing training." scored growth
  0.50 (fit 85.00; correct 0.00/70.00), and with a degree requirement it
  flipped to 75.00 Strong Fit (correct 0.00/60.00 Realistic). Fix: for
  -ing forms only, "must have" governs across a longer,
  digit-containing requirement tail, but not across a new clause
  ("Must have 2 years of experience and are providing training." stays
  growth 0.50, X4/X5 preserved); base forms keep the short gap so S5
  ("Must have 2 years of experience and we offer paid training.") stays
  growth 0.50. Masking note (Qoder): "We require 3 years of experience,
  including certification. Offer now includes training." nets 0.50/75.00
  at both SHAs — B7-R1 and B7-R2 cancel out — so the masking-case
  regression asserts the growth evidence string ("growth signals:
  training") rather than the net score. Known limitation (documented,
  not fixed): a require/requires-governed gerund still truncates the
  anchor and counts as growth — "We require 3 years of experience
  providing training." scores growth 0.50 (fit 75.00; correct 0.00/
  60.00); B7-R3 covers only the "must have" form. Out of scope
  (Qoder observations, pre-existing at parent, not B7 gates): O1 modal
  periphrasis ("must be able to provide", "must be willing to provide",
  3 adverbs); O2 noun "Requirements:" not a predicate anchor; O3
  reviewer-host environment quirks. 14 new regression tests (109
  ranking tests total): B7-R1 x4, B7-R2 x5, B7-R3 x2, lock-ins x3.
- **Verification after Fix Round B7:** 360/360 tests, demo byte-identical,
  corpus 40/40 (gq 18 / ir 16 / nc 6), controls 7/7, live uvicorn smoke
  9 shortlist / 5 review / 5 excluded / 19 total, 19 unique job ids,
  detail parity 19/19, /api/stats consistent. Verified on Python 3.12.3
  (venv); Python 3.15.2 was not available in the verification VM.
- **Fix Round B8** — Qoder's B7 re-review verdict was NO-GO at 2ed0c02
  (hof_01M7STZ65QXC27P63C, Nov 19, 12:02:13Z; G1 PASS, G2 3 Majors: two
  NEW class-flipping regressions introduced by B7's own fixes, plus the
  disclosed require-gerund residual). Antigravity said GO
  (hof_01M7STZ167GPP5DMAB, Nov 19, 12:02:08Z; G1 PASS, G2 ACCEPT), but
  the NO-GO closes the merge gate — 2ed0c02 does not merge. All 11 B7
  probes and every verification claim were reproduced independently on
  both runtimes. Kyle ordered Fix Round B8 with ALL THREE fixes (Nov 19).
  B8-R1 (G2-B7-01): the requirement-governed "including" scope bled
  across clause breaks and suppressed genuinely offered growth — "We
  require experience, including certification, and are providing
  training." scored growth 0.00 (fit 70.00; correct 0.50/85.00), and
  with a degree requirement it flipped to 0.00/60.00 Realistic (correct
  0.50/75.00 Strong Fit). Fix: the including-governance is now bound to
  the list's own clause — a coordinated VP ("and are providing", "and
  we provide", "and offer") starts a fresh governance domain, so the
  term after it is offered growth. A plain noun continuation is NOT a
  break ("We require experience, including certification and training."
  stays growth 0.00; likewise "... including certification, and
  training."). Per-concept regression tests assert the growth evidence
  string ("growth signals: training") for S1/S19/T4, not just net
  scores. B8-R2 (G2-B7-02): the B7-R2 finite-verb-ANYWHERE exemption
  misread instructions containing participial adjectives or
  relative-clause finite verbs as noun-subject offers — "Include the
  provided certification." went from growth 0.00 (B6) to 0.50/85.00
  (B7, false growth), and with a degree requirement it flipped to
  0.50/75.00 Strong Fit (correct 0.00/60.00 Realistic); "Include your
  certification that includes training." likewise counted training via
  the relative-clause "includes". Fix: the exemption now requires a
  noun-subject reading — "Offer" as a head noun followed (after up to
  two adverbs/auxiliaries) by a finite verb form ("Offer now includes
  training.", "Offer actually provides training.", "Offer provided
  training." all stay growth 0.50; "Offer has provided training." and
  "Offer quickly provides training." likewise). A participle after a
  determiner ("Include the provided certification.", "Please provide
  the included certification.", "Include the offered training.") is
  adjectival, and a finite verb inside a relative clause ("Include
  your certification that includes training.", "Include your training
  that provides certification.") belongs to the clause — neither
  exempts the imperative. Only "offer" can be the head noun:
  "include"/"provide" as sentence subjects are not posting grammar.
  "Include copies of any certification provided." stays growth 0.00
  (the participle sits after the term). B8-R3 (G2-B7-03, the disclosed
  residual): require/requires/required/preferred-governed gerunds still
  truncated the anchor and counted as growth — "We require 3 years of
  experience providing training." scored growth 0.50 (fit 75.00;
  correct 0.00/60.00), and with a degree requirement it flipped to
  0.50/75.00 Strong Fit (correct 0.00/60.00 Realistic); "Requires ..."
  and "Preferred: ..." likewise. Fix: the B7-R3 governance is extended
  from "must have" to require/requires/required/preferred anchors for
  -ing forms, with the SAME clause-break protection — "We require 3
  years of experience providing training and are offering
  certification." now scores growth 0.50 with evidence "growth
  signals: certification" (training suppressed, certification still
  offered); the "Preferred: ..." and semicolon variants likewise.
  Base forms keep the short gap, so S5 and X4/X5 stay offered. One
  B7 test expectation was corrected, not just added: the B7 R1-4 test
  asserted growth 0.00/70.00 for "We require experience, including
  certification, and are providing training." — that value was the
  G2-B7-01 defect itself; it now asserts growth 0.50/85.00. Verdict
  arithmetic note: Qoder's stated correct value for S18 is 0.50/85.00,
  but that is inconsistent with its own B7 value of 1.00/90.00 — the
  growth-weight drop from 1.00 to 0.50 is 15 points (30% x 0.50), so
  the correct fit is 75.00, verified here (skills 30 + growth 15 +
  requirements 10 + commute 10 + comp 10); the per-concept behavior is
  exactly as specified. Known limitations (documented, not fixed):
  a "mandatory"-governed gerund is not covered (B8-R3 anchors are must
  have/require/requires/required/preferred only); "Offer will provide
  training." scores growth 0.00 (base-form "provide" after "will" is
  not a listed finite form — same as B7, pre-existing); the
  noun-subject gap is a closed adverb/auxiliary list, so an adverb
  outside it would fall back to imperative. Out of scope (Qoder
  observations, pre-existing at parent, not B8 gates): O1 modal
  periphrasis; O2 noun "Requirements:" not a predicate anchor; O3
  reviewer-host environment quirks. 25 new regression tests (134
  ranking tests total): B8-R1 x7, B8-R2 x10, B8-R3 x8.
- **Verification after Fix Round B8:** 385/385 tests, demo byte-identical,
  corpus 40/40 (gq 18 / ir 16 / nc 6), controls 7/7, live uvicorn smoke
  9 shortlist / 5 review / 5 excluded / 19 total, 19 unique job ids,
  detail parity 19/19, /api/stats consistent (thresholds 70/45).
  All 37 probe sentences (16 B8 targets + 21 lock-ins) match expected
  values through the real pipeline. contracts.py verified byte-identical
  to remote staging (SHA-256
  ded4f7eb2f8600a49def2a7d0a966e5f5c0aac5fdffbf8b6e570849fae4d26ec);
  policy.py, app.py, eligibility.py, demo.py, __main__.py, pipeline.py
  likewise identical — only ranking.py (+101/-53 vs 2ed0c02),
  tests/test_ranking.py (+206/-3), REPORT.md, and test-output.txt
  changed. Verified on Python 3.12.3 (venv); Python 3.15.2 was not
  available in the verification VM.
- **Fix Round B9** — Qoder's B8 re-review verdict was NO-GO at 81d1a7b
  (hof_01M7SXH1Z0D6KHQ87K, Nov 19, 12:46:56Z; G1 PASS, G2 1 Major:
  G2-B8-01, a closed-adverb-list regression introduced by B8-R2, plus
  two non-gating observations O-B8-1/O-B8-2 recommended for the same
  surgical round). Antigravity said GO
  (hof_01M7SWRV1EJYBV5TRZ, Nov 19, 12:33:42Z; G1 PASS, G2 ACCEPT), but
  the NO-GO closes the merge gate — 81d1a7b does not merge. All 16 B8
  probe values and every verification claim were reproduced
  independently on both runtimes (3.12.10 and 3.15.2). Kyle ordered Fix
  Round B9 with the full scope (Nov 19): the G2-B8-01 adverb fix PLUS
  O-B8-1 and O-B8-2. B9-R1 (G2-B8-01): the B8 noun-subject gap was a
  closed 28-word adverb/auxiliary list, so any adverb outside it fell
  back to imperative and suppressed a genuine noun-subject offer —
  "Offer actively provides training." was growth 0.50 at B7, 0.00 at
  B8, and with a degree requirement it flipped to 0.00/60.00 Realistic
  (correct 0.50/75.00 Strong Fit); "Offer soon provides training."
  likewise (0.00/70.00 at B8, correct 0.50/85.00). Fix: the gap is now
  PRODUCTIVE for -ly adverbs — any -ly word counts as an adverb —
  instead of a closed list; no whack-a-mole appending ("actively" and
  "soon" are covered by the rule, not by list entries). A small
  blocklist of -ly nouns ("family", "supply", "ally", "bully", "folly",
  "jelly", "rally", "belly", "volley", "valley", "gully", "dolly",
  "holly", "lily") keeps those from counting as adverbs: "Offer family
  provides training." stays growth 0.00. The closed list survives only
  for the common non-ly adverbs ("now", "soon" — added with B9-R1 —
  "actually", "also", ...) and the auxiliaries. Per-concept regression
  tests for N4/N4d/N4e plus both imperative lock-ins ("Include the
  provided certification." and "Offer certification that includes
  training." stay growth 0.00). Side effect, now fixed rather than
  merely documented: the B8 limitation "Offer gleefully provides
  training." (growth 0.00) now scores 0.50/85.00, and "Offer only
  provides training." likewise (0.00 at B8, 0.50/85.00 now). B9-R3
  (O-B8-1): "mandatory" was in _REQUIREMENT_PREDICATE but missing from
  the -ing anchor list — the duplicated anchor lists had diverged — so
  "Mandatory: 3 years of experience providing training." counted as
  growth (0.50/85.00 at B8; 0.50/75.00 Strong Fit with a degree,
  correct 0.00/60.00 Realistic). Fix: one token — "mandatory" added to
  _ING_GOVERNING_ANCHOR, with the same clause-break protection, so
  "Mandatory: 3 years of experience providing training and are
  offering certification." scores growth 0.50 with evidence "growth
  signals: certification" (training suppressed, certification still
  offered). Note the fit there is 85.00, not 75.00 as in the "We
  require" S18 twin: the years after "mandatory" are not extracted
  (the years grammar lists requires?/required/minimum/at least/
  preferred, not "mandatory" — pre-existing), so the requirements
  factor scores 1.00; the per-concept behavior is what the test pins.
  B9-R2 (O-B8-2): the B8 noun-subject finite list had no modal+base
  form, so "Offer will provide training." fell back to imperative
  (growth 0.00 at B8, correct 0.50/85.00; 0.00/60.00 Realistic with a
  degree, correct 0.50/75.00 Strong Fit). Fix: the main-verb slot now
  accepts a modal auxiliary (will/would/can/could/shall/should/may/
  might/must) followed by a base offer verb (offer/provide/include),
  with an optional gap token between them ("Offer will quickly provide
  training." scores 0.50/85.00). "Offer can provide training." and
  "Offer should include training." likewise score 0.50/85.00. Known
  behavior, documented not hidden: "Offer must provide training."
  still scores growth 0.00 — the modal slot exempts the imperative,
  but the requirement predicate ("must provide") then attaches to the
  term downstream, so it is suppressed as a requirement (the same
  B5-N4 rule that makes "You must provide certification details." a
  requirement). S18 ruling (from Qoder's B8 verdict): Qoder confirmed
  Muse's 0.50/75.00 for S18 is correct and its own B7 0.50/85.00 was
  its arithmetic error (it used the no-years other-factor sum); the
  REPORT.md arithmetic note was accurate. Known limitations
  (documented, not fixed): the years grammar does not extract years
  after "mandatory" (see B9-R3 note above); a modal+adverb+base form
  with TWO gap tokens between modal and base ("Offer will very quickly
  provide training.") is not covered — the modal slot takes at most
  one gap token; "Offer gleefully provides training." is fixed, but
  any future closed-list-style gap would need the same productive
  treatment. Out of scope (Qoder observations, pre-existing at parent,
  not B9 gates): O1 modal periphrasis ("must be able to provide");
  O2 noun "Requirements:" not a predicate anchor; O3 reviewer-host
  environment quirks. One handoff transcription nit from B8, corrected
  here: the B8 handoff listed R2-3 as 0.50/85.00, but R2-3 ("Offer now
  includes training. Bachelor's degree required.") is 0.50/75.00 at
  both parent and B8 — behavior unchanged and correct; only the
  handoff value was mislabeled. 15 new regression tests (149 ranking
  tests total): B9-R1 x7, B9-R3 x3, B9-R2 x5.
- **Verification after Fix Round B9:** 400/400 tests, demo byte-identical,
  corpus 40/40 (gq 18 / ir 16 / nc 6), controls 7/7, live uvicorn smoke
  9 shortlist / 5 review / 5 excluded / 19 total, 19 unique job ids,
  detail parity 19/19, /api/stats consistent (thresholds 70/45).
  All 24 probe sentences (9 B9 targets + 15 lock-ins/edges) match
  expected values through the real pipeline. contracts.py verified
  byte-identical to remote staging (SHA-256
  ded4f7eb2f8600a49def2a7d0a966e5f5c0aac5fdffbf8b6e570849fae4d26ec);
  policy.py, app.py, eligibility.py, demo.py, __main__.py, pipeline.py,
  demo-output.txt likewise identical — only ranking.py (+60/-25 vs
  81d1a7b), tests/test_ranking.py (+114/-0), REPORT.md, and
  test-output.txt changed. Verified on Python 3.12.3 (venv); Python
  3.15.2 was not available in the verification VM.

## 1. Location and baseline

- Work directory: `C:\Users\kbaue\Documents\Qoder\2026-11-14\2ba455d7\career-navigator\` (new, isolated; session workspace is not a git repo).
- JobFinder (`C:\Users\kbaue\Projects\JobFinder`) **unchanged**: branch `master`, 0 commits, same 11 untracked entries before and after (verified via read-only `git status` both times). No git writes, no reset/clean, no private files opened or copied.
- Verified baseline: `baseline/BASELINE.md` + `baseline/jobfinder-source-manifest.json` (SHA-256 of 35 approved public source files, 149,514 bytes; generated by `baseline/make_manifest.py`).

## 2. Changed/created files (all under career-navigator/)

| File | Purpose |
|---|---|
| `career_navigator/__init__.py` | package marker |
| `career_navigator/models.py` | `UrlIdentity`, `Posting`, `Reason`, `Decision` (pure dataclasses) |
| `career_navigator/urls.py` | A1 own tested URL identity: exact/dot-boundary host checks (JF-15), deterministic vjk>jk precedence (JF-45), provenance retention, untrusted hosts verbatim |
| `career_navigator/dedup.py` | non-lossy `dup_key` (accents preserved, JF-44); exact/uncertain/single groups; uncertain matches stay visible (A2) |
| `career_navigator/pay.py` | A1 own parser: hour/week/month/year (JF-08), bounded/open-ended/missing/unparseable, unrounded floor (JF-40/41), explicit 40h/52w assumptions never silently invented |
| `career_navigator/text.py` | C7/JF-38 script/style suppression, tag stripping, HTML escaping |
| `career_navigator/policy.py` | configurable policy + weights (conservative defaults; open decisions flagged) |
| `career_navigator/eligibility.py` | driving negation/contradiction (JF-25), location re-derivation (JF-39), restaurant-operations exclusion with technical-hybrid precedence, employer exclusions, pay floor; upstream verdict/status are source records only (A2) |
| `career_navigator/relevance.py` | job relevance index: configurable weights (sum=1 validated), factor+evidence, inclusive threshold edge, deterministic tie-break, no priority for review/rejected, no proficiency claims |
| `career_navigator/pipeline.py` | identity→eligibility→relevance→rank; duplicate visibility; upstream records carried |
| `career_navigator/demo.py`, `__main__.py` | manually invoked stdout-only synthetic demo |
| `tests/test_urls.py` (12), `test_pay.py` (20), `test_dedup.py` (8), `test_eligibility.py` (31), `test_relevance.py` (12), `test_pipeline.py` (7), `test_policy_decisions.py` (48) | 138 synthetic unittest tests (90 at Phase 1 + 48 policy regressions; see §6) |
| `baseline/*`, `test-output.txt`, `demo-output.txt`, `REPORT.md` | baseline + evidence artifacts |

Stdlib only. No network, model, browser, background/scheduled work, persistence, or production-data access. TDD red-green per module (each batch watched failing before implementation); two test-side corrections (method-name typo; case-insensitive demo assertion).

## 3. Actual commands and output

```
cd career-navigator
PYTHONDONTWRITEBYTECODE=1 py -m unittest discover -s tests -v
  → Ran 90 tests in 0.004s — OK        (Python 3.15.2; full log: test-output.txt)
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests
  → Ran 90 tests in 0.005s — OK        (Python 3.12.10)
PYTHONDONTWRITEBYTECODE=1 py -m career_navigator
  → 139-line stdout report, exit 0     (demo-output.txt)
py baseline/make_manifest.py
  → files: 35, total bytes: 149514
git -C C:/Users/kbaue/Projects/JobFinder status --porcelain=v1   (before/after identical)
```

## 4. Acceptance coverage (approval handoff)

- Ambiguous/missing driving: negation passes; contradiction & "preferred" → review; missing → unknown (not verified no-driving); script-injected phrases ignored. ✔
- Salary: missing/unparseable/open-ended/week/month/annual; unrounded floor; open-ended low below floor → review (Kyle decision pending); unknown never coerced. ✔
- Location: Pittsburgh primary / Philadelphia optional / remote with geographic restrictions (compatible→pass, incompatible→review) / missing→review / out-of-area→rejected / hybrid. ✔
- Restaurant exclusion vs technical hybrids: GM/KM/cook/shift-leader excluded; POS/IT at restaurant employers allowed; technical precedence on overlapping & hyphenated titles; employer exclusion list. ✔
- URL boundary/identity: lookalike suffix/prefix untrusted & verbatim; dot-boundary subdomains trusted; vjk/jk order-independent; original provenance retained. ✔
- Duplicate uncertainty: same provider ID w/ differing content and same title/company w/ differing location → `uncertain`, both records visible; exact groups visible; nothing silently merged. ✔
- Configurable weights & explainability: custom weights change score; invalid sum/unknown factor rejected; value = 100·Σ(weight·raw) tested; factor evidence is posting-side ("posting mentions…"), wording "not verified candidate fit". ✔
- Review/rejected receive no priority band; deterministic ranking ties. ✔
- Upstream `verdict`/`status` carried as source records; CN decisions independent in both directions. ✔

## 5. Unresolved gates (NOT authorized by this phase)

1. **Live JobFinder integration (plan Phase 2)** — separate approval; verify real schema first; JF-14/21/08/40/25/38/09/39 remain integration preconditions (none applied).
2. **Kyle policy decisions** (combined plan): open-ended low-pay floor treatment (default: review), unknown-pay ranking (default: clarity 0, visible), Philadelphia/remote scope confirmation.
3. **Persistence** (`career_navigator.db`), HTML report, tracker/CSV adapters, daily picks/quotas — deferred, separate scope approval.
4. **Independent review**: per Kyle's standing feedback, no subagent dispatches; this core was built and verified in-session (TDD), not by independent goldfish reviewers. A fresh-context review pass is recommended before integration.
5. No git commit/branch created (workspace is not a repo; integration gated; none requested). *(Superseded: root commit `af99000` on `main`; policy revision on `cn/policy-decisions` — see §6.)*

## 6. Policy revision (2026-11-15) — branch `cn/policy-decisions`, review candidate

- **Authority:** handoff `hof_01M7FPD05XTSBPK4T7` (hermes → qoder, claimed); Kyle's three recorded decisions: `mem_01M7FP498GDPD5D5C2` (salary), `mem_01M7FP76DD2CRMCYN3` (Philadelphia), `mem_01M7FP863YA8TVBCCH` (remote PA).
- **Base:** `af99000131740d9e1ff89560b65e640139f24d73` (clean `main`, verified; no remote, no other branches). Isolated branch `cn/policy-decisions`; **not merged, not pushed**. Independent review by Antigravity precedes any integration.

### Behavior changes (defaults; all still configurable)

| Decision | Implementation |
|---|---|
| Missing salary → Needs Review | `eligibility._pay_reason`: missing → `review` (value stays unknown, never coerced). |
| Open-ended starting pay < $19/hr, no maximum → Needs Review | Default `salary_floor_annual = 19.0 × 40 × 52 = 39520.0`; open-ended low below floor → `review`. Bounded ranges entirely below floor **still excluded** (Kyle's confirmation covered open-ended + missing only). |
| Philadelphia regular default | `primary_cities = ("pittsburgh", "philadelphia")`, `optional_cities = ()`; location factor raw 1.0 (was 0.6 optional). |
| Remote explicitly open to PA → eligible | Restriction/suffix/text evidence naming Pennsylvania (statewide, abbr, residents-only, mixed lists including PA) or nationwide US scope (`US only`, `United States`, `nationwide`, `Remote - US`) → `pass`. |
| Remote explicitly excludes PA → excluded | Other-state restrictions (names/abbrs/lists), explicit PA negation ("not available in Pennsylvania", "Pennsylvania excluded") → `exclude`. |
| Remote unclear/unstated → Needs Review | Bare `Remote`, ambiguous regions ("East Coast"), conflicting PA + other-state evidence → `review`. No geographic restriction invented; pronoun "us" is not nationwide. |

Policy outcomes never override other verified disqualifications (exclude > review > eligible unchanged). Unparseable pay and high-only open-ended ("up to") pay are **unchanged** (`info`, visible) — not covered by Kyle's decisions. Reason/evidence remains posting-side; unknowns stay unknown.

### Files changed

`career_navigator/policy.py` (defaults + docstring), `career_navigator/eligibility.py` (pay review, remote PA engine, state-name/abbr sets; removed superseded `_US_ONLY`/`_RESIDENTS_ONLY`), `career_navigator/demo.py` (default floor; +2 fixtures REM2 bare-remote review, REM3 PA-remote eligible), `tests/test_policy_decisions.py` (new, 48), `tests/test_eligibility.py` (6 expectations revised for the approved policy; helper default pay now `$25 an hour` so factor-isolation tests are not pulled into review by the missing-salary rule), `test-output.txt`, `demo-output.txt`, `REPORT.md`. `relevance.py` needed **no change** (city tiers are data-driven).

### TDD evidence (actual commands)

```
cd career-navigator
PYTHONDONTWRITEBYTECODE=1 py -m unittest discover -s tests        # RED
  → Ran 138 tests — FAILED (failures=30, errors=0 after one test-helper fix)
  → all 30 assertion failures against old policy (e.g. Philadelphia 92.5 != 82.5)
# implementation (policy.py, eligibility.py, demo.py)
PYTHONDONTWRITEBYTECODE=1 py -m unittest discover -s tests -v     # GREEN
  → Ran 138 tests in 0.006s — OK   (Python 3.15.2; full log: test-output.txt)
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests
  → Ran 138 tests in 0.006s — OK   (Python 3.12.10)
PYTHONDONTWRITEBYTECODE=1 py -m career_navigator
  → 11-fixture stdout report, exit 0 (demo-output.txt)
```

One real bug caught by the new tests during green: restriction capture crossed the location/details boundary ("Remote" + "US only." captured "remote us"); fixed by scanning each field separately. Refactor removed the redundant `_RESIDENTS_ONLY` pattern (subsumed by `_ONLY`); suite re-verified green on both Pythons afterwards.

### Still gated (unchanged)

Live JobFinder integration, real-schema verification, migrations, persistence/HTML/tracker adapters, push/merge — all require separate approval. This branch is a review candidate only.

## 7. Fix Round 1 (2026-11-15) — branch `cn/fix-round-1-qoder`, review candidate

- **Authority:** ChatGPT coordinator handoff "Career Navigator — Policy Fix Round 1" (P1), resolving Antigravity G2 defects on `6da5663`; G2-03 approved by Kyle 2026-11-15.
- **Isolation:** sibling worktree `career-navigator-fix-round-1/` off `6da5663`. The main worktree's uncommitted Hermes fix attempt (Sol-blocked) was left untouched. Not merged, not pushed.

### Fixes

| Defect | Implementation |
|---|---|
| G2-01 non-geographic "only" | Open `_ONLY` capture replaced by anchored `_RESIDENTS_ONLY` + `_GEO_ONLY` with vocabulary validation (`_is_geo_only`): every token must be geographic or a candidate-scope connector, and at least one token must be a collision-safe geographic term (IN/OR/ME/ID/OH/OK/HI alone never qualify). "Python only", "Valid ID only", "Bachelors or higher only", "Full time only" invent no region. |
| G2-02 incidental state mentions | Unanchored `other_mentioned` substring scan removed; conflict/exclusion now require an anchored restriction region. `_RESTRICTION` matches in sentences whose subject is customers/clients/company/headquarters/incorporation (and no candidate context) are skipped. Nationwide fallback uses `_NATIONWIDE_TEXT` (pronoun "us only" is not nationwide). Nationwide + CA HQ / DE incorporation → eligible; nationwide + explicit PA exclusion → excluded; unverified remote → review. |
| G2-03 bounded straddle (Kyle-approved) | New `pay.annual_high` (mirrors `annual_low`, unrounded, explicit 40h/52w). Bounded range with annualized low < floor ≤ annualized high → review. Entirely below floor still excluded; at/above floor still eligible; open-ended/missing rules unchanged; configured floor honored. |
| G2-04 | Not implemented (scope rule): "Remote - Pittsburgh/Philadelphia" remains review; documented follow-up. |

### TDD evidence

- RED on pristine `6da5663` (temp `git archive` export + new test file): `Ran 174 tests — FAILED (failures=38, errors=9)`; all 47 from `tests/test_fix_round_1.py` (9 errors = `annual_high` absent on base); original 138 pass on base.
- GREEN: `py -B -m unittest discover -s tests` → 174 OK (Python 3.15.2); `python -B` → 174 OK (Python 3.12.10). Zero existing assertions modified.
- 66-case adversarial matrix (hand-derived expectations): all match. Demo regenerated: only change is REM1 evidence wording ("california residents" → "california"); statuses/ranking identical.

### Files changed

`career_navigator/eligibility.py`, `career_navigator/pay.py` (+`annual_high`), `tests/test_fix_round_1.py` (new, 36 methods), `test-output.txt`, `demo-output.txt` (regenerated), `REPORT.md` (§7).

### Limitations / follow-ups

- Bare state mentions without restriction language ("Remote" + "Texas") now → review (was exclude; safe direction). Unanchored phrasings ("We hire in Texas.") → review.
- `_RESTRICTION` anchors limited to reside in / live in / residents of; incidental-subject guard is a sentence-prefix heuristic.
- G2-04 remote-city scope expansion deferred.
- Independent G1/G2 review of this commit pending; live integration/merge still gated.

## 8. Fix Round 2 (2026-11-16) — branch `cn/fix-round-2-qoder`, review candidate

- **Authority:** GPT Orchestrator handoff "Fix Round 2: PA restrictions and geography conflicts" (Notion `41a2b8e72a0881068823ffa8f4563f6d`, P1, release-blocking; merge decision NO-GO on `4330052`).
- **Isolation:** new sibling worktree `C:\Users\kbaue\Documents\Qoder\2026-11-14\2ba455d7\career-navigator-fix-round-2`, branch `cn/fix-round-2-qoder`, created from exact `4330052dd7570e1594a4908a4c8145abef5dc1dd` (parent `6da566312ef8875028810e28e94b56738425cc5c`, verified). Hermes's dirty `cn/policy-decisions` worktree read-only throughout (status identical before/after); JobFinder untouched (read-only status identical). Not merged, not pushed, no remotes.
- **Ownership note:** Hermes hivemind task `hof_01M7FY0MGMF14BWB19` remains `working` (never independently closed). No shared surface modified: round-2 changes live only in the new worktree; coordinator should formally stand down the overlapping Hermes task.

### Fixes

| Defect | Implementation |
|---|---|
| FR2-01 explicit PA exclusions missed | `_PA_EXCLUDED` extended with applicant-noun-anchored patterns: `cannot/can't/will not/unable to/rejects/refuses/declines/prohibits … PA residents/applicants/candidates`; `PA residents/applicants/candidates … cannot/may not/are not/need not … apply/qualify/considered/eligible/hired/employed`; `cannot/may not/must not … reside/live/be based/be located … PA`. Explicit exclusion still beats nationwide/PA remote text. Applicant-noun anchor prevents false exclusion of e.g. "cannot hire enough engineers in Pennsylvania". |
| FR2-02 conflicting/unconsumed qualifiers | New `_geo_qualifiers`: labeled fields (`Location:/Region:/Geographic region:/Territory:/Area:/Country:/State:/Province:` — values consumed without vocabulary guard) plus soft phrases (`X required/preferred/mandatory`, `within X`, `limited to X`, `work from X`, `X-based`) guarded by a strong geographic token (US states/abbrs minus collisions + common foreign names), so "Python required"/"CDL required" invent nothing. Qualifier conflicting with PA/nationwide/grammar scope → review; alone under bare Remote → review (never hard-reject); compatible qualifiers (PA/US) become scope evidence and never degrade. |
| FR2-03 preservation | No changes to pay, ranking, role/driving grammar, incidental-subject guard, or source-record semantics. All 174 prior tests unmodified and green; demo byte-identical. |

### Corpus recovery and before/after (exact baseline `4330052` → new candidate)

- All 40 Hermes mismatch rows recovered verbatim from his triage raw results `cn_triage_433_312.json` / `cn_triage_433_315.json` (scratch cache; script `cn_triage_433.py` and spillover source still present), originally Sol session `local-0672494ed4d8dd51b48fa5a0910ef358fd96ef30ba95d8ae6464946607c399fa`. Stable fixture: `tests/test_fix_round_2.py` (`HERMES_40`, ids H01–H40, group + allowed outcomes preserved). Reproducibility runner: `run_fix_round_2_corpus.py` (JSON per-case status/priority/location evidence, same shape as Hermes's triage).
- Hermes's untracked experimental tests (`test_geography_blockers.py`, `test_geography_closed_grammar.py`, `test_geography_review_regressions.py`) compared read-only for phrasing ideas only (prohibits/limited-to/work-from/X-based variants); his unreviewed `geography.py` code and differing expectations were NOT transplanted.

| Suite | Before (pristine 4330052) | After |
|---|---|---|
| Hermes 40-case corpus | 12 fixed / 28 failing (gq 0/18, ir 6/16, nc 6/6) | **40/40 fixed** (gq 18/18, ir 16/16, nc 6/6) |
| 7 named controls | 6/7 (C3 `Remote - PA` + `Region: Canada.` eligible/high) | **7/7** |
| FR2-01 quoted (F01–F05, strict rejected) | F01–F03 eligible; F04/F05 rejected | 5/5 rejected |
| FR2-01 variants (V01–V15) | 12 eligible (V03/V09/V15 already rejected) | 15/15 rejected |
| FR2-02 quoted (Q01–Q04, strict review) | Q01–Q03 eligible/high; Q04 review | 4/4 review |
| FR2-02 variants (Q05–Q14) | 9 eligible, Q13 review | 10/10 review |
| Compatible qualifiers (K01–K06) | K03 review, rest eligible | 6/6 eligible |
| Antigravity 22 probes | 21/22 as documented; **A09 discrepancy** (see below) | 21/22 preserved; A09 locked at observed baseline |
| Existing unit tests | 174/174 | 174/174 (unmodified) |
| Full suite | 189 tests, FAILED (failures=58, errors=0) | **189 tests OK** |

- Failing-before corpus ids: H01–H18, H20–H24, H28–H32 (exactly Hermes's 28). All review/rejected rows carry no priority; eligible corpus rows high. Outputs identical on Python 3.12.10 and 3.15.2.
- **A09 discrepancy (pre-existing, not introduced here):** Antigravity's review table claims `Remote` + "Our company was founded in California. Remote across the US." → eligible, but pristine `4330052` returns review ("across the US" is not consumed by the nationwide grammar). Locked at observed baseline behavior in `test_a09_discrepancy_locked_at_observed_baseline`; flagged for coordinator decision, not silently scope-expanded.
- The full 1,043-case matrix was NOT rerun (not required; claimed nowhere).

### TDD evidence (actual commands, from worktree root)

```
PYTHONDONTWRITEBYTECODE=1 py -3.12 -m unittest discover -s tests        # RED on pristine 4330052
  → Ran 189 tests — FAILED (failures=58, errors=0); all 58 in test_fix_round_2.py
PYTHONDONTWRITEBYTECODE=1 py -3.15-arm64 -m unittest discover -s tests  # RED, identical
PYTHONDONTWRITEBYTECODE=1 py -3.12 run_fix_round_2_corpus.py            # before: 12 fixed / 28 failing
# implementation: career_navigator/eligibility.py only
PYTHONDONTWRITEBYTECODE=1 py -3.12 -m unittest discover -s tests        # GREEN → 189 OK
PYTHONDONTWRITEBYTECODE=1 py -3.15-arm64 -m unittest discover -s tests  # GREEN → 189 OK (log: test-output.txt)
PYTHONDONTWRITEBYTECODE=1 py -3.12 run_fix_round_2_corpus.py            # after: 40/40, controls 7/7
PYTHONDONTWRITEBYTECODE=1 py -3.15-arm64 -m career_navigator            # exit 0; byte-identical to demo-output.txt
git diff --check                                                        # clean
```

- Independent-style RED-on-base re-verification: `git archive 4330052` exported to a temp sandbox + new test file copied in → `Ran 189 tests — FAILED (failures=58)` (mirrors Antigravity G1 methodology).
- Safety: stdlib-only imports (`re`, `dataclasses`, `html`, `urllib.parse`); no network, file writes, persistence, subprocess, or live-source access added; synthetic postings only; bounded regex scans (adversarial-whitespace test included).

### Files changed

`career_navigator/eligibility.py` (PA exclusion grammar + qualifier consumption), `tests/test_fix_round_2.py` (new, 15 methods / 123 attributed synthetic cases in 10 tables), `run_fix_round_2_corpus.py` (new reproducibility runner), `test-output.txt` (regenerated), `REPORT.md` (§8). `demo-output.txt` unchanged (byte-identical demo).

### Limitations / follow-ups

- Conditional scope ("Open to candidates in PA; if approved.", "Nationwide US if approved.") and negated-scope phrases ("Not open to candidates in US.") remain unconsumed → eligible on nationwide suffix; Hermes's experimental closed-grammar treated these as review. Deferred: grammar-scope decision for coordinator.
- "Remote across the US" / bare "Location: US." style scope wording: A09 stays review (pre-existing); compatible labeled qualifiers now pass (K03).
- Soft-phrase guard requires a strong geographic token; unknown foreign names in soft phrases (not labeled fields) are not consumed. Labeled-field values are always consumed; unknown values → review (conservative).
- `_RESTRICTION` anchors and incidental-subject heuristic unchanged from Round 1; G2-04 remote-city expansion still deferred.
- Release gate: G1 then distinct independent G2 (must include the 40-case corpus) required; Qoder cannot self-approve. No merge/push/deploy/UI integration until coordinator AND Kyle explicitly approve.