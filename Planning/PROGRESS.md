# Atlas Capital — Progress Tracker

Update this file at the start and end of **every** stage (see `02-stage-protocol.md §3, §6`).

> **Rules (from [`Project.md`](../Project.md)):** statuses are `NOT STARTED` / `IN PROGRESS` / `BLOCKED` / `DONE`. A box may be ticked, and a stage set to `DONE`, **only after** a *Verification evidence* block with real output from that stage's chat is written in the stage folder's `verification.md` and summarised below (Rule 1). Each stage's records live in its folder `stages/stage-NN-<slug>/` (Rule 2A). Stage N cannot start until Stage N−1 is `DONE` (Rule 4).

- **Current stage:** 02 — Configuration system & fund mandate — `NOT STARTED`
- **Stages done:** 1 / 66
- **Last updated:** 2026-10-26 (Stage 01 DONE)

## Stage checklist

### Phase 0 Foundation
- [x] **01** [Project scaffolding & toolchain](stages/stage-01-project-scaffolding/brief.md)
- [ ] **02** [Configuration system & fund mandate](stages/stage-02-configuration/brief.md)
- [ ] **03** [Logging, run context & audit trail](stages/stage-03-logging-audit/brief.md)
- [ ] **04** [Core domain model](stages/stage-04-domain-model/brief.md)
- [ ] **05** [Clock, trading calendar & point-in-time guard](stages/stage-05-clock-pit/brief.md)
- [ ] **06** [Storage layer (DuckDB + Parquet lake)](stages/stage-06-storage/brief.md)

### Phase 1 Data Platform → M1
- [ ] **07** [Price data providers (yfinance, Alpaca)](stages/stage-07-price-data/brief.md)
- [ ] **08** [Universe & historical index membership](stages/stage-08-universe/brief.md)
- [ ] **09** [Point-in-time fundamentals (SEC EDGAR)](stages/stage-09-fundamentals/brief.md)
- [ ] **10** [Macro data (FRED / ALFRED)](stages/stage-10-macro/brief.md)
- [ ] **11** [News data pipeline](stages/stage-11-news/brief.md)
- [ ] **12** [Events calendar & sentiment sources](stages/stage-12-events-sentiment/brief.md)
- [ ] **13** [Data quality checks & DataView facade](stages/stage-13-data-quality-dataview/brief.md)
- [ ] **14** [Data sync CLI & bootstrap backfill](stages/stage-14-data-cli/brief.md)

### Phase 2 Quant Engine → M2
- [ ] **15** [Technical feature library](stages/stage-15-technical-features/brief.md)
- [ ] **16** [Fundamental factor library](stages/stage-16-fundamental-factors/brief.md)
- [ ] **17** [Signal research toolkit (IC, quantiles, decay)](stages/stage-17-signal-research/brief.md)
- [ ] **18** [Covariance & volatility forecasting](stages/stage-18-covariance-vol/brief.md)
- [ ] **19** [Factor risk model](stages/stage-19-factor-risk-model/brief.md)
- [ ] **20** [Risk & performance metrics library](stages/stage-20-risk-metrics/brief.md)
- [ ] **21** [Regime detection](stages/stage-21-regime-detection/brief.md)
- [ ] **22** [Systematic alpha models](stages/stage-22-systematic-alpha/brief.md)
- [ ] **23** [Statistical arbitrage (pairs)](stages/stage-23-stat-arb/brief.md)

### Phase 3 LLM & Agent Infra
- [ ] **24** [LLM gateway, model tiers & cost budget](stages/stage-24-llm-gateway/brief.md)
- [ ] **25** [LLM record/replay cache](stages/stage-25-llm-cache/brief.md)
- [ ] **26** [Agent framework, prompts & tool registry](stages/stage-26-agent-framework/brief.md)
- [ ] **27** [Memory system (episodic + lessons, point-in-time)](stages/stage-27-memory/brief.md)
- [ ] **28** [LangGraph orchestration skeleton](stages/stage-28-langgraph-skeleton/brief.md)

