# Atlas Capital — Progress Tracker

Update this file at the start and end of **every** stage (see `02-stage-protocol.md §3, §6`).

> **Rules (from [`Project.md`](../Project.md)):** statuses are `NOT STARTED` / `IN PROGRESS` / `BLOCKED` / `DONE`. A box may be ticked, and a stage set to `DONE`, **only after** a *Verification evidence* block with real output from that stage's chat is written in the stage folder's `verification.md` and summarised below (Rule 1). Each stage's records live in its folder `stages/stage-NN-<slug>/` (Rule 2A). Stage N cannot start until Stage N−1 is `DONE` (Rule 4).

- **Current stage:** 04 — Core domain model — `NOT STARTED`
- **Stages done:** 3 / 66
- **Last updated:** 2026-10-26 (Stage 03 DONE)

## Stage checklist

### Phase 0 Foundation
- [x] **01** [Project scaffolding & toolchain](stages/stage-01-project-scaffolding/brief.md)
- [x] **02** [Configuration system & fund mandate](stages/stage-02-configuration/brief.md)
- [x] **03** [Logging, run context & audit trail](stages/stage-03-logging-audit/brief.md)
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
| 2026-10-26 | 02 | Collision (brief vs brief): Stage 02 acceptance needs `ConfigError`, but Stage 03's brief creates `core/errors.py`. Stage 02 creates `errors.py` with only `AtlasError` + `ConfigError`; Stage 03 adds the rest of the hierarchy and its tests | Conventions put custom exceptions in `atlas.core.errors`; roadmap and spec are silent; smallest step that satisfies both briefs |
| 2026-10-26 | 02 | Plan decisions D1–D9 (YAML layout, `profile`/`secrets` fields on `AtlasConfig`, secret names live only on `Secrets`, list-replace merge, budgets sum over all pods, reserved keys and unknown-`ATLAS__`-section guard, path rules, provisional LLM/cost values, duplicate-key-safe YAML) | See [stage-02 plan › Design decisions](stages/stage-02-configuration/plan.md) |
| 2026-10-26 | 02 | Owner confirmed the `errors.py` collision resolution (row above): Stage 02's `src/atlas/core/errors.py` keeps only `AtlasError` + `ConfigError`; Stage 03 adds the rest of the hierarchy and its tests; Stage 03's brief is not edited. Owner's answer: "Confirm as is (Recommended)" | Rule 3 collision record; owner decision in chat |
| 2026-10-26 | 02 | D4 amended after the whole-branch review: `pods/*.yaml` files define the whole pod set (deleting a file removes the pod; each file is a full definition) and a non-empty `universe.yaml` replaces the default universes; other mappings still merge key by key. Collections are immutable: `pods`/`universes` are read-only `Mapping`s (`_FrozenDict`), `drawdown_tiers`/`tickers` are tuples | A removed pod must not keep trading on its default budget; a shared config must not be mutated in place. See [stage-02 worklog](stages/stage-02-configuration/worklog.md) |
| 2026-10-26 | 02 | Deviation 1 approved: `AtlasConfig` has `profile` (dev/backtest/paper, set by `load_config`; Stage 03's run mode) and `secrets` (five `SecretStr`, excluded from the fingerprint) beyond the brief's interface. Owner's answer to the four deviation questions: "continue with the recommended choice." (the recommended option for each was Approve) | Owner decision in chat |
| 2026-10-26 | 02 | Deviation 2 approved: the five secret env-var names live only on `Secrets`' fields (tested against `.env.example`), not duplicated as "API key names" in `DataConfig`. Owner's answer to the four deviation questions: "continue with the recommended choice." (the recommended option for each was Approve) | Owner decision in chat |
| 2026-10-26 | 02 | Deviation 3 approved: `config/pods/*.yaml` define the whole pod set (deleting a file removes the pod; each file is a full definition) and a non-empty `universe.yaml` replaces the built-in universes; profiles, env and overrides still change single keys. Owner's answer to the four deviation questions: "continue with the recommended choice." (the recommended option for each was Approve) | Owner decision in chat |
| 2026-10-26 | 02 | Deviation 4 approved: `pods` and `universes` are read-only `Mapping`s; `drawdown_tiers` and `tickers` are tuples. Owner's answer to the four deviation questions: "continue with the recommended choice." (the recommended option for each was Approve) | Owner decision in chat |
| 2026-10-26 | 02 | Approved as is: Stage 02 has two commits, `f0db607 stage-02: Configuration system & fund mandate` and `b2cbc3c stage-02: follow-up records` (plus this records-only follow-up), against the Definition of Done's "one git commit" per stage. The follow-ups hold records only (evidence and owner decisions), made at the owner's request after DONE. Owner's words: "Record the three unlogged Stage 02 items in the PROGRESS.md Decisions log (approved as they are), as a records-only follow-up, and fast-forward main." | Found by the post-DONE audit; owner decision in chat |
| 2026-10-26 | 02 | Approved as is: `LLMConfig.provider` is one default-provider field (`anthropic` \| `openai_compatible`), where the brief lists "providers"; Stage 24 may widen it. Owner's words: "Record the three unlogged Stage 02 items in the PROGRESS.md Decisions log (approved as they are), as a records-only follow-up, and fast-forward main." | Found by the post-DONE audit; owner decision in chat |
| 2026-10-26 | 02 | Approved as is: the DoD's "every config key … with a default" is met at the collection level. `AtlasConfig()` is fully populated (every built-in pod, drawdown tier and universe is fully defined in code), while fields inside one entry (`PodConfig.name`/`risk_budget`/`rebalance`/`universe`, `DrawdownTier` fields, `UniverseConfig.description`) are deliberately required, matching "each pod file is a full definition". Owner's words: "Record the three unlogged Stage 02 items in the PROGRESS.md Decisions log (approved as they are), as a records-only follow-up, and fast-forward main." | Found by the post-DONE audit; owner decision in chat |
| 2026-10-26 | 03 | D1: the five new errors (`LookAheadError`, `DataError`, `LLMError`, `RiskLimitError`, `ComplianceError`) subclass `AtlasError` directly; `LookAheadError` is not a `DataError` | A handler that tolerates bad data must never swallow a look-ahead bug; see [stage-03 plan › Design decisions](stages/stage-03-logging-audit/plan.md) |
| 2026-10-26 | 03 | D2: no wall-clock call in Atlas code. `AuditEvent.ts` is the timestamp inside the event's ULID (`ULID().datetime`, UTC, ms); log timestamps come from structlog's `TimeStamper(utc=True)`; no `core/clock.py` is created. Tension: stage protocol §4 forbids `datetime.now()` outside `atlas.core.clock`, which Stage 05 creates, while the brief needs `ts`. Resolved without breaking either and without building Stage 05 early (Rule 4) | Conventions + Rule 4; Stage 05 may route these through its `Clock`; see [stage-03 plan › Design decisions](stages/stage-03-logging-audit/plan.md) |
| 2026-10-26 | 03 | D3/D4: `RunContext` is a frozen Pydantic model (`run_id` 26-char ULID, `mode: Profile`, `as_of`, `config_fingerprint`, `stage`). `run_context()` at top level opens a new run (`mode` from `config.profile`, else `"dev"`; mismatch → `AtlasError`); nested it is a child scope that may change only `as_of`/`stage` | One run has one id, mode and config; a backtest moves `as_of`, the fund day moves `stage`; see [stage-03 plan › Design decisions](stages/stage-03-logging-audit/plan.md) |
| 2026-10-26 | 03 | D5: logging is structlog on stdlib `logging`; one processor chain (run fields, level, logger, UTC ISO timestamp) also used as `foreign_pre_chain`, so third-party lines carry `run_id`; JSON file handler, pretty console; `configure_logging` idempotent, `reset_logging`; log level is a parameter, not a config key | "Logs carry the run_id" must hold for library logs too; no new config key needed; see [stage-03 plan › Design decisions](stages/stage-03-logging-audit/plan.md) |
| 2026-10-26 | 03 | D6/D7: `canonical_json` = sorted keys, compact, UTF-8, no NaN/inf; `Decimal` → exact string, aware `datetime` → UTC ISO (naive rejected), `date` → ISO, `Enum` → value, model → fields, anything else `TypeError`. `hash = sha256(prev_hash + canonical_json(event without hash))`, genesis `"0"*64`; payload stored JSON-native so the file alone re-derives every hash | Deterministic bytes; money keeps its digits; ambiguous types fail loudly; see [stage-03 plan › Design decisions](stages/stage-03-logging-audit/plan.md) |
| 2026-10-26 | 03 | D8/D9: `AuditLog(path)` verifies an existing file's whole chain and refuses a broken one (`AtlasError`); per-file lock + last hash shared by every instance in the process; flush + fsync per line; a failed write makes the log unusable; `record()` needs an active run context; `verify_chain`: missing → False, empty → True, no final newline → False. One writer process per file | Fail safe: never extend a tampered or half-written log; see [stage-03 plan › Design decisions](stages/stage-03-logging-audit/plan.md) |
| 2026-10-26 | 03 | D10: plan builds canonical JSON (brief task 5) before `AuditLog` (brief task 4) | The hash depends on it; same content; see [stage-03 plan › Design decisions](stages/stage-03-logging-audit/plan.md) |
| 2026-10-26 | 03 | D11: `run_dir(lake_path, run_id)` = `<lake>/runs/<run_id>`, `audit_path(...)` = `run_dir/audit.jsonl`; `lake_path` is `cfg.data.lake_path` | The brief's `data/runs/<run_id>/audit.jsonl` without hard-coding `data`; see [stage-03 plan › Design decisions](stages/stage-03-logging-audit/plan.md) |
| 2026-10-26 | 03 | Approved as is: Stage 03 has two commits, `bb1eca1 stage-03: Logging, run context & audit trail` and `stage-03: follow-up records`, against the Definition of Done's "one git commit" per stage. The follow-up holds records only, from the post-DONE audit: the commit and merge entries in the worklog, the merged-`main` verification (artifacts/merged-main-check.txt) and three process deviations (executing-plans/TDD skills followed by hand, not loaded; no owner plan review before execution, as the kick-off prompt says to execute; plan Task 6's commit box ticked before the commit). Owner's words: "according to the planning markdown files, please determine whether everything has been completed or not and audit; if not, please help me to fix all the remaining gaps of this stage" | Found by the post-DONE audit; owner request in chat; see [stage-03 worklog](stages/stage-03-logging-audit/worklog.md) |

