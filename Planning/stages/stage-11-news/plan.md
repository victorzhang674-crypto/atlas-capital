# Stage 11 — News data pipeline · Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (inline TDD). Steps use checkbox (`- [ ]`) syntax; tick only after the step's tests have run and passed in this chat.

- **Brief:** [brief.md](brief.md) (`Planning/stages/stage-11-news/brief.md`)
- **Status:** DONE (2026-10-29; see [verification.md](verification.md)); deviations as `Ruling:` lines in [worklog.md](worklog.md)
- **Gate check:** Stage 10 is DONE (its [verification.md](../stage-10-macro/verification.md) says ALL CRITERIA VERIFIED; evidence block also in `PROGRESS.md`; branch `stage-10-followup` merged into `main`); `uv run pytest -q` → 1032 passed, 5 deselected, exit 0 (2026-10-29) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution method:** executing-plans (native, inline TDD). The owner asked in chat (2026-10-29): "start the planning for stage 11 in for a fully broken-down plan. Execute the plan after building it", so the plan is executed straight after it is written (as in Stages 06–10).
- **Branch:** `stage-11-news` from `main` `b795993`.

## Context

Stage 10 is `DONE` (10/66). Stage 11 adds company and market news that agents can cite. Later consumers checked: Stage 12 (sentiment sources; news sentiment scored there), 13 (`DataView.news(...)` wraps this stage; quality checks), 14 (sync CLI `--datasets news`, backfill, M1 "8+ years of … news"), 26 (tools return evidence ids), 31 (News & Event analyst: cites `news:<article_id>`), 32 (sentiment analyst), 33 (Macro Economist: market-wide news).

**Goal:** timestamped, de-duplicated, ticker-tagged news with stable evidence ids that agents can cite.

**Architecture:** package `atlas.data.news`. `base` holds the `NewsProvider` protocol, the article frame contract, the `news` table (registered on import) and normalisation; `alpaca_news` and `yahoo_news` are the two vendor adapters; `dedup` holds text normalisation, the content hash and the point-in-time duplicate marking; `relevance` scores an article for one ticker and drops roundups; `ingest` writes provider output to the lake (plus the fallback chain and the provider factory); `pit` answers what was known at `as_of` as `Evidence`. Storage gains a list-of-strings column type for `symbols[]`, as Stage 06 D1 planned.

**Tech stack:** httpx 0.28 + respx, rapidfuzz 3.14 (new dependency, `rapidfuzz>=3.9`), yfinance 1.7 (`Ticker.get_news`), pandas 3, pyarrow, Stage 06 `Lake`, Stage 05 `AsOf` / `pit_filter` / `assert_no_lookahead`, Stage 07 `RetryPolicy` / `call_with_retry` / `Throttle`, Stage 04 `Evidence` / `normalize_symbol`.

**Spec:** brief above; design spec §3 (point in time everywhere; fail safe), §11 (News = Alpaca News API (Benzinga, history from 2015), Yahoo fallback; free, timestamped), §16 (hallucinated facts: tools return evidence ids; theses must cite them); `atlas.core.pit.AVAILABILITY_RULES["news"]` ("the article's publish timestamp"); Stage 06 Backlog D1 (list-of-strings column type for `news.symbols[]`); stage protocol §4–§5.

## Source probe (2026-10-29, before planning)

- `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` are **empty** in the project `.env`, so the Alpaca fixtures are built in the documented API shape (as Stage 07 did for bars): `GET https://data.alpaca.markets/v1beta1/news?symbols=AAPL,MSFT&start=<RFC 3339>&end=<RFC 3339>&limit=50&sort=asc&include_content=false&page_token=…` → `{"news": [{"id": int, "headline", "author", "created_at": "…Z", "updated_at": "…Z", "summary", "content", "url", "images": [], "symbols": [str], "source": "benzinga"}], "next_page_token": str | null}`; `limit` ≤ 50. A `@pytest.mark.live` test is skipped until the owner adds the keys; it is not an acceptance criterion (Rule 6 missing-secret exception).
- yfinance 1.7.0 `Ticker(sym).get_news(count, tab="news")` returns the Yahoo stream: `[{"id": uuid, "content": {"title", "summary", "pubDate": "…Z", "provider": {"displayName"}, "canonicalUrl": {"url"}, "clickThroughUrl": {"url"}, "finance": {"stockTickers": [{"symbol"}]}, …}}]`, ads removed; recent items only (no date parameters). Task 4 tries to record one real answer; if Yahoo refuses, the fixture is built in that shape.
- `rapidfuzz` was not installed; `uv add 'rapidfuzz>=3.9'` resolved 3.14.6 (pyproject + `uv.lock` changed).

