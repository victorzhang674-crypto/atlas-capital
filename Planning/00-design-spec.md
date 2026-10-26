# Atlas Capital — Multi-Agent Hedge Fund Simulator · Design Spec

- **Date:** 2026-10-25
- **Status:** Draft v1. Approve it, or mark changes, before Stage 01 starts.
- **Method:** Superpowers *brainstorming* → *writing-plans* (design first, then a staged plan; each stage is expanded into a bite-sized TDD plan at the start of its own chat)
- **Reference:** [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) (v0.7.0, Oct 2026)

> Atlas is a research and paper-trading system. Nothing here promises profit. Backtests overstate real results. Moving to real money is outside this plan and is the owner's own decision (see §15).

---

## 1. Vision

Build a software hedge fund. LLM agents act as the firm's analysts, PMs, CIO and risk officers. A deterministic quant core does the maths: it sizes, constrains, hedges and accounts for every dollar.

The fund aims for **better risk-adjusted returns** (higher Sharpe, smaller drawdowns, low market beta), not maximum raw return. It gets there through four things:

1. **Diversification across uncorrelated strategy pods** (fundamental long/short, systematic factors, stat-arb, global macro, event-driven), like a multi-manager platform.
2. **Hard, quantitative risk management.** Limits are enforced in code. The LLM can explain a limit or argue about it, but cannot override it.
3. **Portfolio construction by optimisation.** LLM convictions become calibrated *views* that feed a Black-Litterman / mean-variance optimiser with risk budgets.
4. **Accountability and learning.** Every prediction is scored. Agents and pods that forecast badly lose influence and capital.

## 2. Reference system: what it does and what it lacks

| Area | TradingAgents (reference) | Atlas (ours) |
|---|---|---|
| Unit of work | One ticker, one date, one run | A whole fund: a universe of about 500 stocks plus about 30 ETFs, run every day |
| Analysts | Market, sentiment, news, fundamentals | The same, plus a macro economist, a quant analyst and an event/earnings analyst |
| Debate | Bull vs bear, then a research manager | Bull vs bear vs **red team**, evidence must be cited, the output is a structured thesis with kill criteria |
| Decision output | 5-tier rating (Buy…Sell) | An expected-return distribution, conviction, horizon and catalysts, turned into **target weights** |
| Portfolio | Caller-supplied holdings as context only | A real book: positions, cash, margin, borrow, fees, NAV |
| Sizing | None (the docs say it is out of scope) | Vol-targeting, fractional-Kelly caps, risk-budget optimisation |
| Risk | Three LLM personas debating (aggressive, neutral, conservative) | Deterministic risk engine (VaR/CVaR, factor and beta exposure, liquidity, drawdown circuit breakers) **plus** a CRO agent for the narrative |
| Strategies | One discretionary flow | Five pods with separate mandates and risk budgets; the CIO allocates capital between them |
| Quant | Indicators read by the LLM | Factor library, factor risk model, covariance shrinkage, regime HMM, pairs cointegration |
| Execution | None | OMS, slippage and market-impact models, TCA, Alpaca paper broker |
| Compliance | None | Restricted list, short-locate/borrow, wash-sale flags, immutable audit log |
| Backtest | Scores ratings over a ticker×date grid | Event-driven, point-in-time portfolio simulation with costs, walk-forward, deflated Sharpe, PBO and ablations |
| Memory | Decision log plus reflections for the PM | Episodic memory per agent, retrieval of similar situations, calibration scores that change agent weights |
| Ops | CLI reports | Scheduler, monitoring, kill switch, dashboard, daily risk report, investor letters |

**What we keep from the reference:** LangGraph orchestration; separate analysts running in parallel, each with its own tools; strict point-in-time data access; quick and deep model tiers; structured (Pydantic) agent outputs; checkpoint/resume; reflections settled after the outcome is known.

## 3. Design principles (non-negotiable)

