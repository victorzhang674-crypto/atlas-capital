# Stage 04 — Core domain model · Verification evidence

Fill this in **only** with output from commands actually run in this stage's final chat (Project.md Rule 1). Save full raw outputs under `artifacts/` and quote the key lines here.

#### Verification evidence — Stage 04 — 2026-10-26
- Commands run (this chat, after the last code change):
  - `uv run pytest -q` → 426 passed, 0 failed, 0 deselected, exit=0 · raw: artifacts/pytest.txt
  - `uv run ruff check .` → All checks passed!, exit=0 · raw: artifacts/lint-type.txt
  - `uv run ruff format --check .` → 55 files already formatted, exit=0 · raw: artifacts/lint-type.txt
  - `uv run mypy src/atlas` → Success: no issues found in 32 source files, exit=0 (strict on `atlas.core.*`, which includes `atlas.core.types`) · raw: artifacts/lint-type.txt
  - `uv run pytest tests/unit/core/types -q && uv run mypy src/atlas/core` (brief's Verify) → 241 passed; Success: no issues found in 15 source files; exit=0 · raw: artifacts/verify.txt
  - `make check` → ruff clean, 55 files formatted, mypy Success, 426 passed, exit=0 · raw: artifacts/make-check.txt
  - The stage's tests with `-v` → 241 PASSED, 0 FAILED/ERROR · raw: artifacts/stage-tests-verbose.txt
  - §7 coverage script (each contract → module, fields, its `test_json_roundtrip[...]` and `test_models_and_maps_are_immutable[...]` results read from the verbose log) → every row `roundtrip=PASSED immutable=PASSED`, "ALL COVERED: True" · raw: artifacts/section7-coverage.txt
  - Public docstring check over the nine `atlas.core.types` modules → `public items without docstring: []` · raw: artifacts/docstrings.txt
  - Wall-clock grep `datetime\.now|date\.today|time\.time|utcnow` over `src/atlas` and `tests` → no match (grep exit 1) · raw: artifacts/wallclock-grep.txt
  - Mutation checks (worklog, Task 9 and fix pass): removing `freeze` from `SymbolWeights` → 4 failed; removing the `Bar` sample → `test_every_model_has_a_sample` failed; removing `FrozenDict.__hash__` → 5 failed; each restored → green
- Acceptance criteria (brief.md):
  - [x] Every contract in spec §7 exists — evidence: artifacts/section7-coverage.txt lists `AnalystReport`, `InvestmentThesis`, `PodProposal`, `CapitalAllocation`, `TargetPortfolio`, `RiskDecision`, `Order`, `Fill`, `Position`, `LedgerEntry` (collision C1), `NAVRecord`, `Prediction` in `atlas.core.types`; `test_every_model_has_a_sample` PASSED (all 29 exported models sampled)
  - [x] … validated — evidence: `test_bad_bars_rejected[*]`, `test_return_distribution_ordered`, `test_confidence_outside_unit_interval_rejected[*]` (18 cases), `test_weights_reject_nan_and_inf[*]` (12 cases), `test_decimal_fields_reject_nan[*]`, `test_fill_quantity_consistency[*]`, `test_limit_price_rules`, `test_capital_allocation_sums_to_one`, `test_approve_with_breach_rejected`, `test_prediction_settlement_rules`, `test_nav_rejects_signs[*]`, `test_prefixed_id_rejects_wrong_prefix`, `test_bool_is_not_a_number[*]`, `test_model_copy_update_revalidates` — all PASSED
  - [x] … and round-trippable — evidence: `test_json_roundtrip[*]` for all 29 models (JSON text, JSON-mode dict, python dict), `test_quantities_are_decimal_exact`, `test_decimal_digits_survive_json` — all PASSED
  - [x] mypy strict passes for `core/types` — evidence: `uv run mypy src/atlas/core` → Success: no issues found in 15 source files (artifacts/verify.txt); `atlas.core.*` is under the strict override in `pyproject.toml`
  - [x] Brief *Tests* — bad bars, unordered distributions, confidence outside [0,1], weights containing NaN, round-trip for every model: as above, all PASSED
  - [x] Brief *Tasks* — IDs + base config (frozen, extra="forbid"): `test_new_id_format`, `test_new_id_rejects_bad_prefix[*]`, `test_id_prefix_values`, `test_atlas_model_frozen_forbid_no_nan`; symbol normalisation (class shares, lower case, whitespace): `test_normalize_class_shares[*]`, `test_normalize_rejects[*]`, `test_normalize_properties` (hypothesis), `test_symbol_map_rejects_colliding_keys`; `TargetPortfolio` helpers with hypothesis (gross ≥ |net|): `test_gross_ge_abs_net`, `test_gross_net_legs_known_answer`, `test_diff_known_answer` — all PASSED
  - [x] Definition of Done extras — docstrings on every public item (artifacts/docstrings.txt); no new config key (contracts carry no thresholds), so no YAML change; whole-branch review done and its Important findings fixed test-first (worklog)
- Waivers: none

**Result:** ALL CRITERIA VERIFIED
