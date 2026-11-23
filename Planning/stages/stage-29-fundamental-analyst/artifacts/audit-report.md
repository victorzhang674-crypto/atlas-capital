# Stage 29 — Fundamental analyst agent · Audit report (2026-11-18)

Self-audit made in the stage's own chat (the owner asked to "plan, execute and audit stage 29";
no reviewer subagent, as the owner's standing preference). Scope: the brief's In scope list and
acceptance criterion, the hard rules of `CLAUDE.md`, the code and tests of this stage, and the
recorded fixture.

## Verdict

**Complete.** Every In scope item is built and tested; the acceptance criterion is shown by the
offline replay of a real recorded run. Three defects or gaps were found and fixed test-first
(A1–A3); the rest are limits recorded in the Backlog.

## Brief, item by item

| Brief item | Where | Evidence |
|---|---|---|
| `get_fundamentals_summary`: last 8 quarters of key lines, TTM, margins, growth | `fundamental_tools.fundamentals_summary` | `test_summary_shows_the_last_eight_quarters_and_the_ttm`, `test_summary_margins_and_growth_match_hand_computation`, `test_summary_items_and_ids` |
| `get_factor_ranks`: value / quality / growth percentiles vs sector | `factor_ranks`, `sector_panel`, `percentile` | `test_ranks_are_mid_rank_percentiles_within_the_sector` (checked against `scipy.stats.percentileofscore`), `test_too_few_peers_are_not_ranked` |
| `get_valuation`: multiples vs own 5y history and sector median | `valuation`, `multiples` | `test_own_history_pe_uses_raw_prices_and_counts_so_a_split_cancels`, `test_current_multiples_and_sector_medians`, `test_thin_history_and_few_peers_are_said_so` |
| `get_red_flags`: accruals spike, receivables >> revenue, negative FCF with rising debt, dilution, going concern if MD&A is available | the five rules + `red_flags` | 42 rule cases in `test_fundamental_red_flags.py`; `test_the_flagged_filer_trips_four_checks`, `test_a_healthy_filer_is_clear`, `test_red_flag_numbers_match_hand_computation`, `test_stale_filings_are_not_evaluated_and_named_stale` |
| Prompt `fundamental_analyst.j2` v1: six sections, every number cited | template + `check_output` | golden prompt file; `test_a_report_problem_gets_a_repair_turn` (7 cases); `test_uncited_numbers`; `test_sections_must_start_lines_and_keep_their_order` |
| `AnalystReport` with signals quality, valuation, growth, balance_sheet and confidence | `FundamentalAnalyst` | `test_a_cited_report_validates_and_returns_its_evidence`; replay test |
| Golden tests with FakeLLM | `test_fundamental.py` | 27 FakeLLM tests incl. `test_the_prompt_matches_the_golden_file` |
| Recorded live run, AAPL 2024-05-01, fixture under `tests/fixtures/recorded/`: exported cache + lake slice; `atlas data slice`; replay needs no lake and no key | fixture folder, `atlas.data.slice`, `atlas data slice` | `test_the_recorded_aapl_run_replays_offline`; `test_data_slice.py` (11), `test_cli_data_slice.py` (8); `fixture-compare.txt`, `live-run.txt`, `live-test.txt` |
| Acceptance: validates, cites evidence for every quantitative claim, runs offline from the cache | — | the replay test asserts the recorded report, `llm_calls == cache_hits`, cost 0, status `ok`, citations ok, `uncited_numbers` empty for the summary and every risk |

## Findings

- **A1 — defect, fixed.** `slice_lake` trimmed `index_membership` with `--since` on `start_date`
  (the first date column of its key), dropping memberships that began before `since` and still
  held: on the real lake 26 rows for 65 names, so the slice's universe (and the analyst's sector
  peers) would have shrunk. Found while slicing the main lake. Test first
  (`test_since_keeps_memberships_that_started_earlier`, a `date_column` case: red), then static
  tables are never trimmed (green).
