# Recorded run: technical analyst, AAPL, 2024-05-01 (Stage 30)

A live run of `TechnicalAnalyst` (Stage 30) on AAPL as of 2024-05-01 (decision time 08:00
New York), kept so later stages and CI can replay it with no main lake, no API key and no
provider call (`tests/unit/agents/research/test_technical.py::test_the_recorded_aapl_run_replays_offline`;
helpers `tests/unit/agents/research/recorded.py` and `recorded_technical.py`).

| Path | What |
|---|---|
| `lake/` | The lake slice the run read: `atlas data slice --symbols <the 502 S&P 500 members on the day, AAPL and SPY> --as-of 2024-05-01 --since 2021-10-01 --tables corporate_actions,index_membership,instruments,prices_daily` (rows known at the as-of only; `lake/slice.json` lists the symbols and row counts; 10.05 MB, 320,863 bars). The members are the snapshot's cross-section (universe percentiles); SPY is the beta benchmark. |
| `regime.json` | The day's market regime (`RegimeState`), `regime_at` on the main lake (15 s). Its inputs (every member's prices since 2009, the FRED vintages) are too large for a fixture, so the run gets it as `inputs["regime"]`, as the fund graph passes `FundState.regime`. |
| `llm_cache.jsonl` | The run's LLM cache entry, `atlas llm cache export` (one request and its answer) |
| `expected_report.json` | The `TechnicalReport` the run returned |
| `run.json` | What the run recorded: provider, model, prompt version, status, calls, cited ids, tokens, cost |

**Recorded** 2026-11-25 with `Planning/stages/stage-30-technical-analyst/artifacts/record_fixture.py`
(steps `members`, `regime`, `fixture`, `compare`, `live`, `export`; output in that folder's
`fixture-*.txt` and `live-run.txt`): DeepSeek `deepseek-flash` (quick tier,
`openai_compatible`), prompt `technical_analyst.j2@v1` with both tools at v1; status `ok`, one
LLM call, 4,352 input + 1,662 output tokens, USD 0.0017.

**Re-recorded** 2026-11-28 (audit M4, the `live` and `export` steps; output in
`artifacts/live-run-m4.txt` and `fixture-export-m4.txt`) because the report check now also
requires each number to be stated by an evidence item cited in its own sentence
(`checks.ungrounded_numbers`): the prompt says so (`technical_analyst.j2@v2`) and the
moving-average item now states the last close it compares with (`get_price_summary@v2`; the
first report cited that item for the close 170.33, which only the range and levels items
state). Status `ok`, two LLM calls (the first answer had one such number; the repair turn fixed
it), 5,828 input + 3,181 output tokens, USD 0.0028.

**Source rows.** Sliced read-only from the main lake (S&P 500 since 2015, Stage 14; prices
synced to 2026-11-24). AAPL's prefetch gives the same 11 evidence items on the main lake and on
this slice (`fixture-compare.txt`: 11 of 11 equal, with the same regime).

**Re-recording** gives another answer (the model is not deterministic) and needs a new
`expected_report.json`; any change to the prompt template, a tool (its `version`) or the
evidence texts changes the request, so the replay test fails with a cache miss until the
fixture is recorded again. Prices come from the lake's vendors (Stage 07).
