# Atlas Capital — Master Roadmap

> **This is the overall plan.** Under [`Project.md`](../Project.md) Rule 3, it wins whenever another document collides with it. Changing it requires the owner's explicit approval.

**66 stages in 10 phases, with 6 milestones.** Each stage is sized to fit **one chat**: roughly 300–900 lines of production code plus tests, about 1–3 hours of agent time.

**Stages run strictly in numeric order (01 → 66).** Under `Project.md` Rule 4, Stage N may not start until Stage N−1 is `DONE` with verification evidence in `PROGRESS.md`. The *Depends on* column only says which earlier stages a stage builds on; it never permits running a stage early.

**Each stage has its own folder** `stages/stage-NN-<slug>/`, holding `brief.md`, plus `plan.md`, `worklog.md`, `verification.md` and `artifacts/` once the stage starts (`Project.md` Rule 2A). Code lives in the shared `src/atlas/` and `tests/`.

Size key: **S** about 300 LOC · **M** about 600 LOC · **L** about 900 LOC (L stages contain sub-steps; you may split one into "part 1 / part 2" chats if context runs low. Both parts record into the same stage folder).

---

## Phase 0 — Foundation (Stages 01–06)

| # | Stage | Size | Depends on |
|---|---|---|---|
| 01 | [Project scaffolding & toolchain](stages/stage-01-project-scaffolding/brief.md) | S | — |
| 02 | [Configuration system & fund mandate](stages/stage-02-configuration/brief.md) | S | 01 |
| 03 | [Logging, run context & audit trail](stages/stage-03-logging-audit/brief.md) | S | 02 |
| 04 | [Core domain model](stages/stage-04-domain-model/brief.md) | M | 01 |
| 05 | [Clock, trading calendar & point-in-time guard](stages/stage-05-clock-pit/brief.md) | M | 04 |
| 06 | [Storage layer (DuckDB + Parquet lake)](stages/stage-06-storage/brief.md) | M | 05 |

## Phase 1 — Data Platform (Stages 07–14) → **Milestone M1: point-in-time data platform**

| # | Stage | Size | Depends on |
|---|---|---|---|
| 07 | [Price data providers (yfinance, Alpaca)](stages/stage-07-price-data/brief.md) | M | 06 |
| 08 | [Universe & historical index membership](stages/stage-08-universe/brief.md) | M | 07 |
| 09 | [Point-in-time fundamentals (SEC EDGAR)](stages/stage-09-fundamentals/brief.md) | L | 06 |
| 10 | [Macro data (FRED / ALFRED)](stages/stage-10-macro/brief.md) | M | 06 |
| 11 | [News data pipeline](stages/stage-11-news/brief.md) | M | 06 |
| 12 | [Events calendar & sentiment sources](stages/stage-12-events-sentiment/brief.md) | M | 11 |
| 13 | [Data quality checks & DataView facade](stages/stage-13-data-quality-dataview/brief.md) | M | 07–12 |
| 14 | [Data sync CLI & bootstrap backfill](stages/stage-14-data-cli/brief.md) | S | 13 |

## Phase 2 — Quant Engine (Stages 15–23) → **Milestone M2: quant research platform**

| # | Stage | Size | Depends on |
|---|---|---|---|
| 15 | [Technical feature library](stages/stage-15-technical-features/brief.md) | M | 13 |
| 16 | [Fundamental factor library](stages/stage-16-fundamental-factors/brief.md) | M | 13 |
| 17 | [Signal research toolkit (IC, quantiles, decay)](stages/stage-17-signal-research/brief.md) | M | 15, 16 |
| 18 | [Covariance & volatility forecasting](stages/stage-18-covariance-vol/brief.md) | M | 15 |
| 19 | [Factor risk model](stages/stage-19-factor-risk-model/brief.md) | L | 16, 18 |
| 20 | [Risk & performance metrics library](stages/stage-20-risk-metrics/brief.md) | M | 18 |
| 21 | [Regime detection](stages/stage-21-regime-detection/brief.md) | M | 10, 15 |
| 22 | [Systematic alpha models](stages/stage-22-systematic-alpha/brief.md) | M | 17, 19 |
| 23 | [Statistical arbitrage (pairs)](stages/stage-23-stat-arb/brief.md) | M | 17 |