- **A2 — silent skip, fixed.** A universe member with no instrument row was skipped from the
  sector peers without a trace. Test first (`test_a_member_without_an_instrument_row_is_skipped_and_logged`:
  red), now a `fundamental.peer_skipped` warning names it and the reason (as `panel_sectors` does
  in Stage 16).
- **A3 — untested paths, closed.** Coverage showed no test for stale filings, too little own
  history, too few peers, debt from none and slicing a table keyed by a `symbols` list (news,
  with a timestamp `since`). Tests added (`test_fundamental_audit.py`, one slice test); they pass
  on the code as written. Coverage after: see `coverage.txt`.
- **A4 — recording step, fixed.** The cache export run from inside the live-run process failed
  on DuckDB's file lock (the recording process held the run lake open). The export is a separate
  step (`record_fixture.py export`). Not a product defect: one writer process per lake is the
  documented rule (Stage 06).
- **A5 — limit, closed in the follow-up.** The main lake had no receivables rows: only mapped
  tags are stored, and it was ingested before the concept existed. On the main lake the
  receivables check read NOT EVALUATED (`fixture-compare.txt`). Follow-up: the main lake's facts
  were re-parsed offline, the factor and risk stores rebuilt (values equal) and the result swapped
  in with a backup. AAPL's tools now give 29 of 29 items equal on the main lake and the fixture
  (see *Follow-up* below).
- **A6 — limit, closed in follow-up 2.** No MD&A text was ingested, so the going-concern check
  was always NOT EVALUATED. The owner approved the spec change ("Approve the spec change"):
  10-K MD&A text now comes from EDGAR (`filing_text`, the `filings` dataset, `DataView.mda`)
  and the check reads it (see *Follow-up 2*).
- **A7 — note, closed without change.** The fixture lake is 5.96 MB. About two thirds are peers'
  bars before 2022 that no tool reads. The blobs are already in the pushed history, so a smaller
  re-recording would not shrink the repository. Later fixtures can use per-symbol windows.
- **A8 — budget measured on a real prompt** (Stage 24 backlog item). One AAPL run: 6,433 input +
  1,730 output tokens, USD 0.0020, one call. `per_agent_call_max_tokens` (8,000, reasoning
  included) leaves 4.6x headroom over the output; a repair turn would roughly double input. Keep
  both budgets; revisit at the first LLM backtest (Stage 56), as the item already says.
- **A9 — mutation testing, done in the follow-up.** At first not run (estimated two hours);
  coverage of 95–99% was used instead. Follow-up: mutmut over the three modules, 2,396 mutants.
  Kill rate 85.7% before, **97.5% after** the follow-up tests; the 61 survivors are all classified
  ([mutation/mutation-summary.md](mutation/mutation-summary.md)). No production defect was found.
- **A10 — out of scope, noted.** Wiring the analyst into the research graph (a `ResearchTeam`
  analyst callable building `AgentContext(DataView(lake, task.as_of), inputs={"symbol": ...})`)
  is not in this brief; the handoff note says how.

## Hard rules

