# Stage 10 — Macro data (FRED / ALFRED) · Verification evidence

Filled in only with output from commands run in this stage's chat (Project.md Rule 1). Raw outputs are under `artifacts/`; key lines are quoted here.

Environment for every command (no project change): `UV_CACHE_DIR=$TMPDIR/uv-cache` (sandbox), `UV_NO_SYNC=1` and `PYTHONPATH=src` (the iCloud-synced folder hides the venv's editable `.pth`; see the worklog).

#### Verification evidence — Stage 10 — 2026-10-29
- Commands run (this chat):
  - `uv run pytest -q` → **1031 passed, 0 failed, 5 deselected** (live), exit 0 · raw: [artifacts/pytest.txt](artifacts/pytest.txt) (the nanobind "leaked function" note after the summary is printed at interpreter exit by a compiled dependency, as in earlier stages; exit code 0)
  - `uv run ruff check .` → All checks passed! · raw: [artifacts/ruff-check.txt](artifacts/ruff-check.txt)
  - `uv run ruff format --check .` → 129 files already formatted · raw: [artifacts/ruff-format.txt](artifacts/ruff-format.txt)
  - `uv run mypy src/atlas` → Success: no issues found in 61 source files · raw: [artifacts/mypy.txt](artifacts/mypy.txt)
  - `uv run pytest tests/unit/data/macro -q` (brief's Verify) → **73 passed, 1 deselected** (live), exit 0 · raw: [artifacts/verify-test-macro.txt](artifacts/verify-test-macro.txt)
  - `uv run pytest tests/unit/data/macro -v -W error` → 73 passed · raw: [artifacts/verify-W-error.txt](artifacts/verify-W-error.txt)
  - `uv run pytest tests/unit/data/macro -k acceptance -v -W error` → 3 passed · raw: [artifacts/acceptance-tests.txt](artifacts/acceptance-tests.txt)
  - `uv run pytest tests/unit/data/macro -m live -v -rs` → `test_live_fred_cpi SKIPPED` ("FRED_API_KEY is not set") · raw: [artifacts/live.txt](artifacts/live.txt). Not an acceptance criterion; Rule 6 missing-secret exception (see Waivers / notes)
  - coverage of `atlas.data.macro` → **100%** (479 statements, 0 missed) · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
  - docstrings (`artifacts/docstrings.py`) → 6 modules, 38 public names checked, missing: none · raw: [artifacts/docstrings.txt](artifacts/docstrings.txt)
  - unit tests with every proxy pointed at a dead port (`HTTP(S)_PROXY=http://127.0.0.1:9`) → 73 passed (no network) · raw: [artifacts/no-network.txt](artifacts/no-network.txt)
  - `make check` → exit 0 (lint, format, mypy, 1031 passed) · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
  - On merged `main` (`e056de5`, fast-forward from `a2593b2`, run in the owner's Terminal panel after the sandbox refused it): `git branch --merged main` lists the stage branch; `main^{tree}` = branch tree; checks on an exact export of `main`'s tree (`git archive`; the main checkout's files are iCloud-offloaded and unreadable) with a fresh offline venv: `make check` → exit 0, 1031 passed, 5 deselected; brief's Verify → 73 passed; `-k acceptance -W error` → 3 passed · raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt)
- Acceptance criteria (brief):
  - [x] **PIT queries never return a value whose release came after as_of** — evidence: `test_acceptance_no_returned_value_released_after_as_of` (every month-end 2019-01 … 2021-12: every row used has `available_at` < cutoff, and the result equals the one from a store holding only rows known then), `test_acceptance_cpi_march_2020_unknown_on_2020_04_05` (brief task 2: March 2020 CPI absent; latest is Feb 2020 = 259.050), `test_acceptance_cpi_march_2020_first_release_on_2020_04_15` (= first release 257.953), `test_cpi_not_known_on_release_morning` (08:30 release, 08:00 cutoff), `test_future_rows_change_nothing`, `test_guard_raises_if_filter_bypassed` (`LookAheadError`), `test_dgs10_pit_strict_cutoff`, `test_market_first_vintage_available_next_day_0800_et` (brief task 3 rule), `test_market_available_at_follows_dst`
  - [x] **Revisions are handled** — evidence: `test_revision_seen_only_after_its_vintage` (March 2020 CPI 257.953 until 2021-02-08, 257.989 from 2021-02-09; March 2019 254.148 → 254.095 after the 2020-02-11 vintage), `test_market_revision_waits_for_its_vintage`, `test_latest_missing_value_hides_older_value`, `test_windowed_ingest_equals_single_request` (vintage history split into 2000-vintage-limit windows gives the same rows), `test_parse_observations_cpi`, `test_ingest_cpi_fixture` (114 rows, 72 revision rows)
  - Brief tasks: 1 client + mocked responses → `test_macro_client.py` (15 tests, incl. `test_api_key_never_logged_or_raised`); 2 vintage ingestion + the CPI test → `test_macro_ingest.py`, `test_macro_pit.py`; 3 daily market rule → `test_macro_market.py`; 4 transforms with known values → `test_macro_transforms.py` (YoY 1.51833, 3m annualised 12.550881, z-score vs hand-computed window, curve slope, real rate)
- Waivers: none. Note: `test_live_fred_cpi` (`-m live`) was skipped because `FRED_API_KEY` is not set in `.env` (Rule 6 exception: missing owner secret); it covers no acceptance criterion. Unit fixtures are real ALFRED data in the API's JSON shape (see `tests/fixtures/macro/README.md`).
  - Follow-up the same day, after the owner added `FRED_API_KEY` (in `~/Projects/atlas-capital`, the project's new home outside iCloud): `uv run pytest tests/unit/data/macro -m live -v -rs` → `test_live_fred_cpi PASSED` (real FRED API; March 2020 CPI 257.953 as of 2020-04-15, absent as of 2020-04-05) · raw: [artifacts/live-fred.txt](artifacts/live-fred.txt)

**Result:** ALL CRITERIA VERIFIED
