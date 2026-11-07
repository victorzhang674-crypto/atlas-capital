# Stage 22 — Systematic alpha models · Work log

Append entries in order. Record failures and dead ends as honestly as successes.

Commands run as `UV_CACHE_DIR=$TMPDIR/uv-cache-s22 UV_NO_SYNC=1 LOKY_MAX_CPU_COUNT=4 MPLCONFIGDIR=$TMPDIR/mpl uv run …` from the worktree `.claude/worktrees/stage-22-systematic-alpha-b15459` (branch `claude/stage-22-systematic-alpha-b15459`).

## 2026-11-07 — Rule 0 / Rule 4 gate check
- Read `Project.md` in full and `Planning/PROGRESS.md`: current stage 22 — Systematic alpha models (`NOT STARTED`); Stage 21 `DONE` with its evidence block; `stage-21-regime-detection/verification.md` says **ALL CRITERIA VERIFIED**.
- Ran `uv run pytest -q` → first attempt failed: the fresh worktree had no `.venv` (`error: Failed to spawn: pytest`). Ran `uv sync --group dev` (pypi allowed), then `uv run pytest -q` → **3605 passed, 29 deselected in 353.73s, exit 0** (raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt); the nanobind leak note printed at interpreter exit is pre-existing and harmless). Gate passed.
- Copied `Planning/stages/_template/` (plan, worklog, verification, artifacts/) into the stage folder; wrote [plan.md](plan.md) (no review pause, owner's standing preference).

## 2026-11-07 — Real-lake timing probe (sizing Task 5)
- One fresh `DataView` on the real lake (2018-03-01): `universe` 65.6 s (Stage 13 quality report), `factor_panel` 140.9 s (503 names x 17 factors), features 1.5 s, 503 `earnings_reactions` reads 2.0 s. A view made with `advance` (2018-04-02, shared cache): `universe` 13.6 s, `factor_panel` 17.2 s. So the in-sample monthly run (~69 days) costs ~35-40 min in one process, via `advance`. First SPY bar in the lake: 2015-01-02 (momentum 12-1 needs 316 sessions: available from ~2016-04).

## 2026-11-07 — Task 0: config
- Test first: `tests/unit/quant/alpha/test_alpha_config.py` → collection error (ImportError: `AlphaConfig` absent): red.
- Added `AlphaComponentConfig`, `AlphaMultiFactorConfig`, `AlphaTSMOMConfig`, `AlphaPEADConfig`, `AlphaConfig`, `QuantConfig.alpha` (`src/atlas/core/config.py`) and `quant.alpha` in `config/fund.yaml` (every key commented). Pre-registered component weights: equal, 0.2 each.
- Ran `pytest tests/unit/quant/alpha tests/unit/core/test_config.py -q` → 299 passed. Lint fix: a `# fmt: skip` inside a dict literal is invalid (RUF028) → moved to the statement.

## 2026-11-07 — Task 1: protocol + Grinold calibration
- Tests written first (`test_alpha_base.py`, `test_alpha_calibration.py`); the red run was not separately recorded (the package did not exist, so the import fails).
- Built `src/atlas/quant/alpha/{__init__,base,calibration}.py`: `ALPHA_COLUMNS`, `AlphaModel` (runtime-checkable protocol with `name`, `horizon`, `predict`), `check_alpha_frame`, `alpha_frame`, `empty_alpha_frame`, `direction`; `horizon_scale`, `grinold_alpha` (`IC × σ_ann × z × √(P/h)`), `CalibrationFit` / `calibration_slope`, `ic_history`, `known_ic`, `trailing_ic`, `ic_weights`, `specific_vol`.
- Ran `pytest tests/unit/quant/alpha -q` → 33 passed. mypy: two fixes (`ic_weights` key type, `specific_vol` return type).

## 2026-11-07 — Task 2: multi-factor model
- Tests first (`test_alpha_multifactor.py`, worlds `alpha_world.py` + `conftest.py` reusing the Stage 16 factor world plus SPY bars) → collection error (`MultiFactorModel` absent): red.
- Built `multifactor.py`: `clean_component`, `ComponentPanel`, `CombineResult`, `member_sectors`, `component_frame`, `combine`, `MultiFactorModel` (+ `weights`). Style components mask the values Stage 16 filled from sector medians (`exposures.mask(filled)`), so a name with no fundamentals of a group has no value rather than a guessed 0.
- First run: 1 failure, `coverage == 1.0` was 0.9999999999999999 (float sum of 0.3+0.2+…) → coverage clipped to [0, 1] and the test compares with `allclose`. Re-run → 17 passed.
- Refactor: `predict(view)` delegates to the new public `predict_components(view, panel)` so the research run scores one panel per day once.

## 2026-11-07 — Task 3: TSMOM
- Tests first (`test_alpha_tsmom.py`: deterministic alternating up / down / mixed paths, a short-history ETF, the inverse-vol cap); built `tsmom.py` (`trend_blend`, `TSMOMModel`). Ran → 5 passed.

## 2026-11-07 — Task 4: PEAD
- Tests first (`test_alpha_pead.py`: reactions written to `earnings_reactions` of the tiny universe; decay at k = 0 / 15 / 30 / 45, unknown before the window closes, gone at k = 60, newer reaction replaces older, zero car); built `pead.py` (`pead_decay`, `pead_signal`, `PEADModel`). Ran → 7 passed.
- `ruff`, `ruff format`, `mypy src/atlas/quant/alpha` clean after small typing fixes (`np.sign` on a Series typed as ndarray → `Series.map/apply`; `float()` of a `.at` lookup → `to_dict`). `pytest tests/unit/quant/alpha -q` → 62 passed.

## 2026-11-07 — Task 5: research run (smoke, then full run)
- Wrote [artifacts/research_run.py](artifacts/research_run.py). Smoke run on the first 3 decision days (2016-01-04 .. 2016-03-01) of the real lake, read only → exit 0, end to end (signals, labels, tear sheet, summary). Findings checked before the full run (`look_signals.py` in scratch, not kept): on 2016-01-04 nothing is scored — the lake's first bar is 2015-01-02, so `momentum_12_1` (253 bars) and `idio_vol_252d` (σ, 253 bars) are missing for every name and a name without σ gets no prediction (by design: no guessed vol); from 2016-02-01 ~393 of 499 members are scored: ~106 members have no bars at all in the lake (no price features, no σ) and coverage < 0.6 drops them; 161 members have no sector (former members; neutralised against the whole cross-section, Stage 16 rule).
- Per-day cost with `advance`: 23–150 s (factor_panel recomputes fundamentals after new filings). Full run started in the background (69 decision days).
- Side note: a scratch script named `inspect.py` shadowed the stdlib `inspect` module (numpy import failed); renamed — not a code issue.

## 2026-11-07 — Task 6: self-audit (part 1)
- Contract gap: `check_alpha_frame` did not enforce the documented sorted index. Test first (`test_contract_rejects[bad8]`, reversed frame) → 1 failed (red); added the check → `pytest tests/unit/quant/alpha -q` 66 passed.
- Mutation check ([artifacts/mutate.py](artifacts/mutate.py)): 12 hand-written mutants of the invariants (PIT IC boundary, √(P/h), negative IC weights, coverage filter, sector neutrality, IC floor, Stage 16 fills masked, inverse-vol scaling, min_lookbacks, decay rate, age off-by-one, confidence bounds). First run: **11/12 killed; "Stage 16 fills kept" survived** (the factor world has no filled values, and the equality test used the same expression). Fix: the masking became a public pure function `style_scores(panel)` with a test on a hand-built `FactorPanel` with fills (`test_style_scores_ignore_stage16_fills`; red: ImportError, then green). Re-run → **12/12 killed**, files restored (raw: [artifacts/mutate.txt](artifacts/mutate.txt)). `pytest tests/unit/quant/alpha -q` → 67 passed; ruff / mypy clean.

## 2026-11-07 — Task 5: full research run → acceptance criterion not met
- Full run: 70 decision days 2016-01-04 .. 2021-10-01, signals in 4,555 s, labels' last session 2021-12-31 (asserted in the script; label view 2022-01-03 08:00), exit 0. No warning or error in the log. Outputs copied to [artifacts/research/](artifacts/research/) (the 5.6 MB `signals.pkl` cache stays in scratch).
- **Result: the pre-registered fixed-weight composite has a negative in-sample IC: Spearman −0.0062 (NW t −0.42) at 21 sessions**; negative at every horizon (h1 −0.004 … h63 −0.015); L/S quintile Sharpe −0.28. Components: momentum −0.023, value −0.016, quality +0.017, low-vol −0.006, reversal +0.010. The PIT IC-weighted variant is also negative (−0.024). PEAD IC −0.016 (t −1.86). TSMOM book Sharpe +0.67, hit rate 54.4%. Full numbers in [verification.md](verification.md).
- Bug checks before accepting the number: the component signs are economically plausible for 2016–2021 large caps (momentum negative, as Stage 17 measured on the raw feature: −0.040; value negative in the 2017–2020 value drawdown; reversal and quality positive); the composite IC is close to the weighted mean of the component ICs (−0.0036) given their correlations; the rolling IC is positive through 2017–2019 and turns sharply negative in Nov 2020 – Jan 2021 (−0.41 on 2020-11-02, the junk / vaccine rally), a known regime for quality / low-vol / momentum composites; by vol regime the IC is positive in LOW / NORMAL and negative in HIGH (t −2.3). Timing: reversal's positive IC (and no implausibly large IC anywhere) argues against a lag or look-ahead error. No defect found.
- Per plan interpretation 9 the weights are **not** tuned to pass; the stage is set `BLOCKED` and the owner is asked to decide (verification.md, PROGRESS).
- Brief's Verify `uv run pytest tests/unit/quant/alpha -q` → 67 passed (raw: [artifacts/verify-alpha-tests.txt](artifacts/verify-alpha-tests.txt)). `make check` on the worktree → 3672 passed, 29 deselected, lint / format / mypy clean (raw: [artifacts/make-check-worktree.txt](artifacts/make-check-worktree.txt); run after the last code change).
