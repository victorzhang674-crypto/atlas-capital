# Stage 04 — Core domain model · Work log

Append entries in order. Record failures and dead ends as honestly as successes. This file is also the executing-plans ledger (rulings are written as `Ruling: <what> — <why> — <cost if wrong>`).

## 2026-10-26 — Gate check, plan and stage setup
- Did: read `Project.md`, `PROGRESS.md` (Stage 03 handoff), the stage protocol, design spec (§3, §6, §7, §11, §12, §15), the brief, the Stage 03 plan/verification, and the later briefs that consume these contracts (08, 11, 26, 27, 33, 35, 36, 38, 41, 43, 44, 48, 49, 50, 52, 53, 60; surveyed by a read-only search agent, Stages 50/52 read in full). Wrote the plan in plan mode (writing-plans skill); the owner approved it and chose native execution. Built this worktree's venv per the handoff (`UV_PROJECT_ENVIRONMENT=.venv.nosync uv sync --group dev`, `.venv` → `.venv.nosync`, `UV_CACHE_DIR="$TMPDIR/uv-cache"`; PyPI access had to be allowed for the sandboxed command). Copied `_template/` (worklog, verification, artifacts) into this folder; `plan.md` is the approved plan. Set the stage to `IN PROGRESS` in `PROGRESS.md` and logged C1, C2, D1–D9 in the Decisions log.
- Ran: `uv run pytest -q` → 185 passed, exit 0 · raw: artifacts/gate-check-pytest.txt. Stage 03 `verification.md` → "ALL CRITERIA VERIFIED"; its evidence block is in `PROGRESS.md`.
- Ruling: the executing-plans skill's separate ledger directory and per-task commits are not used; this worklog is the ledger and the stage gets one commit — Project.md Rule 2A (records in the stage folder) and stage protocol §3 (one commit) outrank the skill — cost if wrong: none beyond a less granular git history.
- Pre-flight (interfaces between tasks): Task 1 → all (AtlasModel, FrozenDict, UnitInterval, NonEmptyStr, UtcDatetime, id aliases); Task 2 → 3–7 (`Symbol`, `SymbolWeights`); Task 4 → 5, 7 (`Direction`, `ReturnDistribution`); Task 6 → 5 (`Order`, `OrderId` in `ComplianceResult`/`Violation`); Task 8 → 9 (`__all__`). Conflict found and ruled in the plan itself: Task 5 consumes Task 6, so order is 0→1→2→3→4→6→5→7→8→9→10.
- Next: Task 1.

## 2026-10-26 — Task 1: base model, field types and ids
- Did: tests `test_types_base.py` (7 tests incl. parametrized) and `test_types_ids.py`; created `src/atlas/core/types/{__init__,base,ids}.py`.
- Ran: `uv run pytest tests/unit/core/types -q` → RED: `ModuleNotFoundError: No module named 'atlas.core.types'` (collection errors, expected); after implementing → 21 passed. `uv run mypy src/atlas/core` → Success: no issues found in 9 source files.
- Ruling: the plan's `prefixed_id(prefix) -> type[str]` became `id_pattern(prefix) -> str` used as `Annotated[str, Field(pattern=id_pattern(...))]` for the seven id aliases — mypy rejects a function-produced alias as an annotation — cost if wrong: one rename; same validation.
- Ruling: `base.py` also exposes `FrozenMap[K, V]` (PEP 695 alias = `Mapping` + `freeze` + `thaw`) and the `freeze`/`thaw` helpers the plan called "frozen_map helpers" — one definition for every read-only mapping field — cost if wrong: none.
- Next: Task 2.

## 2026-10-26 — Task 2: instruments and symbols
- Did: `test_types_instruments.py` (class shares, plain tickers, rejects, `vendor_symbol`, hypothesis idempotence/round trip, map key normalisation and collisions, `Instrument`, cash not shortable, ISO currency); created `instruments.py` (`normalize_symbol`, `vendor_symbol`, `Symbol`, `SymbolWeights`, `AssetClass`, `Instrument`).
- Ran: `uv run pytest tests/unit/core/types/test_types_instruments.py -q` → RED: `ModuleNotFoundError: atlas.core.types.instruments`; after implementing, `uv run pytest tests/unit/core/types -q` → 52 passed; mypy → Success (10 files).
- Ruling: non-string inputs pass through the symbol before-validators untouched so pydantic's own `str` check rejects them (pydantic only turns `ValueError`, not `TypeError`, into a `ValidationError`) — cost if wrong: none.
- Next: Task 3.

