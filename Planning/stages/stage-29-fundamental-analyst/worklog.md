# Stage 29 — Fundamental analyst agent · Work log

Append entries in order. Record failures and dead ends as honestly as successes.

## 2026-11-18 05:05 — Start, read-in and gate check
- Did: read `Project.md`, `PROGRESS.md` (Stage 29 `NOT STARTED`, Stage 28 DONE), the brief, the approved brief notes (Phase 3 checkpoint Tier 2 item 7), the Stage 26–28 agent framework (`BaseAgent`, evidence ledger and citation check, tool registry, prompts, memory), `DataView`, the Stage 16 factor library and the LLM gateway / cache / CLI. Probed the main lake read-only: AAPL is Information Technology, 65 IT members of the S&P 500 on 2024-05-01, 262,230 IT fact rows, 140,127 IT bars since 2018.
- Found: the concept map has no receivables line (`config/xbrl_map.yaml`), and only mapped tags are stored, so the receivables red flag needs a new concept and a re-parse (plan D6, D11).
- Ran: `uv sync --group dev` (fresh worktree venv, ~25 min download) → exit 0. Gate: `uv run pytest -q` → 5034 passed, 81 deselected in 404 s, exit 0 ([artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)). Stage 28's `verification.md` says ALL CRITERIA VERIFIED.
- Did: copied the template; wrote [plan.md](plan.md) (decisions D1–D11, tasks 1–7); set Stage 29 `IN PROGRESS` in `PROGRESS.md` right after the gate (Stage 28 re-audit R3).
- Next: Task 1 (config and concept).

## 2026-11-18 05:35 — Task 1: config and the receivables concept
- Wrote `tests/unit/agents/research/test_fundamental_config.py` (defaults, `fund.yaml` equals the defaults, bounds, overrides, `accounts_receivable` as a stock with a fallback tag). Ran → collection error (no `FundamentalAnalystConfig`): red.
- Did: `RedFlagsConfig`, `FundamentalAnalystConfig` under `AgentsConfig.fundamental` (`src/atlas/core/config.py`); `agents.fundamental` in `config/fund.yaml`; concept `accounts_receivable` (`AccountsReceivableNetCurrent`, `ReceivablesNetCurrent`) appended to `config/xbrl_map.yaml`, so no existing metric moves.
- Ran: the new file → 13 passed. Neighbouring suites (core, data, agents, factors, cli, llm, orchestration; `-n 4`) → 3 failed, 3250 passed: three Stage 09 tests pin the concept map (`test_load_concept_map_repo_file` names, tag count 44; `test_concept_map_is_a_read_only_mapping` length 17; `test_acceptance_fundamentals_pit_aapl_fy2023` the metric set). Updated each for the added concept (names + `accounts_receivable`, 46 tags, 18 concepts; the AAPL fixture is trimmed to the pre-Stage-29 tags, so its receivables are NaN and the other 23 values are compared unchanged). These assertions were not weakened: each still pins the full list. Rerun → 94 passed.

## 2026-11-18 05:45 — Task 2: red-flag rules
- Wrote `test_fundamental_red_flags.py` (42 cases: flag / clear / not evaluated at and around each threshold, going-concern word boundaries, config-driven thresholds, bad thresholds refused). Ran → module missing: red.
- Did: `src/atlas/agents/tools/fundamental_tools.py` part 1: formatting helpers, `RedFlag`, `accruals_rule`, `receivables_rule`, `fcf_debt_rule`, `dilution_rule`, `going_concern_hits`, `going_concern_rule`.
- Ran → 42 passed.

