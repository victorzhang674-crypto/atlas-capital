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
- Commit and merge:
  - Committed `f97d30e` `stage-29: follow-up 2 (10-K MD&A text, going-concern check, final self-audit)` after reviewing the staged list: 54 files. No `.env`, lake, DuckDB or mutant file; no `.env` value in any file. The only user name is the pre-existing one in a PROGRESS line from the original planning commit; I redacted the home path from my artifacts.
  - Fast-forwarded `main` addaf65 → f97d30e in the owner's Terminal panel ([followup2-merged-main-check.txt](artifacts/followup2-merged-main-check.txt)). Trees equal.
  - Merged result: `uv run pytest -q` → 5374 passed, 82 deselected, exit 0; `make check` → exit 0; `git branch --merged main` lists the stage branch.
  - This records commit goes onto `main` the same way, then I push and check CI.

## 2026-11-22 — Follow-up 3 (owner: "can you help me to complete the tasks that are still being opened?")
- Open items:
  - 10-Q MD&As;
  - the seven companies without an MD&A;
  - the four untested-edge mutants;
  - the uncommitted CI record `ci-runs-1e32337.json`.

  Graph wiring stays with Stage 35, as the owner decided in follow-up 2. Plan addendum H1–H5 was written before the code ([plan.md](plan.md)). Spec §11's row was widened to 10-Qs, with a Decisions-log row: the owner's request is the approval.
- H2, the seven companies, test-first:
  - EX-13 and EX-99 exhibits are read (Clorox files its annual report as EX-99.1);
  - "Financial Review" and upper-case company-name MD&A headings are accepted (Wells Fargo);
  - the MD&A heading later in the 10-K itself is used, skipping "Item" lines (AEP, MOS, TWC, TWX, WIN);
  - bullets and cross-references are not end headings.
- H1, 10-Qs: `extract_mda(..., form=)` reads Part I Item 2; `data.filing_text.forms` is `["10-K", "10-Q"]`; `DataView.mda` returns the latest filing whose MD&A was found; the check says "period ended".
- H3: the four edge mutants were killed by three tests (pass 7 in the mutation summary). Stage 29 is now 2,339 of 2,396 killed (97.6%).
- Mutation testing of the H1–H2 code: 853 of 889 detected (96.0%). One real gap: an inline bullet before an end heading, killed by a new test. 36 equivalent survivors.
- H4, round 1:
  - The follow-up 2 scratch lake was re-synced: 748 equities, 0 failed, 30,597 filings, 4 h 8 min ([filing-text-backfill-followup3.txt](artifacts/filing-text-backfill-followup3.txt)).
  - Swapped into the main lake from the owner's Terminal panel with backup `cache/backup-stage29-filing-text-2026-11-22` ([main-lake-filing-text-replace.txt](artifacts/main-lake-filing-text-replace.txt)).
  - **The review and the read-back of round 1 showed defects in my own code**, so the round-1 table was not left in place:
    - H6, going concern: 8 of 79 FLAGs were false, and 3 quoted a sentence from its middle. Sentence joining and three exclusions fixed them, test-first, giving 71 FLAGs.
    - H7, extraction:
      - 5,732 10-Qs had taken Part II Item 2's contents entry. A bare "Item 2." now needs "Management…" after it.
      - Pointer pages (Southern Company, Entergy, TSS, Sempra) give way to a later MD&A section or an exhibit MD&A when shorter than the new `data.filing_text.short_mda_chars` (15,000).
      - Entergy's heading is accepted, and condensed statements end an MD&A.
  - Mutation testing of the H6–H7 code: 231, then **237 of 240** after two tests and an assertion. 3 equivalent survivors.
  - The whole-table read overflow, the XOM CIK change and GE's 10-Q contents entries went to the Backlog.
- H8, re-ingest:
  - 7,065 accessions (7,152 rows) were deleted from the scratch lake and fetched again: 748 keys, 0 failed, 7,152 rows, 1 h 14 min ([filing-text-refetch-followup3.txt](artifacts/filing-text-refetch-followup3.txt)).
  - Control sample: 200 random untouched rows extracted again with the new code from freshly fetched documents. 200 of 200 identical, no document changed ([filing-text-control-sample-followup3.txt](artifacts/filing-text-control-sample-followup3.txt)).
  - Comparison with round 1 ([filing-text-compare-round1-followup3.txt](artifacts/filing-text-compare-round1-followup3.txt)): 5,700 10-Q texts shorter (no longer running past the MD&A), 101 longer, 36 found that were not, and 6 10-Ks moved to their EX-13.
  - **Two 10-Qs lost their MD&A.** A. O. Smith's rightly: round 1 had the Part II contents entry. Roper's wrongly.
- H9: Roper's 2017-11 10-Q heads its MD&A "MANAGMENT'S DISCUSSION".
  - Fix: the Item 2 and Item 7 headings accept "Managment". Test first: `test_a_heading_that_misspells_management_is_still_read`, red, then green.
  - The rows the fix could change were extracted again from freshly fetched documents: all 92 not-found rows, the 8 rows whose text holds "managment", and 300 fresh random rows. Only Roper's row differed. It was fetched again ([filing-text-recheck-followup3.txt](artifacts/filing-text-recheck-followup3.txt)).
