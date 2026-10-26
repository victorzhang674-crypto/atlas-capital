# Stage 52 — Fund accounting: ledger, NAV, fees
**Phase:** 7 · **Size:** L · **Depends on:** 51

## Goal
Exact, double-entry fund accounting: positions, cash, margin, corporate actions, borrow, fees, and a daily NAV that reconciles to the cent.

## In scope
- `Decimal`-based double-entry ledger (`ledger_entries`: account, debit/credit, amount, ref): accounts = cash, securities (long/short), dividends receivable/payable, borrow fees, commissions, management fees, performance fees, P&L realised/unrealised
- Position keeping: average-cost and lot-level (FIFO) for realised P&L; shorts with a margin requirement (Reg T 150% simplification); cash interest on credit balances (BIL yield proxy) and debit interest
- Corporate actions: splits (adjust qty/cost), cash dividends (long receives / short pays on ex-date), delistings (close at the last price with a flag)
- Daily NAV: mark at close (adj handling), NAV per share, investor units (subscriptions/redemptions supported but optional), **2% management fee accrued daily and 20% performance fee over a high-water mark, crystallised annually** (configurable)
- Pod sub-ledgers: P&L attribution per pod (positions tagged), pod NAV-equivalent for pod drawdowns (feeds Stages 45 and 49)
- Invariants (hypothesis): assets − liabilities = NAV; sum of pod P&L = fund P&L before fees; the ledger balances

## Files
`src/atlas/accounting/{ledger.py,positions.py,corporate_actions.py,nav.py,fees.py,pods.py}`, tests

## Acceptance criteria
- Every invariant holds under random fill sequences; fee calculations match a hand-computed example; split/dividend tests pass.

## Verify
`uv run pytest tests/unit/accounting -q && uv run mypy src/atlas/accounting`
