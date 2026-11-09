"""Stage 23 Task 1 diagnosis: false-positive rate of the pair filters on correlated random walks.

Independent 2-name universes (market factor + own walk, return corr ~0.86), 504-session window.
Prints the share failing at each filter and the share passing all filters, with a binomial s.e.

    UV_NO_SYNC=1 uv run python Planning/stages/stage-23-stat-arb/artifacts/fp_rate_probe.py [N]
"""

import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "unit" / "quant" / "statarb"))
from statarb_world import random_walk_universe  # noqa: E402

from atlas.core.config import StatArbConfig  # noqa: E402
from atlas.quant.statarb.selection import evaluate_pair  # noqa: E402

n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
cfg = StatArbConfig()
fails: Counter[str] = Counter()
eg_only = 0
for seed in range(n):
    u = random_walk_universe(100_000 + seed, n_names=2)
    lp = u.log_prices.iloc[-cfg.formation :]
    res = evaluate_pair(lp["RW00"].to_numpy(), lp["RW01"].to_numpy(), cfg)
    fails[res.failed or "PASSED"] += 1
    eg_only += res.pvalue < cfg.max_pvalue if res.failed != "corr" else 0
rate = fails["PASSED"] / n
print(f"pairs {n}; outcome counts {dict(fails)}")
print(f"EG p < {cfg.max_pvalue}: {eg_only / n:.4f}")
print(f"pass all filters: {rate:.4f} (s.e. {np.sqrt(rate * (1 - rate) / n):.4f})")
