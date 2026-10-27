# Stage 14 — Data sync CLI & bootstrap backfill  → Milestone M1
**Phase:** 1 · **Size:** S · **Depends on:** 13

## Goal
One command to backfill and incrementally update the whole lake, plus a health summary. This completes **M1**.

## In scope
- Typer app `atlas` (entry point in pyproject) with the group `atlas data`:
  `sync --from 2015-01-01 [--datasets prices,fundamentals,macro,news,events] [--symbols …]`, `update` (incremental from the last available date), `status` (row counts, date ranges, last sync, open quality issues), `quality --as-of DATE`
- Resumable backfill (checkpoint per dataset/symbol chunk in `data/cache/sync_state.json`)
- Progress bars (Rich); a summary table at the end; `DATA_SYNC` audit events
- Concurrency: thread pool per provider respecting rate limits
- `docs/data.md`: sources, licences/ToS notes, limitations, how to refresh

## Out of scope
Scheduling (Stage 61).

## Files
`src/atlas/cli/{__init__.py,app.py,data_cmd.py}`, `src/atlas/data/sync.py`, tests (CliRunner with fake providers), `docs/data.md`

## Tasks
1. Sync orchestrator with fake providers; resume-after-crash test.
2. CLI commands + CliRunner tests.
3. Live run, executed by the AI itself in the background (Project.md Rule 6): backfill the S&P 500 + ETFs from 2015, monitor it to completion, save the log under the stage's `artifacts/`, and record timings in the handoff note. If an API key is missing, ask the owner to add it to `.env`, then run it.

## Acceptance criteria
- `atlas data status` shows ≥ 8 years of prices for ≥ 95% of current members, fundamentals for ≥ 90%, and all macro series.

## Verify
`uv run atlas data sync --from 2015-01-01 && uv run atlas data status`
