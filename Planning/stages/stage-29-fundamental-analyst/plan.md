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

## Follow-up 3 — the items left open (2026-11-22)

Owner: **"can you help me to complete the tasks that are still being opened?"** These are the open items named in the follow-up 2 report. Graph wiring is not among them: the owner kept it with Stage 35 ("Leave it to Stage 35"; Rule 4).
- **H1, 10-Q MD&As.** `data.filing_text.forms` becomes `["10-K", "10-Q"]`. The owner's request above is the approval to widen the approved spec row (Rule 3, Decisions log).
  - `extract_mda(text, min_chars=, form=)` reads Item 7 of a 10-K, or Part I Item 2 of a 10-Q (ending at Item 3 Quantitative…, Item 4 Controls…, or Part II).
  - `DataView.mda` returns the latest filing whose MD&A was found; only when none was found does it return the latest filing, to name why. The going-concern check therefore reads a 10-Q as soon as it is filed.
  - The check's text says "period ended" (it was "fiscal year ended").
- **H2, the seven companies without an MD&A** (AEP, CLX, MOS, TWC, TWX, WFC, WIN), all `by_reference` today. In order:
  1. Item 7 itself.
  2. The filing's EX-13 and EX-99 exhibits: CLX files its annual report as EX-99.1. "Financial Review" is accepted as an exhibit heading: Wells Fargo's EX-13 titles its MD&A so.
  3. The MD&A heading later in the same primary document, skipping "Item" heading lines (AEP, MOS, TWC, TWX, WIN).
- **H3, the four remaining edge mutants:**
  - the dilution split window reaches the year-ago tolerance band (two mutants);
  - the price split factor is read on the last bar's day;
  - `_window_sessions` uses the view's calendar config.
