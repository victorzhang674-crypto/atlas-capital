# Stage 60 — Alpaca paper broker adapter & reconciliation
**Phase:** 9 Production · **Size:** M · **Depends on:** 52 · **Spec refs:** A4

## Goal
Connect the OMS to an Alpaca **paper** account and keep the internal ledger reconciled with the broker.

## In scope
- `AlpacaVenue(ExecutionVenue)` using `alpaca-py`: submit market/limit/MOO orders, TIF, client_order_id = OMS idempotency key, cancel, stream or poll trade updates → Fills
- Account sync: positions, cash, buying power, shortability (`easy_to_borrow`) → feeds compliance borrow
- **Safety:** the base URL is forced to paper in v1; a startup check asserts `account.account_number` is a paper account, otherwise refuse; there is no code path to live endpoints (a test asserts this)
- Reconciliation: internal positions/cash vs broker after each sync → breaks report (qty diff, cash diff > $1) → `RECON_BREAK` audit + alert; auto-adopt broker truth for fills only, never silently for positions
- Rate-limit handling, retries, clock skew tolerance
- Recorded HTTP fixtures for tests; `@pytest.mark.live` paper smoke test (places and cancels a far-limit order)

## Files
`src/atlas/execution/brokers/alpaca.py`, `src/atlas/live/reconciliation.py`, tests

## Acceptance criteria
- Paper-only enforcement is tested; reconciliation detects injected breaks; the live smoke test passes with the owner's paper keys.

## Verify
`uv run pytest tests/unit/execution/brokers -q && uv run pytest -m live tests/live/test_alpaca_paper.py`