- Final review ([filing-text-review-followup3.txt](artifacts/filing-text-review-followup3.txt)): 30,506 of 30,597 filings have an MD&A (99.7%).
  - 75 FLAGs (23 companies), all read: 59 own, 8 subsidiary, 8 another company's (Toshiba).
  - Against round 1's fixed review: SO/GAS +6 (the combined MD&A read whole); HST and MNK −2 (round 1 had read the notes to the statements).
- Main lake, second swap:
  - From the owner's Terminal panel with backup suffix `2026-11-22b` (the script now takes it as an argument). Old table backed up equal (742 files), new table equal to the scratch lake (742 files), only `steps.filing_text` replaced ([main-lake-filing-text-replace-2.txt](artifacts/main-lake-filing-text-replace-2.txt)).
  - Read-back ([main-lake-filing-text-readback-followup3.txt](artifacts/main-lake-filing-text-readback-followup3.txt)): `_meta.json` unchanged and only the `filing_text` sync step changed; 21 `DataView` cases as expected. Examples:
    - BBBY is FLAG on the day after its 10-Q and CLEAR the day before;
    - SO's combined MD&A is 391,491 characters;
    - HST in 2020 is CLEAR;
    - ROP's 10-Q is read;
    - TSS's 10-K reads its EX-13.
- Docs: `docs/data-limitations.md` item 9 (10-K and 10-Q, the pointer rule, final numbers, known gaps), `docs/agents.md` (going concern, final numbers), `docs/data.md` (the dataset row).
- Code files changed (none created):
  - `src/atlas/data/filings/{mda,text}.py`, `src/atlas/data/view.py`, `src/atlas/agents/tools/fundamental_tools.py`, `src/atlas/core/config.py`, `config/fund.yaml`;
  - tests `tests/unit/data/filings/test_filings_{mda,ingest,view}.py`, `tests/unit/agents/research/test_fundamental_{contracts,going_concern,red_flags}.py`.
- Self-audit: [audit-report.md › Follow-up 3](artifacts/audit-report.md).
- Commit and merge:
  - Committed `4ebcd40` `stage-29: follow-up 3 (10-Q MD&A text, seven filers' MD&A, last edge mutants)` after reviewing the staged list: 52 files. No `.env`, lake, DuckDB or mutant file. A scan of the staged diff for the 5 `.env` values found none, and no home path or username is in it.
  - Fast-forwarded `main` 1e32337 → 4ebcd40 in the owner's Terminal panel ([followup3-merged-main-check.txt](artifacts/followup3-merged-main-check.txt)). Trees equal.
  - Merged result: `uv run pytest -q` → 5418 passed, 82 deselected, exit 0; `make check` → exit 0; `git branch --merged main` lists the stage branch.
  - This records commit goes onto `main` the same way, then I push both branches and check CI.

## 2026-11-23 — Follow-up 4 (owner: "can you complete the still open ones and complete them?")

