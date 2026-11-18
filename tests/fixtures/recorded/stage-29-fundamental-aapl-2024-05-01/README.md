# Recorded run: fundamental analyst, AAPL, 2024-05-01 (Stage 29)

A live run of `FundamentalAnalyst` (Stage 29) on AAPL as of 2024-05-01 (decision time 08:00
New York), kept so later stages and CI can replay it with no lake, no API key and no provider
call (`tests/unit/agents/research/test_fundamental.py::test_the_recorded_aapl_run_replays_offline`;
helper `tests/unit/agents/research/recorded.py`).

| Path | What |
|---|---|
| `lake/` | The lake slice the run read: `atlas data slice --symbols <AAPL and its 64 Information Technology peers> --as-of 2024-05-01 --since 2017-01-01 --tables corporate_actions,fundamentals_facts,index_membership,instruments,prices_daily` (rows known at the as-of only; `lake/slice.json` lists the symbols and row counts; 5.96 MB) |
| `llm_cache.jsonl` | The run's LLM cache entry, `atlas llm cache export` (one request and its answer) |
| `expected_report.json` | The `AnalystReport` the run returned |
| `run.json` | What the run recorded: provider, model, prompt version, status, calls, cited ids, tokens, cost |

**Recorded** 2026-11-18 with `Planning/stages/stage-29-fundamental-analyst/artifacts/record_fixture.py`
(steps `peers`, `slice`, `reparse`, `fixture`, `compare`, `live`, `export`; output in that
folder's `fixture-compare.txt` and `live-run.txt`): DeepSeek `deepseek-flash` (quick tier,
`openai_compatible`), prompt `fundamental_analyst.j2@v1` with the four tools at v1; status
`ok`, one LLM call, 6,433 input + 1,730 output tokens, USD 0.0020.

**Source rows.** Sliced from the main lake (S&P 500 since 2015, Stage 14) read-only. The
main lake was ingested before Stage 29 added the `accounts_receivable` concept, and only mapped
tags are stored, so the slice's `fundamentals_facts` were re-parsed offline (archive only, no
download) from the main lake's raw SEC companyfacts with the current `config/xbrl_map.yaml`
before the final slice. On every other item AAPL's tools gave the same evidence on the main lake
and on this slice (27 of 28 items equal; the receivables line and check were the difference:
`fixture-compare.txt`). After the main lake's own re-ingest the same day, all 29 items are equal
(`main-lake-readback.txt`).

**Re-recording** gives another answer (the model is not deterministic) and needs a new
`expected_report.json`; any change to the prompt template, a tool (its `version`), the evidence
texts or the concept map changes the request, so the replay test fails with a cache miss until
the fixture is recorded again. SEC EDGAR data is public information of the U.S. Securities and
Exchange Commission; prices come from the lake's vendors (Stage 07).
