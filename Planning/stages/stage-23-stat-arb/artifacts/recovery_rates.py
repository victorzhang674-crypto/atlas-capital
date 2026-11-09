"""Stage 23 evidence: recovery rates behind `test_planted_pairs_are_recovered` (same 40 seeds,
same world, default config).

    UV_NO_SYNC=1 uv run python Planning/stages/stage-23-stat-arb/artifacts/recovery_rates.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "unit" / "quant" / "statarb"))
from statarb_world import planted_universe  # noqa: E402

from atlas.core.config import StatArbConfig  # noqa: E402
from atlas.quant.statarb import select_pairs  # noqa: E402

cfg = StatArbConfig()
hits = {5.0: 0, 10.0: 0}
cross = extra = 0
for seed in range(40):
    u = planted_universe(seed)
    ids = {p.pair_id for p in select_pairs(u.log_prices.iloc[-cfg.formation :], u.sectors, config=cfg)}
    cross += u.cross in ids
    extra += len(ids - set(u.planted))
    for pid in u.planted:
        hits[5.0 if "Y0" in pid else 10.0] += pid in ids
for hl, n in hits.items():
    print(f"half-life {hl:>4}: recovered {n}/120 = {n / 120:.1%}")
print(f"cross-sector planted pair selected: {cross}/40")
print(f"other (non-planted) pairs selected: {extra} over 40 universes ({extra / 40:.2f} per universe)")