- No `datetime.now()` / `date.today()` in `src` (grep: none in the new modules; the recording
  script's `date.today()` is a stage artifact, not product code).
- Agent data only through `DataView`: every tool reads `ctx.view` (fundamentals history and
  panel, prices, split factors, universe, instruments, and the Stage 16 `factor_panel(view)`).
  `slice_lake` reads a `Lake` directly: it is a data tool, not agent code.
- Point in time: the dirty lake (a later quarter, later bars, a late split) gives exactly the
  clean lake's evidence at the as-of for six names, and every item is dated before the cutoff
  (`test_fundamental_tools_pit.py`); the registry's `guard_evidence` and `BaseAgent.gather`
  check it again on every run.
- No network or LLM in unit tests: FakeLLM and the offline replay; the live test is marked `llm`.
- Thresholds and windows in `config/fund.yaml` (`agents.fundamental`), read through `AtlasConfig`.
- No secrets: no `.env` value occurs in the fixture or the artifacts (scan in the work log); the
  slice manifest records no source path; the local user name was redacted from one artifact.

## Follow-up (2026-11-18)

Owner: "please help me to fix the problems and complete what you have not done. after the fix,
please have a final audit and report your progress."

### Done

- **A5, main-lake receivables** ([worklog](../worklog.md), follow-up entries):
  - Re-parse on a scratch copy, offline: 742 symbols, 0 failed; 68,846 receivables rows added;
    all 2,574,827 old rows unchanged ([main-lake-reparse.txt](main-lake-reparse.txt)).
  - `atlas factors build` and `atlas risk-model build --cached-factors` rebuilt. The stored
    exposures, factor returns and risk-model snapshots are equal to their digests from before
    the rebuild ([main-lake-rebuild.txt](main-lake-rebuild.txt),
    [main-lake-stores-compare.txt](main-lake-stores-compare.txt)).
  - Swapped into the main lake from the owner's Terminal panel, with a checked backup
    ([main-lake-swap.txt](main-lake-swap.txt)). The first attempt failed safely, on a wrong temp
    path, before any table was replaced.
  - Read-back: factor cache 142 of 142 dates fresh; AAPL's prefetch has 29 of 29 items equal to
    the fixture's ([main-lake-readback.txt](main-lake-readback.txt)).
- **A9, mutation testing:** see above and [mutation/mutation-summary.md](mutation/mutation-summary.md).
  New and changed tests:
  - `tests/unit/agents/research/test_fundamental_contracts.py` (new, 75 tests);
  - `test_fundamental.py` (the five-sentence repair quote);
  - `test_fundamental_tools.py` (gap source);
  - `tests/unit/data/test_data_slice.py` (3 tests: custom registry, `symbols` list, manifest
    text and log, exact errors).
- **A7:** closed without change (above).

### Not done, with reasons (as of the first follow-up; see *Follow-up 2* for what changed)

- MD&A text (A6): no filing-text source was ingested; a data-pipeline addition outside this
  brief. **Done in follow-up 2** on the owner's approval.
- Graph wiring (A10): Stage 35 / 36 / 55 (Rule 2.4 and Rule 4). **Kept with Stage 35** by the
  owner in follow-up 2.
- The 16 untested-edge mutants: recorded in the Backlog. **12 killed in follow-up 2.**

### Final audit

The brief's items and the acceptance criterion are unchanged, and still evidenced as in the
table above. The follow-up changed tests, stage records and the main lake's data. It changed no
code under `src/`, so the recorded fixture, the prompt and the replay are unchanged. Hard rules
were re-checked for the new files:
- the new tests use FakeLLM and synthetic lakes only (no network);
- no `datetime.now()`;
- no secrets: the scan is in the worklog;
- the local user name was redacted from `classify.sh` and `main-lake-swap.txt`.

Final verification (full suite, `make check`, integration): see [../verification.md](../verification.md),
section *Follow-up*.

## Follow-up 2 (2026-11-18)

Owner: "please complete the ones that are not done and have a final audit on your own work."
Two of the open items needed the owner's decision, which was asked and answered in chat:
- Graph wiring is Stage 35's task 4, and Rule 4 forbids starting it early: **"Leave it to Stage 35"**.
- MD&A text needs a new data source, a design-spec change (Rule 3): **"Approve the spec change"**.

### Done

- **MD&A text (A6).** Plan addendum decisions E1–E7 ([../plan.md](../plan.md)). Spec §11 has a new "Filing text" row.
  - `atlas.data.filings`:
    - `mda.py`: HTML to text; Item 7 extraction, or Exhibit 13; a fallback to the MD&A heading.
    - `text.py`: the `filing_text` table, the filing list, `ingest_filing_text`, `MdaText`.
  - `EdgarClient.filing_documents` and `filing_document`.
  - The `filings` sync dataset (step `filing_text`).
  - `DataView.mda`, point in time and blocked-aware, in every PIT suite: leak, guard, block.
  - The going-concern check reads the latest 10-K MD&A. It FLAGs a sentence that holds all of the terms and none of `going_concern_exclusions`, and quotes it.
  - It is NOT EVALUATED, with the reason named, when there is no MD&A, the 10-K is older than 460 days, or no MD&A was found in the filing.
  - The recorded fixture has no `filing_text`, so its replay is byte-identical.
- **Main lake.**
  - Backfilled in a scratch lake through `atlas data sync --datasets filings`: 748 equities, 0 failed, 7,643 10-Ks, 52 minutes ([filing-text-backfill.txt](filing-text-backfill.txt)).
  - 428 filings without an MD&A were fetched again after E7 ([filing-text-refetch.txt](filing-text-refetch.txt)).
  - Swapped in from the owner's Terminal panel with a checked backup; only the new table was added ([main-lake-filing-text-swap.txt](main-lake-filing-text-swap.txt)).
  - Read back, with point in time checked ([main-lake-filing-text-readback.txt](main-lake-filing-text-readback.txt)).
  - Coverage: 7,558 of 7,643 10-Ks have an MD&A (98.9%).
- **The rule on real data** ([filing-text-review.txt](filing-text-review.txt)).
  - Every MD&A sentence holding both terms was reviewed: 71 MD&As.
  - The first rule (any term anywhere) would have flagged mostly descriptions of ASU 2014-15.
  - The final rule flags 16 of 7,558 MD&As. Each was reviewed:
    - 12 about the company itself;
    - 3 about a material subsidiary that went bankrupt (FES, GenOn);
    - 1 about another company (Toshiba, in Western Digital's).
- **Untested-edge mutants.** 12 of 16 killed by new tests; 4 are recorded with reasons.
  - Stage 29's run is now 2,335 of 2,396 killed (97.5%).
- **Mutation testing of the new code.** 778 of 811 killed (95.9%); the 33 survivors are all equivalent ([mutation/mutation-summary.md](mutation/mutation-summary.md)).

### Self-audit of my own follow-up work

I re-read what I built and recorded, and checked it against real data and the rules. Found and fixed:
1. **Extraction cut a real MD&A short.** Apple's FY2015 10-K breaks lines mid-sentence, so a cross-reference ("Item 8 of this Form 10-K …") started a line and ended the section after 6,128 characters.
   - Headings must now be followed by their item's title.
   - Test first: `test_cross_references_at_a_line_start_are_not_headings`. The section is now 58,354 characters.
2. **Text decoding.**
   - Documents without a declared charset were read as Latin-1, so curly apostrophes broke the heading match.
   - Bytes are now decoded as UTF-8, else Windows-1252. Test first.
3. **30 companies had no MD&A at all,** JPMorgan, Bank of America, GE and Chevron among them: annual-report layouts with no "Item 7" heading. E7 added a fallback; coverage went from 94.4% to 98.9%.
4. **The keyword rule was noisy:** about 2% of MD&As named a term, almost all as ASU 2014-15 wording. It was replaced by the sentence rule with exclusions; three rounds of review on the whole lake.
5. **My own record errors:**
   - a Decisions-log row I first wrote over several lines (a broken table row), rejoined;
   - "82 new tests" corrected to 80 in the earlier follow-up;
   - one of my own test assertions kept the old CLEAR wording.
6. **Dead code removed:** a namespace split that lxml.html never needs, found by mutation testing.

Checked and found right:
- Hard rules: no `datetime.now()` in the new code; the agent tool reads only `DataView`; no network in unit tests (respx); no `.env` value in any changed or new file; the username appears only in a pre-existing PROGRESS line from the original planning commit.
- Config: every new setting is in `config/fund.yaml` and `AtlasConfig`, and the yaml-equals-defaults tests cover it.
- The full suite, `make check` and the integration tests (below).

Still open, with reasons:
- **10-Q MD&As are not read.** This is the limit you approved; documented. A doubt first disclosed in a 10-Q (Chesapeake 2020) is seen only at the next 10-K.
- **Seven companies have no MD&A,** because they use other headings (e.g. Wells Fargo's "Financial Review").
- **Graph wiring stays with Stage 35.**
- **Four mutants remain untested edges,** all needing a split dated on a boundary day, or a non-default calendar.

## Follow-up 3 (2026-11-22)

Owner: "can you help me to complete the tasks that are still being opened?" The open items were 10-Q MD&As, the seven companies without an MD&A, the four untested-edge mutants and the uncommitted CI record. Graph wiring stays with Stage 35, as decided in follow-up 2. Widening the approved spec row to 10-Qs is recorded in the Decisions log, with the request as the approval.

### Done

- **10-Q MD&As (H1).** Part I Item 2 is read; `DataView.mda` returns the latest filing whose MD&A was found. A doubt first disclosed in a 10-Q is now seen at once: Chesapeake 2019-11-05, Bed Bath & Beyond 2023-01-26.
- **The seven companies (H2).** AEP, CLX, MOS, TWC, TWX, WFC and WIN all have their MD&A now, from EX-13/EX-99 exhibits, "Financial Review" headings or the MD&A heading later in the 10-K.
- **The four edge mutants (H3).** All killed. Stage 29 is 2,339 of 2,396 (97.6%), with no untested edge left.
- **Main lake.** 30,597 filings (7,646 10-Ks, 22,951 10-Qs) of 742 symbols; 30,506 with an MD&A (99.7%).
  - Swapped in with checked backups ([main-lake-filing-text-replace-2.txt](main-lake-filing-text-replace-2.txt)).
  - Read back with 21 `DataView` cases ([main-lake-filing-text-readback-followup3.txt](main-lake-filing-text-readback-followup3.txt)).
- **Going concern on real data.** 75 FLAGs of 30,506 MD&As, every one read: 59 own, 8 subsidiary, 8 another company's ([filing-text-review-followup3.txt](filing-text-review-followup3.txt)).
- **Mutation testing of the changed code.** H1–H2: 853 of 889 (96.0%). H6–H7: 237 of 240 (98.75%). All survivors are equivalent ([mutation/mutation-summary.md](mutation/mutation-summary.md)).
- **The CI record** `ci-runs-1e32337.json` is committed with this follow-up.

### Self-audit of my own follow-up work

I checked my code against the whole real data set twice: the review of every both-term sentence, and a row-by-row comparison of the rebuilt table with round 1. Each check found defects in my own work:
1. **IBM regression during H2.** The new heading rules first shortened three IBM 10-K MD&As by about 6,000 characters: one of IBM's headings was rejected. A regression check of 142 earlier MD&As caught it, and it was fixed to 142 of 142 identical before the backfill.
2. **An inline bullet ended an MD&A** ("• Contingencies" before an end heading). Mutation testing found it; a new test covers it.
3. **The round-1 table I swapped into the main lake was wrong for 5,732 10-Qs.** A bare "Item 2." also heads Part II's "Unregistered Sales …", whose contents entry then ran through the financial statements. The synthetic tests had no Part II contents entry, so they passed. The review exposed it: HST and MNK were "flagged" by sentences in the notes to the statements, not in the MD&A. Fixed test-first (H7), and the affected rows were fetched again.
4. **Pointer pages were stored as the MD&A.** Southern Company's 10-Q Item 2 is a page of pointers to its combined MD&A; Entergy's and Sempra's likewise. Fixed by `short_mda_chars` and the later-section or exhibit rule (H7).
5. **The going-concern rule had 8 false FLAGs and 3 truncated quotes** (H6): a negation, ASU wording without the standard's name, a borrower definition, and sentences cut by line breaks or "Inc.". Fixed test-first.
6. **My own fix caused a regression (H9).** Requiring "Management" after "Item 2." lost Roper's 10-Q, which misspells it "MANAGMENT'S". The row-by-row comparison found it.
   - Fixed test-first.
   - I then proved the fix touches nothing else: every not-found row, every row with the misspelling and 300 fresh random rows were extracted again from freshly fetched documents. Only Roper's row differed.
7. **Record errors I made and corrected:** the Roper filing date (written as 2017-04; it is 2017-11-06), and two read-back cases that pointed at the wrong filing (fixed before saving the output).

Checked and found right:
- **Re-ingest scope.** 200 random untouched rows came out identical from freshly fetched documents.
- **Main-lake swaps.** Both swaps kept a hash-checked backup. Only `filing_text` and its sync step changed; `_meta.json` is equal.
- **Hard rules.**
  - No `datetime.now()` in changed code.
  - Agents read only `DataView`.
  - No network in unit tests.
  - No `.env` value in any changed or new file. I redacted the home path and the sandbox temp path from new artifacts.
- **Config.** The new `short_mda_chars` is in `config/fund.yaml` and `FilingTextConfig`, covered by the yaml-equals-defaults test. It is a tuning key, not a spec change.

Still open, with reasons (Backlog):
- Bank of New York Mellon and Northern Trust 10-Q MD&As are under no heading read here (70 filings).
- GE's 10-Q MD&A has no heading either, so for three 10-Qs the contents entry is stored.
- ExxonMobil is read under its 2026 CIK only. That is the Stage 09 `CikMap` (→ 09).
- A whole-table `Lake.read_table("filing_text")` overflows Arrow's string offsets. No code path does that (→ 06).
- Windstream's 2019 quote starts at a page header inside the sentence.
- Graph wiring stays with Stage 35.

## Follow-up 4 (2026-11-23)

Owner: "can you complete the still open ones and complete them?" The open items were the six above (graph wiring stays with Stage 35) and the uncommitted CI record `ci-runs-c579104.json`. Investigating them showed more than the Backlog said, so the follow-up covers what was found. Plan J1–J6 in [plan.md](../plan.md); the Decisions log records that code of Stages 06, 09 and 12 changed on this request.

### Done

- **Whole-table read (J1, Stage 06).** `Lake.read_table` sorts by the key columns alone and takes each column in its 64-bit-offset form. The main lake's whole `filing_text` (2.14 GiB of text) now reads in 29 s, sorted by key ([main-lake-whole-read-followup4.txt](main-lake-whole-read-followup4.txt)).
- **MD&A headings (J2).** Every main-lake filing without an MD&A, or with one under 15,000 characters (156), was fetched and read. The rules found from them are listed in `docs/data-limitations.md` item 9, each with a test.
  - Bank of New York Mellon and Northern Trust ("Items 2. and 3."), GE (a run-in heading), U.S. Bancorp and Total System Services (more wordings of "incorporated by reference", so their EX-13 is read), JPMorgan's 2015-16 10-Qs, A. O. Smith, Gardner Denver ("MANGEMENT'S"), Synchrony, Loews, PNC, Noble Energy and Avery Dennison now all have their MD&A.
  - Before: 91 filings had no MD&A. Now every one of the 30,927 filings has one.
- **Windstream (J3).** The going-concern quote now reads the whole sentence: a page number or header inside it is left out.
- **Former CIKs (J4, Stage 09 and 12).** Comparing each member's first 10-K/10-Q with the start of its S&P 500 window found **16 issuers**, not just ExxonMobil, whose history sat under a former CIK:
  - ExxonMobil, BlackRock, Bunge, APA, Viatris/Mylan, Disney, Cigna, Broadcom, Linde/Praxair, DuPont, L3, TechnipFMC, Alphabet (GOOG, GOOGL), Paramount (SKYD);
  - SanDisk's reused ticker.

  Twenty-First Century Fox (TFCF, TFCFA) had no CIK at all. Each pair was checked on EDGAR ([former-ciks-followup4.txt](former-ciks-followup4.txt)). `CikOverride.former` / `since` and `CikMap.sources` feed the fundamentals, filing-text and earnings ingests.
- **Main lake (J5).** On a scratch copy ([main-lake-followup4.txt](main-lake-followup4.txt)):
  - **Facts:** +45,765 rows (ExxonMobil 55 → 3,330); 214 removed, all merger shells' or Upjohn's filings that are not the member's.
  - **SEC events:** +362, 11 removed. **Reactions:** +321, 11 removed.
  - **MD&As:** 840 rows changed, 340 added, 10 removed; every change elsewhere is one the recheck found ([filing-text-compare-followup4.txt](filing-text-compare-followup4.txt)).
  - **Other symbols:** not one other symbol's row changed in facts, events or reactions.
- **Going concern on real data.** 76 FLAGs of 30,927 MD&As: the 75 reviewed in follow-up 3, plus Enphase's 10-Q of 2017-05-10 (its own doubt, correct) ([filing-text-review-followup4.txt](filing-text-review-followup4.txt)).
- **Mutation testing (J6).** 768 of 883 killed. The 32 survivors on new lines are equivalent; the 83 others are on old, never-tested lines of the Stage 06/09/12 functions ([mutation/mutation-summary.md](mutation/mutation-summary.md)).

### Self-audit of my own follow-up work

Each check below found defects in my own new code before the swap. Each was fixed test-first.
1. **The run-in heading rule was too loose (twice).**
   - Texas Instruments: the MD&A's own opening sentence, inside Item 2, replaced the MD&A with a section that ran to the signatures. Fixed: a later heading must follow the short MD&A.
   - Weyerhaeuser: a sentence in Item 1 ("In our Management's Discussion … (MD&A) section you will find …") started a 180k-character "MD&A". Fixed: a run-in heading must open with "Management…".
2. **The new "Item 1" end cut five MD&As** at a quoted cross-reference (Blackstone, Devon) or at "Item 1. Business" (Everest). Fixed: a line holding a closing quote is no end, and Item 1 needs its statements' title.
3. **The auditor's-report end cut PepsiCo's 10-Ks to 1,700 characters.** Its MD&A opens with its own contents, which list that report. Fixed: an entry followed by a page number is no end.
4. **The combined-heading rule first swallowed contents lines** (two existing tests failed). Fixed: a start line ending in a page number is a contents entry.
5. **My re-ingest dropped 15 recent MD&As** filed in November 2026. It read the main lake's archived submissions, which are older than the follow-up 3 sync's (archived in its own scratch lake). The row-by-row comparison found it. That sync's submissions were copied in, and the swap brings them into the main archive.
6. **My first recheck script crashed twice** (an SEC 502; an accession shared by GOOG and GOOGL) and was fixed. Its candidate list missed two rule kinds ("Part II. Item 7", the auditor line), and a scan of every stored row added them (97 rows).
7. **13 mutation gaps in my new code** (the "after" search, override order, page furniture, refresh, co-registrants) were killed by new tests.

Checked and found right:
- **The control sample.** 600 random rows: 3 changed (Expedia, PepsiCo, Boston Scientific), each an improvement.
- **Former CIKs.**
  - Every `until` is the day before the next CIK's first 10-K/10-Q, or the merger's close. Former issuers that kept filing as subsidiaries add nothing later.
  - A co-registrant filing is stored once.
  - Facts of other symbols are unchanged.
- **Hard rules.** No `datetime.now()`. No network in unit tests: new tests use respx and fixtures. No `.env` value in any new file. Home and temp paths are redacted in the artifacts.

Still open, with reasons (Backlog):
- Graph wiring stays with Stage 35 (owner).
- Emerson's FY2015 MD&A is its 4.5k pointer page: its annual report has no MD&A heading. A few MD&As where the filer prints no end heading run past their end (SolarEdge 2024).
- The old lines of `ingest_fundamentals`, `ingest_earnings` and `Lake.read_table` (83 surviving mutants: log fields, report counters, defaults) were never mutation-tested (→ 06/09/12).
- OKTA and VYLR, members since October 2026, have no fundamentals yet: the main lake's fundamentals sync predates them (a routine sync).