## Global constraints

- Point in time: an article is known at `as_of` iff `available_at < as_of.cutoff` (strict; `pit_filter`), with `available_at = published_at` = the vendor's publish time (Alpaca `created_at`, Yahoo `pubDate`), UTC. An article published at 08:30 ET is invisible at an 08:00 ET cutoff; one at 08:00:00 too.
- Evidence ids: `Evidence.id = "news:<article_id>"`, `article_id = "<provider>-<vendor id>"` (`alpaca-24843171`, `yahoo-<uuid>`). The id is derived from the vendor id only, so a re-ingest gives the same id.
- Stored text is headline + summary only (`include_content=false` on every Alpaca request; summaries cut to `max_summary_chars`); no article body, no images.
- De-duplication never uses the future: an article is a duplicate only of an article known no later than itself (see D5), so hiding it never depends on data after `as_of`.
- Settings in `config/fund.yaml › data.news` (`NewsConfig`, defaults in code); every YAML key has a comment; the repo YAML equals the code defaults (as Stage 10 pinned for macro).
- Alpaca keys are secrets: sent only as request headers, never logged, never in an error, never in `repr`.
- No network in unit tests (respx for Alpaca, an injected fetch function for Yahoo); live tests are `@pytest.mark.live`.
- Tests in `tests/unit/data/news/` (the brief's Verify target), basenames `test_news_*.py`, helpers in `news_helpers.py`, fixtures in `tests/fixtures/news/`; storage tests in `tests/unit/core/storage/`; config tests extend `tests/unit/core/test_config.py`.
- One stage commit `stage-11: News data pipeline` after DONE, then merge into `main` and re-verify (Rule 6A).

## Collisions with the overall plan

None with the roadmap or the spec. Interpretations (logged in the Decisions log):
- Brief `news_pit(symbol, as_of, lookback_days=14, limit=50)` needs the stored rows: `News(lake, cfg).pit(symbol, as_of, lookback_days=None, limit=None)` reads the lake (defaults 14 and 50 from `data.news`); the frame-level `news_pit(articles, symbol, as_of, *, lookback_days, limit, settings)` keeps the brief's name (as Stages 09–10 did).
- Brief "curated news … de-duplicated": every vendor article is stored once (by `article_id`); cross-source duplicates are marked at query time over the rows known at `as_of` (D5). Dropping a duplicate at ingest would make the surviving id depend on ingest order, against "evidence IDs are stable across re-ingests" and point in time.
- Brief Files list names five modules; the relevance rules get `relevance.py` and ingestion (with the fallback chain and provider factory) `ingest.py`, so no module mixes HTTP with table writing.
- "Ticker mentioned in the headline" is read literally as the ticker symbol, not the company name (names give false positives: "Target", "Apple"); candidates are the articles tagged with the ticker.
- Columns beyond the brief's list: `provider` (which adapter served the row; also the article-id prefix) and `updated_at` (vendor's last edit, nullable; recorded for audit, D2).

## Design decisions

