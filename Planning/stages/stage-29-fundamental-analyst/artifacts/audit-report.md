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
