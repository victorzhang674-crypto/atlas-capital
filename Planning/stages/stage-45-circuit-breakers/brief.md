# Stage 45 — Risk monitor & circuit breakers
**Phase:** 6 · **Size:** M · **Depends on:** 44 · **Spec refs:** §9 (drawdown tiers, pod stop)

## Goal
Continuous (daily in backtest, intraday in paper) monitoring that de-risks automatically when losses or market stress exceed thresholds.

## In scope
- Fund drawdown state machine: NORMAL → DEGROSS_25 (−5%) → DEGROSS_50_NO_NEW (−10%) → HALT_HEDGED (−15%, owner reset required); recovery rules (step back down a tier only after recovering half the tier's drawdown and 5 sessions)
- Pod stops: −8% → budget × 0.5; −12% → frozen (liquidate over 3 days); monthly review re-enables
- Position stops (from Stage 43): breached → exit order flagged urgent
- Market stress triggers: regime CRISIS or VIX > 40 → risk_scalar floor; realised fund vol > 1.5× target over 10d → de-gross to target; correlation spike (average pairwise correlation of the book > 0.6)
- Daily loss limit: −2.5% NAV in a day → no new positions next session
- Kill switch: a file/flag `data/KILL` or CLI `atlas risk kill` → flatten-to-cash order set, all pods frozen
- `MonitorReport` + `CIRCUIT_BREAKER` audit events; state persisted

## Files
`src/atlas/risk/{monitor.py,circuit_breakers.py,kill_switch.py}`, tests

## Acceptance criteria
- State-machine transitions are exhaustively tested (table-driven); HALT requires an explicit reset; the kill switch produces a flatten order set.

## Verify
`uv run pytest tests/unit/risk -q`
