# Phase 1 checkpoint (Stages 07–14, M1): report — 2026-11-02

Scope: are Stages 07–14 complete against their briefs, what gaps remain, and what is worth
improving before Phase 2 builds on the data platform. Kept in the Stage 14 folder because
Stage 14 completes M1. Fixes are recorded in the folder of the stage whose code they change
(Rule 2A), as in the Phase 0 checkpoint.

Base: branch `claude/phase-one-checkpoint-stages-80625b` at `8056137` (= `main`).

## 1. Checks re-run in this chat

| Check | Result |
|---|---|
| `uv run pytest -q` | 2118 passed, 0 failed, 12 deselected (exit 0; the known nanobind shutdown lines follow) |
| `uv run pytest tests/unit/data -m live -v -rs` (keys from the root `.env`) | **11 passed**, 0 skipped: yfinance, **Alpaca prices**, Wikipedia, EDGAR, FRED, **Alpaca news**, Yahoo news, LM lexicon, EDGAR earnings, FRED release dates, Yahoo calendar |
| `atlas data status` on the main lake (read-only, `ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data`) | exit 0; prices 475/503 raw (94.4%), fundamentals 502/503, macro 22/22, news 503/503, events 503/503; quality 2026-11-02: 2 critical, 86 warning, blocked WBD; last sync ok 2026-11-02 00:48 UTC |

Raw output: `phase1-checkpoint-2026-11-02-*.txt` in this folder.

## 2. Stage by stage

Every stage is `DONE` with `ALL CRITERIA VERIFIED` in its `verification.md`, evidence in
`PROGRESS.md`, and no waivers. Each acceptance criterion still holds today:

| Stage | Acceptance criteria | Still holds | Open in records |
|---|---|---|---|
| 07 Prices | split-adjusted fixtures; idempotent re-ingest; failed symbol never aborts the batch | yes (suite green) | Alpaca live smoke was SKIPPED at DONE (no keys); now PASSES. Alpaca fixtures are still built, not recorded; `fetch_actions` is never exercised live for either vendor |
| 08 Universe | `members(2019-06-03)` excludes TSLA, includes leavers; `tradable` PIT | yes | backlog item "sync should call `fetch_sp500_pages`" is done (Stage 14 `universe` step) but not marked |
| 09 Fundamentals | AAPL PIT figures; restatements; look-ahead | yes | CIK overrides cover leavers since 2018 only, while the backfill starts 2015 |
| 10 Macro | no value released after `as_of`; revisions | yes | credit spreads from 2023-10-30 only (Stage 21 concern) |
| 11 News | stable evidence ids; PIT; dedup | yes | Alpaca live test SKIPPED at DONE; now PASSES. Fixtures still built. Yahoo news adapter still retries programming errors (Stage 12's fix was not carried over); a `pd.NA` list cell is rejected |
| 12 Events/sentiment | PIT events; deterministic sentiment | yes | backlog item "Stage 14's sync should run …" is done but not marked |
| 13 Quality/DataView | leak suite; blocked symbols absent; nothing else needed | yes | backlog item "sync should persist the daily report" is done but not marked |
| 14 CLI/backfill (M1) | ≥ 8y prices ≥ 95% (owner-approved listed-8y reading), fundamentals ≥ 90%, all macro | yes: 475/475 listed ≥ 8y; 502/503; 22/22 | `atlas data status` prints only the raw 94.4%, so the Verify command does not show the approved reading; AVB (a former member) has no prices because one null Yahoo bar rejected its whole history |

**Conclusion: no stage needs to reopen.** The gaps are data-completeness defects, stale
records and hardening items, none of which invalidates a criterion.

## 3. Gaps and improvements, in tiers

### Tier 1 — real gaps (data loss, unverified vendor paths, stale records)
1. **(07) One bad bar drops a symbol's whole history.** `ingest_prices` validates a symbol's
   frame as a whole, so one bar with a missing OHLC / adj_close / volume value fails the
   symbol (AVB: 2015–2026 lost, a former member, so it feeds survivorship bias). Change: drop
   only the incomplete bars, log `prices.bars_dropped`, report them in
   `IngestReport.bars_dropped`; the Stage 13 `missing_sessions` check already flags the gap.
   Then re-ingest AVB into the main lake.
2. **(07) Corporate actions never verified live.** Neither live smoke test calls
   `fetch_actions`; Alpaca's `/v1/corporate-actions` adapter is tested only on a built
   fixture. Add live tests: AAPL's 4:1 split on 2020-08-31 from both vendors.
3. **(07, 11) Alpaca now verified live.** Record the passing live runs; compare the built
   fixtures' field names and types with real responses for the same requests (recorded as
   artifacts) so the built fixtures are known to match the real shape.