## Phase 3 — LLM & Agent Infrastructure (Stages 24–28)

| # | Stage | Size | Depends on |
|---|---|---|---|
| 24 | [LLM gateway, model tiers & cost budget](stages/stage-24-llm-gateway/brief.md) | M | 03 |
| 25 | [LLM record/replay cache](stages/stage-25-llm-cache/brief.md) | S | 24 |
| 26 | [Agent framework, prompts & tool registry](stages/stage-26-agent-framework/brief.md) | M | 13, 25 |
| 27 | [Memory system (episodic + lessons, point-in-time)](stages/stage-27-memory/brief.md) | M | 26 |
| 28 | [LangGraph orchestration skeleton](stages/stage-28-langgraph-skeleton/brief.md) | M | 26 |

## Phase 4 — Research Division (Stages 29–36) → **Milestone M3: thesis for any ticker (goes beyond the reference's scope)**

| # | Stage | Size | Depends on |
|---|---|---|---|
| 29 | [Fundamental analyst agent](stages/stage-29-fundamental-analyst/brief.md) | M | 16, 28 |
| 30 | [Technical analyst agent](stages/stage-30-technical-analyst/brief.md) | S | 15, 28 |
| 31 | [News & event analyst agent](stages/stage-31-news-analyst/brief.md) | M | 12, 28 |
| 32 | [Sentiment analyst agent](stages/stage-32-sentiment-analyst/brief.md) | S | 12, 28 |
| 33 | [Macro economist agent](stages/stage-33-macro-economist/brief.md) | M | 21, 28 |
| 34 | [Quant analyst agent](stages/stage-34-quant-analyst/brief.md) | S | 19, 22, 28 |
| 35 | [Bull / Bear / Red-team debate](stages/stage-35-debate/brief.md) | M | 29–34 |
| 36 | [Research Director & thesis registry](stages/stage-36-research-director/brief.md) | M | 35, 27 |

## Phase 5 — Portfolio Management (Stages 37–43)

| # | Stage | Size | Depends on |
|---|---|---|---|
| 37 | [Screening & research prioritisation funnel](stages/stage-37-screening-funnel/brief.md) | M | 22, 36 |
| 38 | [Pod framework & Fundamental L/S pod](stages/stage-38-pod-framework/brief.md) | L | 36 |
| 39 | [Systematic & Stat-Arb pods](stages/stage-39-quant-pods/brief.md) | M | 22, 23, 38 |
| 40 | [Global Macro & Event-Driven pods](stages/stage-40-macro-event-pods/brief.md) | L | 33, 38 |
| 41 | [Views → Black-Litterman posterior](stages/stage-41-black-litterman/brief.md) | M | 19, 38 |
| 42 | [Portfolio optimizer (cvxpy)](stages/stage-42-optimizer/brief.md) | L | 41 |
| 43 | [Position sizing, stops & hedging overlay](stages/stage-43-sizing-hedging/brief.md) | M | 42 |

## Phase 6 — Risk, Compliance & Governance (Stages 44–49) → **Milestone M4: complete decision stack**

| # | Stage | Size | Depends on |
|---|---|---|---|
| 44 | [Risk engine: pre-trade hard limits](stages/stage-44-risk-engine-pretrade/brief.md) | L | 20, 19, 43 |
| 45 | [Risk monitor & circuit breakers](stages/stage-45-circuit-breakers/brief.md) | M | 44 |
| 46 | [Stress testing & scenarios](stages/stage-46-stress-testing/brief.md) | M | 19, 44 |
| 47 | [CRO agent & risk memo](stages/stage-47-cro-agent/brief.md) | S | 44–46 |
| 48 | [Compliance engine](stages/stage-48-compliance/brief.md) | M | 04, 06 |
| 49 | [CIO capital allocation & Investment Committee](stages/stage-49-cio-ic/brief.md) | L | 38–40, 44 |

## Phase 7 — Execution & Operations (Stages 50–54)

