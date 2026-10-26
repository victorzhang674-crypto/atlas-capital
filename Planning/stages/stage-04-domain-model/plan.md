# Stage 04 — Core domain model · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** DONE (2026-10-26; see [verification.md](verification.md))
- **Gate check:** Stage 03 is DONE (its [verification.md](../stage-03-logging-audit/verification.md) says ALL CRITERIA VERIFIED; evidence block also in `PROGRESS.md`); `uv run pytest -q` → 185 passed, exit 0 (2026-10-26) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution method:** executing-plans (native, inline TDD), chosen by the owner in chat 2026-10-26; one whole-branch review by a fresh reviewer before DONE.

> Steps use checkbox (`- [ ]`) syntax; tick only after the step's tests have run and passed in this chat.

## Context
Stage 03 is `DONE` (PROGRESS.md and its `verification.md` both say ALL CRITERIA VERIFIED). Stage 04 builds the shared, typed vocabulary in `src/atlas/core/types/` (spec §7). Every later stage imports it: research agents (29–36), pods (38–43), risk and compliance (44, 48), OMS (50) and accounting (52). The owner asked for the planning to come first, then the implementation; the owner approved this plan in chat on 2026-10-26.

**Goal:** Frozen, validated, JSON round-trippable Pydantic contracts for instruments, bars, research outputs, portfolio decisions, orders, fills, positions, predictions, lessons, NAV and ledger records, plus prefixed ULID ids.

**Architecture:** A package `atlas.core.types` split by area. `base.py` holds the shared model config and the annotated field types. `ids.py` holds `new_id` and the id prefixes. One module per area follows. `__init__.py` re-exports every public name through `__all__`. The modules carry no behaviour beyond small helpers: OMS transitions are Stage 50, ledger maths Stage 52.

**Tech stack:** pydantic 2.14 (already a dependency), python-ulid 4.0.1 (already a dependency), and hypothesis 6.170 (already in the `dev` group). No new dependencies.

**Spec:** `Planning/stages/stage-04-domain-model/brief.md`; design spec §7 (contracts), §3 principle 5 (ids), §6 (contract owners); stage protocol §4 (units: weights as fractions of NAV with shorts negative, simple returns, `Decimal` for money, tz-aware UTC, `date` for trading days). Fields were cross-checked against the later briefs that consume them: 08, 11, 26, 27, 33, 35, 36, 38, 41, 43, 44, 48, 49, 50, 52, 53, 60.

## Global constraints
- Every model subclasses `AtlasModel`: `ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)`. Sequence fields are `tuple[...]`. Mapping fields are read-only `FrozenDict`s that dump as plain dicts.
- mypy is strict on `atlas.core.*`, which is already configured. `__init__.py` exports through `__all__` because `no_implicit_reexport` is on.
- Every public class and function has a docstring that states its units.
- Code makes no wall-clock call. Timestamps are passed in by callers (Stage 05 owns the clock). ULIDs use python-ulid internals, as decided in Stage 03 D2.
- No config keys are added, because these are contracts with no thresholds.
- There is one commit for the whole stage, `stage-04: Core domain model`, and no per-task commits (stage protocol §3).
- Tests go in `tests/unit/core/types/`. Files are named `test_types_<module>.py`, because the test tree has no `__init__.py` and basenames must be unique.
- Hypothesis tests use `@settings(derandomize=True)` so runs are reproducible.

## Collisions with the overall plan (logged in PROGRESS.md › Decisions log)
- **C1 — `LedgerEntry`:** spec §7 lists it, but the brief's In-scope list leaves it out. The spec ranks higher (Rule 3), and the brief's acceptance criterion says "every contract in spec §7 exists". So Stage 04 creates the `LedgerEntry` contract, using the fields from Stage 52's brief (account, debit/credit, amount, ref). The ledger maths stays in Stage 52.
- **C2 — ids:** the brief says "IDs are ULIDs with prefixes". The Stage 11 and 26 briefs require evidence ids that are stable and come from the source (`news:<article_id>`). Resolution: ULID ids are used only for objects Atlas mints. `Evidence.id` is a non-empty source key.

