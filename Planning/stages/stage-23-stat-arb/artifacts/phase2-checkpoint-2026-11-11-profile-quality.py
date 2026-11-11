"""Phase 2 checkpoint: where a fresh `DataView`'s first call spends its time on the real lake.

Read only. Run from the repo root with `ATLAS__DATA__LAKE_PATH` pointing at the main lake:
    uv run python Planning/stages/stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-profile-quality.py
"""

import cProfile
import pstats
import sys
import time
from datetime import date

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.quality import quality_report

cfg = load_config()
lake = Lake(cfg.data.lake_path)
for day in (date(2020, 3, 16), date(2024, 6, 3)):
    started = time.perf_counter()
    profile = cProfile.Profile()
    profile.enable()
    report = quality_report(lake, day, config=cfg)
    profile.disable()
    print(day, "seconds", round(time.perf_counter() - started, 1), "issues", len(report), flush=True)
    pstats.Stats(profile, stream=sys.stdout).sort_stats("cumulative").print_stats(30)
