"""Stage 18 real-lake check (read only): covariance estimates for the S&P 500 members and vol
forecasts for a few names, from one point-in-time DataView on the main lake.

Decision day 2022-01-03 (08:00 New York): the view knows bars up to the close of 2021-12-31.
Every estimator gets the members' last 252 daily returns (`DataView.returns`); the vol
forecasts get 1,000 sessions. Prints the shape, the drops by reason, the repairs, the
shrinkage, the eigenvalue range and condition number, PSD/symmetry, and timings. An
independent cross-check rebuilds the sample variances and the realised vols with pandas.

Run from the repository root:
    MPLCONFIGDIR=$TMPDIR/mpl UV_CACHE_DIR=$TMPDIR/uv-cache-s18 UV_NO_SYNC=1 uv run python \
        Planning/stages/stage-18-covariance-vol/artifacts/real_lake_check.py
"""

import math
import time
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np

from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.risk_model import (
    METHODS,
    annualise_vol,
    blend_vol,
    covariance_estimator,
    ewma_vol,
    garch_vol,
    is_psd,
    realised_vol,
)

DAY = date(2022, 1, 3)
lake = Lake(Path.home() / "Projects/atlas-capital/data")
t0 = time.perf_counter()
view = DataView(lake, DAY)
members = view.universe("sp500")
rets = view.returns(members, 252)
print(f"# Stage 18 real-lake check · decision day {DAY} · lake {lake.root}")
print(f"view + returns: {time.perf_counter() - t0:.1f} s")
print(
    f"returns: {rets.shape[0]} sessions x {rets.shape[1]} names "
    f"({rets.index[0].date()} .. {rets.index[-1].date()}), members {len(members)}, "
    f"blocked {len(view.blocked)}, NaN share {rets.isna().to_numpy().mean():.4f}"
)
assert rets.index[-1].date() < DAY, "look-ahead: a return dated on or after the decision day"
print(f"look-ahead: last return {rets.index[-1].date()} < decision day {DAY}: ok")

print("\nmethod       missing    names dropped(by reason)          adjustments                 "
      "shrinkage  min_eig      max_eig      cond      psd  sym  med_ann_vol mean_corr  secs")
results = {}
for method, missing in [(m, "pairwise") for m in METHODS] + [("ledoit_wolf", "complete")]:
    t1 = time.perf_counter()
    try:
        res = covariance_estimator(method, missing=missing).estimate(rets)
    except DataError as exc:  # the fail-safe answer: no covariance, reported
        print(f"{method:<12} {missing:<10} DataError: {exc}")
        continue
    secs = time.perf_counter() - t1
    m = res.matrix
    eig = np.linalg.eigvalsh(m)
    corr = res.corr.to_numpy()
    n = len(m)
    mean_corr = (corr.sum() - n) / (n * (n - 1))
    reasons = dict(Counter(res.dropped.values()))
    shrink = "-" if res.shrinkage is None else f"{res.shrinkage:.4f}"
    print(
        f"{method:<12} {missing:<10} {n:>5} {reasons!s:<30} {','.join(res.adjustments) or '-':<27} "
        f"{shrink:>9}  {eig.min():.3e}  {eig.max():.3e}  {res.condition_number:.3e} "
        f"{is_psd(m)!s:<4} {np.array_equal(m, m.T)!s:<4} "
        f"{float(np.median(annualise_vol(res.vol.to_numpy()))):.4f}      {mean_corr:.4f}  {secs:.2f}"
    )
    results[(method, missing)] = res

# Independent cross-check: the sample variances against pandas (the condition floor may raise
# the smallest eigenvalues, so the diagonal can move by at most lambda_max / max_condition).
sample = results[("sample", "pairwise")]
pd_var = rets[list(sample.symbols)].var(ddof=1).to_numpy()
diff = np.abs(np.diag(sample.matrix) - pd_var)
bound = np.linalg.eigvalsh(sample.matrix).max() / 1e5
print(f"\ncross-check sample variances vs pandas: max |diff| {diff.max():.3e} "
      f"(bound lambda_max/max_condition {bound:.3e}): {'ok' if diff.max() <= bound else 'FAIL'}")

print("\nvol forecasts (1,000 sessions to 2021-12-31; annualised, sqrt(252))")
long = view.returns(["AAPL", "MSFT", "JPM", "XOM", "SPY"], 1000)
print("name   n_obs  ewma    realised  garch   blend   garch alpha  beta    persist  fallback")
for name in long.columns:
    r = long[name]
    e, rv, g, b = ewma_vol(r), realised_vol(r), garch_vol(r), blend_vol(r)
    by_hand = math.sqrt(float(np.nanmean(r.to_numpy()[-21:] ** 2)) * 252)
    assert abs(rv.annual_vol() - by_hand) < 1e-12, (name, rv.annual_vol(), by_hand)
    alpha = g.params.get("alpha", float("nan"))
    beta = g.params.get("beta", float("nan"))
    print(
        f"{name:<6} {e.n_obs:>5}  {e.annual_vol():.4f}  {rv.annual_vol():.4f}    "
        f"{g.annual_vol():.4f}  {b.annual_vol():.4f}  {alpha:.4f}       {beta:.4f}  "
        f"{alpha + beta:.4f}   {g.fallback or '-'}"
    )
print("realised vols cross-checked against numpy: ok")
print(f"\ntotal {time.perf_counter() - t0:.1f} s")