- Gate: Stage 29 DONE with evidence; `uv run pytest -q` → 5418 passed before this follow-up (follow-up 3 merged result). Graph wiring stays with Stage 35 (owner, 2026-11-18). The items routed to Stages 06 and 09 are done here on this request (Decisions log, 2026-11-23).
- Investigation first, then the [plan](plan.md) (J1–J6), then test-first work. I record honestly that J1 and J2 code was written while investigating, before the plan section existed; each change still went red, then green.
- J1, whole-table read: `Lake.read_table` → `_sorted` / `_large_type` (`src/atlas/core/storage/lake.py`). The scratch lake's and then the main lake's whole `filing_text` read without the overflow ([main-lake-whole-read-followup4.txt](artifacts/main-lake-whole-read-followup4.txt)).
- J2, MD&A rules (`src/atlas/data/filings/{mda,text}.py`):
  - First pass: every main-lake filing without an MD&A or with one under 15,000 characters (156) was fetched into a gzip cache (`filing_text_caching_followup4.py`) and read. Rules found:
    - "Items 2. and 3."; "ITEM" on its own line; "MANGEMENT";
    - the 10-Q Item 1 end;
    - run-in "(MD&A)" headings;
    - "FINANCIAL STATEMENTS" and "Report of Management" ends;
    - statement lines ending in section words are no end;
    - link and mid-sentence lines are no end;
    - more "incorporated … by reference" wordings;
    - a later heading must follow the short MD&A (Texas Instruments).
  - Recheck: every stored row those rules could change, found from the stored text (1,816), plus 600 random controls, re-extracted from freshly fetched documents (`filing_text_recheck_followup4.py`). Reading the changes found seven defects in my own rules, each fixed test-first:
    - Weyerhaeuser's sentence taken as a run-in heading;
    - Axon's misnumbered "Item 7" running into Part II;
    - quoted cross-references (Blackstone, Devon) and "Item 1. Business" (Everest) ending 10-Q MD&As;
    - Expedia's "Part II. Item 7.";
    - Hartford's and PepsiCo's MD&As running into the statements (the auditor's-report end);
    - PepsiCo's contents entry of that report;
    - Regions' combined "Item 7." / "Item 7A." heading.

    Two scans of every stored row added the rows the Part II and auditor rules could change (35 + 62).
  - Final recheck: 2,513 rows re-read, 840 changed, each kind reviewed. 3 of 600 controls changed (Expedia, PepsiCo, Boston Scientific), each an improvement ([filing-text-recheck-followup4.txt](artifacts/filing-text-recheck-followup4.txt)).
- J3: `going_concern_sentences` drops page furniture between the halves of a sentence (`src/atlas/agents/tools/fundamental_tools.py`). Windstream now quotes "Our financial condition, … master lease agreement with Uniti, and …".
- J4, former CIKs:
  - Found by comparing each member's first 10-K/10-Q filing date with the start of its S&P 500 window: 16 issuers. TFCF / TFCFA had no CIK.
  - Checked on EDGAR ([former-ciks-followup4.txt](artifacts/former-ciks-followup4.txt)).
  - Code: `FormerCik`, `CikSource`, `CikOverride.former` / `since`, `CikMap.sources` (`edgar_client.py`); `ingest_fundamentals` (`ingest.py`), `ingest_filing_text` (`_filings_of`, `_rows` take each filing's CIK), `ingest_earnings` (`events/earnings.py`); `config/cik_overrides.yaml` (+16 entries, FTI and LLL extended).
- J5, main lake ([main-lake-followup4.txt](artifacts/main-lake-followup4.txt), script [main_lake_followup4.py](artifacts/main_lake_followup4.py)):
  - `copy` → 1,044.9 MB, `raw/sec` as per-file links.
  - `cik`: facts 24,542 → 70,093 rows for the 18 symbols, SEC events 418 → 769, MD&As 406 → 728, reactions 393 → 703; 0 failed.
  - `text`: 839 changed rows plus the 18 symbols' MD&As fetched again.
  - **My defect:** `text` re-read the main lake's archived submissions, which are older than the follow-up 3 sync's (archived in its own scratch lake), so 15 recent MD&As were missing. I copied that sync's 563 newer submissions into the copy, and `missing` added 15 rows.
  - Comparison ([filing-text-compare-followup4.txt](artifacts/filing-text-compare-followup4.txt)): 839 changed rows, every one of them a recheck-changed row; 10 removed (merger shells, Upjohn); 340 added, all of former-CIK symbols; 30,927 of 30,927 with an MD&A.
  - `check`: facts, events and reactions changed only for the 18 symbols. The 214 facts and 11 events removed are the merger shells' and Upjohn's.
  - Rebuild ([main-lake-rebuild-followup4.txt](artifacts/main-lake-rebuild-followup4.txt)):
    - `atlas factors build --from 2015 --to 2026` → 142 month-ends, exit 0 (25 min);
    - `atlas risk-model build --from 2017 --to 2026 --cached-factors` → 2,471 sessions, 115 snapshots, exit 0 (27 min).

    Raw exposures changed only for the 18 symbols (13,727 rows, 13,242 filled); other names moved through standardisation, median 0.0043 ([main-lake-stores-compare-followup4.txt](artifacts/main-lake-stores-compare-followup4.txt)).
  - Swap from the owner's Terminal panel ([main_lake_followup4_swap.sh](artifacts/main_lake_followup4_swap.sh), [main-lake-swap-followup4.txt](artifacts/main-lake-swap-followup4.txt)):
    - 7 tables backed up (digest-checked) and replaced, each equal to the scratch copy;
    - raw/sec: 70 files added, 562 replaced (old ones in `cache/backup-stage29-followup4-2026-11-23/raw-sec`);
    - `_meta.json` differs byte for byte, equal in content.
  - Read-back ([main-lake-readback-followup4.txt](artifacts/main-lake-readback-followup4.txt)): row counts equal; 11 `DataView` cases as expected (XOM 2020 revenue TTM $255.9B from CIK 34088; VTRS 2020 Mylan's; BNY, NTRS, GE, USB MD&As; WIN and ENPH FLAG).
- Going-concern review of every MD&A ([filing-text-review-followup4.txt](artifacts/filing-text-review-followup4.txt)): 76 FLAGs of 30,927, the 75 reviewed plus ENPH 10-Q 2017-05-10 (its own doubt, correct).
- J6, mutation testing ([mutation-summary.md](artifacts/mutation/mutation-summary.md)):
  - 883 mutants of the changed functions: first 745 killed. Tests added for 14 gaps; then 768 killed.
  - The survivors on new lines are equivalent (32); 83 are on old Stage 06/09/12 lines (Backlog).
  - mutmut 3.8.1 was installed into a scratch folder: the `uv run --with` layer would not install it this time.
- Docs: `docs/data-limitations.md` (fundamentals item 2: former CIKs; item 9: the MD&A rules and numbers), `docs/data.md` (former CIKs), `docs/agents.md` (76 FLAGs).
- Code files changed (none created):
  - `src/atlas/core/storage/lake.py`;
  - `src/atlas/data/filings/{mda,text}.py`;
  - `src/atlas/data/fundamentals/{__init__,edgar_client,ingest}.py`;
  - `src/atlas/data/events/earnings.py`;
  - `src/atlas/agents/tools/fundamental_tools.py`;
  - `config/cik_overrides.yaml`.
- Test files changed:
  - `tests/unit/core/storage/test_storage_lake.py`;
  - `tests/unit/data/filings/test_filings_{mda,ingest}.py`;
  - `tests/unit/data/fundamentals/test_fundamentals_{client,ingest}.py`;
  - `tests/unit/data/events/test_events_earnings.py`;
  - `tests/unit/agents/research/test_fundamental_red_flags.py`.
- Self-audit: [audit-report.md › Follow-up 4](artifacts/audit-report.md).
- Commit and merge:
  - Committed `8611b27` `stage-29: follow-up 4 (former CIKs, MD&A headings, whole-table read)` after reviewing the staged list: 55 files, all under `Planning/`, `src/`, `tests/`, `docs/` or `config/cik_overrides.yaml`. No `.env`, lake, DuckDB or mutant file. A scan of the staged diff for the 5 `.env` values found none, and no home path, temp path or email address. Hooks passed.
  - Fast-forwarded `main` c579104 → 8611b27 in the owner's Terminal panel ([followup4-merged-main-check.txt](artifacts/followup4-merged-main-check.txt)). Trees equal.
  - Merged result: `uv run pytest -q` → 5472 passed, 82 deselected, exit 0; `make check` → exit 0; `git branch --merged main` lists the stage branch.
  - This records commit goes onto `main` the same way; then I push both branches and check CI.

## 2026-11-23 — Follow-up 5 (task from the session that started this one: "Close every gap that Stage 29 (fundamental analyst) still has open")

- Read-in: `Project.md`, `CLAUDE.md`, `Planning/PROGRESS.md` (Stage 29 DONE, follow-ups 1–4 merged, `main` = `3d77679`; Stage 30 NOT STARTED and not started here). The five gaps were checked against `PROGRESS.md › Backlog` and the Stage 29 "Known gaps": Emerson FY2015, MD&As without an end heading (SolarEdge 2024), 83 old-line mutants, OKTA/VYLR fundamentals, and the uncommitted `ci-runs-3d77679.json`. Graph wiring stays with Stage 35 (owner).
- Gate: `uv run pytest -q` → 5472 passed, 82 deselected, exit 0 ([followup5-gate-pytest.txt](artifacts/followup5-gate-pytest.txt)).
- `ci-runs-3d77679.json` copied from the planning-audit worktree into `artifacts/`: both runs of `3d77679` (`main` and `claude/stage-29-planning-audit-3185f8`) completed with success.
- Investigation, before the plan:
  - A scan of every stored MD&A for text that only follows an MD&A found 408 rows (`scan_overrun`, kept in the scratch folder).
  - Re-extracting those 408 with the current code (`filing_text_recheck_followup5.py`, documents cached by `filing_text_caching_followup5.py`) changed 83 of them, so **stored rows exist that the current code extracts differently**. The biggest: Comerica's 11 10-Ks store the 10-K's contents and Part I, not the MD&A, and the current code finds no MD&A at all (its Item 7 is one sentence naming the sections of its Financial Section). Also U.S. Bancorp's 2024–26 10-Ks, FedEx 2018–20, Wells Fargo's 10-Qs, PPL, SolarEdge's 10-Qs, Realty Income.
  - Hence the plan's K0: re-extract **every** stored filing. `filing_text_full_recheck_followup5.py` re-reads all 30,927 from EDGAR with the current code, keeping each document's text gzipped, so rule changes are checked offline on the whole corpus. It runs in the background (4 workers at 2.5 requests/s each, ~2.4 filings/s).
- [Plan](plan.md) section "Follow-up 5" written (K0–K6), then carried out without a pause (owner's standing instruction).
- K1, the sections an MD&A item names (test-first: 5 new tests in `test_filings_mda.py`, 3 in `test_filings_ingest.py`, red, then green):
  - `item_sections`, `named_sections` and `extract_titled_mda` in `mda.py`;
  - `_named`, `_longer` and their use in `_mda_of` (`text.py`): only where the present rules find nothing longer, in the same document after the item and in the EX-13/EX-99 exhibits.
  - On the real documents (text cache, offline): Emerson FY2015 → EX-13, 56,539 characters from "Results of Operations" to the statements (was its 4,539-character Item 7). Comerica 2015–2025 → the Financial Section's MD&A, 134k–183k characters from "<year> OVERVIEW …" to the statements (the current code had found none).
- K0: the full re-read was first run with 4 workers at 2.5 requests/s each (~2.5 filings/s). It was then stopped and resumed with 8 workers at 1.25 requests/s each (the same 10 requests/s in all; ~3.7 filings/s). Because the resumed run used the worktree's changed code, the online pass now only fills the text cache (`$TMPDIR/s29f5/txt`). HEAD's extraction (from `git archive HEAD src`) and the final code are both run offline over the whole cache and compared.
- K2, MD&As that run past their end (test-first, each red then green; `mda.py`):
  - Rules:
    - "ITEM" on a line of its own in 10-K headings (`_ITEM`);
    - "Items 7. and 7A." (Freeport-McMoRan);
    - "/" and "|" between an item and its title (AIG);
    - a "Part I," prefix on a 10-Q's Item 2 (Computer Sciences);
    - later 10-K items (9, 9A–9C, 10–16, titled) and "PART III"/"PART IV" lines end Item 7;
    - a 10-Q's titled Part II items end Item 2;
    - the signatures and the exhibit index end any MD&A;
    - none of these when the line is a contents entry (a page number at its end or on the next line) or names its own place ("… of this report", "… in the 2015 Annual Report").
  - The MD&A-heading path (`extract_exhibit_mda`):
    - ends at the signatures, the exhibit index, a titled Part II item or "Item <n>. Exhibits";
    - an MD&A heading followed by a bare page number is a contents entry, not a start (Entergy, AEP, Aetna).
  - Checked first on the 408 marker rows, then on every cached row. Reading the changes found five defects in my own rules, each fixed test-first:
    - Waste Management prints "Index to Financial Statements" in every page header, so that end was dropped;
    - AIG's wrapped "… Retirement and Part II," / "Item 1A. Risk Factors.": a line ending in a comma goes on with its sentence;
    - AIG's "Part II, Item 7. MD&A — … in the 2015 Annual Report": the 10-Q "Part II" end now skips a line naming another document;
    - Everest's word-per-line "under" / "ITEM" / "8" / "of": a previous line that is just such a word counts;
    - CSC's "PART I, ITEM 2." heading had come through the MD&A-heading path, so its Part II items did not end it.
  - Two existing tests were adjusted (Rule 1.6):
    - `test_skip_items_takes_the_mda_heading_after_an_item_7_pointer`: its incidental "Item 15. Exhibits" line now (rightly) ends the Item 7 section. The line became "Item 9B. Other Information", which keeps the test's point, and an added assertion pins the new end.
    - `test_a_10q_mda_before_its_financial_statements_ends_at_item_1`: its "Item 1A. Risk Factors" line is a contents entry by its docstring, so it got its page number ("… 95"). BNY Mellon's real 10-Q has no such heading inside the MD&A (checked).
- K3: see [mutation-summary.md › Follow-up 5](artifacts/mutation/mutation-summary.md). 70 of the 83 old-line survivors are killed by 9 new tests; the 13 left are equivalent, each with its reason ([followup5-old-lines-classified.tsv](artifacts/mutation/followup5-old-lines-classified.tsv)). No production code changed for them.
- K4, OKTA and VYLR, on the scratch copy (`main_lake_followup5.py copy` → 1,049.9 MB, 2,405 raw links; `before` digests):
  - `atlas data sync --from 2015-01-01 --to 2026-10-30 --datasets prices,fundamentals --symbols OKTA,VYLR`: prices 2,405 rows (OKTA from its 2017-04-07 IPO; VYLR's 22 bars were checkpointed already); fundamentals 1,957 rows (OKTA). VYLR's fundamentals were skipped: its checkpoint said "empty" from 2026-11-01, before its first 10-Q (2026-11-12).
  - `atlas data sync --from 2015-01-01 --to 2026-11-23 --datasets fundamentals --symbols OKTA,VYLR` → 2,029 rows, both ok (VYLR 72 facts).
  - `new`: OKTA's trade dates set from `prices_daily` (2017-04-07 … 2026-10-30; the universe step was not run, it would apply today's index changes). Then `ingest_earnings` → 42 events (OKTA 41, VYLR 1), and `ingest_earnings_reactions` → 41 (VYLR's 10-Q of 2026-11-12 is after the last price day).
  - Rebuild ([main-lake-rebuild-followup5.txt](artifacts/main-lake-rebuild-followup5.txt)): `atlas factors build --from 2015 --to 2026` exit 0 (24 min); `atlas risk-model build --from 2017 --to 2026 --cached-factors` exit 0 (39 min).
  - Store comparison ([main-lake-stores-compare-followup5.txt](artifacts/main-lake-stores-compare-followup5.txt)): the same row counts. Raw exposures changed only for OKTA (12 values filled at 2026-10-30). Other names' standardised values moved on that date alone (5,864 rows, median 0.0002). Factor returns and risk snapshots changed only on 2026-10-30.
- K2, final rounds on the whole corpus (every cached row, then all 30,927):
  - More defects in my own rules, each fixed test-first:
    - Mastercard's "Special" / "Items 3" table column: end headings are singular again;
    - General Mills' word-per-line "… in" / "Part" / "II,": a line ending in a comma of any length goes on with its sentence;
    - Morgan Stanley's start at "… in Part II," / "Item 7." / "Competition.": a start that goes on with a sentence is no heading;
    - Clorox's named "Quantitative and Qualitative Disclosures …" title: a title that is itself an end heading starts nothing;
    - Southern Company's 10-Qs: "PART II — OTHER INFORMATION" ends the MD&A-heading path.
  - A hidden regression of the current code: Sempra's 2015–17 10-Ks ("set forth under “Management's Discussion …” in the Annual Report") came out empty under HEAD. Fixed test-first: an item naming its sections reads the exhibits when the same document holds none of them.
  - **My harness defect:** for the 162 rows whose MD&A came from an exhibit, the lake's `document` is the exhibit's name, and my script passed it as the primary document. The texts were equal, but the source would have read "primary". The script now takes the primary document from the filing index; those rows were re-run online for HEAD and the final code.
  - Final: HEAD differs from 131 stored rows (stale); the final code differs from 533 (all 131 among them), 0 errors, 30,927 with an MD&A. Every kind was reviewed. Over-run markers fall from 181 rows to 14, all cross-references ([filing-text-full-recheck-followup5.txt](artifacts/filing-text-full-recheck-followup5.txt)). Review scripts: `filing_text_{scan,markers,compare}_followup5.py`.
- K5, main lake ([main-lake-followup5.txt](artifacts/main-lake-followup5.txt)):
  - `atlas data sync --datasets filings --symbols OKTA,VYLR --to 2026-11-23` → 0 new filings.
  - `text` → 533 rows replaced (22 from EX-13).
  - `check`: outside `filing_text` only OKTA and VYLR changed; in `filing_text` exactly the 533 rows. My script first showed `instruments`' one-column key as "O"; fixed and re-run.
  - Going-concern review ([filing-text-review-followup5.txt](artifacts/filing-text-review-followup5.txt)): 76 FLAGs, the same 76 as follow-up 4.
  - The longest MD&As left are Southern Company's combined one (962k) and the big banks'; none carries a marker.
  - Swap from the owner's Terminal panel ([main_lake_followup5_swap.sh](artifacts/main_lake_followup5_swap.sh), [main-lake-swap-followup5.txt](artifacts/main-lake-swap-followup5.txt)):
    - 9 tables backed up (digest-checked) in `cache/backup-stage29-followup5-2026-11-23` and replaced, each equal to the scratch copy;
    - `sync_state.json` replaced (old one in the backup);
    - raw/sec: 2 files added, 3 replaced.
  - Read-back ([main-lake-readback-followup5.txt](artifacts/main-lake-readback-followup5.txt)): row counts and `_meta.json` tables equal; 9 `DataView` cases as expected (OKTA revenue TTM $3.07B, VYLR's 10-Q MD&A, EMR EX-13 56,539, CMA 168,720, SEDG 75,160, FCX, AIG; WIN and ENPH FLAG).
- K6:
  - Mutation of the changed functions: 425 of 440 detected after 3 more tests; 15 equivalent ([mutation-summary.md](artifacts/mutation/mutation-summary.md)).
  - Docs: `docs/data-limitations.md` item 9.
  - Records: `PROGRESS.md` (last updated, Decisions log, Backlog, Known gaps, handoff), `verification.md`, `audit-report.md › Follow-up 5`.
  - Verification on the final code ([verification.md › Follow-up 5](verification.md)): brief's Verify 29 passed; `uv run pytest -q` 5563 passed, 82 deselected; `make check` exit 0; `make test-integration` 48 passed, 15 skipped.
- Code files changed (none created): `src/atlas/data/filings/mda.py`, `src/atlas/data/filings/text.py`.
- Test files changed: `tests/unit/data/filings/test_filings_{mda,ingest}.py`, `tests/unit/data/fundamentals/test_fundamentals_ingest.py`, `tests/unit/data/events/test_events_earnings.py`.
- Commit and merge: committed `4b2d460` `stage-29: follow-up 5 (...)` after reviewing the staged list (47 files, all under `Planning/`, `src/`, `tests/`, `docs/`). A scan of the staged diff found none of the `.env` values and no home or temp path. The one email address is the commit author's, in GitHub's CI record `ci-runs-3d77679.json` (as in the four CI records already committed). Hooks passed. Fast-forwarded `main` 3d77679 → 4b2d460 in the owner's Terminal panel ([followup5-merged-main-check.txt](artifacts/followup5-merged-main-check.txt)), trees equal. Merged result: `uv run pytest -q` → 5563 passed, 82 deselected, exit 0; `make check` → exit 0; `git branch --merged main` lists the stage branch. This records commit goes onto `main` the same way; then both branches are pushed and CI is checked.

## 2026-11-25 — Follow-up 6: prices up to today (owner: "Sync prices to today")

- Asked "can you complete the work that would move the performance of the system?". I explained that the work that could move performance belongs to later stages or needs paid data, which Rule 4 holds back: Stage 35 (analysts into the trading graph), Stages 39/58 (pairs edge after costs, the out-of-sample test, which would use up the held-out years), and delisted names' prices. The owner chose "Sync prices to today". [Plan](plan.md) section "Follow-up 6" and the Decisions log entry were written first.
- CI of follow-up 5's records commit `3195aff`: success on `main` and the branch (GitHub API, 2026-11-23 22:28Z).
- L1: `main_lake_followup6.py copy` → `$TMPDIR/s29f6/main-copy`, 1,045.2 MB.
- L2: `atlas data sync --datasets prices` on the copy → 778 keys: 627 ok, 88 empty (former members), 63 skipped by checkpoint, 0 failed; 13,928 rows in 55 min. Yahoo's cookie fetch failed throughout and the downloads went on without it, which is slow but works.
- **Defect found:** DHI, DHR and DIS (current members) got "no price data found" from Yahoo in that run, yet the prices step checkpointed them as synced to 2026-11-24, so a later sync would have skipped them. Their checkpoints were put back to the main lake's (2026-10-30) and `atlas data sync --datasets prices --symbols DHI,DHR,DIS` re-run → 3 ok, 67 rows. The checkpoint rule is Stage 14 code, so it is recorded in the Backlog, not changed here.
- L3: `dates` → every instrument's trade dates from `prices_daily`.
- L4 `check` ([main-lake-followup6.txt](artifacts/main-lake-followup6.txt)):
  - prices: +10,675 bars, 2026-11-02 … 2026-11-24 (17 sessions, 629 symbols), none removed. The 831 changed bars are the sync's 5-session overlap (2026-10-26 … 30): `adj_close` of 154 names that went ex-dividend since (not point-in-time, Stage 07 Backlog), plus Yahoo's revised volume (71) and high (2); open and close are unchanged.
  - corporate actions: +155, none changed.
  - instruments: only `last_trade_date` (629 rows, null-aware comparison).
  - All 503 current members have bars to 2026-11-24.
- L5: swap from the owner's Terminal panel ([main_lake_followup6_swap.sh](artifacts/main_lake_followup6_swap.sh), [main-lake-swap-followup6.txt](artifacts/main-lake-swap-followup6.txt)): 3 tables backed up in `cache/backup-stage29-followup6-2026-11-25` and replaced, each equal to the scratch copy; `sync_state.json` replaced; swap exit 0. Read-back ([main-lake-readback-followup6.txt](artifacts/main-lake-readback-followup6.txt)): row counts and `_meta.json` tables equal; `DataView` as of 2026-11-25 gives bars to 2026-11-24 for AAPL, SPY, OKTA, VYLR, DHI, DHR and DIS.
- Not done here (Backlog): fundamentals, events, filings and news since 2026-11-01; the factor and risk stores' sessions after 2026-10-30 (the next month-end build is 2026-11-30).
- No code changed. A stray `push-f5.txt` left in the worktree by follow-up 5 was removed (never committed).
- Commit and merge:
  - Committed `940a3fc` `stage-29: follow-up 6 (main-lake prices up to 2026-11-24)` after reviewing the staged diff: 10 files, all under `Planning/`; no `.env` value, home or temp path, or email address. Hooks passed.
  - The first fast-forward was refused: `main` had moved on (`be8c31d`, `83c91c1`: Project.md Rules 7 and 8, Rule 6A.7 corrected). I read the new rules and merged `main` into the branch (`98c7671`). The only conflict was in the Decisions log, where both sides had added rows; all rows were kept, in date order.
  - Fast-forwarded `main` 83c91c1 → 98c7671 in the owner's Terminal panel, trees equal ([followup6-merged-main-check.txt](artifacts/followup6-merged-main-check.txt)). Merged result: `uv run pytest -q` → 5563 passed, 82 deselected, exit 0; `make check` → exit 0; `git branch --merged main` lists the branch.
  - Pushed `main` and the branch at `98c7671`; `git status -sb` → `## main...origin/main`. CI on `98c7671` (full SHA): `main` run 45628846076 success, branch run 45628845883 success ([ci-runs-98c7671.json](artifacts/ci-runs-98c7671.json)).
  - This records commit goes onto `main` and `origin` the same way; its CI result is reported in the reply (Rule 6A.7).

## 2026-11-25 — Follow-up 7: the full data sync (owner: "run the full data sync for the rest")

- Started in a new session from [handoffs/handoff-1.md](handoffs/handoff-1.md) (Project.md Rule 7). The session that did follow-ups 5 and 6 was close to a full context.
- Rule 0: read `Project.md` (Rules 7 and 8 new), `CLAUDE.md`, `PROGRESS.md` and the hand-off.
- State as stated:
  - clean tree; HEAD = `main` = `origin/main` = `f800722`;
  - this session runs in its own worktree, branch `claude/exciting-faraday-bba38a`, cut from `f800722`. The old worktree is not writable from this session, so follow-up 7 is committed on this branch and fast-forwarded into `main` as before;
  - the worktree's venv was installed with `uv sync --group dev`.
- [Plan](plan.md) section "Follow-up 7" (M1–M8) and the Decisions log row written first; no code change is planned.
- M1: `main_lake_followup7.py copy` → `$TMPDIR/s29f7/main-copy`, 1,244.1 MB. It includes `news` and `news_sentiment` this time; 2,407 raw links.
- M2: `atlas data sync` on the copy, every dataset, to today, started 2026-11-25T14:57Z in the background. Allowed hosts: Wikipedia, SEC EDGAR, Yahoo, FRED, Alpaca news, and the lexicon host. Social posts are off in `config/fund.yaml`.
- **Gate:** `uv run pytest -q` → 5563 passed, 82 deselected, exit 0 ([followup7-gate-pytest.txt](artifacts/followup7-gate-pytest.txt)). CI on `f800722` (full SHA): `main` 45637903881 success, branch `claude/stage-29-followup-5-235c7b` 45637903307 success.
- **Sync run 1** (raw log kept as `$TMPDIR/s29f7/sync-run1.txt`):
  - universe ok (no membership change since 2026-11-01: 880 rows equal);
  - prices: all 778 skipped as current to 2026-11-24 (today's bar was not published yet);
  - fundamentals 746 ok, 2 empty; filings 745 ok, 3 empty, 27 rows.
  - **The macro step crashed:** the copy's `raw/fred` was one link to the main folder, which the sandbox cannot write. Fixed in `main_lake_followup7.py` (`link_fred`: per-file links, as `raw/sec`). The resumed run: macro 22 ok, news 778 ok (9,764 rows), earnings 746 ok, upcoming 503 ok, macro calendar ok, reactions ok, quality ok.
  - **The news-sentiment step failed:** this worktree has no `data/reference/lm_lexicon.csv`, and the download from Google Drive answered "Quota exceeded" (an HTML page). The code read that page as a CSV and refused it ("lacks columns"), so it failed safe but with a misleading message (Backlog). Re-run with `ATLAS__DATA__SENTIMENT__LEXICON_FILE` set to the main checkout's file (the one the main lake's sentiment was built with since 2026-10-29) → ok.
  - **M3, members:** SNDK's bars stop at 2026-11-19, yet its price checkpoint (set in follow-up 6) said 2026-11-24. This is the same checkpoint defect, which follow-up 6's check ("a bar after 2026-10-30") missed. A retry with its checkpoint reset stored nothing new: Yahoo's bars after SNDK's 8-for-1 split (2026-11-20) lack values (11-20, 11-24) or are missing (11-23). Its checkpoint is kept at 2026-11-19 so the next sync tries again.
  - **M3, members (upstream gap):** 24 current members have no fundamentals fact filed since 2026-08-01 (8 none since 2026-05-01, Citigroup none since 2026-02-20): AEP, C, CMS, CNP, D, DLR, DTE, DUK, ESS, ETR, EVRG, EXC, FE, FRT, LNT, MAA, NEE, PCG, PLD, PPL, REG, SO, SRE, VMRK. Their 10-Qs are filed (in `filing_text`), but SEC's own `companyfacts` answer, fetched fresh, stops there: Citigroup's us-gaap facts end at 2026-02-20, and only its filing-fee taxonomy (`ffd`) is current. This is an upstream gap, already in the main lake; a sync cannot fill it (Backlog: read the filings' own XBRL).
  - **M4 check of run 1 found a point-in-time regression:** 10,573 stored facts in 178 old filings lost their acceptance time. Cause: SEC rewrote the history page (Agilent's `-001`: 1,364 filings archived, 1,382 now), and `EdgarClient.submissions_page` reused the archived page. Not swapped in.
- **M2b, Stage 09 client fix** (test-first; the new tests failed on the old code, 7 of 8, and pass on the new):
  - `submissions_page(name, entry)` fetches a cached page again when it no longer matches its index entry (`filingCount`, `filingFrom`, `filingTo`). `filing_index`, `filing_list` and `fetch_filings` pass the entry when `refresh=True`; `refresh=False` still reuses every archived answer.
  - Tests in `test_fundamentals_client.py`:
    - `test_a_history_page_sec_rewrote_is_fetched_again`;
    - `test_each_field_of_the_entry_can_show_a_page_is_stale` (3 cases);
    - `test_a_page_without_an_index_entry_is_reused_as_before`;
    - `test_the_filing_index_reads_a_rewritten_page_and_keeps_acceptance_times`;
    - `test_refresh_false_reuses_an_archived_page_even_when_it_looks_stale`.
  - One existing assertion changed (Rule 1.6): `test_refresh_false_reads_the_archived_answers_of_every_cik` counted `refresh=True` as 4 requests "(history pages never change and stay archived)". The recorded fixture pages are trimmed (their README says so) and so never match their index entries; they are now fetched again, so the count is 4 + 2 and the comment says why.
  - Code changed: `src/atlas/data/fundamentals/edgar_client.py`, `src/atlas/data/filings/text.py`, `src/atlas/data/events/earnings.py`.
- **Sync run 2:** a fresh scratch copy, then `atlas data sync` again with the fixed client and the lexicon override, started 2026-11-25 (`$TMPDIR/s29f7/sync.txt`).
- **Run 2 check (fundamentals, while the sync ran):** lost acceptance times went from 10,573 down to 193 (123 facts gained one). The 193 are a second Stage 09 limit: a needed filing whose companyfacts `filed` date falls between two history pages' ranges (Citigroup `0000831001-23-000037`: filed 2023-02-27, in page 016, which ends 2023-02-26). Fixed test-first: `filing_index` reads the other pages when a needed filing is still missing (`test_a_needed_filing_between_two_pages_date_ranges_is_still_found`, red then green; 568 tests of fundamentals/filings/events pass; ruff and mypy clean).
- **Stopped here (usage limit):** sync run 2 was still running in the background on `$TMPDIR/s29f7/main-copy`, with the code from before the page fallback. Next: [handoffs/handoff-2.md](handoffs/handoff-2.md).