## 2026-10-26 — Task 3: market bars
- Did: `test_types_market.py` (valid bar, doji, 12 bad bars incl. NaN/inf and an unknown `vendor` key); created `market.py` (`Bar`).
- Ran: RED: `ModuleNotFoundError: atlas.core.types.market`; GREEN: `uv run pytest tests/unit/core/types -q` → 66 passed; mypy → Success (11 files).
- Ruling: `Bar.date` is annotated `dt.date` (module import) so the field name does not shadow the type in the class body — cost if wrong: none.
- Next: Task 4.

## 2026-10-26 — Task 4: research contracts
- Did: `test_types_research.py` (ordered/bounded `ReturnDistribution`, unit-interval checks on six fields × {−0.1, 1.1, NaN} plus bounds accepted, sector tilts, ticker normalisation, aware `Evidence.as_of`, `DebateTurn`, thesis defaults/horizon/id prefix, `Direction.sign`); created `research.py`.
- Ran: RED: `ModuleNotFoundError: atlas.core.types.research`; GREEN: `uv run pytest tests/unit/core/types -q` → 99 passed; mypy → Success (12 files).
- Ruling: `HorizonDays` (int ≥ 1) is a public alias in `research.py`, reused by `View`/`Prediction` — cost if wrong: none.
- Next: Task 6 (before Task 5, per plan order).

## 2026-10-26 — Task 6: trading contracts
- Did: `test_types_trading.py` (enum values, terminal set, defaults/helpers, limit and MOO/OPG rules, 13 status/filled-quantity cases, pods required, bad quantity/naive time/wrong parent prefix, exact Decimal round trip, NaN/±Infinity/sNaN rejected in Order, Fill and Position, notional, short market value); created `trading.py`.
- Ran: RED: `ModuleNotFoundError: atlas.core.types.trading`; GREEN: `uv run pytest tests/unit/core/types -q` → 126 passed; mypy → Success (13 files). Pydantic rejects non-finite `Decimal`s under `allow_inf_nan=False` without extra code.
- Next: Task 5.

## 2026-10-26 — Task 5: portfolio contracts and TargetPortfolio helpers
- Did: `test_types_portfolio.py` (gross/net/legs known answer, hedges merging on a shared symbol, zero weight in neither leg, diff known answer, hypothesis property test over 300 derandomized examples: gross ≥ |net|, gross = long − short, `other + diff == self`, no zero entries; NaN/±inf rejected in all four weight maps; maps frozen; optional fields; View/PodProposal incl. wrong id prefix; CapitalAllocation sums and same pods; APPROVE-with-breach; compliance buckets and `passed`); created `portfolio.py`.
- Ran: RED: `ModuleNotFoundError: atlas.core.types.portfolio`; GREEN: `uv run pytest tests/unit/core/types -q` → 152 passed; mypy → Success (14 files).
- Ruling: mapping fields with an empty default (`TargetPortfolio.hedges`, `RiskDecision.metrics`) use `default_factory=dict, validate_default=True` so the default is also a `FrozenDict` — a plain `{}` default would be mutable — cost if wrong: none.
- Ruling: `CapitalAllocation` maps take values in [0, 1] (a single pod cannot exceed the fund); sums use `math.fsum` with tolerance 1e-9 — cost if wrong: Stage 49 relaxes a bound.
- Next: Task 7.

## 2026-10-26 — Task 7: performance and accounting records
- Did: `test_types_performance.py` (settlement rules incl. "settle both or none", score needs settlement, NaN outcome, `settled_at > made_at`, forecast bounds, lesson fields and id prefix, NAV exposures known answer (gross 2.0, net 0.4), six sign/NaN rejections, negative cash allowed, ledger amount positive Decimal and non-empty account); created `performance.py`.
- Ran: RED: `ModuleNotFoundError: atlas.core.types.performance`; GREEN: `uv run pytest tests/unit/core/types -q` → 165 passed; mypy → Success (15 files).
- Next: Task 8.

## 2026-10-26 — Task 8: package exports
- Did: `test_types_exports.py` (every `__all__` name resolves and is unique; every public class defined in the eight modules is exported; helpers exported); filled `types/__init__.py` (explicit imports + sorted `__all__`, 59 names; internal helpers `freeze`, `thaw`, `id_pattern` stay module-level only).
- Ran: RED: 3 failed, `AttributeError: module 'atlas.core.types' has no attribute '__all__'`; GREEN: 168 passed; mypy → Success (15 files). `ruff check` then flagged 7 items (en dash, minus and multiplication signs in docstrings — RUF002; an unescaped `match=` pattern — RUF043); replaced with ASCII and a raw pattern; `ruff check .` → All checks passed!, `ruff format --check .` → 53 files already formatted.
- Ruling: the exports test lives in its own file `test_types_exports.py` (the plan named no file for Task 8) — cost if wrong: none.
- Next: Task 9.