## Backlog (out-of-scope ideas captured during stages)

- PCA/statistical risk model alongside the fundamental model
- ML alpha (gradient boosting with purged CV)
- Johansen basket stat-arb
- Earnings-call transcript analysis
- Options overlay (real tail hedges) and futures for macro
- Paid data adapters (point-in-time constituents, consensus estimates, short interest)
- (Stage 02 review, minor) `FundConfig.initial_capital` accepts ≤ 0, and `.inf` is accepted for `max_liquidation_days` and cost fields — add `gt=0` / `allow_inf_nan=False`
- (Stage 02 review, minor) Equal Decimals written differently (`1.0e7` vs `10000000`) give different `config_fingerprint`s — normalise Decimals before hashing
- (Stage 02 review, minor) YAML merge keys (`<<: *anchor` plus an override) are rejected as duplicate keys — support or document
- (Stage 02 review, minor) Drawdown tiers do not check that `halt` / `block_new_names` escalate (a halting tier followed by a non-halting one is accepted)
- (Stage 03) The audit hash chain has no external anchor: rewriting the whole file with freshly computed hashes still passes `verify_chain`. Anchor the last hash somewhere else (e.g. the Stage 06 DuckDB mirror or the Stage 55 run summary)
- (Stage 03) `AuditLog` supports one writer process per file (threads are fine); add a file lock (`fcntl.flock`) if several processes ever share one run's log

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

