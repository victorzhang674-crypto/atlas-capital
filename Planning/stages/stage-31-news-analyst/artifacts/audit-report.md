# Stage 31 — audit report (2026-11-28)

Self-audit by the implementing chat (no reviewer subagent, as the owner's standing preference). Every brief line against evidence, then the Rule 8 corners. Findings were fixed test-first where they were defects.

## The brief, line by line
| Brief | Evidence |
|---|---|
| Tools `get_company_news` (deduped evidence list), `get_upcoming_events` (earnings date/time, ex-div), `get_past_earnings_reactions` (last 8), `get_sector_news` | `news_tools.NEWS_TOOLS`; `test_news_tools.py` (36), real-lake check on 10 symbol/day cases (`main-lake-check.txt`). Dedup and roundup removal are Stage 11's (`DataView.news`); reactions = `agents.news.reactions` 8 |
| Prompt: material vs noise, catalysts with dates, risk events, narrative shifts, what the market may be missing | `news_analyst.j2` v1, six sections checked by `check_output`; golden prompt; the recorded live report (read in full) separates noise by tag, dates the May 7 event, flags the estimated earnings date |
| Output `AnalystReport` + `catalysts[{date?, description, evidence_id, expected_impact}]` | `NewsReport(AnalystReport)`, `NewsCatalyst`, `Impact` (`test_types_news.py`, round-trip sample); the evidence id is a framework citation (unknown id → repair → `low_quality`) |
| Guard: 0 articles → says so, confidence ≤ 0.2 | `test_with_no_news_*`, `test_the_cap_holds_even_when_the_repair_fails` (0.2, not the 0.3 low-quality cap), missing news table = same path with a citable `gap:`; `says_no_news` tested on 12 phrasings |
| Every catalyst has an evidence ID; empty-news path tested; offline replay | `test_every_catalyst_has_an_evidence_id_the_run_showed`, empty-news tests, `test_the_recorded_aapl_run_replays_offline` (no key, no lake, 1 cache hit, cost 0) |
| Recorded as Stage 29's fixtures (exported cache + lake slice under `tests/fixtures/recorded/`) | `stage-31-news-aapl-2024-05-01/`; slice equal to the main lake on 47 of 47 items |

## Point in time
- Every tool reads only through `DataView`; `test_news_tools_pit.py`: the dirty lake (later articles, a correction of an article, a later earnings schedule, a later report and its reaction) gives exactly the clean lake's evidence; no item is timed at or after the cutoff on the world (all members) or the real lake (10 cases).
- Computed items (coverage, none, summary, gap) are timed at cutoff − 1 µs; the correction of an article shows only once public (tested at two days).

## Fail-safe and honesty
- A blocked symbol fails the run with no LLM call (tested; all four tools refuse it). A tool failure becomes a citable `gap:`; the guard treats an unreadable news table as "no news" (the cautious side) and logs why (A2).
- "Estimated" earnings dates say so in the item text; the recorded run's report repeats it ("an estimate rather than a confirmed date").
- Rule 8: nothing weakened. No mock of real behaviour beyond FakeLLM in unit tests; the live run is real and its answer is committed.

## Findings
- **A1 (fixed)** Sector news capped each peer's articles before leaving out the company's own, so a peer that shared an article with the stock lost one of its own: the view is asked for the cap plus the company's articles (test).
- **A2 (fixed)** `company_article_count` turned a `DataError` into 0 silently: now logs `news.company_count_unavailable` (test).
- **A3 (fixed)** `past_reactions` had branches for a reaction without a return that the table schema makes impossible (non-nullable `car`): removed instead of left as untestable code.
- **A4 (fixed)** Test module name `test_news_live.py` collided with Stage 11's: renamed `test_news_analyst_live.py` (the first full run failed collection; recorded in the worklog).
- **A5 (fixed, mutation)** the unused `settings` parameter of `catalyst_problems`; untested guard conditions and message texts (see `mutation/mutation-summary.md`).
- **A6 (recorded → Backlog)** Stored article text holds HTML entities (`&#39;`); Stage 11 should decode at ingest (changing the text changes ids' content for the default `news` tool, so not done here).
- **A7 (recorded → Backlog)** Checkpoint allow-list (Stage 35): add `NewsReport`, `NewsCatalyst`, `Impact` with `TechnicalReport` and friends, and test a checkpoint round trip.
- **A8 (recorded)** Sectors in `instruments` are today's classification (a 2023 view of SIVB reads today's "Financials" peers): an existing Stage 14/29 limit, unchanged.
- **A9 (recorded)** Historical earnings dates are estimates (no Yahoo schedule history): the item text says so and the model repeated it.

## Residual risk
The model's judgement of "material" is not testable offline beyond structure and grounding; the recorded report was read and is sound. A re-recording gives another answer.