## Design decisions (logged in PROGRESS.md › Decisions log)
| # | Decision |
|---|---|
| D1 | `new_id(prefix)` returns `f"{prefix}_{ULID()}"`. The prefix must match `^[a-z]{2,5}$`, otherwise `ValueError`. `IdPrefix(StrEnum)` has: THESIS `th`, PROPOSAL `prop`, ORDER `ord`, FILL `fill`, PREDICTION `pred`, LESSON `les`, LEDGER `led`. `prefixed_id(prefix)` returns an `Annotated[str, ...]` that validates `^{prefix}_[0-9A-HJKMNP-TV-Z]{26}$`. Id fields are **required**, with no default factory, so a record reloaded without an id fails instead of getting a fresh one. |
| D2 | `PodProposal` gets an `id` (`prop_`) even though §7 does not list one, because §3 principle 5 says proposals have IDs. `ICDecision` and `RiskMemo` (spec §6, not §7) are left to Stages 49 and 47, which define their fields. |
| D3 | Number types: `float` for analytics (bar prices, weights, returns, confidence). `Decimal` for share quantities, trade prices, commissions, cash, NAV and ledger amounts. Non-finite values are rejected everywhere. |
| D4 | Symbols: the canonical form is uppercase with a dot for the share class (`BRK.B`). `normalize_symbol` strips the input, uppercases it, and maps `-` and `/` to `.`. The canonical pattern is `^[A-Z][A-Z0-9]{0,9}(\.[A-Z0-9]{1,4})?$`; anything else raises `ValueError`. `vendor_symbol(symbol, separator)` converts back (`"-"` for yfinance). `Symbol` is `Annotated[str, BeforeValidator(normalize_symbol)]`. A symbol-keyed map rejects two keys that normalise to the same symbol. |
| D5 | Field names follow the higher-ranked source: §7 contracts use `ticker`; brief types (`Instrument`, `Bar`, trading) use `symbol`. Both use the `Symbol` type. |
| D6 | `TargetPortfolio` helpers act on the whole book, `weights` plus `hedges` (Stage 43 keeps hedges in a separate dict). `gross() = Σ\|w\|`, `net() = Σw`, `long_leg()` holds w > 0, `short_leg()` holds w < 0. `diff(other)` gives self − other over the union of symbols, leaving out exact zeros, so `other + diff == self`. |
| D7 | `OrderStatus` is the Stage 50 enum (`NEW, ACCEPTED, SUBMITTED, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED`) with an `is_terminal` property only. The transition table is Stage 50's. `Order` checks that its record is consistent (fill amount against status). It does not check transitions. |
| D8 | Contracts whose fields only a later brief spells out take that brief's fields: `MacroOutlook` from 33:9, `DebateTurn` from 35:10, `Lesson` from 27:8, `ComplianceResult` from 48:10. Fields that later briefs add to §7 contracts (for example `low_quality`, `exits`, `stop_level`, urgency, idempotency key) are **not** added now (YAGNI); those stages extend the models. |
| D9 | `FrozenDict` is a public copy of `config.py`'s private `_FrozenDict` pattern. `config.py` is not touched, and folding the two together is added to the Backlog. |

## File structure
| File | Responsibility |
|---|---|
| `src/atlas/core/types/__init__.py` | Package docstring; re-exports every public name; `__all__` |
| `src/atlas/core/types/base.py` | `AtlasModel`, `FrozenDict`, `UnitInterval`, `NonEmptyStr`, `UtcDatetime`, `frozen_map` helpers |
| `src/atlas/core/types/ids.py` | `IdPrefix`, `new_id`, `prefixed_id` |
| `src/atlas/core/types/instruments.py` | `normalize_symbol`, `vendor_symbol`, `Symbol`, `SymbolWeights`, `AssetClass`, `Instrument` |
| `src/atlas/core/types/market.py` | `Bar` |
| `src/atlas/core/types/research.py` | `Direction`, `Signal`, `Evidence`, `AnalystReport`, `AssetView`, `MacroOutlook`, `DebateRole`, `Claim`, `DebateTurn`, `ReturnDistribution`, `Catalyst`, `ThesisStatus`, `InvestmentThesis` |
| `src/atlas/core/types/portfolio.py` | `View`, `PodProposal`, `CapitalAllocation`, `TargetPortfolio`, `RiskStatus`, `Breach`, `RiskDecision`, `Severity`, `Violation`, `ComplianceResult` |
| `src/atlas/core/types/trading.py` | `Side`, `OrderType`, `TimeInForce`, `OrderStatus`, `Order`, `Fill`, `Position` |
| `src/atlas/core/types/performance.py` | `Forecast`, `Prediction`, `Lesson`, `NAVRecord`, `EntrySide`, `LedgerEntry` |
| `tests/unit/core/types/test_types_{ids,base,instruments,market,research,portfolio,trading,performance,roundtrip}.py` | Tests per module plus the round-trip sweep |