1. **LLMs judge, maths decides size, risk decides last.** LLM output is a view with a confidence. Only the optimiser turns views into weights, and the risk engine can resize or veto anything.
2. **Point-in-time everywhere.** All data goes through `DataView(as_of=...)`. Reading data timestamped after `as_of` raises `LookAheadError`. Memory and lessons are point-in-time as well.
3. **Determinism and replay.** Every LLM call is cached by content hash. A backtest re-run in replay mode makes zero API calls and gives identical results.
4. **Quant funnel before LLM.** A cheap quant screen picks around 20–40 names a day for deep LLM research. LLM spend is capped by a budget per day and per run.
5. **Everything is a recorded, scored decision.** Theses, views and proposals have IDs, timestamps and later outcomes.
6. **Fail safe.** A missing input, a vendor outage, a malformed LLM output or a limit breach leads to *no new risk*, never to a guess.
7. **YAGNI and TDD.** Each stage builds only what it needs, test-first.

## 4. Organisation chart

```
                               ┌───────────────────────┐
                               │  Fund Owner (you)     │  kill switch, mandate, limits
                               └──────────┬────────────┘
                                          │
          ┌───────────────────────────────┼─────────────────────────────────┐
          │                               │                                 │
 ┌────────▼────────┐            ┌─────────▼─────────┐              ┌────────▼────────┐
 │ CIO agent       │◄──────────►│ Investment        │              │ CRO agent +     │
 │ capital to pods │            │ Committee (vote)  │              │ RISK ENGINE     │ ◄─ hard veto
 └────────┬────────┘            └─────────▲─────────┘              │ (deterministic) │
          │                               │                        └────────▲────────┘
 ┌────────▼──────────────────────────────────────────────────────┐          │
 │ STRATEGY PODS (each: PM + mandate + risk budget + book)        │──────────┤
 │ Fundamental L/S │ Systematic │ Stat-Arb │ Global Macro │ Event │          │
 └────────▲──────────────────────────────────────────────────────┘          │
          │ theses / signals                                                │
 ┌────────┴──────────────────────────┐   ┌───────────────────────────┐      │
 │ RESEARCH DIVISION                 │   │ QUANT ENGINE              │      │
 │ Fundamental · Technical · News ·  │   │ factors · risk model ·    │──────┘
 │ Sentiment · Macro · Quant analyst │◄──│ regimes · alpha · pairs   │
 │ Bull ⇄ Bear ⇄ Red Team debate     │   └───────────────────────────┘
 │ Research Director → Thesis        │
 └───────────────────────────────────┘
          │ target portfolio (post-risk)
 ┌────────▼────────┐   ┌──────────────┐   ┌────────────────┐   ┌─────────────────┐
 │ Compliance      │──►│ OMS /        │──►│ Fund accounting│──►│ Performance &   │
 │ pre-trade       │   │ Execution    │   │ NAV, fees      │   │ learning loop   │
 └─────────────────┘   └──────────────┘   └────────────────┘   └─────────────────┘
```

## 5. The Fund Day (one cycle)

| # | Step | Owner | Type |
|---|---|---|---|
| 1 | Refresh data and run quality checks; the cycle aborts on a critical failure | Data layer | Deterministic |
| 2 | Mark the book to market, update NAV, run circuit-breaker checks | Accounting + risk | Deterministic |
| 3 | Detect the regime (volatility, trend, macro quadrant) | Quant engine | Deterministic |
| 4 | Write the macro briefing (MacroOutlook) | Macro economist | LLM (deep) |
| 5 | Screen the universe and pick a research queue (new candidates, holdings due for review, triggered theses) | Screener | Deterministic |
| 6 | Run analyst reports in parallel for each queued name | Research analysts | LLM (quick) |
| 7 | Bull ⇄ Bear ⇄ Red-team debate, then the InvestmentThesis | Debate + Research Director | LLM (quick/deep) |
| 8 | Pods propose their books (views and signals within their mandates) | Pod PMs | LLM + deterministic |
| 9 | Allocate capital and risk budgets to pods | CIO | Hybrid |
| 10 | Turn views into a Black-Litterman posterior, optimise, size and hedge | Portfolio construction | Deterministic |
| 11 | Pre-trade risk check: approve, resize or reject | Risk engine | Deterministic |
| 12 | Write the risk narrative and challenge it; IC vote on large changes | CRO + IC | LLM (deep) |
| 13 | Pre-trade compliance check | Compliance | Deterministic |
| 14 | Generate orders, then simulate or route the executions | OMS / execution | Deterministic |
| 15 | Post-trade: ledger, TCA, attribution, settle past predictions | Ops + performance | Deterministic |
| 16 | Reflect on settled outcomes, update lessons and calibration weights | Reflection agent | LLM (quick) |
| 17 | Write reports and the audit trail | Reporting | Hybrid |

