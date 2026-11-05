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
