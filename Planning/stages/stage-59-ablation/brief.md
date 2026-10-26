# Stage 59 — Ablations, benchmarks & tuning  → Milestone M5
**Phase:** 8 · **Size:** M · **Depends on:** 58 · **Spec refs:** §13 (ablation row)

## Goal
Prove each component earns its place, compare against the reference-style single agent, and settle the production configuration. This completes **M5**.

## In scope
- Ablation runner: a matrix of variants: full; −each pod; −LLM pods (quant_only); −risk engine (optimiser only); −hedging overlay; −regime scalar; −debate (director only); −memory/trust; equal-weight pods vs CIO
- "TradingAgents-style" baseline: single analyst flow per name → 5-tier rating → equal-weight long-only book of Buy/Overweight names (re-implemented minimally inside Atlas for a fair comparison on the same data/costs)
- Results table: Sharpe, MaxDD, vol, beta, turnover, LLM cost per year, marginal contribution of each component; recommendation rule: remove components that do not improve Sharpe or MaxDD OOS
- Tuning within the walk-forward protocol only; a final production `config/profiles/paper.yaml` written with a justification doc
- **Final evaluation:** a single run on the hold-out with the chosen config → `docs/results/<date>-final-evaluation.md` (honest reporting, including failures vs targets)

## Files
`src/atlas/backtest/ablation.py`, `src/atlas/backtest/baselines/tradingagents_style.py`, `docs/results/`, tests

## Acceptance criteria
- The ablation matrix runs (budget/quant modes where needed); the final evaluation doc exists with all spec §13 metrics vs targets.

## Verify
`uv run atlas backtest ablate --from 2018-01-01 --to 2021-12-31 --mode budget`