## Review focus
1. NaN or inf hidden in a mapping value (`weights={"AAPL": nan}`) or in a `Decimal` (`Decimal("NaN")`) must be rejected. Tests: `test_weights_reject_nan_and_inf` (Task 5) and `test_decimal_fields_reject_nan` (Task 6).
2. `{"BRK-B": 0.01, "BRK.B": 0.02}` must raise, not silently keep one. Test: `test_symbol_map_rejects_colliding_keys` (Task 2).
3. Mutating a shared contract in place (`tp.weights["AAPL"] = 1`, `tp.as_of = …`) must raise. Test: `test_models_and_maps_are_immutable` (Task 9).
4. A naive datetime passed as `created_at`/`ts`/`made_at` must be rejected, and an aware non-UTC one must be stored as UTC. Test: `test_utc_datetime_rejects_naive_and_converts` (Task 1).
5. Reloading JSON that lacks an id, or carries an id with the wrong prefix (`ord_` given where `th_` is expected), must fail. Tests: `test_prefixed_id_rejects_wrong_prefix` (Task 1) and `test_roundtrip_missing_id_fails` (Task 9).

---

### Task 0: Gate check and stage folder
- [x] Set up the worktree environment: `UV_CACHE_DIR="$TMPDIR/uv-cache" UV_PROJECT_ENVIRONMENT=.venv.nosync uv sync --group dev && ln -s .venv.nosync .venv`.
- [x] Gate: confirm Stage 03 is DONE and its `verification.md` says ALL CRITERIA VERIFIED. Run `uv run pytest -q`; expect 185 passed. Save the output to `artifacts/gate-check-pytest.txt`. **Stop if it is not green.**
- [x] Copy `Planning/stages/_template/{plan.md,worklog.md,verification.md,artifacts/}` into `stage-04-domain-model/`. Write `plan.md` (this plan). Set PROGRESS.md to Stage 04 `IN PROGRESS` and add the C1, C2 and D1–D9 rows. Start `worklog.md`.

### Task 1: Base model, field types and ids
**Files:** create `types/__init__.py`, `types/base.py`, `types/ids.py`, `test_types_base.py`, `test_types_ids.py`.
**Produces:** `AtlasModel`; `FrozenDict[K, V]`; `UnitInterval = Annotated[float, Field(ge=0, le=1)]`; `NonEmptyStr` (stripped, min length 1); `UtcDatetime` (`AwareDatetime`, converted to UTC); `IdPrefix`; `new_id(prefix: str) -> str`; `prefixed_id(prefix: str) -> type[str]` (an Annotated alias). Also these aliases: `ThesisId`, `ProposalId`, `OrderId`, `FillId`, `PredictionId`, `LessonId`, `LedgerId`.
- [x] Tests:
  - `test_atlas_model_frozen_forbid_no_nan`: a subclass rejects an extra key, rejects assignment, and rejects `float("nan")`.
  - `test_frozen_dict_readonly_and_equal`: no item assignment; equal to a dict with the same items; pickles.
  - `test_unit_interval_bounds`: 0 and 1 accepted; −0.01 and 1.01 rejected.
  - `test_non_empty_str`: `"  "` rejected.
  - `test_utc_datetime_rejects_naive_and_converts`: naive raises; `09:30 America/New_York` becomes `14:30+00:00`.
  - `test_new_id_format`: `new_id("th")` matches `^th_[0-9A-HJKMNP-TV-Z]{26}$`; two calls differ.
  - `test_new_id_rejects_bad_prefix`: `""`, `"TH"`, `"th_"`, `"toolong"` raise `ValueError`.
  - `test_id_prefix_values`: the seven values from D1.
  - `test_prefixed_id_rejects_wrong_prefix`: a model with `ThesisId` rejects `new_id("ord")` and accepts `new_id("th")`.
