# Stage 29 — Fundamental analyst agent · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** IN PROGRESS
- **Gate check:** Stage 28 is DONE (its [verification.md](../stage-28-langgraph-skeleton/verification.md) says ALL CRITERIA VERIFIED); `uv run pytest -q` → 5034 passed, 81 deselected, exit 0 (raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)) (2026-11-18)
- **How:** planned and executed in one go, inline TDD (owner: "please plan, execute and audit stage 29"; standing preference: no review pause), then a self-audit recorded in `artifacts/audit-report.md`.

## Collisions with the overall plan
None found. Roadmap row 29 (depends on 16, 28), spec §6 (Fundamental Analyst: LLM + tools, quick tier, `AnalystReport`), §7 (`AnalystReport` contract unchanged), §12 layout (`tests/fixtures/`). The brief's fixture note was approved by the owner on 2026-11-17 (Decisions log).

## Decisions

- **D1 Layout.** New subpackage `atlas.agents.research` (`fundamental.py`: `FundamentalAnalyst`); tools in `atlas.agents.tools.fundamental_tools` (the tools package says "one `<name>_tools.py` per analyst" from Stage 29); prompt `agents/prompts/fundamental_analyst.j2` v1. The tools module imports only `tools/registry.py` (its own symbol args, as the registry's docstring asks); the agent module joins them with the built-in tools (`fundamental_registry()`). `atlas.agents.tools` keeps its public names (the package test pins them): the module is imported by path.
- **D2 One evidence item per citable fact group.** Tools return `Evidence` with stable ids so every number the model writes has an id next to it: `fsum:<SYM>:<latest period end>:<concept|margins|growth>`, `frank:<SYM>:<day>:<group>`, `val:<SYM>:<day>:<multiple|market>`, `rflag:<SYM>:<day>:<check>`.
- **D3 Evidence time.** Items built from filings only (`fsum:`) carry the latest `available_at` of the rows they show; items computed from prices and a cross-section (`frank:`, `val:`, `rflag:`) carry the decision cutoff minus 1 µs (the `FactorStore` convention: computed at the decision moment from data known then). Both pass `guard_evidence`.
- **D4 Factor ranks vs sector.** `factor_panel(view, peers)` over the symbol's sector peers (members of `quant.factors.universe` at the as-of with the same GICS sector, plus the symbol): a twelfth of the full-universe cost and exactly the cross-section a sector percentile needs. Percentile = mid-rank share of the peers with a value (100 = most of the style). Groups: value, quality, growth (the brief) and leverage (feeds the balance-sheet signal). A group or factor with fewer than `min_sector_names` values is "not ranked".
- **D5 Valuation.** Multiples P/E, P/S, P/B, EV/EBITDA and FCF yield. Now: the panel's market cap / EV (latest close × cover-page shares) over the panel's TTM / latest balances. Own history: at each fiscal quarter end of the last `valuation_years` (TTM from `fundamentals_history`), raw close of the last session on or before it × its split factor × the split-adjusted cover-page count dated nearest the quarter end (so a split in the window cancels). A negative or zero denominator gives "n/m" (never a guess). Sector median over peers with a value.
- **D6 Red flags** (deterministic, thresholds in `config/fund.yaml` `agents.fundamental.red_flags`): accruals spike, receivables growth >> revenue growth, negative FCF with rising debt, dilution (split-adjusted), going concern. Each check is one evidence item with status FLAG / CLEAR / NOT EVALUATED and its numbers. Receivables need a new concept `accounts_receivable` in `config/xbrl_map.yaml` (tags `AccountsReceivableNetCurrent`, `ReceivablesNetCurrent`). The lake holds no MD&A text: the going-concern rule is a pure text scan (`going_concern_hits`) and the tool reports it NOT EVALUATED.
- **D7 Prefetch plus tools.** `gather` runs the four tools for `inputs["symbol"]` (one LLM round in the normal case: cheaper, reproducible) and the four tools stay offered (for a peer). A `DataError` from the summary (blocked symbol, no filings) fails the run (no report, no new risk); one from another tool becomes an evidence item `gap:<SYM>:<day>:<tool>` in the data block (what is missing and why; citable, and no per-run state is needed to carry it to the prompt).
- **D8 Report checks (`check_output`).** `ticker`, `as_of`, `analyst` match the run; exactly the four signals `quality`, `valuation`, `growth`, `balance_sheet`; the summary has the six labelled sections in order; every sentence of the summary and every risk that states a number (ISO dates and tags aside) carries an `[E:<id>]` tag. Problems get the framework's one repair turn; left-overs make the report `low_quality`.
- **D9 Memory.** The prompt shows the analyst's recalled memories for the symbol (`ctx.recall`; none without a memory), so lessons from Stage 54 reach it without a prompt change.
- **D10 `atlas data slice --symbols … --as-of … --out DIR [--since DATE] [--tables …]`.** `atlas.data.slice.slice_lake` copies, read-only on the source, the rows of `symbols` known at the as-of (`available_at` before the cutoff; static tables whole for those symbols) from every table with a `symbol` column (or the named `--tables`; a table without one is copied whole), dated on or after `--since` where the table has a date key, into a new lake at `DIR`, plus `slice.json` (as-of, symbols, since, tables and row counts; no source path). Refuses a non-empty `DIR`.
- **D11 Recorded fixture** `tests/fixtures/recorded/stage-29-fundamental-aapl-2024-05-01/`: `lake/` (the slice: AAPL and its Information Technology peers), `llm_cache.jsonl` (`atlas llm cache export`), `expected_report.json`, `README.md` (command, date, model, cost). The main lake has no `accounts_receivable` rows yet (only mapped tags are stored), so the slice's `fundamentals_facts` are re-parsed from the main lake's raw SEC archive with the new concept map (offline, `refresh=False`) before the run; the live run then reads the slice itself (a scratch copy), so the replay reads exactly what the run read. A check compares the tool outputs on the main lake and on the slice (equal except the receivables check).

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

### Task 1 — Config and concept for the tools
- Files: modify `src/atlas/core/config.py` (`FundamentalAnalystConfig`, `RedFlagsConfig` under `AgentsConfig.fundamental`), `config/fund.yaml`, `config/xbrl_map.yaml` (`accounts_receivable`); test `tests/unit/agents/research/test_fundamental_config.py`
- [ ] Failing tests: defaults load from `config/fund.yaml`; bounds rejected; `accounts_receivable` resolves `AccountsReceivableNetCurrent` / `ReceivablesNetCurrent` as a stock
- [ ] Run → FAIL (no such config / concept)
- [ ] Implement; run → PASS; run the config and fundamentals mapping test modules → PASS (update any test that lists every concept)

### Task 2 — Red-flag rules (pure, synthetic cases)
- Files: create `src/atlas/agents/tools/fundamental_tools.py` (rule functions first); test `tests/unit/agents/research/test_fundamental_red_flags.py`
- [ ] Failing tests per rule: flag / clear / not evaluated at and around each threshold (accruals level and jump, receivables excess growth, negative FCF with debt growth, dilution, going-concern terms incl. case and word boundaries)
- [ ] Run → FAIL (module missing)
- [ ] Implement `RedFlag` (check, status, text) and the five rules; run → PASS

### Task 3 — Tools over DataView with evidence ids
- Files: `src/atlas/agents/tools/fundamental_tools.py` (`get_fundamentals_summary`, `get_factor_ranks`, `get_valuation`, `get_red_flags`, `fundamental_tools()`); `src/atlas/agents/tools/__init__.py`; test world `tests/unit/agents/research/fundamental_world.py` (Stage 16 factor world + receivables and red-flag companies, a dirty copy with later filings); tests `test_fundamental_tools.py`, `test_fundamental_tools_pit.py`
- [ ] Failing tests: summary shows the last 8 quarters, TTM, margins and growth equal to hand-computed values; ranks equal to a hand-computed mid-rank percentile within the sector; valuation multiples and own-history medians equal to hand computation, a split inside the window cancels, sector medians; red-flag statuses on the world's companies; every id taggable and stable; the registry serves all four (bad args, blocked symbol → `ERROR:` text); later filings on the dirty lake never appear and no `LookAheadError`
- [ ] Run → FAIL
- [ ] Implement; run → PASS; tools package public API test updated

### Task 4 — Agent and prompt (FakeLLM golden tests)
- Files: `src/atlas/agents/research/__init__.py`, `src/atlas/agents/research/fundamental.py`, `src/atlas/agents/prompts/fundamental_analyst.j2`; tests `tests/unit/agents/research/test_fundamental.py` (+ golden prompt file `golden/fundamental_analyst_prompt.txt`)
- [ ] Failing tests: the rendered prompt equals the golden file (sections, rules, data block); a FakeLLM report validates and cites only shown ids → `ok`, cited evidence returned; `check_output` problems (wrong ticker, missing signal, missing section, uncited number) → one repair turn, fixed → `ok`, not fixed → `low_quality` with confidence capped; a gap from a failing secondary tool is stated in the prompt; a blocked symbol fails the run with a `failed` trace; tool calls for a peer work; prompt version label includes the four tools
- [ ] Run → FAIL
- [ ] Implement; run → PASS

### Task 5 — `atlas data slice`
- Files: create `src/atlas/data/slice.py`; modify `src/atlas/cli/data_cmd.py`; tests `tests/unit/data/test_data_slice.py`, `tests/unit/cli/test_cli_data_slice.py`
- [ ] Failing tests: only the symbols' rows, only rows known at the as-of, `--since` trims dated tables, static tables kept, `--tables` limits, manifest content, refuses a non-empty out dir, source untouched; a `DataView` on the slice gives the same tool evidence as on the full lake for the sliced symbols; CLI exit codes 0 / 2
- [ ] Run → FAIL
- [ ] Implement; run → PASS

### Task 6 — Recorded live fixture and offline replay
- Files: `tests/fixtures/recorded/stage-29-fundamental-aapl-2024-05-01/` (`lake/`, `llm_cache.jsonl`, `expected_report.json`, `README.md`); script `artifacts/record_fixture.py` (stage folder); tests `tests/unit/agents/research/test_fundamental.py::test_the_recorded_aapl_run_replays_offline` (no lake, no key, zero provider calls, same report) and `test_fundamental_live.py` (`-m llm`)
- [ ] Write the replay test first → FAIL (no fixture)
- [ ] Build the slice from the main lake (read-only), re-parse its fundamentals offline, compare tool outputs main lake vs slice (artifact)
- [ ] Live run on DeepSeek (quick tier) against a scratch copy of the slice, cache `read_write`; export the cache; write `expected_report.json` and `README.md`; grep the fixture for secrets
- [ ] Replay test → PASS; `-m llm` live test → PASS (cost recorded)

### Task 7 — Verification, audit, records, git
- [ ] `uv run pytest tests/unit/agents/research/test_fundamental.py -q` (brief's Verify), `uv run pytest -q`, `make check`; raw output to `artifacts/`
- [ ] Coverage of the new modules; self-audit against the brief and the hard rules (`artifacts/audit-report.md`); fix any finding test-first
- [ ] `verification.md`, `PROGRESS.md` (handoff, Decisions log, Backlog: main-lake re-ingest for receivables, budgets measured on a real prompt), commit `stage-29: Fundamental analyst agent`, merge into `main`, re-run checks on the merged result, push and CI

## Follow-up 2 — MD&A text for the going-concern check (2026-11-18)

Owner, asked whether to add a 10-K MD&A text source (a design-spec change, Rule 3): **"Approve the spec change"**. The same answer kept graph wiring with Stage 35 ("Leave it to Stage 35"). The brief's In scope item "going-concern keywords if MD&A is available" is completed by making the MD&A available.

Decisions:
- **E1, source.**
  - The data is 10-K MD&A text from SEC EDGAR (the primary document's Item 7), keyed by filing.
  - `available_at` is the acceptance time; otherwise it is the first session open after the filing date, the rule `fundamentals_facts` uses.
  - When Item 7 only points to Exhibit 13 (the annual report), the MD&A is read from that exhibit. The exhibit is found through the filing's `-index.htm`.
  - Forms are configurable, but only `10-K` is read: 10-Q MD&A (Part I, Item 2) is not. This is a known limit, since a going-concern doubt often appears first in a 10-Q (Chesapeake 2020).
- **E2, storage.**
  - New table `filing_text`, partitioned by symbol, key `(symbol, accn, section)`.
  - Columns: the extracted text with its status (`ok` / `by_reference` / `not_found`) and character count, plus the document's name, URL and SHA-256.
  - The filing documents themselves are not archived under `raw/` (1.5–8 MB of HTML each, about 20 GB for the backfill). The text and the digest are kept instead, and a re-fetch can be checked against the digest.
- **E3, ingest.**
  - `EdgarClient` gains `filing_document` and `filing_documents` (the latter reads the `-index.htm`).
  - `parse_filing_list` reads the submissions tables with `primaryDocument` and `reportDate`.
  - `ingest_filing_text` skips filings already stored, unless `refresh=True`.
  - A new sync dataset `filings` (step `filing_text`) runs it chunked, like `fundamentals`.
  - Config: `data.filing_text` (URL templates, forms, `since`, `min_mda_chars`).
- **E4, read.** `DataView.mda(symbol)`: the latest MD&A row of the symbol known before the cutoff (`_known`, point in time), or `None`. Look-ahead test in the view's PIT suite.
- **E5, the check.**
  - The going-concern check reads the latest 10-K MD&A when its fiscal year ended at most `red_flags.mda_max_age_days` (460) days before the as-of.
  - It stays NOT EVALUATED with the reason named in these cases:
    - no MD&A in the data: the old text, kept exactly so the recorded fixture still replays;
    - a stale 10-K;
    - an MD&A not found in the filing.
  - A FLAG quotes the sentence(s) it rests on.
  - Keyword semantics are settled on the real lake: every hit is reviewed. If negated mentions ("no substantial doubt") are common, the rule matches sentences and skips configured negations.
- **E7, added during F7 (from the real lake).** A 10-K with no "Item 7" heading falls back to its own MD&A heading. These are annual-report layouts with a cross-reference index, from JPMorgan, Bank of America, GE, Chevron, Deere and others: 30 companies had no MD&A found before this. The exhibit heading also accepts "Management Discussion" (IBM). The going-concern rule is settled as in E5:
  - one sentence must hold all of the terms and none of `going_concern_exclusions`;
  - the exclusions cover ASU 2014-15 wording, hypotheticals, other parties and negations;
  - every both-term sentence on the main lake was reviewed before choosing them.
- **E6, main lake.** Backfill the main lake's equities for 10-Ks filed since 2015-01-01. Because the sandbox cannot write the main lake, the backfill runs from the owner's Terminal panel, with a backup of `_meta.json`; then read it back. The recorded fixture is not re-sliced: its replay must stay byte-identical.

Tasks (test-first, in order):
- **F1** — Config: `FilingTextConfig`, `RedFlagsConfig.mda_max_age_days`, sync chunk; `config/fund.yaml`.
- **F2** — `mda.py`: HTML to text; Item 7 / Exhibit 13 extraction, with synthetic HTML cases (table of contents, by reference, missing, Item 7A / 8 end).
- **F3** — `parse_filing_list`, `EdgarClient.filing_document(s)`, `ingest_filing_text` with an `httpx.MockTransport`: idempotent, exhibit fallback, failures per symbol.
- **F4** — Sync dataset `filings` and its step.
- **F5** — `DataView.mda`, point in time, with a look-ahead test.
- **F6** — Going-concern check on the view's MD&A: every branch, exact texts; the fixture replay unchanged.
- **F7** — Live smoke on a few real 10-Ks; main-lake backfill and read-back; every FLAG sentence reviewed and E5 settled.
- **F8** — Docs and design spec (§11 data row), the 16 untested-edge mutants, verification, records, git.