| # | Decision | Why |
|---|---|---|
| D1 | Storage: new `ColumnType.STRING_LIST` → Arrow `list<string>` (items non-null); write accepts list / tuple / numpy array of `str` per cell (`None` only if nullable); read returns `tuple[str, ...]` per cell; not allowed in the primary key, partitioning or read filters (`ValueError`). | Stage 06 D1 deferred it to this stage; `symbols[]` is the brief's contract; a joined string would need parsing everywhere. |
| D2 | `news(article_id, published_at, available_at, updated_at?, provider, source, headline, summary, url?, symbols: string_list, content_hash)`, PK `(article_id, published_at)`, partitioned by `year` of `published_at`, version 1. `summary` may be `""`; `source` is the publisher (`benzinga`, `Reuters`); `updated_at` is stored but not used for availability (headline edits after publication are a documented leak). | Brief columns; year partitions keep a write to one year's file; `published_at` must be in the key to drive `year`. |
| D3 | `NewsProvider` protocol: `name: str`; `fetch_news(symbols: Sequence[str] \| None, start: datetime, end: datetime) -> DataFrame` in the article contract (`ARTICLE_COLUMNS` = the `news` columns), `start` inclusive, `end` exclusive, both aware (naive → `ValueError`); `None` symbols = market-wide. `normalize_news(df, settings)`: headline/summary stripped, empty headline dropped, summary cut to `max_summary_chars`, symbols normalised with `normalize_symbol` (an invalid tag such as an index `^GSPC`, `1ABC` or `""` is dropped with a `news.bad_symbol` debug log), de-duplicated and sorted, `available_at = published_at`, `content_hash` added, one row per `article_id` (first kept), sorted by `published_at, article_id`. | One contract for both adapters and the table. |
| D4 | `content_hash = sha256(normalize_text(headline) + "\n" + normalize_text(summary))` hex, `normalize_text` = NFKC, lower case, every run of non-alphanumerics → one space, stripped. | Same story re-published with different punctuation or case hashes equal. |
| D5 | Duplicates (`mark_duplicates(articles, settings) -> DataFrame` + `duplicate_of` column, `None` for originals): order rows by `(available_at, article_id)`; row *d* is a duplicate if some **earlier** row *e* in that order has `available_at(d) − available_at(e) ≤ dedup_window_hours` (48) and (`content_hash` equal or `rapidfuzz.fuzz.ratio(normalize_text(headline_e), normalize_text(headline_d)) ≥ fuzzy_threshold` (92)); `duplicate_of` = *e*'s root (its own `duplicate_of`, or *e*). Whether *d* is a duplicate depends only on rows at most 48 h before it, so the result inside any window is the same whatever earlier rows were loaded, as long as 48 h of context before the window are included. | "Same story from two sources → one"; the earliest-known copy survives, so the surviving id never changes once seen; no look-ahead. |
| D6 | Relevance for symbol *s*: `headline_mentions(headline, s)` matches `$S`, `(S)`, `EXCHANGE:S` / `EXCHANGE: S` (NYSE, NASDAQ, AMEX, ARCA, BATS, CBOE), and the bare upper-case token `S` when `len(S) ≥ 3`; share-class symbols also match `-` / `/` spellings (`BRK.B`, `BRK-B`, `BRK/B`); not preceded or followed by a letter or digit. Score `headline_relevance` (1.0) if mentioned, else `symbols_relevance` (0.5) when tagged; an article with more than `roundup_max_symbols` (10) tags is dropped unless its headline names *s*. | Brief; one- and two-letter tickers (`T`, `F`, `GM`) would match ordinary capitalised words, so they need a marker. |
| D7 | `NewsConfig` (`data.news`): `alpaca_news_url` `https://data.alpaca.markets/v1beta1/news`, `page_limit` 50 (1–50), `symbols_per_request` 50 (≥ 1), `max_retries` 3, `backoff_seconds` 1.0, `min_request_interval_seconds` 0.3 (Alpaca free tier 200 requests/min), `request_timeout_seconds` 30, `yahoo_max_items` 20 (≥ 1), `max_summary_chars` 1000 (≥ 1), `dedup_window_hours` 48 (> 0), `fuzzy_threshold` 92 (0–100), `roundup_max_symbols` 10 (≥ 1), `headline_relevance` 1.0, `symbols_relevance` 0.5 (validator: 0 < symbols < headline), `market_symbols` `(SPY, QQQ)` (normalised, non-empty), `lookback_days` 14 (≥ 1), `limit` 50 (≥ 1). | Brief values; nothing hard-coded. |
| D8 | PIT: `news_pit_detail(articles, symbol, as_of, *, lookback_days, limit, settings)` → frame `ARTICLE_COLUMNS + (relevance, headline_match)`: `pit_filter` + `assert_no_lookahead`; tagged with `symbol`; roundups dropped (D6); duplicates dropped (D5) over rows from `cutoff − lookback − dedup window`; then `available_at ≥ cutoff − lookback_days`; sorted by `relevance` desc, `available_at` desc, `article_id`; first `limit` rows. `news_pit(...) -> list[Evidence]` maps each row: `id = "news:<article_id>"`, `source`, `as_of = available_at`, `text = headline` + `"\n\n" + summary` when the summary is not empty, `url`. `market_news_pit(articles, as_of, …)`: rows with no symbols or any tag in `market_symbols`, duplicates dropped, newest first, no relevance filter. `News(lake, cfg)`: `.pit(symbol, as_of, lookback_days=None, limit=None)`, `.pit_detail(...)`, `.market(as_of, lookback_days=None, limit=None)`; `as_of` is a `date` (configured decision time) or `AsOf`, a `datetime` → `TypeError`; reads only rows with `available_at` in `[cutoff − lookback − window, cutoff)`. `limit`, `lookback_days` < 1 → `ValueError`. | Brief; matches `Macro`'s shape; the read window keeps a 14-day query from scanning all of history in memory. |
| D9 | `AlpacaNewsProvider(key_id, secret_key, config=None, *, client=None, sleep=time.sleep)`, `from_config(cfg) -> Self \| None` (no keys → `None`): batches of `symbols_per_request`, `limit=page_limit`, `sort=asc`, `include_content=false`, follows `next_page_token`; retries transport errors, 429, 5xx (`call_with_retry`, event `news.request_failed`); other HTTP errors and a body that is not `{"news": list}` → `DataError`; an item without `id` or `created_at` → `DataError`; results outside `[start, end)` dropped. `YahooNewsProvider(config=None, *, news_fn=None, sleep=time.sleep)`: one `news_fn(vendor_symbol)` call per symbol (`yf.Ticker(s).get_news(count=yahoo_max_items, tab="news")` by default), `None` symbols → `market_symbols`; items without `content`, `id`, `title` or `pubDate` are skipped; tags = `finance.stockTickers` ∪ {the requested symbol}; any exception inside `news_fn` is retried, then `DataError` (as the yfinance price adapter). | Brief "Alpaca … paginated", "Yahoo (recent only, fallback)". |
| D10 | `ingest_news(lake, provider, symbols, start, end, *, config=None) -> NewsIngestReport(rows, rows_by_provider)`: fetch, upsert into `news`; a provider `DataError` propagates (nothing written: fail safe). `FallbackNewsProvider(primary, secondary)`: primary first, secondary on `DataError`, both failing → `DataError`. `build_news_provider(cfg)`: `providers.news == "alpaca"` with keys → `FallbackNewsProvider(alpaca, yahoo)`; without keys → Yahoo alone with a `news.alpaca_unavailable` warning; `"yahoo"` → Yahoo. | Spec §16 vendor breakage; mirrors Stage 07's price chain. |