### Stage 02 — Configuration system & fund mandate — DONE 2026-10-26
- Folder: [stages/stage-02-configuration/](stages/stage-02-configuration/) (`plan.md`, `worklog.md`, `verification.md`, `artifacts/`)
- Built: `atlas.core.config` (frozen Pydantic models `FundConfig`, `LimitsConfig` with every §9 limit, `DrawdownTier`, `PodStops`, `PodConfig`, `UniverseConfig`, `DataConfig`, `LLMConfig`, `BacktestConfig`, `CostConfig`, `Secrets`, `AtlasConfig(BaseSettings)`), `load_config`, `config_fingerprint`; `atlas.core.errors` (`AtlasError`, `ConfigError` only); `config/` (`fund.yaml`, `limits.yaml`, `universe.yaml`, 5 `pods/*.yaml`, `profiles/{dev,backtest,paper}.yaml`, every key commented); `.env.example`; 92 tests in `tests/unit/core/test_config.py`. Runtime deps pydantic, pydantic-settings, pyyaml, python-dotenv; dev dep types-PyYAML; mypy pydantic plugin
- Key interfaces: `load_config(profile="dev", overrides=None, config_dir=None) -> AtlasConfig` (layers: code defaults < fund.yaml < limits.yaml < universe.yaml < pods/*.yaml < profiles/<p>.yaml < `.env` < `ATLAS__*` env < overrides; `config_dir` defaults to `./config`, `.env` is read from its parent); `config_fingerprint(cfg) -> str` (12 hex, secrets excluded) for `RunContext.config_fingerprint`; `cfg.profile` (`dev`/`backtest`/`paper`) is the run mode; `cfg.pods: Mapping[str, PodConfig]` keyed `fundamental_ls`, `systematic`, `statarb`, `global_macro`, `event_driven`; `cfg.data.universes: Mapping[str, UniverseConfig]` (`sp500`, `macro_etfs`); `cfg.limits.drawdown_tiers: tuple[DrawdownTier, ...]`; secrets as `SecretStr | None` on `cfg.secrets`
- Decisions: Decisions log rows dated 2026-10-26, stage 02 (collision, D1–D9, D4 amendment)
- Collisions with the overall plan: none with the roadmap or spec; one brief-vs-brief (`ConfigError` vs Stage 03's `errors.py`), resolved as logged
- Deviations from brief (all four approved by the owner 2026-10-26, see Decisions log): `AtlasConfig` also has `profile` and `secrets`; secret env names live only on `Secrets` (not duplicated in `DataConfig`); pod files define the whole pod set and a non-empty `universe.yaml` replaces the built-in universes; `pods`/`universes` are read-only `Mapping`s and the list fields are tuples
- Known gaps → backlog: four minor review findings (see Backlog, "Stage 02 review")
- Next stage should know: `src/atlas/core/errors.py` already exists and contains exactly two classes: `AtlasError(Exception)` ("Base class for every error raised by Atlas code") and `ConfigError(AtlasError)`; its module docstring says Stage 03 extends it. `atlas.core.config` imports `ConfigError` from it, and the only test touching it is `test_config_error_is_atlas_error` in `tests/unit/core/test_config.py`; there is no `tests/unit/core/test_errors.py` yet. Stage 03 adds `LookAheadError`, `DataError`, `LLMError`, `RiskLimitError`, `ComplianceError` and the hierarchy tests, and must keep both existing classes and `ConfigError` subclassing `AtlasError` (owner-confirmed 2026-10-26; Stage 03's brief is unchanged). `config_fingerprint` is ready for `RunContext`; take `mode` from `cfg.profile`. Every config failure raises `ConfigError` whose message names the file, variable or dotted key and never a value; `ValidationError`s are re-raised `from None` so pasted secrets can't reach a traceback. The pod files define the pod set (deleting one removes the pod; each file is a full definition); to switch a pod off, set `enabled: false` (its budget stays unused, budgets must still sum to 1.0). Unit tests must keep the autouse `_isolated_env` pattern (clears `ATLAS__*` and the secret vars, `chdir(tmp_path)`) and must not load `repo_root/"config"` directly, because `load_config` reads the `.env` next to `config/`. In this sandbox, a new worktree needs `UV_PROJECT_ENVIRONMENT=.venv.nosync uv sync --group dev` + `ln -s .venv.nosync .venv` and `UV_CACHE_DIR="$TMPDIR/uv-cache"`

#### Verification evidence — Stage 02 — 2026-10-26
Full block and raw output: [verification.md](stages/stage-02-configuration/verification.md) (**ALL CRITERIA VERIFIED**).
- Commands run (this chat):
  - `uv run pytest -q` → 117 passed, 0 failed, 0 deselected
  - `uv run ruff check .` → All checks passed!
  - `uv run ruff format --check .` → 28 files already formatted
  - `uv run mypy src/atlas` → Success: no issues found in 20 source files
  - `uv run pytest tests/unit/core/test_config.py -q` (brief's Verify) → 92 passed
  - `make check` → lint/format/mypy clean, 117 passed, exit=0
  - Follow-up on merged `main` (`f0db607`): `uv run pytest -q` → 117 passed; `ruff check` → All checks passed!; `ruff format --check` → 28 files already formatted; `mypy src/atlas` → Success; `make check` → exit=0 · raw: [artifacts/merged-main-check.txt](stages/stage-02-configuration/artifacts/merged-main-check.txt)
- Acceptance criteria:
  - [x] `load_config("backtest")` returns a fully populated config — evidence: `test_repo_backtest_profile_fully_populated`; artifacts/load-config-backtest.txt
  - [x] Invalid YAML raises `ConfigError` naming the bad key — evidence: `test_invalid_value_names_dotted_key`, `test_unknown_key_names_dotted_key`, `test_malformed_yaml_names_file_and_line`, `test_duplicate_yaml_key_raises`, `test_invalid_config_raises[*]`
  - [x] Every limit in spec §9 exists as a typed field — evidence: `test_limits_cover_spec_section_9[*]`, `test_drawdown_tiers_and_pod_stops_defaults`
- Waivers: none

### Stage 03 — Logging, run context & audit trail — DONE 2026-10-26
- Folder: [stages/stage-03-logging-audit/](stages/stage-03-logging-audit/) (`plan.md`, `worklog.md`, `verification.md`, `artifacts/`)
- Built: `atlas.core.errors` (adds `LookAheadError`, `DataError`, `LLMError`, `RiskLimitError`, `ComplianceError`, all direct `AtlasError` subclasses); `atlas.core.run_context` (`RunContext`, `run_context()`, `current_run_context()`, `run_dir()`); `atlas.core.logging` (`configure_logging`, `reset_logging`, `get_logger`, `add_run_context`); `atlas.core.audit` (`canonical_json`, `EventType`, `AuditEvent`, `AuditLog`, `GENESIS_HASH`, `audit_path`). 68 tests in `tests/unit/core/test_{errors,run_context,logging,audit}.py`. Runtime deps structlog 26.1.0, python-ulid 4.0.1
- Key interfaces: `with run_context(mode="backtest", as_of=date(2024, 1, 2)) as ctx:` or `run_context(config=cfg, as_of=...)` (mode = `cfg.profile`, fingerprint = `config_fingerprint(cfg)`); nested `run_context(as_of=..., stage=...)` keeps `run_id`/`mode`/fingerprint; `current_run_context() -> RunContext | None`; `configure_logging(*, level="INFO", log_file=None, console=None)`; `get_logger(__name__)`; `AuditLog(path).record(EventType.X, actor, payload) -> AuditEvent`; `AuditLog.verify_chain(path) -> bool`; `audit_path(cfg.data.lake_path, ctx.run_id)`; `canonical_json(obj) -> str`
- Decisions: Decisions log rows dated 2026-10-26, stage 03 (D1–D11)
- Collisions with the overall plan: none with the roadmap or spec; one tension inside the conventions (wall-clock rule vs `AuditEvent.ts`), resolved by D2 without Stage 05 code
- Process deviations (records only, found by the post-DONE audit): see the Decisions log row "Stage 03 has two commits"
- Deviations from brief: none in scope. Additions: `current_run_context()`, `run_dir()`, `audit_path()`, `reset_logging()`, `GENESIS_HASH`; `run_context(config=...)` as well as `mode=`; `AuditLog` refuses to open a file whose chain is broken
- Known gaps → backlog: no external anchor for the chain; one writer process per audit file (see Backlog, "Stage 03")
- Next stage should know: Stage 04's `new_id(prefix)` should build on python-ulid (already a dependency); Stage 03's `run_id`/`event_id` are bare 26-char ULIDs (no prefix). `RunContext` and `AuditEvent` are frozen Pydantic models with `extra="forbid"`. `record()` raises `AtlasError` outside a run context, and normalises the payload with `canonical_json` first (`Decimal` → string, aware datetimes → UTC ISO; NaN/inf, naive datetimes and sets raise). Event kinds are a `StrEnum`; later stages add members (Stage 26 `AGENT_REPORT`, Stage 60 `RECON_BREAK`). A new `threading.Thread` has no run context: start it with `contextvars.copy_context().run`. Logging tests must call `reset_logging()` in teardown (handlers sit on the root logger). There is still no `atlas.core.clock`: the only time sources are python-ulid and structlog internals, and the grep for `datetime.now|date.today|time.time|utcnow` over `src/` and `tests/` is empty, ready for Stage 05's banned-API rule. Stage 06 mirrors `AuditLog` into DuckDB: read the JSONL lines, they are the source of truth. In this sandbox, a new worktree needs `UV_PROJECT_ENVIRONMENT=.venv.nosync uv sync --group dev` + `ln -s .venv.nosync .venv` and `UV_CACHE_DIR="$TMPDIR/uv-cache"`

#### Verification evidence — Stage 03 — 2026-10-26
Full block and raw output: [verification.md](stages/stage-03-logging-audit/verification.md) (**ALL CRITERIA VERIFIED**).
- Commands run (this chat):
  - `uv run pytest -q` → 185 passed, 0 failed, 0 deselected
  - `uv run ruff check .` → All checks passed!
  - `uv run ruff format --check .` → 35 files already formatted
  - `uv run mypy src/atlas` → Success: no issues found in 23 source files
  - `uv run pytest tests/unit/core -q` (brief's Verify) → 160 passed
  - `make check` → lint/format/mypy clean, 185 passed, exit=0
  - Acceptance demo → every log line carries the run_id: True; 4 chained events; `verify_chain` True untouched, False after one payload edit
  - Follow-up on merged `main` (`bb1eca1`): `uv run pytest -q` → 185 passed; `ruff check` → All checks passed!; `ruff format --check` → 35 files already formatted; `mypy src/atlas` → Success; `uv run pytest tests/unit/core -q` → 160 passed; `make check` → exit=0 · raw: [artifacts/merged-main-check.txt](stages/stage-03-logging-audit/artifacts/merged-main-check.txt)
- Acceptance criteria:
  - [x] Every audit event is chained and verifiable — evidence: `test_record_chains_events`, `test_reopen_continues_chain`, ten tamper/truncation tests, `test_concurrent_threads_keep_chain_valid`; artifacts/acceptance-demo.txt
  - [x] Logs carry the run_id — evidence: `test_every_file_line_is_json_with_run_id` (incl. third-party stdlib lines), `test_console_is_pretty_text`; artifacts/acceptance-demo.txt
- Waivers: none
