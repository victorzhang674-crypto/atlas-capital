# Recorded run: news analyst, AAPL, 2024-05-01 (Stage 31)

A live run of `NewsAnalyst` (Stage 31) on AAPL as of 2024-05-01 (decision time 08:00 New York),
kept so later stages and CI can replay it with no main lake, no API key and no provider call
(`tests/unit/agents/research/test_news.py::test_the_recorded_aapl_run_replays_offline`; helpers
`tests/unit/agents/research/recorded.py` and `recorded_news.py`).

| Path | What |
|---|---|
| `lake/` | The lake slice the run read, built by `Planning/stages/stage-31-news-analyst/artifacts/record_fixture.py fixture` from the 65 members of AAPL's GICS sector on the day (Information Technology: the news analyst reads the sector peers' news) and SPY (the reaction benchmark), rows known at the as-of only: `news` since 2024-04-01 (the 21-day window and 48 hours of deduplication context), `events`, `macro_events` and `earnings_reactions` since 2022-01-01 (the last 8 reactions; the earlier report dates an estimated next date is projected from), `prices_daily` since 2024-03-01 (the 20-session average dollar volume that ranks the peers), `corporate_actions` since 2023-01-01, `instruments` and `index_membership` whole. `lake/slice.json` lists the symbols, the `since` of each table and the row counts (1.15 MB, 2,269 articles). |
| `llm_cache.jsonl` | The run's LLM cache entry, `atlas llm cache export` (one request and its answer) |
| `expected_report.json` | The `NewsReport` the run returned |
| `run.json` | What the run recorded: provider, model, prompt version, status, calls, cited ids, tokens, cost |

**Recorded** 2026-11-28 with `Planning/stages/stage-31-news-analyst/artifacts/record_fixture.py`
(steps `members`, `fixture`, `compare`, `live`, `export`; output in that folder's `fixture-*.txt`
and `live-run.txt`): DeepSeek `deepseek-flash` (quick tier, `openai_compatible`), prompt
`news_analyst.j2@v1` with the four tools at v1; status `ok`, one LLM call, 6,684 input + 1,763
output tokens, USD 0.0021.

**Source rows.** Sliced read-only from the main lake (S&P 500 since 2015, Stage 14; news since
Stage 11, events since Stage 12). AAPL's prefetch gives the same 47 evidence items on the main
lake and on this slice (`fixture-compare.txt`: 47 of 47 equal). The next earnings date is an
estimate (2024-05-02 = the report a year earlier plus 52 weeks): the lake holds no Yahoo
schedule snapshot from 2024, so the tool says "estimated", as it should.

**Re-recording** gives another answer (the model is not deterministic) and needs a new
`expected_report.json`; any change to the prompt template, a tool (its `version`) or the
evidence texts changes the request, so the replay test fails with a cache miss until the
fixture is recorded again.
