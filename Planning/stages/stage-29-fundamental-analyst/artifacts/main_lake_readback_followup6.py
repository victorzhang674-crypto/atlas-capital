"""Stage 29 follow-up 6: read the main lake back after the price swap (read-only): row counts
against the scratch copy, `_meta.json`, and `DataView` prices as of 2026-11-25 (today; the last
published session is 2026-11-24) for a few names, including the three re-run (DHI, DHR, DIS).

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_readback_followup6.py
"""

import json
import os
from datetime import date
from pathlib import Path

import pyarrow.dataset as ds

import atlas.cli.app  # noqa: F401  (registers every table)
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView

ROOT = Path(__file__).resolve().parents[4]
MAIN = Path.home() / "Projects" / "atlas-capital" / "data"
COPY = Path(os.environ["TMPDIR"]) / "s29f6" / "main-copy"
for t in ("prices_daily", "corporate_actions", "instruments"):
    a = ds.dataset(str(MAIN / "curated" / t), format="parquet").count_rows()
    b = ds.dataset(str(COPY / "curated" / t), format="parquet").count_rows()
    print(f"{t}: main {a} rows, scratch copy {b}: {'EQUAL' if a == b else 'DIFFERENT'}")
meta_main = json.loads((MAIN / "curated" / "_meta.json").read_text())["tables"]
meta_copy = json.loads((COPY / "curated" / "_meta.json").read_text())["tables"]
print("_meta.json tables equal to the scratch copy's:", meta_main == meta_copy)
cfg = load_config("dev", overrides={"data": {"lake_path": str(MAIN)}}, config_dir=ROOT / "config")
view = DataView(Lake(MAIN), date(2026, 11, 25), cfg)
names = ["AAPL", "SPY", "OKTA", "VYLR", "DHI", "DHR", "DIS"]
bars = view.prices(names, 25)
for s in names:
    b = bars[bars["symbol"] == s] if "symbol" in bars.columns else bars.xs(s, level="symbol")
    print(f"{s}: {len(b)} bars, last {b['date'].max() if 'date' in b.columns else b.index.max()}")
