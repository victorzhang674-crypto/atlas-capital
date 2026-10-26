# Stage 66 — Hardening, end-to-end tests & paper burn-in  → Milestone M6
**Phase:** 9 · **Size:** M · **Depends on:** all

## Goal
Production-grade confidence: end-to-end tests, performance, security, and a structured paper-trading burn-in. This completes **M6**.

## In scope
- End-to-end suite: fresh clone → `uv sync` → fixture data → research → fund day → 30-day mini backtest → reports → dashboard smoke, all offline
- Chaos tests: vendor outage, LLM timeout/invalid output, broker reject, partial fills, a crash in every job, a corrupted cache, a clock jump; each must end in a fail-safe state, never in unintended risk
- Performance profiling of the Fund Day (target: < 10 min daily run with 25 research names; full-universe quant steps < 2 min); fix the top hotspots
- Security review: secrets only in env/.env (gitignored), no keys in logs (log scrubber test), dependency audit (`pip-audit`), paper-only guard, Telegram whitelist
- Audit-chain verification job daily
- **Paper burn-in plan** (`docs/burn-in.md`): 4+ weeks of daily paper operation; a checklist of daily reviews; success gates (no unreconciled breaks, no unhandled exceptions, live tracking error vs the same-day simulation within tolerance, behaviour of risk limits as expected); a weekly review template
- Final `PROGRESS.md` retrospective + v1.0.0 tag

## Files
`tests/e2e/**`, `tests/chaos/**`, `docs/burn-in.md`, `docs/security.md`, profiling notes

## Acceptance criteria
- The e2e and chaos suites are green; a security checklist is complete; the burn-in has started, with day 1 reports produced.

## Verify
`uv run pytest -m "integration or e2e" -q && uv run atlas doctor && uv run atlas live status`

> Real-money trading is **out of scope**. If you later decide to pursue it, that is your own decision, and it calls for professional, legal and regulatory review beyond this plan.