- [x] Run `uv run pytest tests/unit/core/types -q` and expect FAIL (`ModuleNotFoundError`). Implement. Run again and expect PASS. Run `uv run mypy src/atlas/core` and expect Success.

### Task 2: Instruments and symbols
**Files:** create `types/instruments.py`, `test_types_instruments.py`.
**Produces:** `normalize_symbol(raw: str) -> str`; `vendor_symbol(symbol: str, separator: str = "-") -> str`; `Symbol`; `SymbolWeights` (a symbol→float map: normalises keys, rejects collisions, stored as `FrozenDict`); `AssetClass` (EQUITY, ETF, CASH); `Instrument{symbol: Symbol, asset_class, exchange: str | None = None, sector: str | None = None, industry: str | None = None, currency: str = "USD" (pattern `^[A-Z]{3}$`), is_shortable: bool = True}`. A validator rejects CASH with `is_shortable=True`.
- [x] Tests:
  - `test_normalize_class_shares`, parametrized: `BRK.B`, `BRK-B`, `BRK/B`, `brk.b` and ` brk-b \n` all give `BRK.B`.
  - `test_normalize_rejects`, parametrized: `""`, `"   "`, `"BR K"`, `"BRK..B"`, `"-B"`, `"BRK.B.C"`, `"AAPL$"`.
  - `test_vendor_symbol`: `vendor_symbol("BRK.B") == "BRK-B"`; `vendor_symbol("AAPL") == "AAPL"`.
  - `test_normalize_properties` (hypothesis, built from the canonical pattern): `normalize_symbol` is idempotent, and `normalize_symbol(vendor_symbol(s)) == s`.
  - `test_symbol_map_rejects_colliding_keys`.
  - `test_instrument_normalises_symbol`.
  - `test_cash_cannot_be_shortable`.
  - `test_currency_must_be_iso`.
- [x] Run, see red, implement, see green, then run mypy.

### Task 3: Market bars
**Files:** create `types/market.py`, `test_types_market.py`.
**Produces:** `Bar{symbol: Symbol, date: date, open, high, low, close, adj_close: float (>0), volume: float (≥0)}`. Prices are per share in the instrument's currency, unadjusted except `adj_close` (adjusted for splits and dividends). Volume is in shares. The validator requires `high ≥ max(open, close, low)` and `low ≤ min(open, close)`.
- [x] Tests:
  - `test_valid_bar`.
  - `test_bad_bars_rejected`, parametrized: high < open; high < close; low > open; low > close; zero or negative price; negative volume; NaN close; inf high.
  - `test_doji_bar_ok`: all four prices equal.
- [x] Run, see red, implement, see green.