### Phase 4 Research Division → M3
- [ ] **29** [Fundamental analyst agent](stages/stage-29-fundamental-analyst/brief.md)
- [ ] **30** [Technical analyst agent](stages/stage-30-technical-analyst/brief.md)
- [ ] **31** [News & event analyst agent](stages/stage-31-news-analyst/brief.md)
- [ ] **32** [Sentiment analyst agent](stages/stage-32-sentiment-analyst/brief.md)
- [ ] **33** [Macro economist agent](stages/stage-33-macro-economist/brief.md)
- [ ] **34** [Quant analyst agent](stages/stage-34-quant-analyst/brief.md)
- [ ] **35** [Bull / Bear / Red-team debate](stages/stage-35-debate/brief.md)
- [ ] **36** [Research Director & thesis registry](stages/stage-36-research-director/brief.md)

### Phase 5 Portfolio Management
- [ ] **37** [Screening & research prioritisation funnel](stages/stage-37-screening-funnel/brief.md)
- [ ] **38** [Pod framework & Fundamental L/S pod](stages/stage-38-pod-framework/brief.md)
- [ ] **39** [Systematic & Stat-Arb pods](stages/stage-39-quant-pods/brief.md)
- [ ] **40** [Global Macro & Event-Driven pods](stages/stage-40-macro-event-pods/brief.md)
- [ ] **41** [Views → Black-Litterman posterior](stages/stage-41-black-litterman/brief.md)
- [ ] **42** [Portfolio optimizer (cvxpy)](stages/stage-42-optimizer/brief.md)
- [ ] **43** [Position sizing, stops & hedging overlay](stages/stage-43-sizing-hedging/brief.md)

### Phase 6 Risk, Compliance & Governance → M4
- [ ] **44** [Risk engine: pre-trade hard limits](stages/stage-44-risk-engine-pretrade/brief.md)
- [ ] **45** [Risk monitor & circuit breakers](stages/stage-45-circuit-breakers/brief.md)
- [ ] **46** [Stress testing & scenarios](stages/stage-46-stress-testing/brief.md)
- [ ] **47** [CRO agent & risk memo](stages/stage-47-cro-agent/brief.md)
- [ ] **48** [Compliance engine](stages/stage-48-compliance/brief.md)
- [ ] **49** [CIO capital allocation & Investment Committee](stages/stage-49-cio-ic/brief.md)

### Phase 7 Execution & Operations
- [ ] **50** [Order management system](stages/stage-50-oms/brief.md)
- [ ] **51** [Execution simulator & transaction-cost models](stages/stage-51-execution-sim/brief.md)
- [ ] **52** [Fund accounting: ledger, NAV, fees](stages/stage-52-accounting/brief.md)
- [ ] **53** [Prediction scoring & performance attribution](stages/stage-53-attribution/brief.md)
- [ ] **54** [Learning loop: reflection & trust weights](stages/stage-54-learning-loop/brief.md)

### Phase 8 Orchestration & Backtesting → M5
- [ ] **55** [Fund daily cycle graph (end-to-end)](stages/stage-55-fund-cycle/brief.md)
- [ ] **56** [Event-driven backtest engine](stages/stage-56-backtest-engine/brief.md)
- [ ] **57** [Backtest analytics & tear sheet](stages/stage-57-tear-sheet/brief.md)
- [ ] **58** [Robustness: walk-forward, deflated Sharpe, PBO](stages/stage-58-robustness/brief.md)
- [ ] **59** [Ablations, benchmarks & tuning](stages/stage-59-ablation/brief.md)

### Phase 9 Paper Trading & Production → M6
- [ ] **60** [Alpaca paper broker adapter & reconciliation](stages/stage-60-broker-alpaca/brief.md)
- [ ] **61** [Scheduler & live runner](stages/stage-61-scheduler/brief.md)
- [ ] **62** [Monitoring, alerts & kill switch](stages/stage-62-monitoring/brief.md)
- [ ] **63** [Reporting: daily risk report & letters](stages/stage-63-reporting/brief.md)
- [ ] **64** [Streamlit dashboard](stages/stage-64-dashboard/brief.md)
- [ ] **65** [CLI polish & documentation](stages/stage-65-cli-docs/brief.md)
- [ ] **66** [Hardening, end-to-end tests & paper burn-in](stages/stage-66-hardening/brief.md)

## Decisions log