4. **Records.** Mark the Backlog items that Stage 14 already closed (Stage 08 live refresh
   wiring, Stage 12 sync wiring, Stage 13 daily report persistence) and the Alpaca live-test
   items, with evidence.

### Tier 2 — correctness hardening
1. (11) Yahoo news adapter: stop retrying programming errors (as Stage 12's calendar does).
2. (06/11) Storage: read a `pd.NA` cell in a `string_list` column as null.
3. (14) `atlas data status`: also print the owner-approved price rule (members listed ≥ 8
   years), so the brief's Verify output shows the criterion as approved.
4. (07, 11) Re-record the Alpaca fixtures from real responses (tests that compare against
   Yahoo numbers need new expected values; the invented XYZ reverse split stays built).

### Tier 3 — completeness and performance for Phase 2
1. (09) Extend `config/cik_overrides.yaml` to leavers since 2015 (the backfill start) and
   re-sync their fundamentals.
2. (13) Vectorise `fundamentals_panel` (~5 s for 500 names per as-of; Stage 16 calls it per
   rebalance date).
3. (13) Persist only the report day's partition of `data_quality_issues`.
4. (07) Bound `YFinanceProvider`'s per-instance memo.
5. (13) Quality warning for XBRL facts whose period ends after their filing (74 facts / 33
   symbols in the lake, e.g. AAL shares outstanding dated 2027-07-17: filer typos).

### Tier 4 — housekeeping
1. (14) Split `sync.py` (1 738 lines) into a package (Stage 65 per Backlog).
2. (14) Known pre-2018 membership anomalies logged at info, not warning.
3. (14) Persistent "known delisted" list so fresh backfills skip yfinance's ~25 s per name.
4. (07, 11) Archive raw vendor payloads under `raw/<vendor>/`.

### Deferred by design (owned by a later stage; no action now)
Credit-spread proxy before 2023 (21); `declared_at` for actions (39/40); point-in-time GICS
(16/19); free prices for delisted names and permanent security ids (no free source);
news text edited after publication (32); untagged market-wide news; the `atlas.duckdb` lock
exit (61/64); nanobind shutdown lines (upstream).

## 4. Status (2026-11-02)
- Tier 1: done (commit `7ae6924`, merged into `main`).
- Tier 2: done. Item 4 found one more bug, also fixed: Alpaca repeats a corporate action once per CUSIP (GE's 2021-06-25 dividend came back twice). The news part of item 4 was done as an added real recording; the built pages stay for their designed edge cases.
- Tier 3: done, except item 2, which was measured rather than changed: `fundamentals_panel` takes ~92–118 s per as-of day for 502 members on the real lake. Windowing the facts gave identical values and no speed-up; a one-pass rewrite or a per-symbol cache is for the owner to choose (Stage 16 sets the access pattern). Found and fixed on the way: facts dated after their own filing are left out (AAL's share count). Found and left for the owner: 6 symbols hold a reused ticker's later company's facts (AA, ADT, NE, POM, SE, TE).
- Tier 4: not started.