## 2026-10-26 — Task 9: round-trip sweep and immutability
- Did: `test_types_roundtrip.py`: `SAMPLES` = one fully populated instance of each of the 29 exported `AtlasModel` subclasses (all optional fields set); `test_every_model_has_a_sample`, `test_json_roundtrip[*]` (JSON text, JSON-mode dict and python dict), `test_models_and_maps_are_immutable[*]` (assignment raises; no list/dict/set attributes; `FrozenDict` item assignment raises), `test_default_maps_are_frozen`, `test_roundtrip_missing_id_fails[*]` (6 id-bearing models), `test_roundtrip_wrong_id_prefix_fails`, `test_decimal_digits_survive_json`.
- Ran: `uv run pytest tests/unit/core/types/test_types_roundtrip.py -q` → 66 passed on first run (expected: the sweep covers finished models). Mutation checks to prove it can fail: (1) removed `AfterValidator(freeze)` from `SymbolWeights` → 4 failed (`test_models_and_maps_are_immutable[TargetPortfolio]`, `[RiskDecision]`, `test_default_maps_are_frozen`, …); (2) removed the `Bar` sample → `test_every_model_has_a_sample` FAILED; both restored → `uv run pytest tests/unit/core/types -q` → 234 passed.
- Next: full suite, then the whole-branch review.

## 2026-10-26 — Pre-review checks
- Ran: `uv run pytest -q` → 419 passed; `uv run ruff check .` → first run flagged one E501 in `test_types_roundtrip.py` (fixed by splitting the expression), then All checks passed!; `uv run ruff format --check .` → 54 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 32 source files.
- Next: whole-branch review by a fresh reviewer (most capable model), read-only, over the uncommitted tree against `main` (2e56ef0).