| Date | Stage | Decision | Reason |
|---|---|---|---|
| 2026-10-25 | 00 | Defaults A1–A9 adopted (see design spec §15) | Planning session; revisit before the stage that uses each |
| 2026-10-25 | 00 | `Project.md` adopted as the governing rules; `CLAUDE.md` imports it so every session loads it | Owner's instruction: read before every task, verify before claiming done, the overall plan wins collisions, strict stage order |
| 2026-10-25 | 00 | Removed "stages may run early/in parallel" wording from the roadmap; execution is strictly 01 → 66 | Owner's instruction (Project.md Rule 4), approved in chat |
| 2026-10-25 | 00 | Added Project.md Rule 2A: each stage's records (`brief.md`, `plan.md`, `worklog.md`, `verification.md`, `artifacts/`) live in `stages/stage-NN-<slug>/`, copied from `stages/_template/`; code stays in shared `src/atlas/` and `tests/`. Briefs 02–66 moved into their folders; roadmap links updated | Owner's instruction: all non-planning work is done in the stage folder (records per stage, chosen by the owner over code per stage) |
| 2026-10-25 | 01 | Stage 01, which began before Rule 2A, was migrated after its session ended: brief → `stages/stage-01-project-scaffolding/brief.md`, plan `stage-plans/stage-01-plan.md` → `stages/stage-01-project-scaffolding/plan.md` (spec path in its header updated); `stage-plans/` deleted; transition note removed from Project.md; all tracker links point to `brief.md` | Owner's instruction, completing the Rule 2A transition |
| 2026-10-25 | 01 | Brief's "core" dependency group is `[project].dependencies` (empty for now); `dev`, `quant`, `llm`, `ui` are PEP 735 `[dependency-groups]`, the last three declared empty | Runtime deps must live in `[project].dependencies` for the package to install; brief wording kept in spirit |
| 2026-10-25 | 01 | Only the 17 top-level subpackages from spec §12 created; nested ones (`data/providers`, `quant/features`, `agents/prompts`, …) are created by the stage that first needs them | YAGNI; brief asks for "empty subpackages", no nested contents |
| 2026-10-25 | 01 | Distribution name `atlas-capital`, import package `atlas`, hatchling backend with `packages = ["src/atlas"]`; `uv.lock` committed | Clear dist name; lock file needed for "green on a clean clone" |
| 2026-10-25 | 01 | Acceptance "CLAUDE.md still begins with the `@Project.md` import" read as: the pre-existing header (title, sentence, import) stays byte-identical and precedes the appended digest | The file never literally started with the import; header untouched; pinned by `test_claude_md_imports_project_rules_first` |
| 2026-10-25 | 01 | Tooling never rewrites governing docs: ruff `extend-exclude = ["Planning", "Project.md"]`; pre-commit whitespace hooks `exclude: ^(Project\.md\|Planning/)`. Hooks are not installed into `.git/hooks` | ruff 0.17 formats Python blocks inside Markdown and tried to reflow `Planning/**/brief.md`; `Project.md` must stay unchanged |
| 2026-10-26 | 01 | Collision: stage plan Task 8 recorded evidence only in `PROGRESS.md`; `Project.md` §1.5 (rank 1, changed mid-stage) requires `verification.md` + `artifacts/` in the stage folder too. Followed `Project.md` | Rule 3 precedence |

## Backlog (out-of-scope ideas captured during stages)

- PCA/statistical risk model alongside the fundamental model
- ML alpha (gradient boosting with purged CV)
- Johansen basket stat-arb
- Earnings-call transcript analysis
- Options overlay (real tail hedges) and futures for macro
- Paid data adapters (point-in-time constituents, consensus estimates, short interest)

## Handoff notes

### Stage 00 — Planning — DONE 2026-10-25
- Built: design spec, master roadmap (66 stages), stage protocol, 66 stage briefs, this tracker, `Project.md` (governing rules), `CLAUDE.md` (imports Project.md)
- Verification (planning is documents only): a script confirmed that all 66 roadmap links resolve to existing briefs, that there are no orphan briefs, and that every brief has its Goal / In scope / Acceptance criteria / Verify sections
- Key decisions: hybrid LLM + quant architecture; deterministic risk engine with final veto; five pods; Python 3.12/uv/LangGraph/DuckDB/cvxpy; Alpaca paper only
- Superpowers plugin was not installed during planning; the method (brainstorm → spec → staged plan) was followed by hand
- Next stage should know: the project root is `/Users/zzjsmacbookair/Desktop/Trading Agents`; there is no git repo yet (Stage 01 runs `git init` and commits `Project.md`, `CLAUDE.md` and `Planning/` first). `CLAUDE.md` already exists and imports `@Project.md`; Stage 01 appends to it and must not remove the import

