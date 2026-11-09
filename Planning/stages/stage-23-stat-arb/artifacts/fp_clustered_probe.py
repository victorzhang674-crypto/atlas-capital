"""Stage 23 evidence: false positives of the selection on *clustered* random-walk universes
(the first test design, at scale): 200 six-name universes (seeds 200000..200199, fixed before
the run) = 3000 same-sector pairs. Per pair: passes every filter; per universe: selected after
the caps. Cluster-robust s.e. from the per-universe counts.

    UV_NO_SYNC=1 uv run python Planning/stages/stage-23-stat-arb/artifacts/fp_clustered_probe.py
"""

import sys
from itertools import combinations
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "unit" / "quant" / "statarb"))
from statarb_world import random_walk_universe  # noqa: E402

from atlas.core.config import StatArbConfig  # noqa: E402
from atlas.quant.statarb import evaluate_pair, select_pairs  # noqa: E402

cfg = StatArbConfig()
passed, selected = [], []
for seed in range(200_000, 200_200):
    lp = random_walk_universe(seed, n_names=6).log_prices.iloc[-cfg.formation :]
    u = random_walk_universe(seed, n_names=6)
    p = sum(
        evaluate_pair(lp[a].to_numpy(), lp[b].to_numpy(), cfg).passed
        for a, b in combinations(lp.columns, 2)
    )
    passed.append(p)
    selected.append(len(select_pairs(lp, u.sectors, config=cfg)))
n = 15 * len(passed)
for name, counts in (("pass every filter", passed), ("selected after caps", selected)):
    c = np.array(counts, dtype=float)
    rate = c.sum() / n
    se = c.std(ddof=1) / 15 / np.sqrt(len(c))  # cluster-robust: universes are independent
    print(f"{name}: {int(c.sum())}/{n} = {rate:.4f} (cluster s.e. {se:.4f})")