Cadence: risk and accounting run daily, the systematic and stat-arb pods daily, the LLM pods weekly (with event triggers in between), and CIO reallocation monthly.

## 6. Agent roster

| Agent | Kind | Model tier | Output contract |
|---|---|---|---|
| Fundamental Analyst | LLM + tools | quick | `AnalystReport` |
| Technical Analyst | LLM (reads computed features) | quick | `AnalystReport` |
| News & Event Analyst | LLM + tools | quick | `AnalystReport` |
| Sentiment Analyst | LLM + tools | quick | `AnalystReport` |
| Quant Analyst | LLM (explains signals and factor risk) | quick | `AnalystReport` |
| Macro Economist | LLM + FRED + regime | deep | `MacroOutlook` |
| Bull / Bear / Red Team | LLM debaters | quick | `DebateTurn` |
| Research Director | LLM | deep | `InvestmentThesis` |
| Pod PMs (Fundamental, Macro, Event) | LLM | deep | `PodProposal` |
| Systematic and Stat-Arb pods | Deterministic | n/a | `PodProposal` |
| CIO | Hybrid (risk-parity maths + LLM tilt within ±bands) | deep | `CapitalAllocation` |
| Investment Committee | 3 LLM voters | deep | `ICDecision` |
| CRO | LLM narrative (cannot loosen limits) | deep | `RiskMemo` |
| Risk Engine | Deterministic | n/a | `RiskDecision` |
| Compliance | Deterministic | n/a | `ComplianceResult` |
| Reflection | LLM | quick | `Lesson` |
| Report Writer | LLM, working only from supplied facts | quick | Markdown/HTML |

## 7. Core data contracts (Pydantic, `atlas.core.types`)

- `AnalystReport{ticker, as_of, analyst, summary, signals[{name, direction, strength, evidence_ids}], risks[], confidence∈[0,1]}`
- `InvestmentThesis{id, ticker, as_of, direction, exp_return{p10,p50,p90}, horizon_days, conviction∈[0,1], catalysts[], kill_criteria[], evidence[], status}`
- `PodProposal{pod, as_of, views[{ticker, exp_return, confidence, horizon}], target_weights?, rationale}`
- `CapitalAllocation{as_of, pod_weights, risk_budgets, rationale}`
- `TargetPortfolio{as_of, weights{ticker: w}, hedges, expected_vol, expected_beta}`
- `RiskDecision{status∈{APPROVE, RESIZE, REJECT}, adjusted_weights, breaches[], metrics}`
- `Order / Fill / Position / LedgerEntry / NAVRecord`
- `Prediction{id, source_agent, made_at, target, horizon, forecast, settled_at?, outcome?, score?}`

## 8. Strategy pods (defaults; set in `config/pods/*.yaml`)

| Pod | Universe | Edge | Horizon | Rebalance | Initial risk budget |
|---|---|---|---|---|---|
| Fundamental L/S | S&P 500 | LLM research theses | 1–6 months | Weekly + triggers | 30% |
| Systematic Factor | S&P 500 | Value/quality/momentum/low-vol composite, sector-neutral | 1–3 months | Weekly | 25% |
| Stat-Arb | S&P 500 pairs within sector | Cointegration mean reversion | Days–weeks | Daily | 15% |
| Global Macro | ~30 liquid ETFs (equity regions, sectors, rates, credit, gold, USD, commodities) | Macro regime + trend | Weeks–months | Weekly | 20% |
| Event-Driven | Names with earnings in the next 10 days | Pre-earnings drift + post-earnings drift (PEAD) read by LLM | Days | Event-based | 10% |

## 9. Risk framework (defaults in `config/limits.yaml`)

