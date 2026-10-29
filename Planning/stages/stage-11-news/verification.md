# Stage 11 — News data pipeline · Verification evidence

Fill this in **only** with output from commands actually run in this stage's final chat (Project.md Rule 1). Save full raw outputs under `artifacts/` and quote the key lines here.

#### Verification evidence — Stage 11 — 2026-10-29
- Commands run (this chat, branch `stage-11-news`, every `uv run` with `UV_CACHE_DIR=$TMPDIR/uv-cache UV_NO_SYNC=1` because the sandbox blocks `~/.cache/uv`; same venv and lock):
  - `uv run pytest -q` → 1187 passed, 0 failed, 7 deselected (live) · raw: [artifacts/pytest.txt](artifacts/pytest.txt)
  - `uv run ruff check .` → All checks passed! · raw: [artifacts/ruff-check.txt](artifacts/ruff-check.txt)
  - `uv run ruff format --check .` → 149 files already formatted · raw: [artifacts/ruff-format.txt](artifacts/ruff-format.txt)
  - `uv run mypy src/atlas` → Success: no issues found in 69 source files · raw: [artifacts/mypy.txt](artifacts/mypy.txt)
  - `make check` → exit 0 (All checks passed!, 149 files already formatted, mypy Success 69 files, 1187 passed, 7 deselected) · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
  - `uv run pytest tests/unit/data/news -q` (brief's Verify) → 126 passed, 2 deselected · raw: [artifacts/verify-test-news.txt](artifacts/verify-test-news.txt)
  - `uv run pytest tests/unit/data/news -v -W error` → 126 passed, 2 deselected · raw: [artifacts/verify-W-error.txt](artifacts/verify-W-error.txt)
  - `uv run pytest tests/unit/data/news -k acceptance -v -W error` → 4 passed · raw: [artifacts/acceptance-tests.txt](artifacts/acceptance-tests.txt)
  - `uv run pytest tests/unit/data/news -m live -v -rs` → `test_live_yahoo_news PASSED` (real Yahoo search API), `test_live_alpaca_news SKIPPED` (`ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` not set; not an acceptance criterion) · raw: [artifacts/live.txt](artifacts/live.txt)
  - Coverage `--cov=atlas.data.news` → 100% of every module (466 statements); the new storage list code (`schema._string_list_array`) is covered · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
  - No network: news + storage unit tests with `HTTP(S)_PROXY`/`ALL_PROXY=http://127.0.0.1:9` → 252 passed · raw: [artifacts/no-network.txt](artifacts/no-network.txt)
  - On merged `main` (`3066bec`, fast-forward from `b795993`): `uv run pytest -q` → 1187 passed, 7 deselected; `make check` → exit 0; brief's Verify → 126 passed; `git branch --merged main` lists `stage-11-news` · raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt)
  - Post-DONE audit 2026-10-29 (branch `stage-11-audit`, after adding the reader look-ahead tests): `uv run pytest -q` → 1190 passed, 7 deselected · raw: [artifacts/audit-pytest.txt](artifacts/audit-pytest.txt); `make check` → exit 0 · raw: [artifacts/audit-make-check.txt](artifacts/audit-make-check.txt); brief's Verify → 129 passed, 2 deselected · raw: [artifacts/audit-verify-test-news.txt](artifacts/audit-verify-test-news.txt)
  - Docstrings (Definition of Done): `uv run python Planning/stages/stage-11-news/artifacts/docstrings.py` → 8 modules, 38 public modules/classes/functions/methods checked, missing: none · raw: [artifacts/docstrings.txt](artifacts/docstrings.txt)
  - Gate check (start of stage): `uv run pytest -q` → 1032 passed, 5 deselected · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- Acceptance criteria (brief):
  - [x] Evidence IDs are stable across re-ingests — evidence: `test_acceptance_evidence_ids_stable_across_reingests` (Alpaca fixtures via respx → `ingest_news` twice + an overlapping window → identical `News.pit` evidence; a wider window keeps the earlier ids), `test_acceptance_ids_independent_of_ingest_order` (Alpaca→Yahoo vs Yahoo→Alpaca → identical), `test_reingest_is_idempotent`, `test_same_article_id_twice_cited_once`; ids are `news:<provider>-<vendor id>` (`test_fetch_follows_pages`, `test_parses_recorded_items`)
  - [x] PIT tests pass — evidence: `test_acceptance_article_after_cutoff_invisible` (08:30 ET article invisible at the 08:00 ET cutoff; 08:00:00 invisible; 07:59:59 visible; visible the next day), `test_guard_raises_if_filter_bypassed`, `test_news_reader_guard_raises_if_filter_bypassed[pit|pit_detail|market]` (audit), `test_future_rows_change_nothing`, `test_lookback_and_limit`, `test_duplicate_of_article_before_window_stays_hidden`, `test_window_independent`, `test_news_reader_date_as_of_uses_decision_time`
  - [x] Dedup tests pass — evidence: `test_same_story_two_sources_is_one`, `test_acceptance_same_story_two_sources_one_evidence` (both copies stored, the later Yahoo copy hidden), `test_exact_hash_match`, `test_fuzzy_threshold_boundary`, `test_outside_48h_not_duplicate`, `test_roundup_does_not_hide_focused_duplicate`
  - [x] In-scope items: `NewsProvider` + `AlpacaNewsProvider` paginated (`test_news_alpaca.py`, 20) + `YahooNewsProvider` fallback (`test_news_yahoo.py`, 14; `test_fallback_uses_secondary_on_error`); curated `news(article_id, published_at=available_at, source, headline, summary, url, symbols[], content_hash)` (`test_news_table_declaration`, `test_normalize_news_contract`); relevance + roundups (`test_news_relevance.py`, 25); `news_pit(symbol, as_of, lookback_days=14, limit=50) -> list[Evidence]` with `Evidence.id = "news:<article_id>"` (`test_evidence_fields`, `test_news_reader_defaults_from_config`); market-wide query (`test_market_news`, `test_news_reader_market`); headline + summary only (`test_content_never_stored`, `test_request_parameters` `include_content=false`, summary cut in `test_normalize_drops_empty_headline_and_cuts_summary`)
- Waivers: none

**Result:** ALL CRITERIA VERIFIED
