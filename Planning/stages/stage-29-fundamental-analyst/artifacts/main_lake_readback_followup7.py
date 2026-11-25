"""Stage 29 follow-up 7: read the main lake back after the swap (read-only): every table's row
count against the scratch copy's, `_meta.json`, and `DataView` cases for the full sync.

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_readback_followup7.py
"""

import json
import os
from datetime import date
from pathlib import Path

import pyarrow.dataset as ds

import atlas.cli.app  # noqa: F401  (registers every table)
from atlas.agents.tools import fundamental_tools as ft
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView

ROOT = Path(__file__).resolve().parents[4]
MAIN = Path.home() / "Projects" / "atlas-capital" / "data"
COPY = Path(os.environ["TMPDIR"]) / "s29f7" / "main-copy"
for t in sorted(p.name for p in (COPY / "curated").iterdir() if p.is_dir()):
    a = ds.dataset(str(MAIN / "curated" / t), format="parquet").count_rows()
    b = ds.dataset(str(COPY / "curated" / t), format="parquet").count_rows()
    print(f"{t}: main {a} rows, scratch copy {b}: {'EQUAL' if a == b else 'DIFFERENT'}")
meta_main = json.loads((MAIN / "curated" / "_meta.json").read_text())["tables"]
meta_copy = json.loads((COPY / "curated" / "_meta.json").read_text())["tables"]
print("_meta.json tables equal to the scratch copy's:", meta_main == meta_copy)

cfg = load_config("dev", overrides={"data": {"lake_path": str(MAIN)}}, config_dir=ROOT / "config")
lake = Lake(MAIN)
view = DataView(lake, date(2026, 11, 25), cfg)
for symbol in ("ABT", "KVUE", "CRWD", "JPM", "C"):
    f = view.fundamentals(symbol)
    mda = view.mda(symbol)
    flag, _ = ft._going_concern(view, symbol, cfg.agents.fundamental.red_flags)
    print(f"{symbol}: fundamentals {int(f.notna().sum())} values, revenue_ttm {f.get('revenue_ttm')}; "
          f"MD&A {mda.form if mda else None} filed {mda.filed if mda else None} "
          f"({len(mda.text) if mda and mda.text else 0} chars) -> going concern {flag.status}")
abt = DataView(lake, date(2026, 8, 31), cfg).fundamentals("ABT")
print(f"ABT at 2026-08-31 (its 10-Q of 2026-07-28 added by this sync): revenue_ttm {abt.get('revenue_ttm')}")
news = view.news("AAPL", lookback=7)
print("AAPL news items in the 7 days to 2026-11-25:", len(news), "latest:", max((n.timestamp for n in news), default=None) if news and hasattr(news[0], "timestamp") else None)
facts = lake.read_table("fundamentals_facts", filters=[("symbol", "=", "JPM"), ("accn", "=", "0000019617-23-000524")], columns=["accepted_at", "available_at"])
print("JPM 0000019617-23-000524 accepted_at:", facts["accepted_at"].dropna().unique()[:1], "available_at:", facts["available_at"].unique()[:1])