- **H4, main lake.**
  - In the follow-up 2 scratch lake (identical to the main lake's `filing_text`, with its sync state), delete the rows without an MD&A and run `atlas data sync --datasets filings`: every 10-Q since 2015, the deleted rows, and any new 10-K.
  - Review every both-term sentence again (10-Q MD&As are new text).
  - Swap the table in from the owner's Terminal panel with a backup of the current table, then read it back.
- **H5.** Mutation testing of the changed code, verification, self-audit, records, git. This includes the uncommitted CI record of `1e32337`.

Tasks run in order, test-first: H2 extraction, H1, H3, H4, H5.

### Follow-up 3, H4 review — what the full 10-K and 10-Q data showed (2026-11-22)

The backfill (748 symbols, 30,597 filings) was read before the swap, and the readback after it. Both showed defects in this follow-up's own code, so they are fixed in it, test-first:
- **H6, going concern.** The first review gave 79 FLAGs. Eight were not a company's doubt:
  - a negation ("… does not exist");
  - ASU 2014-15 wording without the standard's name;
  - a definition ("the borrower's ability …");
  - a fragment of a sentence cut by a line break.

  Two more quoted a sentence from its middle (cut after "Inc." or by a line break).
  - Fix: a piece starting with a lower-case letter goes on with the sentence before it.
  - New exclusions: "does not exist", "provide certain disclosure", "borrower's".
- **H7, extraction.**
  - **Part II Item 2.** A bare "Item 2." line is a 10-Q's MD&A heading only when "Management…" follows on the same or the next line. 5,732 10-Qs had taken the contents entry of Part II Item 2 ("Unregistered Sales …"), which ran through the financial statements to Part I Item 3.
  - **Pointers.** An MD&A shorter than `data.filing_text.short_mda_chars` (15,000) gives way to a longer section under a later MD&A heading of the same document (Southern Company's combined MD&A). Failing that, when it points to the annual report, it gives way to a longer exhibit MD&A (TSS, Sempra, Emerson).
  - **Headings.** Entergy's "MANAGEMENT'S FINANCIAL DISCUSSION AND ANALYSIS" is an MD&A heading, and "condensed" statements end an MD&A.
- **H8, re-ingest.** Only the rows those fixes can change are fetched again (7,065 filings): the bare-Item-2 10-Qs, MD&As under 15,000 characters, rows without an MD&A, and rows from an exhibit or the heading fallback. A random control sample of 200 untouched rows is extracted again with the new code to show they do not change. Then the review, the swap and the readback are repeated.
- **H9, found by the re-ingest's comparison with round 1.** Of 30,597 rows, two 10-Qs lost their MD&A:
  - A. O. Smith (2018): rightly, because round 1 had taken the Part II contents entry.
  - Roper (2017-11): wrongly. It heads its MD&A "MANAGMENT'S DISCUSSION", and since H7 a bare "Item 2." needs the word after it.

  The fix: the Item 2 and Item 7 headings accept "Managment" (test-first). The rows it could change are then extracted again with the final code and compared with the table, and any row that differs is fetched again before the swap:
  - every row without an MD&A;
  - every row whose text holds "managment";
  - a fresh random sample of 300 rows.
- **Not fixed here (Backlog):**
  - A whole-table `Lake.read_table("filing_text")` overflows Arrow's 2 GB string offsets. Every reader in the code reads one symbol at a time.
  - A ticker whose issuer moved to a new CIK (ExxonMobil, 2026) has only the new CIK's filings and fundamentals (Stage 09's `CikMap`).
  - GE's 10-Q MD&A sits under no heading the extraction reads, so its contents entry is stored.

## Follow-up 4 — the last open items (2026-11-23)

Owner: "can you complete the still open ones and complete them?" The open items, from the Backlog and the follow-up 3 audit:
- Bank of New York Mellon and Northern Trust 10-Q MD&As under no heading read here (70 filings);
- GE's 10-Q contents entries;
- Windstream's 2019 going-concern quote, which starts at a page header;
- ExxonMobil read under its 2026 CIK only (Stage 09's `CikMap`);
- the whole-table `filing_text` read overflowing Arrow's string offsets (Stage 06's `Lake.read_table`);
- the uncommitted CI record `ci-runs-c579104.json`.

Graph wiring stays with Stage 35 (owner, follow-up 2: "Leave it to Stage 35").

The Stage 06 and Stage 09 items change code of done stages. The owner's request is the authorization; it is recorded in the Decisions log. No spec change: `config/cik_overrides.yaml` gains optional keys; the design spec does not describe it.

The items were investigated first: every main-lake filing without an MD&A or with one under 15,000 characters (156) was fetched again and read by hand, and the symbols whose fundamentals start long after their S&P 500 window were listed. Both showed more than the Backlog said, so this plan covers the whole of what was found. Each change is test-first.

- **J1, whole-table read (Stage 06).** `Lake.read_table` sorts by the key columns alone and takes every column in its 64-bit-offset form (`large_string`, ...), so a text column past 2 GB no longer overflows. Evidence: a whole-table read of the main lake's `filing_text`.
- **J2, MD&A headings.** The 156 filings were read; the rules below come from them.
  - 10-Q headings: "Items 2. and 3." (BNY Mellon, Northern Trust), "ITEM" on its own line (A. O. Smith), "MANGEMENT'S" (Gardner Denver).
  - A 10-Q MD&A ends at a later Part I Item 1 (statements after the MD&A).
  - Exhibit and fallback headings: a heading run into its first paragraph and marked "(MD&A)" or ", or MD&A," (GE, JPMorgan, Avery Dennison).
  - Ends: an upper-case "FINANCIAL STATEMENTS" line (GE) and "Report of Management" (U.S. Bancorp) end an MD&A. A statement line ending in "Information", "Analysis", "Overview", "Review", "Data", "Summary", "Highlights" or "Discussion" does not (Synchrony, JPMorgan, PNC).
  - Ends that are no heading: a link to part of an item ("Item 8. … – Note 5", Noble Energy), or a line going on with the sentence before it ("… included under" / "Item 8.", Loews; PNC).
  - More wordings of "incorporated … by reference" (U.S. Bancorp, Total System Services), so their annual-report exhibits are read.
  - A later MD&A heading must follow the short MD&A, not sit inside it (Texas Instruments).
- **J3, going-concern quote.** A page number or header between the halves of a sentence is left out (Windstream).
- **J4, former CIKs (Stage 09).** 16 members filed under another CIK before:
  - holding companies or new parents: ExxonMobil, BlackRock, APA, Disney, Cigna, Broadcom, Alphabet (GOOG, GOOGL), DuPont, Linde, TechnipFMC, L3, Bunge, Paramount (SKYD), Viatris;
  - SanDisk's reused ticker.

  `CikOverride` gains `former` (CIK, `until`, SEC name) and `since`. `CikMap.sources` gives each symbol's CIKs with their filing dates. The fundamentals, filing-text and earnings ingests read every source over its dates, and a co-registrants' filing is read once. Each pair is checked on EDGAR (`former_ciks_followup4.py`). Twenty-First Century Fox (TFCF, TFCFA), never mapped, gets its CIK.
- **J5, main lake.**
  - On a scratch copy (`main_lake_followup4.py`), re-ingest the former-CIK symbols: facts, SEC events, reactions, MD&As.
  - Re-extract with the new code every stored MD&A row the new rules can change (1,816, found from the stored text), plus 600 random others as a control. Fetch every changed row again.
  - Check every table against the main lake, then rebuild the factor and risk stores.
  - Swap from the owner's Terminal panel with backups, then read back.
- **J6.** Mutation testing of the changed functions, the review of every going-concern FLAG, verification, self-audit, records, git (with the CI record of `c579104`).

## Follow-up 5 — the gaps follow-up 4 left (2026-11-23)

Task (from the session that started this one, on the owner's behalf): close every gap Stage 29 still has open, recorded as follow-up 5. Confirmed against `PROGRESS.md › Backlog` and the Stage 29 "Known gaps":
- Emerson's FY2015 10-K stores its 4,539-character Item 7 pointer page;
- MD&As whose filer prints no end heading run past their end (SolarEdge 2024);
- 83 surviving mutants on old Stage 06/09/12 lines;
- OKTA and VYLR have no fundamentals;
- the CI record `ci-runs-3d77679.json` of follow-up 4 is uncommitted.

Graph wiring stays with Stage 35 (owner). Gate: `uv run pytest -q` → 5472 passed, 82 deselected ([followup5-gate-pytest.txt](artifacts/followup5-gate-pytest.txt)).

Investigation before this plan:
- A scan of every stored MD&A for text that only follows an MD&A (signatures, "Pursuant to the requirements of …", Items 9A–16, Part III/IV, a 10-Q's Items 5/6, exhibit index, notes, certifications) found 408 rows. Re-extracting them with the current code changed 83, so **some stored rows predate the current rules**. Among them Comerica's 10-Ks (11) come out with no MD&A under the current code: their stored text was the 10-K's contents and Part I, not the MD&A, and the current rules rightly reject it but find nothing else.
- Comerica's Item 7 says "Reference is made to the sections entitled “2014 Overview …,” “Results of Operations,” …"; Emerson's FY2015 Item 7 says "set forth in Exhibit 13 hereto under “Results of Operations,” … is hereby incorporated by reference". The MD&A item names the sections that make up the MD&A.
- SolarEdge's 2024 10-K breaks "ITEM" and its number onto two lines ("ITEM" / "7A. Quantitative …"), so no end heading is seen.

So this follow-up re-extracts **every** stored filing, not only pattern-found candidates.

- **K0, full re-extraction.** `filing_text_full_recheck_followup5.py` re-reads all 30,927 stored filings from EDGAR with the current code and keeps each document's text gzipped (the text conversion does not change here), so every later rule change is checked offline on the whole corpus. Each kind of difference is reviewed.
- **K1, the sections the item names (Emerson, Comerica).** `named_sections` takes the quoted titles from the lines of a short or missing MD&A item that point elsewhere ("incorporated … by reference", "Reference is made to", "set forth … under", Exhibit 13). `extract_titled_mda` reads the MD&A from the first such title standing as a heading (not a contents entry) to the usual annual-report end (statements, auditor's report, …). `_mda_of` uses it only where the present rules find nothing longer: in the same document after the item, and in the EX-13/EX-99 exhibits. Test-first, with Emerson's and Comerica's layouts.
- **K2, MD&As that run past their end.** From the full re-extraction: every row whose MD&A holds text that only follows an MD&A, plus unusually long ones. Candidate rules, each test-first and kept only if the review shows it right:
  - "ITEM" on a line of its own before the number in 10-K headings (as the 10-Q rule already allows);
  - a safe end for every MD&A: a "SIGNATURES" heading or "Pursuant to the requirements of the Securities Exchange Act" (never inside an MD&A; not a contents entry followed by a page number);
  - a 10-K's later items (9, 9A–9C, 10–16) and Part III/IV headings, and a 10-Q's Part II Items 5 and 6, titled, end the MD&A.

  The follow-up 4 rules stay. A rule that changes rows the review finds wrong is narrowed or dropped.
- **K3, the 83 old-line mutants.** Re-run mutmut 3.8.1 (scratch install) over the same functions with the current tests. Then, for each survivor: kill it with a real test (exact failure reasons and messages, the summary log events via `structlog.testing.capture_logs`, the report counters over two symbols, the `refresh` default, the config's calendar and concept map), or show in writing why it is equivalent. Code is never weakened to kill a mutant. The decision is logged under Stage 29 in the Decisions log.
- **K4, OKTA and VYLR.** On the scratch copy: `atlas data sync --datasets prices,fundamentals,filings --symbols OKTA,VYLR --to 2026-10-30`, which is the main lake's last price day. OKTA has no prices at all (its membership starts that day), and the factor store needs them. Then `ingest_earnings` and `ingest_earnings_reactions` for the two symbols: the sync's events steps without the market-wide macro calendar.
- **K5, main lake.**
  - On a scratch copy (`main_lake_followup5.py`): K4, then every `filing_text` row whose final extraction differs replaced from the text cache.
  - Row-by-row check against the main lake: only intended rows change.
  - Factor and risk stores rebuilt and compared.
  - Swap from the owner's Terminal panel with a dated backup under `<lake>/cache/` (`main_lake_followup5_swap.sh`), then read back (`main_lake_readback_followup5.py`).
  - Going-concern review of every MD&A: the 76 FLAGs rechecked, any new or lost FLAG read.
- **K6.** Mutation testing of the changed functions, verification (`uv run pytest -q`, `make check`, `make test-integration`, the brief's Verify), self-audit, records (`plan.md`, `worklog.md`, `verification.md`, `audit-report.md`, `mutation-summary.md`, `PROGRESS.md`, `docs/data-limitations.md`), commit with the CI record of `3d77679`, fast-forward `main` from the Terminal panel, merged-`main` checks, push, CI.
