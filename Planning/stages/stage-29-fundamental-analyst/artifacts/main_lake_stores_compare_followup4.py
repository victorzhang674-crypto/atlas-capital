"""Stage 29 follow-up 4: where the rebuilt factor exposures differ from the main lake's (read-only).
The former-CIK symbols gain fundamentals, so their exposures fill in; the cross-sectional
standardisation then moves every name's values a little on the dates they enter.

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_stores_compare_followup4.py
"""

import os
from pathlib import Path

import numpy as np
import pyarrow.dataset as ds

MAIN = Path.home() / "Projects/atlas-capital/data/curated"
COPY = Path(os.environ["TMPDIR"]) / "s29f4/main-copy/curated"
SYM = {"APA", "AVGO", "BG", "BLK", "CI", "DD", "DIS", "FTI", "GOOG", "GOOGL", "LIN", "LLL",
       "SKYD", "SNDK", "TFCF", "TFCFA", "VTRS", "XOM"}
cols = ["date", "symbol", "factor", "raw", "value"]
o = ds.dataset(str(MAIN / "factor_exposures"), format="parquet").to_table(columns=cols).to_pandas()
n = ds.dataset(str(COPY / "factor_exposures"), format="parquet").to_table(columns=cols).to_pandas()
m = o.merge(n, on=["date", "symbol", "factor"], suffixes=("_old", "_new"), how="outer", indicator=True)
print("rows only in one table:", m["_merge"].value_counts().to_dict())
m = m[m["_merge"] == "both"]
raw_diff = ~np.isclose(m.raw_old, m.raw_new, equal_nan=True, rtol=0, atol=1e-12)
val_diff = ~np.isclose(m.value_old, m.value_new, equal_nan=True, rtol=0, atol=1e-12)
inside = m.symbol.isin(SYM)
print(f"raw exposure changed: {int(raw_diff.sum())} rows, of which in former-CIK symbols "
      f"{int((raw_diff & inside).sum())}; outside them {int((raw_diff & ~inside).sum())}")
print("  raw changed outside, by factor:", m[raw_diff & ~inside].factor.value_counts().to_dict())
filled = m.raw_old.isna() & m.raw_new.notna()
print(f"raw NaN -> value: {int(filled.sum())} (former-CIK symbols {int((filled & inside).sum())})")
print("  by symbol:", m[filled].groupby("symbol").size().sort_values(ascending=False).head(20).to_dict())
d = (m.value_new - m.value_old).abs()[val_diff & ~inside]
print(f"standardised value changed for other symbols: {int((val_diff & ~inside).sum())} rows, median "
      f"|change| {d.median():.4f}, p99 {d.quantile(.99):.4f}")
for t in ("factor_returns", "risk_model_snapshots"):
    a = ds.dataset(str(MAIN / t), format="parquet").count_rows()
    b = ds.dataset(str(COPY / t), format="parquet").count_rows()
    print(f"{t}: {a} -> {b} rows")