## File structure

| File | Responsibility |
|---|---|
| `src/atlas/core/storage/schema.py`, `src/atlas/core/storage/lake.py` (modify) | `ColumnType.STRING_LIST`: Arrow type, validation, read conversion, key/partition/filter guards |
| `src/atlas/core/config.py`, `config/fund.yaml` (modify) | `NewsConfig`, `DataConfig.news`, `data.news` keys |
| `pyproject.toml`, `uv.lock` (modify) | `rapidfuzz>=3.9`; dependency comment names Stage 11 |
| `src/atlas/data/news/__init__.py` | exports; importing registers `news` |
| `src/atlas/data/news/base.py` | `NewsProvider`, `ARTICLE_COLUMNS`, `NEWS` table, `normalize_news`, `empty_news`, `check_news_window` |
| `src/atlas/data/news/alpaca_news.py` | `AlpacaNewsProvider` |
| `src/atlas/data/news/yahoo_news.py` | `YahooNewsProvider` |
| `src/atlas/data/news/dedup.py` | `normalize_text`, `content_hash`, `mark_duplicates` |
| `src/atlas/data/news/relevance.py` | `headline_mentions`, `score_relevance` |
| `src/atlas/data/news/ingest.py` | `ingest_news`, `NewsIngestReport`, `FallbackNewsProvider`, `build_news_provider` |
| `src/atlas/data/news/pit.py` | `news_pit_detail`, `news_pit`, `market_news_pit`, `to_evidence`, `News`, `DETAIL_COLUMNS` |
| `tests/fixtures/news/` | `alpaca_news_p1.json`, `alpaca_news_p2.json`, `yahoo_news_aapl.json`, `README.md` (shapes, provenance) |
| `tests/unit/data/news/` | `news_helpers.py`, `test_news_base.py`, `test_news_alpaca.py`, `test_news_yahoo.py`, `test_news_ingest.py`, `test_news_dedup.py`, `test_news_relevance.py`, `test_news_pit.py`, `test_news_live.py` |
| `tests/unit/core/storage/test_storage_string_list.py`, `tests/unit/core/test_config.py` (modify) | storage and config tests |
| `docs/data-limitations.md` (modify) | `## News (Stage 11)` |

## Review focus

1. A share-class or one-letter ticker in a headline (`$T`, `(BRK.B)`, `NYSE:F`) counts as a mention; the bare word `T` or `F` does not → `test_headline_mentions_markers_and_short_tickers`.
2. The same article returned in two symbol batches, or on two pages, is stored once → `test_alpaca_article_in_two_batches_stored_once`.
3. Re-ingesting the same news in a different provider order (Yahoo first, then Alpaca, and the reverse) gives the same `news_pit` ids → `test_acceptance_ids_independent_of_ingest_order`.
4. A vendor tag that is not a valid symbol (`^GSPC`, `1ABC`, `""`) is dropped without failing the batch → `test_normalize_drops_invalid_symbols`.
5. A roundup that names nothing is dropped *before* de-duplication, so it never hides the focused copy of the story → `test_roundup_does_not_hide_focused_duplicate`.

---

### Task 1 — Storage: list-of-strings column type