| Limit | Default |
|---|---|
| Target annualised volatility (fund) | 10% |
| Gross exposure | ≤ 200% NAV |
| Net exposure | −10% … +40% NAV |
| Ex-ante beta to SPY | −0.10 … +0.30 |
| Single name | ≤ 5% NAV long, ≤ 3% short |
| Sector net | ≤ ±15% NAV |
| Style factor exposure | ≤ ±0.5σ each |
| Liquidity | Order ≤ 5% of 20-day ADV; position liquidates in ≤ 3 days at 10% ADV |
| 1-day 95% VaR (historical) | ≤ 1.5% NAV |
| 1-day 97.5% CVaR | ≤ 2.5% NAV |
| Stress loss (worst historical scenario) | ≤ 15% NAV |
| Drawdown circuit breakers | −5%: de-gross 25% · −10%: de-gross 50%, no new names · −15%: flatten to hedged core and halt; owner must reset |
| Pod stop | Pod drawdown −8% from its high-water mark: risk budget halved; −12%: pod frozen |

## 10. Learning loop

Each `Prediction` is settled after its horizon. Scores are Brier scores (direction), CRPS-lite (return quantiles) and alpha vs benchmark. An agent's rolling calibration score becomes a **trust weight** used (a) in Black-Litterman view confidence and (b) in the CIO's pod allocation. The Reflection agent writes short lessons, which are stored point-in-time and retrieved by similarity for later decisions.

## 11. Tech stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12, `uv` | Matches the reference; fast env management |
| Orchestration | LangGraph | Proven in the reference; subgraphs, parallel fan-out, checkpoints |
| Data models | Pydantic v2 | Typed contracts and structured LLM outputs |
| Storage | DuckDB + Parquet lake | Embedded, fast analytics, no server |
| Dataframes | pandas (+ polars where hot) | Ecosystem |
| Numerics | numpy, scipy, statsmodels, scikit-learn, arch, hmmlearn | Factor models, cointegration, GARCH, HMM |
| Optimisation | cvxpy (OSQP/Clarabel) | Constrained portfolio optimisation |
| Calendars | exchange_calendars | Trading days and sessions |
| LLM | Anthropic Claude by default (deep: `claude-opus-5-5`, quick: `claude-haiku-5-5`), OpenAI-compatible optional | Provider-agnostic gateway |
| Vector memory | ChromaDB (local) | Similar-situation retrieval |
| Market data | yfinance (free), Alpaca market data (free with a paper account) | Free to start |
| Fundamentals | SEC EDGAR companyfacts (XBRL, keyed by filing date) | Free and point-in-time |
| Macro | FRED / ALFRED | Free, with vintages |
| News | Alpaca News API (Benzinga, history from 2015), Yahoo fallback | Free, timestamped |
| Broker (paper) | Alpaca paper trading | Free, good API |
| CLI / UI | Typer + Rich; Streamlit dashboard | Quick to build |
| Quality | pytest, hypothesis, ruff, mypy (strict on `core/`, `risk/`, `accounting/`) | TDD |

## 12. Repository layout

```
Trading Agents/
├── Project.md                   ← governing rules, read before every task
├── CLAUDE.md                    ← auto-loaded; imports @Project.md (+ conventions from Stage 01)
├── Planning/                    ← this plan; stages/stage-NN-<slug>/ holds each stage's brief, plan, worklog, verification, artifacts
├── pyproject.toml
├── config/ fund.yaml · limits.yaml · pods/*.yaml · universe.yaml
├── src/atlas/
│   ├── core/          config, logging, audit, clock, calendar, types, storage, errors
│   ├── data/          providers/, universe, prices, fundamentals, macro, news, events, quality, view.py
│   ├── quant/         features/, factors, research, risk_model/, metrics, regime, alpha/, statarb
│   ├── llm/           gateway, providers/, cache, cost
│   ├── agents/        base, prompts/, tools, memory/, research/, debate/, director, pods/, cio, cro, committee, reflection
│   ├── portfolio/     screening, views, black_litterman, optimizer, sizing, hedging
│   ├── risk/          limits, engine, monitor, circuit_breakers, stress
│   ├── compliance/    rules, checker, restricted
│   ├── execution/     oms, cost_models, simulator, tca, brokers/
│   ├── accounting/    ledger, nav, fees, corporate_actions
│   ├── performance/   predictions, scoring, attribution, scorecards
│   ├── orchestration/ state, fund_graph, cycle
│   ├── backtest/      engine, analytics, robustness, ablation
│   ├── live/          scheduler, runner, reconciliation, monitoring, alerts
│   ├── reporting/     daily_risk, letters, html
│   ├── dashboard/     streamlit app
│   └── cli/           typer app
├── tests/ unit/ · integration/ · fixtures/
└── data/  (gitignored lake: raw/, curated/, cache/, llm_cache/, runs/)
```