### Stage 01 — Project scaffolding & toolchain — DONE 2026-10-26
- Folder: [stages/stage-01-project-scaffolding/](stages/stage-01-project-scaffolding/) (`plan.md`, `worklog.md`, `verification.md`, `artifacts/`)
- Built: uv project `atlas-capital` (Python 3.12, src layout), package `atlas` with 17 empty subpackages, ruff/mypy/pytest config in `pyproject.toml`, `Makefile` (`install test lint fmt fmt-check type check`), `.pre-commit-config.yaml`, `.gitignore`, `README.md` quick start, conventions digest appended to `CLAUDE.md`
- Key interfaces: `atlas.__version__ == "0.1.0"`; importable `atlas.{core,data,quant,llm,agents,portfolio,risk,compliance,execution,accounting,performance,orchestration,backtest,live,reporting,dashboard,cli}`; session fixture `repo_root() -> Path` in `tests/conftest.py`; markers `live`, `llm`, `integration` deselected by default with `--strict-markers`
- Decisions: see Decisions log rows dated 2026-10-25/26, stage 01
- Collisions with the overall plan: none with the roadmap or spec; one with the stage plan (evidence location), resolved in favour of `Project.md` §1.5
- Deviations from brief: none in scope; ruff also excludes `Planning/` and `Project.md` (not in the brief, needed to keep `Project.md` unchanged)
- Known gaps → backlog: none
- Incident: an unchecked piped command amended the commit while `make check` was failing (iCloud had hidden the venv's `.pth` files). Root cause fixed and full verification re-run; details in the stage worklog
- Next stage should know: Stage 02 adds the first runtime deps (pydantic, pyyaml) to `[project].dependencies` and creates `config/`. In this sandbox, `uv` and `pre-commit` need `UV_CACHE_DIR="$TMPDIR/uv-cache"` and `PRE_COMMIT_HOME="$TMPDIR/pre-commit"` (home caches are not writable); nothing in the repo depends on that. New subpackage dirs under `src/atlas/` must have an `__init__.py` and be added to `SUBPACKAGES` in `tests/unit/test_package_layout.py` only if they are top-level. **The project is on the iCloud-synced Desktop:** the venv lives in `.venv.nosync` (iCloud skips `*.nosync`) and `.venv` is a symlink to it. If `import atlas` ever fails with `ModuleNotFoundError`, check `ls -lO .venv.nosync/lib/python3.12/site-packages/*.pth` for `hidden` and rebuild with `rm -rf .venv.nosync && UV_PROJECT_ENVIRONMENT=.venv.nosync uv sync --group dev`. Moving the repo off iCloud is the durable fix (owner's call). `.gitignore` ignores the lake only at the repo root (`/data/`) so `src/atlas/data/` stays tracked

#### Verification evidence — Stage 01 — 2026-10-26
Full block and raw output: [verification.md](stages/stage-01-project-scaffolding/verification.md) (**ALL CRITERIA VERIFIED**).
- Commands run (this chat):
  - `uv run pytest -q` → 25 passed, 0 failed, 0 deselected
  - `uv run ruff check .` → All checks passed!
  - `uv run ruff format --check .` → 25 files already formatted
  - `uv run mypy src/atlas` → Success: no issues found in 18 source files
  - `make check` (working tree) → 25 passed, lint/format/mypy clean (re-run after the venv fix; an earlier run failed with `ModuleNotFoundError`, see worklog)
  - `uv sync --group dev && make check && git log --oneline` (fresh clone) → 25 passed, lint/format/mypy clean, log shows `planning: …` and `stage-01: …`, exit=0
- Acceptance criteria:
  - [x] `make check` green on a clean clone after `uv sync` — evidence: artifacts/clean-clone-verify.txt
  - [x] `git log` shows the planning commit and the stage-01 commit — evidence: same file
  - [x] `CLAUDE.md` still begins with the `@Project.md` import; `Project.md` unchanged — evidence: `test_claude_md_imports_project_rules_first`; artifacts/docs-unchanged.txt
- Waivers: none
