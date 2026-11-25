"""Stage 29 follow-up 7: where the rebuilt factor exposures differ from the main lake's (read-only).
The full sync added 8 issuers' facts from July-October 2026 filings, so their exposures change at
the 2026-07 .. 2026-10 month-ends (rebuilt with --refresh); the cross-sectional standardisation
then moves the other names' values a little on those dates, and the risk model follows.

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_stores_compare_followup7.py
"""

import os
from pathlib import Path

import numpy as np
import pyarrow.dataset as ds

MAIN = Path.home() / "Projects/atlas-capital/data/curated"
COPY = Path(os.environ["TMPDIR"]) / "s29f7/main-copy/curated"
SYM = {"AAL", "ABT", "HUM", "JCI", "TYC", "UDR", "V", "VRSK"}  # issuers whose July-October filings the sync added
cols = ["date", "symbol", "factor", "raw", "value"]
o = ds.dataset(str(MAIN / "factor_exposures"), format="parquet").to_table(columns=cols).to_pandas()
n = ds.dataset(str(COPY / "factor_exposures"), format="parquet").to_table(columns=cols).to_pandas()
m = o.merge(n, on=["date", "symbol", "factor"], suffixes=("_old", "_new"), how="outer", indicator=True)
print("rows only in one table:", m["_merge"].value_counts().to_dict())
m = m[m["_merge"] == "both"]
raw_diff = ~np.isclose(m.raw_old, m.raw_new, equal_nan=True, rtol=0, atol=1e-12)
val_diff = ~np.isclose(m.value_old, m.value_new, equal_nan=True, rtol=0, atol=1e-12)
inside = m.symbol.isin(SYM)
print(f"raw exposure changed: {int(raw_diff.sum())} rows, of which in the 8 issuers "
      f"{int((raw_diff & inside).sum())}; outside them {int((raw_diff & ~inside).sum())}")
print("  raw changed outside, by factor:", m[raw_diff & ~inside].factor.value_counts().to_dict())
filled = m.raw_old.isna() & m.raw_new.notna()
print(f"raw NaN -> value: {int(filled.sum())} (the 8 issuers {int((filled & inside).sum())})")
print("  by symbol:", m[filled].groupby("symbol").size().sort_values(ascending=False).head(20).to_dict())
d = (m.value_new - m.value_old).abs()[val_diff & ~inside]
print(f"standardised value changed for other symbols: {int((val_diff & ~inside).sum())} rows, median "
      f"|change| {d.median():.4f}, p99 {d.quantile(.99):.4f}")
for t in ("factor_returns", "risk_model_snapshots"):
    a = ds.dataset(str(MAIN / t), format="parquet").count_rows()
    b = ds.dataset(str(COPY / t), format="parquet").count_rows()
    print(f"{t}: {a} -> {b} rows")
print("dates with a changed standardised value:", sorted({str(x) for x in m[val_diff].date}))
fr = {}
for t, keys in (("factor_returns", ["date", "factor"]), ("risk_model_snapshots", ["date", "component", "row", "column"])):
    a = ds.dataset(str(MAIN / t), format="parquet").to_table().to_pandas()
    b = ds.dataset(str(COPY / t), format="parquet").to_table().to_pandas()
    vcols = [c for c in a.columns if c not in keys and c not in ("available_at", "data_fingerprint", "config_hash") and a[c].dtype.kind == "f"]
    j = a.merge(b, on=keys, suffixes=("_o", "_n"))
    changed = np.zeros(len(j), dtype=bool)
    for c in vcols:
        changed |= ~np.isclose(j[c + "_o"], j[c + "_n"], equal_nan=True, rtol=0, atol=1e-12)
    print(f"{t}: values changed on dates {sorted({str(x) for x in j[changed].date})[:5]} ... ({j[changed].date.nunique()} dates, first {j[changed].date.min()})")
