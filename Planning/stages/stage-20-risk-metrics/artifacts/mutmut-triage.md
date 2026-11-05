# Stage 20 — generated mutation run (mutmut 3.8.1) · triage

Tool: mutmut 3.8.1, run as a throwaway overlay (`uv run --project <worktree> --with mutmut`, not a project dependency) on a scratch copy of the working tree, `PYTHONPATH=<copy>/mutants/src` so the tests import the mutated package. Config: [mutmut-setup.cfg](mutmut-setup.cfg) — mutates `src/atlas/quant/metrics/`, runs `tests/unit/quant/metrics/`. Raw: [mutmut-results.txt](mutmut-results.txt) (survivors and timeouts), [mutmut-survivors.txt](mutmut-survivors.txt) (diff of every survivor), [mutmut-runs.txt](mutmut-runs.txt); scripts [mutmut_dump_survivors.py](mutmut_dump_survivors.py), [mutmut_classify.py](mutmut_classify.py).

## Runs

| Run | Code / tests | Mutants | Killed | Timeout | Survived |
|---|---|---|---|---|---|
| 0 | first attempt: tests imported the installed package, not the mutants | — | — | — | "no test covers any mutant" (setup error, fixed with `also_copy` + `PYTHONPATH`) |
| 1 | stage code at `082eae5` | 2,682 | 2,201 | 5 | 476 |
| 2 | + first batch of gap tests, dead drawdown branch removed | 2,663 | 2,390 | 5 | 268 |
| 3 | + second batch, **Monte Carlo mean bug fixed** | 2,678 | 2,465 | 5 | 208 |
| 4 | + Omega / CVaR-boundary / flat-window tests | 2,678 | 2,466 | 5 | 207 |
| final | run 4 + the corrected Omega test (its 2 mutants re-run) | 2,678 | **2,468** | 5 | **205** |

Timeouts count as caught (the mutant made the suite hang). Kill rate 82% → 92%.

## What the survivors of run 1 found

