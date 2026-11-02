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
  - Follow-up, on merged `main` (main checkout, `f73fba5`, clean before and after; 2026-10-26 15:03 EDT): `uv run pytest -q` → 426 passed · `uv run ruff check .` → All checks passed! · `uv run ruff format --check .` → 55 files already formatted · `uv run mypy src/atlas` → Success: no issues found in 32 source files · `uv run pytest tests/unit/core/types -q && uv run mypy src/atlas/core` → 241 passed; Success: no issues found in 15 source files · `make check` → all four clean, 426 passed; every command exit=0 · raw: artifacts/merged-main-check.txt (script: artifacts/merged-main-check.sh)
  - Mutation checks (worklog, Task 9 and fix pass): removing `freeze` from `SymbolWeights` → 4 failed; removing the `Bar` sample → `test_every_model_has_a_sample` failed; removing `FrozenDict.__hash__` → 5 failed; each restored → green
- Acceptance criteria (brief.md):
  - [x] Every contract in spec §7 exists — evidence: artifacts/section7-coverage.txt lists `AnalystReport`, `InvestmentThesis`, `PodProposal`, `CapitalAllocation`, `TargetPortfolio`, `RiskDecision`, `Order`, `Fill`, `Position`, `LedgerEntry` (collision C1), `NAVRecord`, `Prediction` in `atlas.core.types`; `test_every_model_has_a_sample` PASSED (all 28 exported models sampled)
  - [x] … validated — evidence: `test_bad_bars_rejected[*]`, `test_return_distribution_ordered`, `test_confidence_outside_unit_interval_rejected[*]` (18 cases), `test_weights_reject_nan_and_inf[*]` (12 cases), `test_decimal_fields_reject_nan[*]`, `test_fill_quantity_consistency[*]`, `test_limit_price_rules`, `test_capital_allocation_sums_to_one`, `test_approve_with_breach_rejected`, `test_prediction_settlement_rules`, `test_nav_rejects_signs[*]`, `test_prefixed_id_rejects_wrong_prefix`, `test_bool_is_not_a_number[*]`, `test_model_copy_update_revalidates` — all PASSED
  - [x] … and round-trippable — evidence: `test_json_roundtrip[*]` for all 28 models (JSON text, JSON-mode dict, python dict), `test_quantities_are_decimal_exact`, `test_decimal_digits_survive_json` — all PASSED
  - [x] mypy strict passes for `core/types` — evidence: `uv run mypy src/atlas/core` → Success: no issues found in 15 source files (artifacts/verify.txt); `atlas.core.*` is under the strict override in `pyproject.toml`
  - [x] Brief *Tests* — bad bars, unordered distributions, confidence outside [0,1], weights containing NaN, round-trip for every model: as above, all PASSED
  - [x] Brief *Tasks* — IDs + base config (frozen, extra="forbid"): `test_new_id_format`, `test_new_id_rejects_bad_prefix[*]`, `test_id_prefix_values`, `test_atlas_model_frozen_forbid_no_nan`; symbol normalisation (class shares, lower case, whitespace): `test_normalize_class_shares[*]`, `test_normalize_rejects[*]`, `test_normalize_properties` (hypothesis), `test_symbol_map_rejects_colliding_keys`; `TargetPortfolio` helpers with hypothesis (gross ≥ |net|): `test_gross_ge_abs_net`, `test_gross_net_legs_known_answer`, `test_diff_known_answer` — all PASSED
  - [x] Definition of Done extras — docstrings on every public item (artifacts/docstrings.txt); no new config key (contracts carry no thresholds), so no YAML change; whole-branch review done and its Important findings fixed test-first (worklog)
- Waivers: none

**Result:** ALL CRITERIA VERIFIED

