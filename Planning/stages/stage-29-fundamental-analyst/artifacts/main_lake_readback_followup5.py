"""Stage 29 follow-up 5: read the main lake back after the swap (read-only): the swapped tables'
row counts against the scratch copy's, `_meta.json`, and `DataView` cases for OKTA and VYLR and
the MD&A fixes.

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_readback_followup5.py
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
MAIN = Path(os.environ.get("READBACK_LAKE", Path.home() / "Projects" / "atlas-capital" / "data"))
COPY = Path(os.environ["TMPDIR"]) / "s29f5" / "main-copy"
TABLES = ["prices_daily", "corporate_actions", "instruments", "fundamentals_facts", "events",
          "earnings_reactions", "filing_text", "factor_exposures", "factor_returns",
          "risk_model_snapshots"]  # fmt: skip

for t in TABLES:
    a = ds.dataset(str(MAIN / "curated" / t), format="parquet").count_rows()
    b = ds.dataset(str(COPY / "curated" / t), format="parquet").count_rows()
    print(f"{t}: main {a} rows, scratch copy {b}: {'EQUAL' if a == b else 'DIFFERENT'}")
meta_main = json.loads((MAIN / "curated" / "_meta.json").read_text())["tables"]
meta_copy = json.loads((COPY / "curated" / "_meta.json").read_text())["tables"]
print("_meta.json tables equal to the scratch copy's:", meta_main == meta_copy)

cfg = load_config("dev", overrides={"data": {"lake_path": str(MAIN)}}, config_dir=ROOT / "config")
lake = Lake(MAIN)
CASES = [  # symbol, as-of day, why
    ("OKTA", date(2026, 10, 30), "new member: fundamentals from CIK 1660134"),
    ("VYLR", date(2026, 11, 20), "new member: its first 10-Q (2026-11-12)"),
    ("EMR", date(2016, 1, 4), "FY2015 MD&A from the sections Item 7 names (EX-13)"),
    ("CMA", date(2015, 3, 2), "MD&A from the sections Item 7 names (Financial Section)"),
    ("SEDG", date(2024, 3, 1), "ITEM / 7A. split heading ends the MD&A"),
    ("FCX", date(2015, 3, 2), "Items 7. and 7A. heading"),
    ("AIG", date(2017, 5, 10), "ITEM 2 | heading, ends before Part II"),
    ("WIN", date(2019, 3, 18), "going-concern FLAG unchanged"),
    ("ENPH", date(2017, 5, 12), "going-concern FLAG unchanged"),
]
for symbol, day, why in CASES:
    view = DataView(lake, day, cfg)
    f = view.fundamentals(symbol)
    revenue = f.get("revenue_ttm") if hasattr(f, "get") else None
    mda = view.mda(symbol)
    flag, _ = ft._going_concern(view, symbol, cfg.agents.fundamental.red_flags)
    print(f"{symbol} at {day} ({why}): fundamentals {int(f.notna().sum())} values, revenue_ttm "
          f"{revenue}; MD&A {mda.form if mda else None} filed {mda.filed if mda else None} "
          f"({mda.source if mda else None}, {len(mda.text) if mda and mda.text else 0} chars) -> "
          f"{flag.status}: {flag.detail[:110]}")
whole = lake.read_table("filing_text", columns=["symbol", "status"])
print(f"whole filing_text read: {len(whole)} rows, statuses {whole['status'].value_counts().to_dict()}")
