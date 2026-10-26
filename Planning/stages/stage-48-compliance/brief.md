# Stage 48 — Compliance engine
**Phase:** 6 · **Size:** M · **Depends on:** 04, 06

## Goal
Deterministic pre-trade and post-trade compliance with an immutable record. It simulates what a real fund's compliance desk enforces.

## In scope
- Rules (each a class with `check(order_set, book, ctx) -> list[Violation]`): restricted list (`config/restricted.yaml`, time-bounded), short-sale locate (simulated borrow availability: easy / hard-to-borrow by market cap and short-interest proxy; HTB → borrow fee tier, no locate → block), wash-sale flag (re-buy within 30 days of a realised loss; flag, not block; tax note), max ownership (≤ 4.9% of shares outstanding: mock 13D threshold), PDT not applicable (note), prohibited instruments (leveraged/inverse ETFs unless whitelisted), trading-halt check (no bar for the symbol today → block)
- Severity: BLOCK (remove the order), WARN (log)
- `ComplianceResult{approved_orders, blocked[], warnings[]}`; `COMPLIANCE` audit events
- Post-trade: a daily compliance attestation summary

## Files
`src/atlas/compliance/{rules/*.py,checker.py,borrow.py,restricted.py}`, `config/restricted.yaml`, tests

## Acceptance criteria
- Each rule has positive/negative tests; blocked orders never reach the OMS (integration test in Stage 50).

## Verify
`uv run pytest tests/unit/compliance -q`