## 2026-10-26 — Final whole-branch review and fix pass
- Reviewer: fresh general-purpose agent on the most capable model (Opus), read-only, over the uncommitted tree vs `main` (2e56ef0). It re-ran the suite (419 passed) and mypy (Success, also `--strict` on `core/types`), probed all five plan Review Focus items (all hold), and reported 2 Important + 10 Minor findings and 8 "declined to judge" items. It disagreed with none of the worklog rulings and found nothing built for a later stage.
- Re-grade: #1 (`model_copy(update=...)` skips validation → mutable/unnormalised/invalid copies) Important; #2 (models holding a `FrozenDict` are unhashable while other frozen models hash) Important; #3 (bool accepted as a number: `confidence=True` → 1.0) upgraded Minor → Important, because LLM structured outputs (Stage 26+) feed these contracts and `true` would silently become maximum confidence.
- Final: fixed #1 — `AtlasModel.model_copy` re-validates when `update` is given — `test_model_copy_update_revalidates` RED→GREEN.
- Final: fixed #2 — `FrozenDict.__hash__` = hash of the frozenset of items — `test_models_with_maps_are_hashable` RED→GREEN; the round-trip sweep now also hashes every sample (mutation check: removing `__hash__` → 5 failed; restored).
- Final: fixed #3 — `reject_bool` before-validator; `Real` (float, no bool) used for weights (`SymbolWeights` values), `View.exp_return`, `ReturnDistribution` quantiles and `sector_tilts`; `UnitInterval` and `HorizonDays` reject bool — `test_bool_is_not_a_number[unit_interval|horizon_days|view_return|weights]` RED→GREEN. Suite 426/426, ruff clean, mypy Success (32 files).
- Final: Ruling: `Real` is applied to the LLM-facing numeric fields listed above, not to every float (bar prices, NAV floats and `Breach` values come from deterministic code) — smallest change covering the finding — cost if wrong: a later stage swaps more `float` fields to `Real`.
- Final: minor (deferred): empty strings accepted inside tuples (evidence ids, kill criteria, risks), duplicate `Order.pods`, unchecked map keys (`sector_tilts` "" or case-duplicates, empty pod names in `CapitalAllocation`), `Position.pod=""`.
- Final: minor (deferred): `normalize_symbol` maps non-ASCII via `str.upper()` (`ß` → `SS`); reject non-ASCII input.
- Final: minor (deferred): id pattern accepts impossible ULIDs (first char 8–Z); tighten to `[0-7]`.
- Final: minor (deferred): `Order` accepts REJECTED with fills and `parent_id == id`.
- Final: minor (deferred): `Prediction.outcome` has no `ge=-1` bound.
- Final: minor (deferred): `exp_return` means annualised in `View` but over-the-horizon in `InvestmentThesis`/`Forecast`; rename or cross-reference before Stage 38/41.
- Final: minor (deferred): `new_id` uses ULID randomness and wall-clock time with no seed/clock parameter (same as Stage 03's run/event ids); ids differ across backtest reruns.
- Final: minor (deferred): `test_roundtrip_missing_id_fails` omits `PodProposal`; the Order/Fill NaN cases in `test_decimal_fields_reject_nan` would also fail on `gt=0` (only `Position` and `NAVRecord.cash` isolate `allow_inf_nan`).
- Final: Ruling: no `nav == cash + long + short − accruals` identity on `NAVRecord` — Stage 52 owns the ledger maths and accrual accounts — cost if wrong: Stage 52 adds the check.
- Final: Ruling: RESIZE/REJECT with no breaches allowed — a REJECT may come from CRITICAL data, missing risk-model coverage or a HALT breaker (Stage 44:10), none of which is a limit breach — cost if wrong: Stage 44 adds the rule.
- Final: Ruling: `ComplianceResult` does not cross-check approved vs blocked order ids — the record shape is Stage 48's; it owns that invariant — cost if wrong: a Stage 48 test.
- Final: Ruling: no direction-vs-`p50`-sign check on `InvestmentThesis` — Stage 36's validator owns thesis consistency (conviction vs debate balance) — cost if wrong: Stage 36 adds it.
- Final: Ruling: `Instrument(asset_class=CASH)` must pass `is_shortable=False` explicitly — explicit is safer than a class-dependent default — cost if wrong: one default change.
- Final: Ruling: integer epoch accepted for `UtcDatetime` (pydantic reads it as UTC) — unambiguous instant, already UTC — cost if wrong: none.
- Final: Ruling: duplicate keys inside one JSON object resolve last-wins — that is the JSON parser's behaviour; symbol-normalisation collisions are still caught — cost if wrong: none for Atlas-written JSON.
- Final: Ruling: `gross()` nets a hedge against a stock on the same symbol (D6, approved) — the book really holds the net position — cost if wrong: Stage 43/44 reports hedges separately.
- Next: Task 10 (Definition of Done).

## 2026-10-26 — Task 10: Definition of Done
- Ran (after the last code change; raw output in `artifacts/`): `uv run pytest tests/unit/core/types -q && uv run mypy src/atlas/core` → 241 passed; Success (15 files) · `uv run pytest -q` → 426 passed · `ruff check .` → All checks passed! · `ruff format --check .` → 55 files already formatted · `mypy src/atlas` → Success (32 files) · `make check` → exit=0 · stage tests `-v` → 241 PASSED, 0 FAILED · docstring check → `[]` · wall-clock grep → no match · §7 coverage → ALL COVERED: True.
- Did: wrote `verification.md` (ALL CRITERIA VERIFIED); ticked `plan.md`; updated `PROGRESS.md` (review decision row, Backlog rows for D9 and the deferred minors/fields, handoff note, evidence summary, box ticked, Stage 04 DONE, current stage 05 NOT STARTED); committed `stage-04: Core domain model` (one commit).

## 2026-10-26 — Commit and merge into main
- Did: committed `f73fba5 stage-04: Core domain model` on `claude/stage-4-planning-implementation-730689` (worktree clean afterwards). On the owner's instruction ("Fast-forward main to the stage-04 commit and re-run make check on main."), tried `git merge --ff-only` in the main checkout from this session: the sandbox refused (`unable to unlink old 'Planning/PROGRESS.md': Operation not permitted`); `main` stayed at `2e56ef0`, clean, nothing half-applied. After the owner said "yes, please help me to do it.", ran the fast-forward and `make check` in the owner's Terminal panel instead (outside the sandbox): `main` 2e56ef0 → f73fba5 (fast-forward, 33 files), `make check` → ruff clean, 55 files formatted, mypy Success (32 files), 426 passed, exit=0.

## 2026-10-26 — Follow-up records (merged-main check)
- Did: on the owner's instruction ("Record the merged-main make check in Stage 04's verification.md, worklog and PROGRESS.md as a records-only follow-up commit on main, like Stages 02 and 03."), wrote `artifacts/merged-main-check.sh` (same shape as Stage 03's) and ran it from the owner's terminal against the main checkout, saving `artifacts/merged-main-check.txt`.
- Ran: Follow-up, on merged `main` (main checkout, `f73fba5`, clean before and after; 2026-10-26 15:03 EDT): `uv run pytest -q` → 426 passed · `uv run ruff check .` → All checks passed! · `uv run ruff format --check .` → 55 files already formatted · `uv run mypy src/atlas` → Success: no issues found in 32 source files · `uv run pytest tests/unit/core/types -q && uv run mypy src/atlas/core` → 241 passed; Success: no issues found in 15 source files · `make check` → all four clean, 426 passed; every command exit=0 · raw: artifacts/merged-main-check.txt (script: artifacts/merged-main-check.sh).
- Did: added the follow-up line to `verification.md` and the `PROGRESS.md` evidence summary, plus a Decisions log row recording that Stage 04 now has two commits (the second records-only). Committed `stage-04: follow-up records` on the branch and fast-forwarded `main` to it from the owner's terminal.