**Files:** modify `src/atlas/core/storage/schema.py`, `src/atlas/core/storage/lake.py`, `pyproject.toml` (comment) · test `tests/unit/core/storage/test_storage_string_list.py`

**Interfaces:** Produces `ColumnType.STRING_LIST = "string_list"`; `Lake.read_table` returns `tuple[str, ...]` cells for it.

- [x] Write the failing tests: `test_string_list_roundtrip` (write `[["AAPL", "MSFT"], [], ("SPY",)]` through `Lake.write_table` upsert, read back `("AAPL", "MSFT")`, `()`, `("SPY",)`; Arrow field type `list<item: string>`), `test_string_list_rejects_non_strings` (`[[1]]`, `["AAPL"]` as a plain string cell, `[[None]]` → `DataError`), `test_string_list_null_cell_only_if_nullable`, `test_string_list_not_in_key_or_partition` (`ValueError`), `test_string_list_filter_rejected` (`read_table(filters=[("symbols", "=", "A")])` → `ValueError`), `test_string_list_empty_table_reads` (registered, never written → empty frame), `test_string_list_database_view` (`Database` query `SELECT len(symbols) …` works).
- [x] Run `uv run pytest tests/unit/core/storage/test_storage_string_list.py -q` → expect FAIL (`STRING_LIST` missing).
- [x] Implement: enum member, `_ARROW_TYPES[STRING_LIST] = pa.list_(pa.string())` (item nulls are rejected in validation, so the Arrow item field stays the default nullable one and Parquet round-trips unchanged); `_to_arrow` branch (each non-null cell must be list/tuple/ndarray of `str`); `TableSchema` check: no list column in `primary_key`; `_filter_expression` rejects list columns; `to_pandas` maps cells to tuples (`None` stays `None`); update the module type table; `pyproject.toml` dependency comment adds "rapidfuzz from Stage 11".
- [x] Run the same command → PASS; then `uv run pytest tests/unit/core/storage -q` → PASS (no regression).

### Task 2 — News config, article contract, `news` table, content hash

**Files:** modify `src/atlas/core/config.py`, `config/fund.yaml`, `tests/unit/core/test_config.py` · create `src/atlas/data/news/{__init__,base,dedup}.py`, `tests/unit/data/news/{news_helpers,test_news_base}.py`

**Interfaces:**
- Produces `NewsConfig` (D7) at `cfg.data.news`; `ARTICLE_COLUMNS: tuple[str, ...]`; `NEWS: TableSchema` (D2); `class NewsProvider(Protocol)` (D3, `runtime_checkable`); `empty_news() -> DataFrame`; `normalize_news(df: DataFrame, settings: NewsConfig | None = None) -> DataFrame`; `check_news_window(symbols: Sequence[str] | None, start: datetime, end: datetime) -> tuple[str, ...] | None`; `dedup.normalize_text(text: str) -> str`; `dedup.content_hash(headline: str, summary: str) -> str`.
- `news_helpers.py`: `FIXTURES`, `REPO_ROOT`, `utc(y, m, d, h=0, mi=0) -> datetime`, `article(**fields) -> dict` (one raw row with defaults), `articles_frame(rows) -> DataFrame` (via `normalize_news`), `FakeNewsProvider(name, frame)` (filters by symbols and `[start, end)`, records calls).

- [x] Write the failing tests: config — `test_news_config_defaults` (every D7 value), `test_repo_yaml_news_equals_defaults`, parametrised `test_news_config_rejects` (`page_limit` 51, `fuzzy_threshold` 101, `symbols_relevance` 1.0 ≥ headline, `market_symbols` `[]`, `dedup_window_hours` 0, `market_symbols` `["^X"]`); base — `test_news_table_declaration` (columns, PK, `year` partition, `symbols` is `STRING_LIST`), `test_import_registers_news_table`, `test_normalize_news_contract` (columns in order, dtypes, `available_at == published_at`, UTC, sorted), `test_normalize_drops_empty_headline_and_cuts_summary` (1000 chars), `test_normalize_drops_invalid_symbols` (`["aapl", "BTC/USD", "^GSPC", "", "AAPL"]` → `("AAPL",)`), `test_normalize_one_row_per_article_id`, `test_normalize_rejects_naive_published_at` (`DataError`), `test_check_news_window` (naive / `start ≥ end` / bare string → `ValueError`; `None` passes through), `test_content_hash_ignores_case_and_punctuation`, `test_normalize_text_examples` (`"Apple’s Q3: Beats!"` → `"apple s q3 beats"`), `test_fake_provider_is_a_news_provider`.
- [x] Run `uv run pytest tests/unit/data/news/test_news_base.py tests/unit/core/test_config.py -q` → expect FAIL (`atlas.data.news` missing, `NewsConfig` missing).
- [x] Implement `NewsConfig` + `DataConfig.news`, the commented `data.news` block in `config/fund.yaml`, `base.py`, `dedup.normalize_text` / `content_hash`, `__init__.py` exports.
- [x] Run the same command → PASS.

