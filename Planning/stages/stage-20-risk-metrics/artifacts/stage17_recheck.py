"""Stage 20 post-DONE audit: re-check the Stage 17 real-lake tear sheet (momentum_12_1) after its
backtest summary moved onto `atlas.quant.metrics`. Reads the stored net returns
(`stage-17-signal-research/artifacts/tearsheet-momentum_12_1/backtest_returns.csv`, 1,511
sessions from the real lake) and compares the summary recorded by Stage 17 with the numbers the
current `_summary` gives, and with the old Stage 17 formulas re-implemented here.

Run from the repository root:
    UV_CACHE_DIR=$TMPDIR/uv-cache-s20 UV_NO_SYNC=1 uv run python \
        Planning/stages/stage-20-risk-metrics/artifacts/stage17_recheck.py
"""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from atlas.quant.research.vector_backtest import _summary

DIR = Path("Planning/stages/stage-17-signal-research/artifacts/tearsheet-momentum_12_1")
frame = pd.read_csv(DIR / "backtest_returns.csv")
recorded = json.loads((DIR / "tearsheet.json").read_text())["backtest"]
net = frame["net_return"].to_numpy()
costs = frame["cost"].to_numpy()
turnover = np.full(recorded["n_rebalances"], recorded["mean_turnover"])  # only counts used here
new = _summary(net, turnover, costs, 252)

# The Stage 17 formulas as they were before Stage 20 (drawdown from the first session's wealth).
wealth = np.cumprod(1.0 + net)
old = {
    "ann_return": float(net.mean() * 252),
    "ann_vol": float(net.std(ddof=1) * math.sqrt(252)),
    "sharpe": float(net.mean() / net.std(ddof=1) * math.sqrt(252)),
    "total_return": float(wealth[-1] - 1.0),
    "max_drawdown": float(-(wealth / np.maximum.accumulate(wealth) - 1.0).min()),
}
print(f"sessions {len(net)} ({frame['date'].iloc[0]} .. {frame['date'].iloc[-1]}); "
      f"first net return {net[0]:.6f}")
print(f"{'metric':<14} {'recorded (S17)':>16} {'old formula':>16} {'library (S20)':>16} {'diff':>10}")
worst = 0.0
for key in ("ann_return", "ann_vol", "sharpe", "total_return", "max_drawdown"):
    diff = new[key] - recorded[key]
    worst = max(worst, abs(diff))
    print(f"{key:<14} {recorded[key]:>16.10f} {old[key]:>16.10f} {new[key]:>16.10f} {diff:>10.2e}")
    assert math.isclose(old[key], recorded[key], rel_tol=1e-9), key  # the CSV reproduces S17
    assert math.isclose(new[key], recorded[key], rel_tol=1e-9, abs_tol=1e-12), key
print(f"largest |library - recorded| = {worst:.2e}: the Stage 17 real-lake tear sheet is unchanged")