### Task 4: Research contracts
**Files:** create `types/research.py`, `test_types_research.py`.
**Produces:**
- `Direction` (LONG, SHORT, FLAT), with `.sign` returning 1, −1 or 0.
- `Signal{name: NonEmptyStr, direction, strength: UnitInterval, evidence_ids: tuple[str, ...] = ()}`.
- `Evidence{id: NonEmptyStr, source: NonEmptyStr, as_of: UtcDatetime (when the evidence became public), text: NonEmptyStr, url: str | None = None}`.
- `AnalystReport{ticker: Symbol, as_of: date, analyst: NonEmptyStr, summary: NonEmptyStr, signals: tuple[Signal, ...], risks: tuple[str, ...], confidence: UnitInterval}`.
- `AssetView{etf: Symbol, direction, conviction: UnitInterval, horizon_days: int ≥ 1}`.
- `MacroOutlook{as_of: date, regime_summary, growth, inflation, policy, credit: NonEmptyStr, risks: tuple[str, ...], asset_views: tuple[AssetView, ...], sector_tilts: map str→float in [−1, 1], risk_appetite: UnitInterval, evidence: tuple[str, ...]}`.
- `DebateRole` (BULL, BEAR, RED_TEAM).
- `Claim{text: NonEmptyStr, evidence_ids: tuple[str, ...], strength: UnitInterval}`.
- `DebateTurn{speaker: DebateRole, claims: tuple[Claim, ...], concessions: tuple[str, ...] = (), questions_for_other_side: tuple[str, ...] = ()}`.
- `ReturnDistribution{p10, p50, p90: float (≥ −1)}`: simple returns over the thesis horizon, with `p10 ≤ p50 ≤ p90`.
- `Catalyst{description: NonEmptyStr, expected_date: date | None = None}`.
- `ThesisStatus` (ACTIVE, INVALIDATED, CLOSED, EXPIRED).
- `InvestmentThesis{id: ThesisId, ticker: Symbol, as_of: date, direction, exp_return: ReturnDistribution, horizon_days: int ≥ 1, conviction: UnitInterval, catalysts: tuple[Catalyst, ...] = (), kill_criteria: tuple[str, ...], evidence: tuple[str, ...], status: ThesisStatus = ACTIVE}`. `kill_criteria` are DSL strings that Stage 36 parses. `evidence` holds evidence ids.
- [x] Tests:
  - `test_return_distribution_ordered`: equal values are OK; `p10 > p50` and `p50 > p90` are rejected; `p10 = -1.5` is rejected.
  - `test_confidence_outside_unit_interval_rejected`, parametrized over `AnalystReport.confidence`, `Signal.strength`, `InvestmentThesis.conviction`, `MacroOutlook.risk_appetite` and `Claim.strength`, with values −0.1, 1.1 and NaN.
  - `test_sector_tilt_bounds`.
  - `test_evidence_as_of_must_be_aware`.
  - `test_thesis_defaults_active`.
  - `test_thesis_rejects_wrong_id_prefix`.
  - `test_direction_sign`.
- [x] Run, see red, implement, see green.

### Task 5: Portfolio contracts and `TargetPortfolio` helpers
**Files:** create `types/portfolio.py`, `test_types_portfolio.py`.
**Produces:**
- `View{ticker: Symbol, exp_return: float (annualised simple return), confidence: UnitInterval, horizon_days: int ≥ 1}`.
- `PodProposal{id: ProposalId, pod: NonEmptyStr, as_of: date, views: tuple[View, ...] = (), target_weights: SymbolWeights | None = None, rationale: NonEmptyStr}`.
- `CapitalAllocation{as_of: date, pod_weights: map str→float ≥ 0, risk_budgets: map str→float ≥ 0, rationale: NonEmptyStr}`. Each map must sum to 1 within 1e-9, and both maps must have the same pod keys.
- `TargetPortfolio{as_of: date, weights: SymbolWeights, hedges: SymbolWeights = {}, expected_vol: float ≥ 0 | None = None (annualised), expected_beta: float | None = None}`, with these methods per D6: `book() -> dict[str, float]`, `gross() -> float`, `net() -> float`, `long_leg() -> dict[str, float]`, `short_leg() -> dict[str, float]`, `diff(other: TargetPortfolio) -> dict[str, float]`.
- `RiskStatus` (APPROVE, RESIZE, REJECT).
- `Breach{limit: NonEmptyStr, value: float, threshold: float, detail: str = ""}`.
- `RiskDecision{status, adjusted_weights: SymbolWeights, breaches: tuple[Breach, ...] = (), metrics: map str→float = {}}`. APPROVE with breaches is rejected.
- `Severity` (BLOCK, WARN).
- `Violation{rule: NonEmptyStr, severity, message: NonEmptyStr, symbol: Symbol | None = None, order_id: OrderId | None = None}`.
- `ComplianceResult{approved_orders: tuple[Order, ...] = (), blocked: tuple[Violation, ...] = (), warnings: tuple[Violation, ...] = ()}`, with a `passed` property (true when nothing is blocked). `blocked` must hold only BLOCK violations and `warnings` only WARN ones. This consumes `Order` from Task 6, so Task 6's `trading.py` is built **before** this task's ComplianceResult part (see the order note below).
- [x] Tests:
  - `test_gross_net_legs_known_answer`: weights `{A: 0.05, B: -0.03}` and hedges `{SPY: -0.02}` give gross 0.10 and net 0.00; the legs are as expected.
  - `test_hedges_merge_with_weights_on_same_symbol`.
  - `test_diff_known_answer`.
  - `test_gross_ge_abs_net` (hypothesis, dicts of up to 20 finite weights in [−0.2, 0.2]): `gross() >= abs(net()) - 1e-12`; `gross == sum long − sum short`; `other.book() + diff == self.book()` within approx.
  - `test_weights_reject_nan_and_inf`, parametrized over `TargetPortfolio.weights`, `hedges`, `PodProposal.target_weights` and `RiskDecision.adjusted_weights`.
  - `test_capital_allocation_sums_to_one`: 0.999 is rejected; mismatched keys are rejected.
  - `test_approve_with_breach_rejected`.
  - `test_compliance_result_severity_buckets_and_passed`.