## 13. Success criteria

Engineering (each stage): all tests green, ruff and mypy clean, `PROGRESS.md` updated, one commit per stage.

Fund performance targets (out-of-sample walk-forward backtest, 2018-01 → latest, net of costs). These are **targets, not promises**:

| Metric | Target | Baselines to beat |
|---|---|---|
| Sharpe ratio | ≥ 1.0 | SPY buy-and-hold, 60/40, single-agent TradingAgents-style flow |
| Max drawdown | ≤ 15% | SPY's maximum drawdown in the same window |
| Beta to SPY | 0.0 – 0.3 | n/a |
| Annualised volatility | 8–12% | n/a |
| Deflated Sharpe probability | ≥ 0.90 | n/a |
| Probability of backtest overfitting (PBO) | ≤ 0.30 | n/a |
| Ablation | Each pod and the risk engine must improve Sharpe or drawdown, or be removed | n/a |

## 14. Scope and non-goals

**In scope:** US equities and ETFs; daily-bar decisions; long/short with simulated borrow; backtest, paper trading, dashboard and reports.

**Non-goals (v1):** intraday/HFT, options and futures execution (ETF proxies are used for rates, commodities and FX), real-money trading, multi-user SaaS, paid data feeds (these can be plugged in later through provider interfaces).

## 15. Assumptions (defaults chosen; change any before the stage that uses it)

| # | Decision | Default | Used from |
|---|---|---|---|
| A1 | Market | US equities (S&P 500) + ~30 ETFs | Stage 08 |
| A2 | LLM provider | Anthropic Claude, provider-agnostic gateway | Stage 24 |
| A3 | Data budget | Free sources only | Stage 07 |
| A4 | Broker | Alpaca **paper** only; any live switch is owner-operated, behind a config flag plus a typed confirmation, and not built in v1 | Stage 60 |
| A5 | Decision frequency | Daily risk, weekly LLM rebalancing | Stage 55 |
| A6 | Simulated starting capital | $10,000,000 | Stage 02 |
| A7 | Backtest window | 2018-01-01 → latest (in-sample to 2021-12, OOS 2022→) | Stage 56 |
| A8 | Daily LLM budget | $5/day live; per-backtest cap set in config | Stage 24 |
| A9 | Benchmarks | SPY, 60/40 (SPY/AGG), cash (BIL) | Stage 57 |

## 16. Project risks and mitigations

| Risk | Mitigation |
|---|---|
| Look-ahead bias | `DataView` guard, point-in-time fundamentals/macro/memory, tests that inject future data and expect errors |
| Survivorship bias (free data) | Historical S&P membership list; document the residual bias; delisted-ticker handling |
| Overfitting | Walk-forward, deflated Sharpe, PBO, fixed OOS hold-out, ablations |
| LLM cost of multi-year backtests | Quant funnel, weekly cadence, quick tier for analysts, record/replay cache, a "budget mode" that samples LLM pods |
| LLM leakage (models know the future) | Prompts give dates only through data; prefer recent OOS windows after the model's training cutoff for the final evaluation; compare against an LLM-free ablation |
| Hallucinated facts | Tools return evidence IDs; theses must cite them; a validator rejects uncited claims |
| Vendor breakage (yfinance) | Provider interface + cache + fallback provider |
| Complexity creep | Strict stage scope, YAGNI, ablations remove components that add nothing |
