"""Phase 2 checkpoint, Tier 1 item 1: on the real lake, the blocking-only quality report gives
the full report's block list, and how long each takes.

Read only. Run from the repo root with `ATLAS__DATA__LAKE_PATH` pointing at the main lake.
"""

import time
from datetime import date

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.quality import blocked_symbols, quality_report
from atlas.data.view import DataView

DAYS = (
    date(2016, 6, 1),
    date(2018, 3, 1),
    date(2020, 3, 16),
    date(2022, 6, 1),
    date(2024, 6, 3),
    date(2026, 10, 1),
)

cfg = load_config()
lake = Lake(cfg.data.lake_path)
ok = True
for day in DAYS:
    t0 = time.perf_counter()
    only = quality_report(lake, day, config=cfg, blocking_only=True)
    t1 = time.perf_counter()
    full = quality_report(lake, day, config=cfg)
    t2 = time.perf_counter()
    critical = full[full["severity"] == "critical"].reset_index(drop=True)
    same_rows = only.equals(critical)
    same_block = blocked_symbols(only) == blocked_symbols(full)
    t3 = time.perf_counter()
    view_blocked = DataView(lake, day, cfg).blocked
    t4 = time.perf_counter()
    same_view = view_blocked == blocked_symbols(full)
    ok = ok and same_rows and same_block and same_view
    print(
        f"{day} blocking_only {t1 - t0:6.1f} s | full {t2 - t1:6.1f} s | DataView.blocked "
        f"{t4 - t3:6.1f} s | full issues {len(full):4d} critical {len(critical):3d} "
        f"blocked {len(view_blocked):3d} | rows equal {same_rows} block list equal "
        f"{same_block} view equal {same_view}",
        flush=True,
    )
print("ALL EQUAL" if ok else "MISMATCH")
raise SystemExit(0 if ok else 1)
