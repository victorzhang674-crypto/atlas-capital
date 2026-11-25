"""Stage 30 Task 10: the technical analyst's prefetch on the main lake (read-only) for cases
the test world only imitates. Run from the worktree root:

    uv run python Planning/stages/stage-30-technical-analyst/artifacts/main_lake_check.py

Each case prints the evidence (regime computed with `regime_at`, the default path) and the
time it took; a `DataError` is printed, not hidden.
"""

import time
from datetime import date
from pathlib import Path

from atlas.agents.tools.technical_tools import prefetch
from atlas.core.config import load_config
from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.view import DataView

ROOT = Path(__file__).resolve().parents[4]
MAIN_LAKE = Path.home() / "Projects" / "atlas-capital" / "data"
CASES = [
    ("NVDA", date(2024, 6, 12), "10:1 split on 2024-06-10: levels must be split-adjusted"),
    ("GEV", date(2024, 6, 3), "spun off 2024-04-02: about 40 sessions of history"),
    ("KVUE", date(2023, 8, 1), "IPO 2023-05-04: under 63 sessions"),
    ("JPM", date(2020, 3, 17), "the March 2020 crash"),
    ("ATVI", date(2023, 10, 20), "delisted 2023-10-13: a stale last bar"),
    ("SNDK", date(2026, 11, 25), "bars stop 2026-11-19 after its 8:1 split (Backlog 07)"),
    ("AAPL", date(2026, 11, 25), "today"),
]

config = load_config(
    "dev", overrides={"data": {"lake_path": str(MAIN_LAKE)}}, config_dir=ROOT / "config"
)
lake = Lake(MAIN_LAKE)
for symbol, day, why in CASES:
    print(f"\n===== {symbol} {day}: {why}")
    started = time.monotonic()
    try:
        items = prefetch(DataView(lake, day, config), symbol)
    except DataError as exc:
        print(f"DataError: {exc} ({time.monotonic() - started:.1f} s)")
        continue
    print(f"{len(items)} items in {time.monotonic() - started:.1f} s")
    for item in items:
        print(f"[{item.id}] ({item.as_of.isoformat()}) {item.text}")