| # | Stage | Size | Depends on |
|---|---|---|---|
| 50 | [Order management system](stages/stage-50-oms/brief.md) | M | 04, 48 |
| 51 | [Execution simulator & transaction-cost models](stages/stage-51-execution-sim/brief.md) | M | 50 |
| 52 | [Fund accounting: ledger, NAV, fees](stages/stage-52-accounting/brief.md) | L | 51 |
| 53 | [Prediction scoring & performance attribution](stages/stage-53-attribution/brief.md) | M | 52, 19 |
| 54 | [Learning loop: reflection & trust weights](stages/stage-54-learning-loop/brief.md) | M | 27, 53 |

## Phase 8 — Fund Orchestration & Backtesting (Stages 55–59) → **Milestone M5: full-fund backtest with evidence**

| # | Stage | Size | Depends on |
|---|---|---|---|
| 55 | [Fund daily cycle graph (end-to-end)](stages/stage-55-fund-cycle/brief.md) | L | 37–54 |
| 56 | [Event-driven backtest engine](stages/stage-56-backtest-engine/brief.md) | L | 55 |
| 57 | [Backtest analytics & tear sheet](stages/stage-57-tear-sheet/brief.md) | M | 56 |
| 58 | [Robustness: walk-forward, deflated Sharpe, PBO](stages/stage-58-robustness/brief.md) | M | 57 |
| 59 | [Ablations, benchmarks & tuning](stages/stage-59-ablation/brief.md) | M | 58 |

## Phase 9 — Paper Trading & Production (Stages 60–66) → **Milestone M6: paper-trading fund running**

| # | Stage | Size | Depends on |
|---|---|---|---|
| 60 | [Alpaca paper broker adapter & reconciliation](stages/stage-60-broker-alpaca/brief.md) | M | 52 |
| 61 | [Scheduler & live runner](stages/stage-61-scheduler/brief.md) | M | 55, 60 |
| 62 | [Monitoring, alerts & kill switch](stages/stage-62-monitoring/brief.md) | M | 61 |
| 63 | [Reporting: daily risk report & letters](stages/stage-63-reporting/brief.md) | M | 53, 57 |
| 64 | [Streamlit dashboard](stages/stage-64-dashboard/brief.md) | L | 63 |
| 65 | [CLI polish & documentation](stages/stage-65-cli-docs/brief.md) | S | 64 |
| 66 | [Hardening, end-to-end tests & paper burn-in](stages/stage-66-hardening/brief.md) | M | all |

---

## Milestones and what you can do at each

| Milestone | After stage | You can… |
|---|---|---|
| **M1** | 14 | Backfill and query 8+ years of point-in-time prices, fundamentals, macro and news for the S&P 500 |
| **M2** | 23 | Research factors, measure IC, build a risk model, run vectorised factor and pairs backtests |
| **M3** | 36 | Run `atlas research AAPL --date 2024-05-01` and get a debated, cited `InvestmentThesis` |
| **M4** | 49 | Produce a risk-approved, compliance-checked target portfolio for the whole fund on any date |
| **M5** | 59 | Run multi-year backtests with costs, a tear sheet, walk-forward, deflated Sharpe and ablations |
| **M6** | 66 | Run Atlas daily against an Alpaca paper account, with dashboard, alerts and reports |

## Order of execution

- Strictly sequential: 01 → 02 → 03 → … → 66. Running stages in parallel or out of order is not allowed (`Project.md` Rule 4).
- Some stages depend only on much earlier ones (for example 09–11 on 06, 24 on 03, 48 on 04/06). This is informational only. It tells a stage chat what it builds on, not that it may run early.
- The only way to deviate is an explicit owner request in chat, confirmed after a reminder of Rule 4 and recorded in the Decisions log of `PROGRESS.md`.

## Rough effort and cost guide

- 66 chats of about 1–3 hours each, so roughly 100–150 agent-hours in total.
- Development LLM spend for the fund itself (separate from the coding assistant): under $20 until Phase 4; Phase 8 backtests are the most expensive. Use the replay cache and "budget mode" (Stage 56) to keep each full backtest under the configured cap.