- **Real bug — `portfolio_var(method="monte_carlo", mean=<Series>)` ignored the mean.** `portfolio_var` handed positional arrays to the simulator, so a by-symbol mean aligned to nothing and became zeros. Fixed (mean aligned to the covariance's names first); `test_portfolio_var_passes_every_argument` fails on the old code, passes now.
- **Dead code** — the `peak > 0` fallback in the drawdown helper could never run (the running peak starts at 1); removed.
- **Test gaps (no code change)** — arguments accepted but never shown to be used (`periods`, `config`, `risk_free`, `method`, `horizon`, `pnl`, `mean`, confidences, default windows from config); boundaries (participation = 1, ADV < 1 currency unit, positions < 1 unit, `periods = 1`, risk-free −0.99 / −1, horizon 2, `var_min_obs` exactly met, tail of a single observation, quantile equal to an observation, symmetry / PSD tolerances relative to scale, zero covariance, tiny negative eigenvalue); type checks with `True` / strings; result metadata (`method`, `n_obs`, confidences, `horizon`, `adjustments`) on every path; Series names and indexes; no `-0.0` in reported numbers. All in `tests/unit/quant/metrics/test_metrics_mutation_gaps.py`.
- **Checked, not a bug** — pandas' incremental rolling variance on a flat window after volatile data returns exactly 0 (rolling beta / correlation / Sharpe NaN, vol 0); pinned by `test_rolling_metrics_flat_after_volatile_windows`.

## The 205 final survivors — all classified, none a behaviour change on valid input

**129 cosmetic** (no number changes): 79 change only the text of a string literal (error messages, labels, log fields: `"XX…XX"`, case changes); 33 drop or `None` a string argument that only names a series in error messages; 17 change an error message, a log call or a NumPy `errstate` (warnings only).

**76 equivalent** (the change cannot alter any result on valid input). Counts are from tagging each survivor by rule and reviewing every class's members by hand (`mutmut_classify.py` output reviewed, 4 re-assigned):

| Class | Count | Why equivalent |
|---|---|---|
| Name-only / non-strict arguments | 24 | the name given to `as_series` / `as_weights` / `weights_on` only appears in error messages; `strict=None` is falsy like `False`; `name=` of a mean only appears in an error that `strict=False` never raises; `adjustments=()` is the dataclass default |
| Zero-width boundaries | 11 | `>= 0` vs `> 0` (or `<=` vs `<`, `&` vs `\|` on finite capacities) where the extra element contributes exactly 0: summing zero weights / returns, zero ADV giving `inf` days either way, zero capacity, `0/0 = NaN` either way (zero-vol percent contributions, rolling variance and covariance both exactly 0) |
| Constant risk-free rate cancels | 10 | `beta`, `rolling_beta`, `relative_summary["beta"]`: dropping `periods` / `config` only changes the default *constant* risk-free rate, which cancels in cov / var (a time-varying rate is passed explicitly and is tested) |
| Spelled-out defaults | 7 | `ddof=1` is pandas' rolling `cov` default; `nan=0.0` is `nan_to_num`'s default; `raw=True/False` for `np.prod` and the drawdown kernel gives the same numbers |
| Unreachable branches | 6 | the `±1` clip of a correlation that is NaN only for constant input (caught first by `is_constant`); constant-series moments multiplied by a vol of exactly 0; NaN in `up_down_capture` propagates whether `or` or `and`; `+ 0.0` / `- 0.0` on a sum that cannot be `-0.0`; the placeholder denominator masked out by the `inf` branch |
| dtype defaults | 4 | `np.asarray(…)` / `pd.Series(dict)` without an explicit dtype on numeric / string input gives the same values after the later `float64` / `str` conversions |
| Sorting undone later | 3 | `align`'s `intersection(sort=…)` is followed by `sort_values()` |
| Algebraic identity | 2 | `Σ(x ± x̄)(y − ȳ) = Σ(x − x̄)(y − ȳ)` because `Σ(y − ȳ) = 0` (and symmetrically) |
| Square-matrix shape | 2 | `shape[1]` vs `shape[0]` after the square check |
| Config already carries the value | 2 | `liquidity_profile` passes the participation explicitly, so the config handed to `days_to_liquidate` is never read |
| Log-only | 2 | the dropped-session count in `portfolio_returns` only feeds a log line |
| Distributional symmetry | 1 | Monte Carlo `mu − sqrt(h)·z` vs `+`: `z` and `−z` have the same normal distribution (the VaR/CVaR estimates are statistically identical; only the individual draws differ) |
| Equivalent minimum-data guard | 1 | `performance_summary`'s `min_obs=2` vs 1: `annualised_vol` raises the same `DataError` for one return |
| Message-only | 1 | how many missing dates an error message lists (`[:5]` vs `[:6]`) |

The per-mutant list with its diff is in [mutmut-survivors.txt](mutmut-survivors.txt).

## After the independent review (2026-11-05)

The review fixes added code (`cornish_fisher_monotone`, the ADV coverage check, the Monte Carlo minimum, argument checks), so mutmut was run again on the final code: **2,781 mutants, 2,552 killed, 5 timeouts, 224 survived** ([mutmut-results-review.txt](mutmut-results-review.txt), diffs in [mutmut-survivors-review.txt](mutmut-survivors-review.txt)). The first run after the fixes had 247 survivors; the new code's gaps were closed with tests in `test_metrics_review.py`:
- `cornish_fisher_monotone` survived coefficient changes (8→9, 6→7, 5→6, …): its first tests sat far from the boundary → a 400-example property test against a dense numerical minimum, designed shallow dips (minimum slope ±0.001, the minimum at several z), a dip just outside `|z| ≤ 4` (S −1.18, K 2.19: minimum at z 4.72), and a case separating `z_max` 4 from 5 through config.
- My CVaR quantile-boundary test was not exact (`5/1000 − 0.05` is not representable, so the quantile was not exactly an observation) → values in multiples of 1/64.
- Overlapping horizon 2 `n_obs`; a Monte Carlo mean with symbols outside the covariance (mutant `strict=True`).

The 224 survivors: **134 cosmetic** (89 string literals, 33 name-only string arguments, 12 messages / logs / errstate) and **90 equivalent**, counted by rule tagging and reviewed per class:

| Class | Count | Why equivalent |
|---|---|---|
| Name-only / non-strict arguments | 24 | as in the table above (series names in error messages, `strict=None` falsy like `False`, default `adjustments=()`) |
| Zero-width boundaries | 11 | as above |
| Log-only | 11 | the fields of the `liquidity_partial_adv` warning (9) and the dropped-session count (2) only feed log lines |
| Constant risk-free rate cancels | 10 | as above |
| Spelled-out defaults | 7 | as above |
| Unreachable branches | 5 | as above |
| Exact-tie boundaries | 5 | `<` vs `<=` at a point hit with probability zero: the slope minimum exactly at ±z_max, a slope of exactly 0, a CVaR exactly equal to the VaR, a drawdown of exactly −1e-12 |
| dtype defaults | 4 | as above |
| Sorting undone later | 3 | as above |
| Config already carries the value | 2 | as above |
| Square-matrix shape | 2 | as above |
| Algebraic identity | 2 | as above |
| Symmetric interval | 1 | the "minimum inside [−z_max, z_max]" test with the sign of the vertex flipped: the interval is symmetric |
| Distributional symmetry | 1 | as above |
| Equivalent minimum-data guard | 1 | as above |
| Message-only | 1 | as above |
