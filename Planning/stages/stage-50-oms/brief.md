# Stage 50 — Order management system
**Phase:** 7 Execution · **Size:** M · **Depends on:** 04, 48

## Goal
Turn an approved target portfolio into orders and track each order's lifecycle: one OMS for backtest, paper and (future) live.

## In scope
- Order generation: `target − current` in shares (round lots to 1 share; respect min trade size $ and a 0.1% NAV no-trade band); split oversized orders by the ADV participation cap across days (parent/child orders); urgency from the source (stop/kill = urgent; rebalance = normal)
- Order state machine: NEW → (compliance) → ACCEPTED → SUBMITTED → PARTIALLY_FILLED → FILLED | CANCELED | REJECTED | EXPIRED; invalid transitions raise
- Order book store (DuckDB `orders`, `fills`), idempotency keys (run_id + symbol + seq)
- `ExecutionVenue` protocol: `submit(order)`, `cancel(id)`, `poll() -> list[Fill|StatusUpdate]` (implemented by the simulator in Stage 51 and Alpaca in Stage 60)
- Position attribution: each order carries the `pod` tag(s); net orders across pods (internal crossing) to avoid trading against yourself, with fills allocated back to pods pro rata

## Files
`src/atlas/execution/{oms.py,order_gen.py,state_machine.py,crossing.py,venue.py}`, tests

## Acceptance criteria
- State-machine transitions are fully tested; internal crossing reduces gross traded in tests; compliance-blocked orders never reach the venue.

## Verify
`uv run pytest tests/unit/execution -q`
