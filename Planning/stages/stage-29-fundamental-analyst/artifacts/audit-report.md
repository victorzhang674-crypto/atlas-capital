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
- **A6 — limit (documented).** No MD&A text is ingested: the going-concern check is always NOT
  EVALUATED; the rule is a pure text scan ready for a text source (`going_concern_hits`).
- **A7 — note, closed without change.** The fixture lake is 5.96 MB. About two thirds are peers'
  bars before 2022 that no tool reads. The blobs are already in the pushed history, so a smaller
  re-recording would not shrink the repository. Later fixtures can use per-symbol windows.
- **A8 — budget measured on a real prompt** (Stage 24 backlog item). One AAPL run: 6,433 input +
  1,730 output tokens, USD 0.0020, one call. `per_agent_call_max_tokens` (8,000, reasoning
  included) leaves 4.6x headroom over the output; a repair turn would roughly double input. Keep
  both budgets; revisit at the first LLM backtest (Stage 56), as the item already says.
- **A9 — mutation testing, done in the follow-up.** At first not run (estimated two hours);
  coverage of 95–99% was used instead. Follow-up: mutmut over the three modules, 2,396 mutants.
  Kill rate 85.7% before, **97.0% after** 80 new tests; the 73 survivors are all classified
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

### Not done, with reasons

- MD&A text (A6): no filing-text source is ingested. This is a data-pipeline addition, not in
  this brief's scope.
- Graph wiring (A10): Stage 35 / 36 / 55 (Rule 2.4 and Rule 4).
- The 16 untested-edge mutants: recorded in the Backlog.

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