#### Phase 0 checkpoint follow-up (Tier 2) — 2026-11-02
Owner-requested follow-up (see worklog, 2026-11-02): ASCII-only symbols, possible ULIDs only, non-empty text items and unique name keys (`NameMap`), REJECTED orders without fills, no self-parent, no duplicate pods, `Prediction.outcome >= -1`. Re-run in this chat on the final working tree (base commit `a7a1e09`):
- Brief's Verify `uv run pytest tests/unit/core/types -q && uv run mypy src/atlas/core` → 269 passed; Success: no issues found in 23 source files; exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier2-verify.txt](artifacts/checkpoint-2026-11-02-tier2-verify.txt)
- The 26 new test cases FAILED on `a7a1e09`'s code and PASS now · raw: [artifacts/checkpoint-2026-11-02-tier2-red-before-fix.txt](artifacts/checkpoint-2026-11-02-tier2-red-before-fix.txt)
- `uv run pytest -q` → 2088 passed, 0 failed, 11 deselected (live/llm/integration), exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier2-pytest.txt](artifacts/checkpoint-2026-11-02-tier2-pytest.txt) (a first run had 1 failure from two empty harness folders under `src/`/`tests/`, removed; see worklog · raw: [artifacts/checkpoint-2026-11-02-tier2-pytest-first-run.txt](artifacts/checkpoint-2026-11-02-tier2-pytest-first-run.txt))
- `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 205 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 88 source files · raw: [artifacts/checkpoint-2026-11-02-tier2-lint-type.txt](artifacts/checkpoint-2026-11-02-tier2-lint-type.txt)
- `make check` → lint, format check, mypy clean; 2088 passed; exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier2-make-check.txt](artifacts/checkpoint-2026-11-02-tier2-make-check.txt)
- Machine load during these runs: 6.88 → 7.56 (`uptime`) · raw: [artifacts/checkpoint-2026-11-02-tier2-load.txt](artifacts/checkpoint-2026-11-02-tier2-load.txt)
- The acceptance criterion still holds (every spec §7 contract exists, validated and round-trippable; mypy strict for `core/types`): `test_every_model_has_a_sample`, `test_json_roundtrip[*]` and the validation tests pass in the runs above; `uv run mypy src/atlas/core` → Success.
- On merged `main` (`af7c1bd`, fast-forward from `a7a1e09`; run in the owner's Terminal panel, the sandbox cannot write the main checkout): `uv run pytest -q` → 2088 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 205 files already formatted; `mypy src/atlas` → Success (88 files); the three briefs' Verify commands → 201 passed (02), 269 passed + mypy core Success (04), 768 passed + ruff clean (05); `make check` → exit 0 (2088 passed); `git branch --merged main` lists `claude/phase-one-checkpoint-28fcd7`; checkout clean before and after; all 13 commands exit 0 · raw: [stage-04 artifacts/checkpoint-2026-11-02-tier2-merged-main-check.txt](artifacts/checkpoint-2026-11-02-tier2-merged-main-check.txt) (script: same name, `.sh`)

**Result:** ALL CRITERIA VERIFIED

#### Phase 0 checkpoint follow-up (Tier 3) — 2026-11-02
Owner-requested follow-up (see worklog, 2026-11-02): reproducible ids through `use_ids(SeededIds(seed, clock))`; `exp_return` units cross-referenced (spec name kept, Rule 3). Re-run in this chat on the final working tree (base commit `e458f2f`):
- Brief's Verify `uv run pytest tests/unit/core/types -q && uv run mypy src/atlas/core` → 275 passed; Success: no issues found in 23 source files; exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier3-verify.txt](artifacts/checkpoint-2026-11-02-tier3-verify.txt)
- The 6 new tests failed on `e458f2f`'s code (import error; cross-reference assertion) and PASS now · raw: [artifacts/checkpoint-2026-11-02-tier3-red-before-fix.txt](artifacts/checkpoint-2026-11-02-tier3-red-before-fix.txt)
- `uv run pytest -q` → 2108 passed, 0 failed, 11 deselected (live/llm/integration), exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier3-pytest.txt](artifacts/checkpoint-2026-11-02-tier3-pytest.txt)
- `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 205 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 88 source files · raw: [artifacts/checkpoint-2026-11-02-tier3-lint-type.txt](artifacts/checkpoint-2026-11-02-tier3-lint-type.txt)
- `make check` → lint, format check, mypy clean; 2108 passed; exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier3-make-check.txt](artifacts/checkpoint-2026-11-02-tier3-make-check.txt)
- Machine load during these runs: 11.68 → 6.43 (`uptime`) · raw: [artifacts/checkpoint-2026-11-02-tier3-load.txt](artifacts/checkpoint-2026-11-02-tier3-load.txt)
- The acceptance criterion still holds (every spec §7 contract exists, validated and round-trippable; mypy strict for `core/types`): `test_every_model_has_a_sample`, `test_json_roundtrip[*]`, `test_every_public_model_and_enum_is_exported` pass; `uv run mypy src/atlas/core` → Success.
- On merged `main` (`d846205`, fast-forward from `e458f2f`; run in the owner's Terminal panel, the sandbox cannot write the main checkout; this run also covers a docstring-only wording fix to `audit.py` made after the working-tree runs): `uv run pytest -q` → 2108 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 205 files already formatted; `mypy src/atlas` → Success (88 files); the three briefs' Verify commands → 788 passed (03), 275 passed + mypy core Success (04), 136 passed (06); 1M-row upsert → 1.12 s PASSED; `make check` → exit 0 (2108 passed); `git branch --merged main` lists `claude/phase-one-checkpoint-28fcd7`; checkout clean before and after; all 14 commands exit 0 · raw: [stage-03 artifacts/checkpoint-2026-11-02-tier3-merged-main-check.txt](../stage-03-logging-audit/artifacts/checkpoint-2026-11-02-tier3-merged-main-check.txt) (script: same name, `.sh`)

**Result:** ALL CRITERIA VERIFIED
