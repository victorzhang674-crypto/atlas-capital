# Stage 62 — Monitoring, alerts & kill switch
**Phase:** 9 · **Size:** M · **Depends on:** 61

## Goal
Know immediately when something is wrong, and be able to stop the fund from your phone.

## In scope
- Health checks: data freshness, job success, heartbeat age, broker connectivity, reconciliation breaks, LLM budget usage, error counts, disk space
- Alert channels (pluggable, all optional): email (SMTP), Telegram bot, Slack webhook, desktop notification; severity routing (INFO digest daily, WARN immediate, CRITICAL immediate + repeat)
- Alert events: circuit-breaker transitions, limit breaches, recon breaks, job failures, budget > 80%, large daily P&L (|r| > 2σ), kill switch activation
- Remote kill switch: Telegram command `/kill` (only from a whitelisted chat ID) or the CLI `atlas risk kill`; `/status` returns NAV, P&L and exposures
- Metrics export: Prometheus-format text file (optional) + DuckDB `health_checks` history

## Files
`src/atlas/live/{monitoring.py,health.py}`, `src/atlas/live/alerts/{base.py,email.py,telegram.py,slack.py}`, tests

## Acceptance criteria
- Each check and the routing logic are tested with fake channels; the kill command path is tested end to end with a fake bot.

## Verify
`uv run pytest tests/unit/live -q`