### Task 3 — Alpaca news adapter with pagination (brief task 1)

**Files:** create `src/atlas/data/news/alpaca_news.py`, `tests/fixtures/news/{alpaca_news_p1,alpaca_news_p2}.json`, `tests/fixtures/news/README.md`, `tests/unit/data/news/test_news_alpaca.py`

**Interfaces:** Consumes Task 2's contract. Produces `AlpacaNewsProvider` (D9), `name = "alpaca"`.

Fixtures: page 1 = three AAPL/MSFT articles from 2020-03-02 (one tagged with 12 symbols, one with `"symbols": ["AAPL", "BTCUSD"]`) and `next_page_token: "page-2"`; page 2 = two articles, one of them a Benzinga copy of a Reuters story used in Task 6, `next_page_token: null`. Every item has `content` text, so the tests can prove it is never stored.

- [x] Write the failing tests: `test_is_a_news_provider`, `test_fetch_follows_pages` (2 requests, 5 rows, ids `alpaca-<id>`, `source` `benzinga`, `provider` `alpaca`), `test_request_parameters` (`symbols=AAPL,MSFT`, `limit=50`, `sort=asc`, `include_content=false`, RFC 3339 `start`/`end`, key headers), `test_market_wide_request_has_no_symbols`, `test_symbols_batched` (`symbols_per_request=1` → one request chain per symbol), `test_alpaca_article_in_two_batches_stored_once`, `test_results_outside_window_dropped`, `test_content_never_stored` (no column holds the fixture's `content` text), `test_retries_then_data_error` (503 × 4 → `DataError`, sleeps 1, 2, 4), `test_http_400_is_data_error`, `test_bad_body_is_data_error` (`[]`, `{"news": {}}`, item without `id`), `test_keys_never_in_repr_logs_or_errors` (caplog + error text), `test_from_config_without_keys_is_none`.
- [x] Run `uv run pytest tests/unit/data/news/test_news_alpaca.py -q` → expect FAIL (module missing).
- [x] Implement `alpaca_news.py` (paging as `AlpacaPriceProvider._pages`; `RetryPolicy(max_retries, backoff_seconds, min_request_interval_seconds)` from `NewsConfig`).
- [x] Run the same command → PASS.

### Task 4 — Yahoo news adapter (recent only, fallback)

**Files:** create `src/atlas/data/news/yahoo_news.py`, `tests/fixtures/news/yahoo_news_aapl.json`, `tests/unit/data/news/test_news_yahoo.py`; recorder `artifacts/record_yahoo_news.py` (try a real answer once; otherwise build the fixture in the probed shape and say so in the fixture README)

**Interfaces:** Produces `YahooNewsProvider` (D9), `name = "yahoo"`; `NewsFn = Callable[[str], list[dict[str, Any]]]`.

- [x] Write the failing tests: `test_is_a_news_provider`, `test_parses_stream_items` (ids `yahoo-<uuid>`, `source` = provider display name, url from `canonicalUrl`, tags include `stockTickers` and the requested symbol), `test_window_filter`, `test_skips_malformed_items` (no `content`, no `pubDate`, no title), `test_market_wide_uses_market_symbols` (calls `SPY`, `QQQ`), `test_vendor_symbol_spelling` (`BRK.B` → `news_fn("BRK-B")`), `test_errors_retried_then_data_error`, `test_same_article_for_two_symbols_once`.
- [x] Run `uv run pytest tests/unit/data/news/test_news_yahoo.py -q` → expect FAIL.
- [x] Implement `yahoo_news.py`.
- [x] Run the same command → PASS.

### Task 5 — Ingest, fallback chain, provider factory

**Files:** create `src/atlas/data/news/ingest.py`, `tests/unit/data/news/test_news_ingest.py`

**Interfaces:** Consumes Tasks 2–4. Produces `ingest_news(lake: Lake, provider: NewsProvider, symbols: Sequence[str] | None, start: datetime, end: datetime, *, config: AtlasConfig | None = None) -> NewsIngestReport`; `NewsIngestReport(rows: int, rows_by_provider: FrozenDict[str, int])`; `FallbackNewsProvider(primary, secondary)` (`name = "fallback"`); `build_news_provider(cfg: AtlasConfig) -> NewsProvider`.

- [x] Write the failing tests: `test_ingest_writes_news_table` (fixture rows land in `news`, `symbols` tuples, report counts), `test_reingest_is_idempotent` (twice → same rows, same ids), `test_overlapping_windows_merge`, `test_provider_error_writes_nothing` (`DataError` propagates, table not created), `test_fallback_uses_secondary_on_error`, `test_fallback_both_fail`, `test_build_provider_alpaca_with_keys` (chain alpaca → yahoo), `test_build_provider_without_keys_is_yahoo` (+ warning), `test_build_provider_yahoo`.
- [x] Run `uv run pytest tests/unit/data/news/test_news_ingest.py -q` → expect FAIL.
- [x] Implement `ingest.py`.
- [x] Run the same command → PASS.

### Task 6 — De-duplication (brief task 2)

**Files:** modify `src/atlas/data/news/dedup.py` · create `tests/unit/data/news/test_news_dedup.py`

**Interfaces:** Produces `mark_duplicates(articles: DataFrame, settings: NewsConfig | None = None) -> DataFrame` (input rows + `duplicate_of: str | None`, same order as input) and `DUPLICATE_OF = "duplicate_of"`.

- [x] Write the failing tests: `test_same_story_two_sources_is_one` (Reuters via Yahoo 10:05 and Benzinga via Alpaca 10:00, headlines differing in punctuation → the Alpaca one is the original, the Yahoo one `duplicate_of="alpaca-…"`), `test_exact_hash_match`, `test_fuzzy_threshold_boundary` (headline pair scored 91 vs 92 by `fuzz.ratio`; computed in the test from rapidfuzz, so the boundary is exact), `test_outside_48h_not_duplicate` (48 h apart → duplicate; 48 h + 1 s → not), `test_tie_on_time_breaks_by_article_id`, `test_chain_points_to_root`, `test_window_independent` (marking a window with 48 h of context equals marking the full set, on a generated set of 200 articles with seeded `random.Random(11)`), `test_empty_frame`.
- [x] Run `uv run pytest tests/unit/data/news/test_news_dedup.py -q` → expect FAIL.
- [x] Implement `mark_duplicates` (sort by `(available_at, article_id)`, two-pointer 48 h window, compare to earlier rows in the window).
- [x] Run the same command → PASS.

### Task 7 — Relevance filter (brief task 3)

**Files:** create `src/atlas/data/news/relevance.py`, `tests/unit/data/news/test_news_relevance.py`

**Interfaces:** Produces `headline_mentions(headline: str, symbol: str) -> bool`; `score_relevance(articles: DataFrame, symbol: str, settings: NewsConfig | None = None) -> DataFrame` (rows tagged with `symbol`, roundups dropped, plus `relevance: float` and `headline_match: bool`, input order).

- [x] Write the failing tests: `test_headline_mention_ranks_above_tag_only` (1.0 vs 0.5), `test_headline_mentions_markers_and_short_tickers` (`"AT&T (T) cuts dividend"` ✓, `"$F rallies"` ✓, `"NYSE: GM recalls"` ✓, `"T-Mobile and Ford"` for `T`/`F` ✗, `"AAPLX fund"` for `AAPL` ✗, `"Apple (AAPL) beats"` ✓, `"BRK-B buys"` for `BRK.B` ✓), `test_roundup_dropped_unless_headline_names_ticker` (11 tags → dropped; 11 tags + `$AAPL` in headline → kept; exactly 10 tags → kept), `test_untagged_article_not_a_candidate`, `test_score_uses_config_weights`.
- [x] Run `uv run pytest tests/unit/data/news/test_news_relevance.py -q` → expect FAIL.
- [x] Implement `relevance.py`.
- [x] Run the same command → PASS.

### Task 8 — Point-in-time query and acceptance (brief task 4)

**Files:** create `src/atlas/data/news/pit.py`, `tests/unit/data/news/test_news_pit.py`

**Interfaces:** Consumes Tasks 2–7. Produces `DETAIL_COLUMNS`; `news_pit_detail(articles, symbol, as_of: AsOf, *, lookback_days: int, limit: int, settings: NewsConfig | None = None) -> DataFrame`; `news_pit(...) -> list[Evidence]` (same parameters); `market_news_pit(articles, as_of: AsOf, *, lookback_days: int, limit: int, settings=None) -> list[Evidence]`; `to_evidence(row: Mapping[str, Any]) -> Evidence`; `News(lake: Lake, config: AtlasConfig | None = None)` with `.pit(symbol, as_of: date | AsOf, lookback_days: int | None = None, limit: int | None = None) -> list[Evidence]`, `.pit_detail(...)`, `.market(as_of, lookback_days=None, limit=None)`, `.articles(start: datetime, end: datetime) -> DataFrame`.

- [x] Write the failing tests: `test_acceptance_article_after_cutoff_invisible` (08:30 ET article invisible at the 08:00 ET cutoff of the same day, visible the next day; 08:00:00 invisible; 07:59:59 visible), `test_acceptance_evidence_ids_stable_across_reingests` (ingest fixtures, query, ingest again + overlapping window, query → equal id lists; ids `news:alpaca-…`), `test_acceptance_ids_independent_of_ingest_order`, `test_acceptance_same_story_two_sources_one_evidence`, `test_guard_raises_if_filter_bypassed` (monkeypatch `pit_filter` to identity → `LookAheadError`), `test_future_rows_change_nothing` (adding articles after the cutoff leaves the result equal), `test_lookback_and_limit` (14-day boundary inclusive at `cutoff − 14 d`; `limit=2`; ordering relevance, then newest), `test_roundup_does_not_hide_focused_duplicate`, `test_evidence_fields` (`source`, `as_of` UTC, `text` headline + blank line + summary; headline only when summary empty; `url`), `test_market_news` (untagged + SPY/QQQ-tagged, newest first, duplicates dropped, AAPL-only excluded), `test_news_reader_date_as_of_uses_decision_time`, `test_news_reader_rejects_datetime` (`TypeError`), `test_news_reader_empty_lake` (`DataError` naming `ingest_news`), `test_bad_lookback_or_limit` (`ValueError`), `test_symbol_normalised` (`"brk-b"` queries `BRK.B`).
- [x] Run `uv run pytest tests/unit/data/news/test_news_pit.py -q` → expect FAIL.
- [x] Implement `pit.py`; export from `__init__.py`.
- [x] Run the same command → PASS; then `uv run pytest tests/unit/data/news -q` → PASS.

### Task 9 — Live tests, docs, coverage, full verification

**Files:** create `tests/unit/data/news/test_news_live.py` · modify `docs/data-limitations.md`, `tests/unit/data/news/test_news_pit.py` (docs test)

- [x] Write `test_live_alpaca_news` (`-m live`; skipped without Alpaca keys: AAPL news 2020-03-02 → rows with ids `alpaca-…`, `published_at` in the window, no content) and `test_live_yahoo_news` (`-m live`; AAPL recent news → ≥ 1 row, ids `yahoo-…`), and `test_data_limitations_doc_covers_news` (section heading `## News (Stage 11)` and the words "headline", "summary", "updated", "roundup", "duplicate", "Yahoo").
- [x] Run the docs test → FAIL; write `## News (Stage 11)` in `docs/data-limitations.md` (what is stored, availability = publish time, edits after publication, Yahoo recent-only and its ids, tag-based candidates, ticker-only headline match, duplicate rule, Benzinga coverage from 2015); run → PASS.
- [x] Run `uv run pytest tests/unit/data/news -m live -v -rs` → record result (Alpaca skipped without keys; Yahoo live if the network allows).
- [x] Coverage: `uv run pytest tests/unit/data/news --cov=atlas.data.news --cov-report=term-missing -q` → target ≥ 95%, add guard tests for uncovered branches.
- [x] Full verification (outputs under `artifacts/`): `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/atlas`, `make check`, the brief's Verify `uv run pytest tests/unit/data/news -q`, `-k acceptance -W error`, unit tests with dead proxies (no network).
- [x] Write `verification.md` (every acceptance criterion with evidence), tick this plan, update `PROGRESS.md` (tick, handoff, evidence, Decisions log, Backlog).
- [ ] Commit `stage-11: News data pipeline` after reviewing the staged diff; merge into `main`; re-run `uv run pytest -q` and `make check` on `main`; record hashes in `worklog.md` (Rule 6A).

## Acceptance criteria → evidence (planned)

| Criterion (brief) | Tests |
|---|---|
| Evidence IDs are stable across re-ingests | `test_acceptance_evidence_ids_stable_across_reingests`, `test_acceptance_ids_independent_of_ingest_order`, `test_reingest_is_idempotent` |
| PIT tests pass | `test_acceptance_article_after_cutoff_invisible`, `test_guard_raises_if_filter_bypassed`, `test_future_rows_change_nothing`, `test_window_independent` |
| Dedup tests pass | `test_same_story_two_sources_is_one`, `test_acceptance_same_story_two_sources_one_evidence`, `test_fuzzy_threshold_boundary`, `test_outside_48h_not_duplicate` |
| Verify `uv run pytest tests/unit/data/news -q` | Task 9 run, saved in `artifacts/verify-test-news.txt` |