## 2026-11-18 05:55 — Task 3: the four tools
- Wrote the test world `fundamental_world.py` (Stage 16 factor world + receivables for every filer + NNN, a Health Care filer built to trip four red flags; a dirty copy with a Q1 2024 filing, later bars and a late AAA split), `conftest.py` (`fworld`), `test_fundamental_tools.py` (hand-computed summary values, mid-rank percentiles checked against `scipy.stats.percentileofscore(kind="mean")`, CCC's P/E history from raw closes x raw cover counts across its split, sector medians, red-flag statuses and numbers, registry calls, gap items) and `test_fundamental_tools_pit.py` (dirty lake = clean lake at the as-of for six names; every item before the cutoff; a later as-of sees the later quarter). Ran → 30 failed (`AttributeError`: tools missing), 7 passed (formats, rules): red.
- Did: part 2 of the module: `fundamentals_summary`, `SectorPanel` / `sector_panel`, `percentile`, `factor_ranks`, `multiples` / `valuation`, `red_flag_results` / `red_flags`, `gap_item`, `prefetch`, the four `Tool`s with their own `FundamentalArgs` (the module imports only `tools/registry.py`); `atlas.agents.research.fundamental.fundamental_registry()` joins them with the built-in tools.
- Ran → 37 passed at the first run, so I printed the evidence of CCC and NNN and read it: numbers right, three wording fixes made (differences of percentages in "points", no internal "(Stage 09)" in factor descriptions, the n/m note only when an n/m is shown); research tests → 92 passed. `ruff check` (18 findings: long lines, unused imports) and `mypy` (4: `frame.at` scalar type, an optional date) fixed → clean.

## 2026-11-18 06:15 — Task 4: the agent and its prompt
- Wrote `test_fundamental.py` (27 tests: golden prompt `golden/fundamental_analyst_prompt.txt`, every prefetched id in the prompt, the four tools offered and versioned in the prompt version, a cited report → `ok` with its evidence, the symbol normalised and required, seven report problems each getting the repair turn, problems left → `low_quality` with confidence capped, an unknown id → citation repair, the sentence splitter and the number check, the section check, a blocked symbol → `DataError` before any LLM call with a `failed` trace, a gap shown and citable, a tool call for a peer, memories shown point in time, "(no memories)" without one). Ran → collection error (no `FundamentalAnalyst`): red.
- Did: `src/atlas/agents/research/fundamental.py` (`FundamentalAnalyst`, `fundamental_registry`, `SECTIONS`, `SIGNALS`, `sentences`, `uncited_numbers`, `missing_sections`), `research/__init__.py`, `agents/prompts/fundamental_analyst.j2` (v1). Wrote the golden file with `ATLAS_UPDATE_GOLDEN=1` and read it in full (system rules, user block with 29 evidence lines for AAA, memory block).
- Ran → 26 passed, 1 failed: the test called `TraceStore.list()`; the method is `traces()` (test bug, fixed). → 27 passed; `tests/unit/agents` → 446 passed; ruff, mypy clean.
- Did: `docs/agents.md` section "The fundamental analyst".

## 2026-11-18 06:30 — Task 5: `atlas data slice`
- Wrote `tests/unit/data/test_data_slice.py` (default tables = source tables with a symbol column, named tables checked, date columns, only the symbols' rows known at the as-of from the dirty lake, `--since` trims dated tables only, refuses a non-empty folder / no symbols / a bare string, the source untouched (file mtimes), and AAA's prefetch on a slice of its sector equal to the full lake's) and `tests/unit/cli/test_cli_data_slice.py` (writes and prints rows, `--tables`, bad arguments exit 2, a used folder or unknown table exit 1, help). Ran → `ModuleNotFoundError: atlas.data.slice`: red.
- Did: `src/atlas/data/slice.py` (`slice_lake`, `slice_tables`, `date_column`, `SliceReport`, `DERIVED_TABLES`, `slice.json` manifest without the source path); `atlas data slice` in `src/atlas/cli/data_cmd.py` (read-only, no run opened; exit 0 / 1 / 2).
- Ran → 14 passed, 1 failed: Rich wrapped the narrow table's title, so the heading is now its own line. → 15 passed (+ 20 existing data CLI tests); ruff (one f-string) and mypy clean.

## 2026-11-18 06:40 — Task 6: the recorded live fixture
- Wrote the replay test first: `recorded.py` (fixture paths, `replay_lake` copies the slice and loads the exported cache, `replay_config` with no secrets) and `test_the_recorded_aapl_run_replays_offline` (keys removed from the environment; `OfflineProvider`; same report; every call a cache hit; status `ok`; citations ok; every number tagged). Ran → `FileNotFoundError` (no fixture): red.
- Did: `artifacts/record_fixture.py`, steps run one by one:
  - `peers` on the main lake (read-only): AAPL is Information Technology; 65 symbols of 502 S&P 500 members on 2024-05-01.
  - `slice` (first try, default tables): **found a defect** in `slice_lake`: `--since` trimmed `index_membership` by `start_date` (the first date column of its key), dropping memberships that began before 2017 and still held (26 rows for 65 names); it also copied news / events / reactions the analyst never reads. Fixed test-first: `test_since_keeps_memberships_that_started_earlier` and a `date_column` case (red: `assert 'start_date' is None`), then static tables are never trimmed (green, 17 slice tests); the fixture names its five tables with `--tables`. Re-run: 225,407 rows (memberships 70).
  - `reparse`: the scratch slice's fundamentals re-parsed offline from the main lake's raw SEC archive (symlinked, `refresh=False`, an `httpx.MockTransport` answering 404 so nothing is downloaded) in 2.9 s: 65 symbols ok, 0 failed; 8,412 receivables rows.
  - `fixture`: the slice of the scratch lake into `tests/fixtures/recorded/stage-29-fundamental-aapl-2024-05-01/lake` (rows known at the as-of, since 2017-01-01): 228,925 rows, 5.96 MB.
  - `compare` ([fixture-compare.txt](artifacts/fixture-compare.txt)): AAPL's prefetch on the main lake (28 items) and on the fixture (29 items): 27 equal; the differences are the receivables balance line (absent on the main lake) and the receivables check (NOT EVALUATED on the main lake, CLEAR on the fixture: receivables -2.3% vs revenue -0.5%). So the slice is faithful.
  - `live` ([live-run.txt](artifacts/live-run.txt)): DeepSeek `deepseek-flash`, status `ok`, 1 LLM call, no tool call, 29 evidence items, 21 cited, 6,433 input + 1,730 output tokens, USD 0.00200, 11.5 s. The cache export inside the same process failed: DuckDB's lock on the run lake is held by the recording process (`Could not set lock on file ... atlas.duckdb`). Added a separate `export` step (`atlas llm cache export`) → 1 entry exported. The local user name in that lock message was redacted from the artifact.
- Checked: no value of any `.env` secret occurs in the fixture or the artifacts (count 0 each); the slice manifest holds no source path.
- Ran: the replay test → passed (28 passed in `test_fundamental.py`). Wrote `test_fundamental_live.py` (`-m llm`, cache off, on a copy of the slice) and ran it: [live-test.txt](artifacts/live-test.txt) → 1 passed, status ok, 1 call, 21 cited, USD 0.00102 (DeepSeek served most of the prompt from its own cache).
- Wrote the fixture's `README.md`.

## 2026-11-18 07:00 — Audit
- Coverage of the new modules (`--cov=src/atlas/agents/tools --cov=src/atlas/agents/research --cov=src/atlas/data`, path form): `fundamental_tools.py` 95%, `research/fundamental.py` 99%, `data/slice.py` 95% ([coverage-first.txt](artifacts/coverage-first.txt)). Read every uncovered line.
- A2 (silent skip): a universe member without an instrument row was dropped from the peers with no trace. Test first (`test_a_member_without_an_instrument_row_is_skipped_and_logged`: red, no `fundamental.peer_skipped` event), then a warning (green).
- A3 (untested paths): `test_fundamental_audit.py` (stale filings → NOT EVALUATED + "stale" note; too little own history and too few peers said so; `_debt_growth` incl. debt from none) and `test_a_symbols_list_table_keeps_the_articles_of_the_symbols` (news by its `symbols` list, `since` on a timestamp key). They passed on the code as written.
- A1 (the membership trim) and A4 (the export lock) were found and fixed during Task 6 (above). A5–A10: limits and notes, in [audit-report.md](artifacts/audit-report.md) and the Backlog. Mutation testing not run (A9: estimated two hours).
- Docs: `docs/data-limitations.md` fundamentals item 8 (receivables need a re-ingest; no MD&A text). Module docstring of `fundamental_tools.py` corrected (it imports only `registry.py` of the tools package; the registry function lives in the agent module).
- `PROGRESS.md`: Decisions log row, the Stage 24 budget item updated with the measured run (6,433 + 1,730 tokens, USD 0.0020: both budgets stay), the Stage 27 `min_similarity` item (still open: this analyst writes no episodes), five new Backlog items.
- Ran: research + slice tests → 138 passed; ruff, format, mypy clean. Then the final verification (next entry).

## 2026-11-18 07:25 — Final verification and records
- Ran (one after another, nothing else running): brief's Verify `uv run pytest tests/unit/agents/research/test_fundamental.py -q` → 28 passed; `uv run pytest -q` → 5180 passed, 82 deselected, exit 0; `make check` → exit 0 (ruff clean, 502 files formatted, mypy clean on 185 files, 5177 + 3 timing passed); `make test-integration` → 48 passed, 15 skipped, exit 0; coverage → 97% / 99% / 99% ([coverage.txt](artifacts/coverage.txt)). Removed the empty `.claude/.cc-writes` folder an earlier `cd` left in `tests/unit/agents/research` (git-ignored).
- Wrote [verification.md](verification.md) (ALL CRITERIA VERIFIED) and the `PROGRESS.md` handoff with its evidence block; ticked Stage 29, set it DONE, current stage 30.
- Next: commit `stage-29: Fundamental analyst agent`, fast-forward `main` in the owner's Terminal panel with the merged-result checks ([merged-main-check.sh](artifacts/merged-main-check.sh)).

## 2026-11-18 07:50 — Commit and merge
- Committed `eb17ddf` `stage-29: Fundamental analyst agent` after reviewing the staged list (127 files: code, tests, config, docs, the fixture, stage records; no `.env`, lake or DuckDB file; no `.env` value in any staged file). Pre-commit hooks passed; `git status` clean.
- Fast-forwarded `main` 65de3a5 → eb17ddf in the owner's Terminal panel (`artifacts/merged-main-check.sh`; the sandbox cannot write the main checkout). Merged result ([merged-main-check.txt](artifacts/merged-main-check.txt)): trees equal; `uv run pytest -q` → 5180 passed, 82 deselected, exit 0; `make check` → exit 0; `git branch --merged main` lists the stage branch; main checkout clean, ahead of `origin/main` by 1.
- Not pushed: the fixture adds about 116,000 vendor price rows (and SEC facts) to a public repository; asked the owner first. This records commit goes onto the branch and `main` the same way.

## 2026-11-18 08:05 — Push and CI
- Owner: "Yes, push". `git push origin main claude/stage-29-planning-audit-3185f8` from the sandbox: `main` 65de3a5..0b1c74f, the stage branch new on origin.
- CI on 0b1c74f (public API, [ci-runs-0b1c74f.json](artifacts/ci-runs-0b1c74f.json)): `main` run 44297802312 **success**, branch run 44297801313 **success**.

## 2026-11-18 08:15 — Follow-up (owner: "please help me to fix the problems and complete what you have not done. after the fix, please have a final audit and report your progress")
- Open items from the audit: A5 (main lake without receivables rows), A9 (no mutation testing), A7 (fixture size), plus graph wiring and MD&A text (later stages: not done here, Rule 2.4).
- A7 closed without change: the fixture's 6 MB of blobs are in the pushed history, so a smaller re-recording would not make the repository smaller; it would only add churn.
- A5: [main_lake_receivables.py](artifacts/main_lake_receivables.py) `copy`, `before`, `reparse`, `check` on a scratch copy of the main lake ([main-lake-reparse.txt](artifacts/main-lake-reparse.txt)): 742 symbols re-parsed offline in 28 s, 0 failed; 2,574,827 rows before, all unchanged; 68,846 rows added, all `AccountsReceivableNetCurrent` / `ReceivablesNetCurrent` (609 of 742 symbols). CHECK OK. The stored factor exposures (142 dates), factor returns and risk-model snapshots (115 dates) were digested (values only) before the rebuild, which the new fact counts make necessary (`input_fingerprint` counts fact rows).
- A5 rebuild on the scratch copy ([main-lake-rebuild.txt](artifacts/main-lake-rebuild.txt)): `atlas factors build --from 2015 --to 2026` → 142 month-ends written, exit 0 (23 min); `atlas risk-model build --from 2017 --to 2026 --cached-factors` → 2,471 sessions, 115 snapshots, exit 0 (27 min). `after` ([main-lake-stores-compare.txt](artifacts/main-lake-stores-compare.txt)): factor exposures (1,212,389 rows), factor returns (49,420) and risk-model snapshots (1,236,160) **equal** to their pre-rebuild digests, so the receivables rows changed no stored value. File digests vs the main lake: only `fundamentals_facts` and `factor_exposures` (new input fingerprints) differ; the risk tables are byte-identical. Both caches fresh (142 of 142 dates).
- A5 swap into the main lake from the owner's Terminal panel ([main_lake_swap.sh](artifacts/main_lake_swap.sh), adapted from the Phase 2 one-pass swap). First attempt **failed safely**: I passed `$TMPDIR/s29/main-copy`, which the owner's shell resolves to its own temp folder (`cp: ... No such file or directory`, swap exit 1) after the backup copy and before any table was replaced; checked: both tables equal to their backup digests, no `.new` / `.old` folders. Second attempt with the absolute path (`/tmp/claude-501/s29/main-copy`; the first backup renamed to `cache/backup-stage29-receivables-2026-11-18-attempt1`, identical copies) → swap exit 0 ([main-lake-swap.txt](artifacts/main-lake-swap.txt)): backup `cache/backup-stage29-receivables-2026-11-18` checked, `fundamentals_facts` and `factor_exposures` replaced and equal to the scratch copy's digests, `_meta.json` identical, build logs kept as `runs/{factors,risk_model}/build-stage29-receivables-2026-11-18.log`.
- A5 read-back ([main-lake-readback.txt](artifacts/main-lake-readback.txt)): main lake factor cache 142 of 142 dates fresh; AAPL's prefetch on the main lake vs the fixture: 29 of 29 items equal (before: 27 of 28). The receivables check now evaluates on the main lake.
- A9 mutation testing ([mutation/mutation-summary.md](artifacts/mutation/mutation-summary.md)):
  - Setup: mutmut 3.8.1 as a throwaway tool on a scratch copy (config [mutmut-setup.cfg](artifacts/mutmut-setup.cfg)), over `fundamental_tools.py`, `research/fundamental.py` and `data/slice.py`.
  - First run: 2,396 mutants, 2,038 killed, 66 survived, 292 "suspicious". Suspicious is exit -5, a SIGTRAP when mutmut's forked workers end after passing tests on this Mac. A plain-process re-run ([classify.sh](artifacts/mutation/classify.sh)) gave 15 killed and 277 passing. So 2,053 killed (85.7%) and 343 survivors.
  - Read every survivor's diff. Wrote tests for the real gaps (sources, stamps, boundaries, per-tool gaps, exact texts, memory query, registry, slice manifest / registry / list columns), in five passes.
  - The new tests and their passes are in the summary's table. Two of my test drafts were wrong and were corrected before they were used:
    - a slice test table lacked `year_column`, so `since` correctly left it untrimmed;
    - a restatement helper filtered on TTM, which `total_assets` lacks.
  - Result: **2,323 of 2,396 killed (97.0%)**. The 73 survivors are all classified in [final-survivors-classified.tsv](artifacts/mutation/final-survivors-classified.tsv): 48 equivalent, 9 unreachable, 16 untested edges (Backlog).
  - No production defect was found; no code under `src/` changed.
  - The pass results are in [after-reruns.txt](artifacts/mutation/after-reruns.txt). The username was removed from `classify.sh` and `main-lake-swap.txt`.
- Test files created or changed in the follow-up:
  - `tests/unit/agents/research/test_fundamental_contracts.py` (new, 75 tests);
  - `tests/unit/agents/research/test_fundamental.py` (five-sentence repair quote);
  - `tests/unit/agents/research/test_fundamental_tools.py` (gap source);
  - `tests/unit/data/test_data_slice.py` (3 tests).

  Docs: `docs/agents.md` and `docs/data-limitations.md` (receivables now in the main lake), and the fixture `README.md`.
- Audit report: A5, A7 and A9 updated, plus a *Follow-up* section ([audit-report.md](artifacts/audit-report.md)).
- Commit and merge:
  - Committed `e574988` `stage-29: follow-up (main-lake receivables, mutation testing, final audit)`. Before that I reviewed the staged list: 33 files, all records and tests, nothing under `src/`. Scans found no `.env` value, no lake or DuckDB file and no user name.
  - The first commit attempt failed in the pre-commit hooks: uv could not open its default cache from the sandbox. The hooks passed with `UV_CACHE_DIR` set.
  - Fast-forwarded `main` 0b1c74f → e574988 in the owner's Terminal panel ([followup-merged-main-check.txt](artifacts/followup-merged-main-check.txt)). Trees equal.
  - Merged result: `uv run pytest -q` → 5260 passed, 82 deselected, exit 0; `make check` → exit 0; `git branch --merged main` lists the stage branch.
  - This records commit goes onto `main` the same way; it changes only `worklog.md` and `PROGRESS.md`.
- CI on `addaf65`: `main` run 44368701108 success, branch run 44368701317 success ([ci-runs-addaf65.json](artifacts/ci-runs-addaf65.json)).

## 2026-11-18 18:40 — Follow-up 2 (owner: "please complete the ones that are not done and have a final audit on your own work")
- Open items: graph wiring (A10), MD&A text (A6), 16 untested-edge mutants, the uncommitted CI record.
- Asked the owner the two that need a decision.
  - Graph wiring is Stage 35's task 4 under Rule 4 → **"Leave it to Stage 35"**.
  - MD&A text is a design-spec change under Rule 3 → **"Approve the spec change"**.
  - Spec §11 gained the "Filing text" row. Decisions log row added. Plan addendum written before code: decisions E1–E6, tasks F1–F8.
- Prototype on 20 real 10-Ks (AAPL, CCL, the former CHK):
  - Item 7 extraction works.
  - Carnival 2020–22 put the MD&A in Exhibit 13.
  - Chesapeake's 2020 doubt first appeared in a 10-Q, so a 10-K-only source misses it. This is a recorded limit.
- F1–F3, test-first:
  - config `data.filing_text`, `filing_*_url`, `red_flags.mda_max_age_days`, chunk `filing_text`;
  - `atlas.data.filings.mda` (`html_to_text`, `extract_mda`, `extract_exhibit_mda`);
  - `EdgarClient.filing_documents` / `filing_document`;
  - `atlas.data.filings.text` (`FILING_TEXT`, `parse_filing_list`, `filing_list`, `ingest_filing_text`).
  - Red first, each time. Two fixes:
    - bytes without a charset were read as Latin-1, so curly apostrophes broke headings: now decoded as UTF-8, else Windows-1252;
    - ruff RUF001: escapes instead of literal Unicode.
  - mypy: lxml added to the `ignore_missing_imports` overrides. It was a dependency already, never imported directly until now.
- F4: the `filings` sync dataset and step `filing_text`, a status row, and the CLI help. The sync test world now mocks the EDGAR archive. `test_sync_defaults` gained the new chunk size; this is a test update for a new setting.
- F5: `DataView.mda`, registered in every view PIT suite (leak, guard, block) and the test world. Semantics tests in `test_filings_view.py`.
- F6: `_going_concern` reads `view.mda`. A lake without `filing_text` gives the old reason word for word, so the recorded fixture's replay is unchanged (213 research tests green).
- Live smoke, scratch lake, 3 symbols:
  - 36 10-Ks, all `ok`.
  - Apple FY2015 was only 6,128 characters. **Defect:** a cross-reference starting a line cut the section.
  - Headings must now be followed by their title. Test first; the section is now 58,354 characters.
- F7, backfill: scratch lake (copies of `instruments` / `index_membership`), `atlas data sync --datasets filings`.
  - 748 equities, 735 ok, 13 empty, 0 failed, 7,643 10-Ks, 52 minutes ([filing-text-backfill.txt](artifacts/filing-text-backfill.txt)).
  - The review showed 30 companies without any MD&A (JPM, BAC, GE, CVX, …: annual-report layouts). Decision E7: a fallback to the MD&A heading, plus IBM's "Management Discussion"; test-first.
  - The 428 filings without an MD&A were deleted and fetched again: 7,558 of 7,643 now have one ([filing-text-refetch.txt](artifacts/filing-text-refetch.txt)).
- E5, the rule settled on real data ([filing-text-review.txt](artifacts/filing-text-review.txt)):
  - Any term anywhere flagged about 2% of MD&As, almost all ASU 2014-15 descriptions.
  - Now: one sentence must hold all of the terms and none of `going_concern_exclusions` (standard wording, hypotheticals, other parties, negations). The FLAG quotes the sentence.
  - Three review rounds, each over every both-term sentence. Result: 16 FLAGs, all reviewed (12 own, 3 subsidiary, 1 other company).
  - Test-first at each round.
- E6, main lake:
  - [main_lake_filing_text_swap.sh](artifacts/main_lake_filing_text_swap.sh) ran in the owner's Terminal panel with the absolute scratch path.
  - Backup in `cache/backup-stage29-filing-text-2026-11-18`; only `tables.filing_text` and `steps.filing_text` were added; 735 files equal ([main-lake-filing-text-swap.txt](artifacts/main-lake-filing-text-swap.txt)).
  - Read-back ([main-lake-filing-text-readback.txt](artifacts/main-lake-filing-text-readback.txt)): other meta and state entries unchanged. Enphase reads CLEAR on 2017-03-16 (filed that day, after the cutoff) and FLAG on 2017-03-17. Frontier is FLAG in 2020; AAPL and JPM are CLEAR; WFC is NOT EVALUATED with its reason.
- Untested-edge mutants: third-pass contract tests for:
  - the window start at `valuation_years` 1 and 2;
  - bar gaps;
  - a quarter without a share count;
  - red-flag periods with under a year of history;
  - peer order;
  - the no-factor-values message;
  - a flow without TTM;
  - a tag-like piece.

  12 of 16 killed; Stage 29 is now 2,335 of 2,396 (97.5%).
- Mutation testing of the new code ([mutmut-setup-followup2.cfg](artifacts/mutmut-setup-followup2.cfg)):
  - First run: 613 of 706.
  - 15 gap tests added for history pages, a shared CIK, symbol order, a given calendar, logs, exact errors, decoding, comments, longest-not-greatest, inclusive bounds and the view's per-symbol cache. One dead line was removed.
  - Then 764 of 801. Three more tests killed 4.
  - Final run after E7: **778 of 811 (95.9%)**; 33 equivalent survivors, classified.
- Docs: `docs/data.md` (the dataset and source), `docs/agents.md` (going concern), `docs/data-limitations.md` (items 8 and 9).
- Code files created:
  - `src/atlas/data/filings/{__init__,mda,text}.py`;
  - tests `tests/unit/data/filings/test_filings_{mda,ingest,view}.py`, `tests/unit/agents/research/test_fundamental_going_concern.py`.
- Code files changed:
  - `src/atlas/core/config.py`, `config/fund.yaml`, `pyproject.toml` (mypy override);
  - `src/atlas/data/fundamentals/edgar_client.py`;
  - `src/atlas/data/sync/{__init__,datasets,steps,status}.py`, `src/atlas/cli/data_cmd.py`;
  - `src/atlas/data/view.py`, `src/atlas/agents/tools/fundamental_tools.py`;
  - tests `test_fundamental_{contracts,red_flags}.py`, `tests/unit/core/test_config.py`, `tests/unit/data/{sync_helpers,test_sync_datasets,test_view_pit,view_helpers}.py`.
- Self-audit: [audit-report.md › Follow-up 2](artifacts/audit-report.md).