- [x] Run, see red, implement, see green, then run mypy.

### Task 6: Trading contracts (run before Task 5's ComplianceResult step)
**Files:** create `types/trading.py`, `test_types_trading.py`.
**Produces:**
- `Side` (BUY, SELL), with `.sign` returning +1 or −1.
- `OrderType` (MARKET, LIMIT, MOO).
- `TimeInForce` (DAY, GTC, IOC, OPG).
- `OrderStatus` (per D7), with `is_terminal`.
- `Order{id: OrderId, symbol: Symbol, side, quantity: Decimal > 0 (shares), order_type, time_in_force = DAY, limit_price: Decimal > 0 | None = None, status = NEW, filled_quantity: Decimal ≥ 0 = 0, pods: tuple[NonEmptyStr, ...] (at least 1), as_of: date (decision day), created_at: UtcDatetime, parent_id: OrderId | None = None}`, with `remaining_quantity` and `signed_quantity` properties. Rules:
  - LIMIT requires `limit_price`, and the other types forbid it.
  - MOO requires OPG.
  - `filled_quantity ≤ quantity`.
  - FILLED requires `filled == quantity`.
  - PARTIALLY_FILLED requires `0 < filled < quantity`.
  - NEW, ACCEPTED and SUBMITTED require `filled == 0`.
- `Fill{id: FillId, order_id: OrderId, symbol, side, quantity: Decimal > 0, price: Decimal > 0 (per share), commission: Decimal ≥ 0 = 0, ts: UtcDatetime, as_of: date}`, with a `notional` property equal to quantity × price.
- `Position{symbol, quantity: Decimal (signed; shorts negative), avg_cost: Decimal ≥ 0 (per share), pod: str | None = None}`, with `is_long`, `is_short` and `market_value(price: Decimal) -> Decimal`.
- [x] Tests:
  - `test_order_status_terminal_set`.
  - `test_limit_price_rules`.
  - `test_moo_requires_opg`.
  - `test_fill_quantity_consistency`, parametrized over each status rule.
  - `test_order_requires_pod`.
  - `test_quantities_are_decimal_exact`: `"0.1"` round-trips as `Decimal("0.1")`.
  - `test_decimal_fields_reject_nan`.
  - `test_fill_notional`.
  - `test_position_market_value_short`.
- [x] Run, see red, implement, see green.

### Task 7: Performance and accounting records
**Files:** create `types/performance.py`, `test_types_performance.py`.
**Produces:**
- `Forecast{direction, confidence: UnitInterval (probability the direction is right), returns: ReturnDistribution}`.
- `Prediction{id: PredictionId, source_agent: NonEmptyStr, made_at: UtcDatetime, target: Symbol, horizon_days: int ≥ 1, forecast: Forecast, settled_at: UtcDatetime | None = None, outcome: float | None = None (realised simple return over the horizon), score: float | None = None}`, with an `is_settled` property. Rules:
  - `settled_at` and `outcome` are set together or not at all.
  - `score` requires a settlement.
  - `settled_at > made_at`.
