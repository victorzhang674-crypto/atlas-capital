# Stage 04 — Core domain model
**Phase:** 0 · **Size:** M · **Depends on:** 01 · **Spec refs:** §7

## Goal
Shared, typed vocabulary for the whole fund: instruments, market data, signals, theses, portfolios, orders, fills and predictions.

## In scope
`src/atlas/core/types/` split by area:
- `instruments.py`: `AssetClass` (EQUITY, ETF, CASH), `Instrument{symbol, asset_class, exchange, sector, industry, currency, is_shortable}`; symbol normalisation (`BRK.B` ↔ `BRK-B`)
- `market.py`: `Bar{symbol, date, open, high, low, close, adj_close, volume}` with validation (high ≥ max(open, close), etc.)
- `research.py`: `Direction` (LONG/SHORT/FLAT), `Signal`, `Evidence{id, source, as_of, text, url?}`, `AnalystReport`, `MacroOutlook`, `DebateTurn`, `ReturnDistribution{p10, p50, p90}` (ordered), `InvestmentThesis` (status: ACTIVE/INVALIDATED/CLOSED/EXPIRED)
- `portfolio.py`: `View`, `PodProposal`, `CapitalAllocation`, `TargetPortfolio` (weights dict, helpers: gross, net, long/short legs), `RiskDecision`, `ComplianceResult`
- `trading.py`: `Side`, `OrderType`, `TimeInForce`, `OrderStatus` (state machine enum), `Order`, `Fill`, `Position`
- `performance.py`: `Prediction`, `Lesson`, `NAVRecord`
- All models frozen where sensible; JSON round-trip; IDs are ULIDs with prefixes (`th_`, `ord_`, …)

## Out of scope
Behaviour (OMS transitions are Stage 50; the ledger maths is Stage 52), apart from small helpers.

## Key interfaces
```python
TargetPortfolio.gross() -> float; .net() -> float; .diff(other) -> dict[str, float]
ReturnDistribution validator: p10 <= p50 <= p90
new_id(prefix: str) -> str
```

## Tasks
1. IDs + base model config (frozen, extra="forbid").
2. Each module test-first: validation edge cases, JSON round-trip.
3. Symbol normalisation tests (class shares, lower case, whitespace).
4. `TargetPortfolio` helpers with hypothesis tests (gross ≥ |net|).

## Tests
Validation rejects bad bars, unordered distributions, confidence outside [0,1], weights containing NaN; round-trip for every model.

## Acceptance criteria
- Every contract in spec §7 exists, validated and round-trippable; mypy strict passes for `core/types`.

## Verify
`uv run pytest tests/unit/core/types -q && uv run mypy src/atlas/core`
