# Stage 08 — Universe & historical index membership · Verification evidence

Filled in only with output from commands run in this stage's chat (Project.md Rule 1). Full raw outputs are under `artifacts/`; key lines are quoted here.

#### Verification evidence — Stage 08 — 2026-10-27
- Commands run (this chat, worktree `stage-8-planning-implementation-fba07a`, branch `claude/stage-8-planning-implementation-fba07a`, base `c1afe56`):
  - `uv run pytest -q` → **791 passed, 0 failed, 3 deselected** (live: 2 from Stage 07, 1 from Stage 08), exit 0 · raw: [artifacts/pytest.txt](artifacts/pytest.txt) (the nanobind "leaked …" lines at shutdown are the known Stage 06 backlog item; exit code 0)
  - `uv run ruff check .` → All checks passed! · raw: [artifacts/ruff-check.txt](artifacts/ruff-check.txt)
  - `uv run ruff format --check .` → 97 files already formatted · raw: [artifacts/ruff-format.txt](artifacts/ruff-format.txt)
  - `uv run mypy src/atlas` → Success: no issues found in 49 source files · raw: [artifacts/mypy.txt](artifacts/mypy.txt)
  - `uv run pytest tests/unit/data/test_universe.py -q` (brief's Verify) → **55 passed, 1 deselected** · raw: [artifacts/verify-test-universe.txt](artifacts/verify-test-universe.txt); verbose list of the 55: [artifacts/stage-tests-verbose.txt](artifacts/stage-tests-verbose.txt)
  - `uv run pytest tests/unit/data/test_universe.py -v -k acceptance -W error` → 2 passed · raw: [artifacts/acceptance-tests.txt](artifacts/acceptance-tests.txt)
  - `uv run pytest tests/unit/data/test_universe.py -m live -v -rs` (optional live refresh, real Wikipedia) → `test_live_wikipedia_pages_parse PASSED` · raw: [artifacts/live.txt](artifacts/live.txt)
  - `make check` → exit 0 (ruff, format, mypy, 791 passed) · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
  - Docstring check `python artifacts/docstrings.py` → 10 modules, 69 public objects, missing: none · raw: [artifacts/docstrings.txt](artifacts/docstrings.txt)
  - On merged `main` (`5e78327`, fast-forward from `c1afe56` run by the owner; checks read-only against the main checkout): `pytest -q` → 791 passed, 3 deselected; `ruff check` → All checks passed!; `ruff format --check` → 97 files already formatted; `mypy src/atlas` → Success (49 files); brief's Verify → 55 passed; all exit 0; checkout clean before and after · raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt)
  - Audit follow-up (base `5e78327`; fixes A1, A2, A3, A7, A8): `uv run pytest -q` → 797 passed, 3 deselected; `ruff check` → All checks passed!; `ruff format --check` → 97 files already formatted; `mypy src/atlas` → Success (49 files); brief's Verify → 61 passed, 1 deselected (also with `-W error`); `make check` → exit 0; docstrings → none missing; coverage of `atlas.data.reference` / `atlas.data.universe` 100% · raw: [artifacts/followup-*.txt](artifacts/)
  - On merged `main` after the follow-up (`8fba0dd`, fast-forward from `5e78327` run by the owner; read-only): `pytest -q` → 797 passed, 3 deselected; `ruff check` → All checks passed!; `ruff format --check` → 97 files already formatted; `mypy src/atlas` → Success (49 files); brief's Verify → 61 passed; all exit 0; checkout clean before and after · raw: [artifacts/merged-main-followup-check.txt](artifacts/merged-main-followup-check.txt)
  - Gap-closing follow-up (base `2b12e27`; renames vs SEC filings, cross-process cache, mutation checks): `uv run pytest -q` → 799 passed, 3 deselected; `ruff check` → All checks passed!; `ruff format --check` → 97 files already formatted; `mypy src/atlas` → Success (49 files); brief's Verify with `-W error` → 63 passed, 1 deselected; `make check` → exit 0; `-m live` → PASSED; docstrings → none missing; coverage 100% / 100%; mutation checks 9 killed, 1 equivalent survivor · raw: [artifacts/gaps-*.txt](artifacts/), [artifacts/mutation-checks.txt](artifacts/mutation-checks.txt)
  - Gate check at start: `uv run pytest -q` → 727 passed, 2 deselected · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- Acceptance criteria (brief: "`members(date(2019,6,3))` excludes TSLA and includes names later removed; `tradable` uses only data before `as_of`"):
  - [x] `members(date(2019,6,3))` excludes TSLA — evidence: `test_acceptance_members_2019_06_03` (recorded fixtures refreshed into a lake; `"TSLA" not in members`, and TSLA is in on 2020-12-21); also `test_fixture_tsla_added_2020_12_21` (out on 2020-12-18, in on 2020-12-21)
  - [x] … and includes names later removed — evidence: `test_acceptance_members_2019_06_03` (AIV, XEC, CELG, ETFC, CTXS, ATVI present; ≥ 90 members of 2019-06-03 are not in today's list; 500–510 names; FB present as META)
  - [x] `tradable` uses only data before `as_of` — evidence: `test_acceptance_tradable_uses_only_data_before_as_of` (bars on/after `as_of` in the lake change nothing, and the result equals the one from a lake holding only `pit_filter`-known bars), `test_tradable_ignores_bar_of_as_of_day` (the 2024-06-03 bar is ignored at 08:00 and counted at 17:00 ET), `test_tradable_ignores_future_bars`, `test_tradable_on_weekend_uses_friday_bars`
- In-scope items (brief), each with evidence:
  - [x] `instruments` table (symbol, name, asset_class, GICS sector/industry, exchange, first/last trade date, is_etf, shortable default True) — `INSTRUMENTS` in `src/atlas/data/reference.py`; `test_build_instruments_fixture`, `test_instruments_frame_matches_schema`, `test_refresh_writes_tables_and_is_idempotent` (trade dates from prices)
  - [x] S&P 500 membership history from a recorded HTML fixture, rebuilt into `index_membership(symbol, start_date, end_date)` — `tests/fixtures/universe/` (README: revisions 1382178819 and 1379179365); `test_parse_*`, `test_reconstruct_*`, `test_fixture_*`; live refresh optional: `fetch_sp500_pages` + live test passed
  - [x] ETF macro universe from `config/universe.yaml` (30 ETFs) — `test_members_static_universe`, `test_build_instruments_fixture` (30 ETF rows)
  - [x] `Universe.members(as_of, name="sp500")`, `Universe.tradable(as_of, min_price=5, min_adv_usd=20e6, min_history_days=252)` — `test_members_*`, `test_tradable_*`; defaults from `cfg.data.tradable`: `test_tradable_defaults`
  - [x] Ticker change mapping (FB→META etc.) via `config/ticker_changes.yaml` — `test_ticker_map_*`, `test_load_ticker_map_repo_file`, `test_fixture_fb_history_is_meta`
  - [x] Residual survivorship bias documented in `docs/data-limitations.md` — file exists; `test_data_limitations_doc`
- Waivers: none

**Result:** ALL CRITERIA VERIFIED

#### Phase 1 checkpoint follow-up (Tier 4) — 2026-11-03
- Reviewed anomalies logged at info (red first) · raw: [artifacts/phase1-checkpoint-2026-11-03-tier4-red-anomalies.txt](artifacts/phase1-checkpoint-2026-11-03-tier4-red-anomalies.txt)
- `uv run pytest tests/unit/data/test_universe.py -q` (brief's Verify) → 68 passed, 1 deselected; `make check` → exit 0 (ruff, format 216 files, mypy 95 files, 2160 passed, 14 deselected); integration 1 passed; `-m live` 12 passed, 1 failed (the LM lexicon source answered "Google Drive - Quota exceeded"; no lexicon code changed) · raw: [stage-14 artifacts/phase1-checkpoint-2026-11-03-tier4-verify.txt](../stage-14-data-cli/artifacts/phase1-checkpoint-2026-11-03-tier4-verify.txt)
- Acceptance criteria still evidenced (`test_acceptance_members_2019_06_03`, the `tradable` tests passed).

**Result:** ALL CRITERIA VERIFIED

#### Phase 2 checkpoint follow-up (Tier 3 item 1: reviewed sectors for former members) — 2026-11-13
- Red first · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-red-item1.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-red-item1.txt); then `test_build_instruments_gives_former_members_their_reviewed_sector`, `test_refresh_reads_the_former_sectors_file_from_config`, `test_load_former_sectors`, `test_bad_former_sectors_file` (5 cases), `test_repo_former_sectors_file` pass; brief's Verify `uv run pytest tests/unit/data/test_universe.py -q` → 77 passed; `make check` → exit 0 (ruff clean, format clean, mypy clean on 157 files, 4130 passed, parallel in 127 s); `uv run pytest -q` (serial) → 4130 passed, 71 deselected in 316 s; `make test-integration` → 41 passed, 15 skipped (no lake) · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-make-check-2.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-make-check-2.txt), [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-pytest-serial-2.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-pytest-serial-2.txt), [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-integration-2.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-integration-2.txt)
- The list: 180 entries, each reviewed, 134 agreeing with the SIC majority · [artifacts/phase2-checkpoint-tier3-former-sectors-review.md](artifacts/phase2-checkpoint-tier3-former-sectors-review.md). Not from a data provider.
- Not applied to the main lake (waits for the owner's review; needs a universe sync and a rebuild of the factor cache and the risk model).
- Acceptance criteria unchanged and still evidenced (in the brief's Verify).

#### Phase 2 checkpoint follow-up (one pass: reviewed sectors applied) — 2026-11-13
- Commands run (this chat): `make check` → exit 0 (ruff clean, 386 files formatted, mypy clean on 157 files, 4168 passed); `uv run pytest -q` → 4168 passed, 71 deselected; integration on the main lake → 55 passed, 1 skipped (the flag-gated 2018–2024 calibration) · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-onepass-final-checks.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-onepass-final-checks.txt); red first: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-onepass-red.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-onepass-red.txt)
- Main lake: `instruments` with 683 sectors (was 503), nothing else changed · [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-onepass-swap.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-onepass-swap.txt)
- Acceptance criteria unchanged and still evidenced.

**Result:** ALL CRITERIA VERIFIED

#### Phase 2 checkpoint follow-up (Tier 4: index changes applied; KVUE; AVB) — 2026-11-14
- Commands run (this chat): `make check` → exit 0 (ruff clean, 393 files formatted, mypy clean on 157 files, 4241 passed in parallel and 3 timing tests alone); `uv run pytest -q` → 4244 passed, 71 deselected; integration on the main lake → 55 passed, 1 skipped (the flag-gated 2018–2024 calibration) · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-final-checks.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-final-checks.txt); red first: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-red.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-red.txt); mutants: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-mutate.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-mutate.txt)
- `tests/unit/data/test_universe.py` passes (81).
- Acceptance criteria unchanged and still evidenced.

**Result:** ALL CRITERIA VERIFIED