- `Lesson{id: LessonId, agent: NonEmptyStr ("global" allowed), created_at: UtcDatetime, text: NonEmptyStr, tags: tuple[str, ...] = (), source_episode_ids: tuple[str, ...] = (), quality_score: UnitInterval | None = None}`.
- `NAVRecord{date, nav: Decimal > 0, cash: Decimal, long_market_value: Decimal ≥ 0, short_market_value: Decimal ≤ 0, nav_per_share: Decimal > 0 | None = None}`. All amounts are in the base currency. `gross_exposure()` and `net_exposure()` return fractions of NAV as `float`.
- `EntrySide` (DEBIT, CREDIT).
- `LedgerEntry{id: LedgerId, as_of: date, account: NonEmptyStr, side: EntrySide, amount: Decimal > 0, ref: NonEmptyStr}`.
- [x] Tests:
  - `test_prediction_settlement_rules`.
  - `test_settled_before_made_rejected`.
  - `test_lesson_fields`.
  - `test_nav_exposures_known_answer`: NAV 100, long 120, short −80 gives gross 2.0 and net 0.4.
  - `test_nav_rejects_signs`.
  - `test_ledger_amount_positive_decimal`.
- [x] Run, see red, implement, see green.

### Task 8: Package exports
**Files:** finish `types/__init__.py`.
- [x] Test `test_all_exports_resolve`: every name in `atlas.core.types.__all__` is importable, and each exported model class is listed there.
- [x] Run, see red, implement, see green. Run `uv run mypy src/atlas/core`.

### Task 9: Round-trip sweep and immutability
**Files:** create `test_types_roundtrip.py`.
- [x] `SAMPLES`: one fully populated valid instance of **every** `AtlasModel` subclass exported, with all optional fields set.
  - `test_every_model_has_a_sample`: the set of exported `AtlasModel` subclasses equals the set of `type(s)` for the samples.
  - `test_json_roundtrip[model]`: `type(m).model_validate_json(m.model_dump_json()) == m`, and the same for `model_validate(m.model_dump(mode="json"))`.
  - `test_models_and_maps_are_immutable`.
  - `test_roundtrip_missing_id_fails`: drop `id` from a dumped thesis or order and expect `ValidationError`.
- [x] Run and expect PASS. If anything fails, fix the model, not the test.

### Task 10: Definition of Done
- [x] Brief's Verify: `uv run pytest tests/unit/core/types -q && uv run mypy src/atlas/core`. Save the output to `artifacts/verify.txt`.
- [x] Run `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/atlas` and `make check`; save the raw output to `artifacts/`.
- [x] Docstring check over `atlas.core.types.*` (expect public items without a docstring = `[]`) → `artifacts/docstrings.txt`. Wall-clock grep over `src` and `tests` → `artifacts/wallclock-grep.txt`. Stage tests with `-v` → `artifacts/stage-tests-verbose.txt`.
- [x] §7 coverage table in `verification.md`: each §7 contract mapped to its class and to its round-trip test.
- [x] Write `verification.md` (ALL CRITERIA VERIFIED only if every box is ticked). Then update PROGRESS.md: evidence summary, handoff note, Backlog rows (D9 FrozenDict dedupe; fields deferred to later stages per D8), tick DONE.
- [x] Single commit: `stage-04: Core domain model` (the commit that contains this tick).

**Task order:** 0 → 1 → 2 → 3 → 4 → 6 → 5 → 7 → 8 → 9 → 10. Trading comes before portfolio because `ComplianceResult` holds `Order`s.

## Verification (end to end)
- `uv run pytest tests/unit/core/types -q && uv run mypy src/atlas/core`: the brief's Verify command.
- `make check`: lint, format, mypy (whole package) and the full suite, all green.
- Acceptance criterion "every §7 contract exists, validated and round-trippable; mypy strict for core/types": evidence is `test_every_model_has_a_sample` + `test_json_roundtrip[*]`, the §7 coverage table, and the mypy output.
