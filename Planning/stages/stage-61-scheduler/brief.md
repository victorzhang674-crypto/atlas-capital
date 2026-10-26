# Stage 61 — Scheduler & live runner
**Phase:** 9 · **Size:** M · **Depends on:** 55, 60

## Goal
Run the Fund Day automatically on the market calendar against the paper account, robustly and idempotently.

## In scope
- Schedule (America/New_York, trading days only): 06:30 data update → 08:00 Fund Day decision (as_of = today with the 08:00 cutoff) → 09:25 submit MOO/opening orders → every 30 min 09:45–15:45 intraday risk check (stops, kill switch, daily loss) → 16:30 post-close accounting, reconciliation, settlement, reflection, reports
- Implementation: APScheduler in a long-running `atlas live start` process + a `launchd` plist template for macOS (and a systemd unit template); a heartbeat file
- Idempotency: each job keyed by (date, job); re-runs skip completed jobs; crash recovery resumes from LangGraph checkpoints
- Holiday/early-close handling via the calendar; a missed-job policy (if the 08:00 decision is missed after 09:15 → skip trading today, still run risk)
- `atlas live status|start|stop|run-job <job> --date D`

## Files
`src/atlas/live/{scheduler.py,runner.py,jobs.py}`, `deploy/launchd/com.atlas.fund.plist`, `deploy/systemd/atlas.service`, tests (SimClock-driven scheduler tests)

## Acceptance criteria
- Simulated clock tests cover a normal day, a holiday, an early close, a crash and resume, and a missed decision window.

## Verify
`uv run pytest tests/unit/live -q`
