"""Stage 29 follow-up 4: read the main lake back after the swap (read-only): the swapped tables'
row counts against the scratch copy's, the stores' digests, and `DataView` cases for the former
CIKs and the MD&A fixes.

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_readback_followup4.py
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
COPY = Path(os.environ["TMPDIR"]) / "s29f4" / "main-copy"
TABLES = ["fundamentals_facts", "events", "earnings_reactions", "filing_text", "factor_exposures",
          "factor_returns", "risk_model_snapshots"]

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
    ("XOM", date(2020, 6, 1), "former CIK 34088: fundamentals before the 2026 holding company"),
    ("DIS", date(2017, 6, 1), "former CIK 1001039"),
    ("GOOGL", date(2015, 6, 1), "Google Inc. before Alphabet"),
    ("VTRS", date(2020, 9, 1), "Mylan N.V., not Upjohn (since 2020-11-16)"),
    ("TFCF", date(2018, 6, 1), "Twenty-First Century Fox, newly mapped"),
    ("BNY", date(2024, 9, 1), "10-Q 'Items 2. and 3.'"),
    ("NTRS", date(2024, 9, 1), "10-Q 'Items 2 and 3:'"),
    ("GE", date(2020, 11, 2), "10-Q run-in heading (was a contents entry)"),
    ("USB", date(2020, 3, 1), "10-K reads its EX-13"),
    ("WIN", date(2019, 3, 18), "going-concern quote read whole"),
    ("ENPH", date(2017, 5, 12), "new FLAG: 10-Q repeating the 10-K's doubt"),
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
