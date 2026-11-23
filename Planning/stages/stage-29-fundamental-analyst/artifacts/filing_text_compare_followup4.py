"""Stage 29 follow-up 4: the scratch copy's `filing_text` against the main lake's, row by row
(SHA-256 of the text): changed rows outside the former-CIK symbols must all be rows the recheck
found changed; removed and added rows are listed.

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/filing_text_compare_followup4.py
"""

import hashlib
import os
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

MAIN = Path.home() / "Projects/atlas-capital/data/curated/filing_text"
WORK = Path(os.environ["TMPDIR"]) / "s29f4"
SYM = {"APA", "AVGO", "BG", "BLK", "CI", "DD", "DIS", "FTI", "GOOG", "GOOGL", "LIN", "LLL",
       "SKYD", "SNDK", "TFCF", "TFCFA", "VTRS", "XOM"}


def load(p: Path) -> dict:
    out = {}
    for b in ds.dataset(str(p), format="parquet").to_batches(
            columns=["symbol", "accn", "form", "filed", "status", "chars", "text"], batch_size=500):
        for r in b.to_pylist():
            out[(r["symbol"], r["accn"])] = (hashlib.sha256((r["text"] or "").encode()).hexdigest(),
                                             r["form"], str(r["filed"]), r["status"], r["chars"])
    return out


o, n = load(MAIN), load(WORK / "main-copy/curated/filing_text")
rc = pd.read_parquet(WORK / "recheck.parquet")
expected = {(s, a) for s, a, same in zip(rc.symbol, rc.accn, rc.same) if not same}
changed = {k for k in o.keys() & n.keys() if o[k][0] != n[k][0]}
outside = {k for k in changed if k[0] not in SYM}
print(f"main {len(o)} rows, copy {len(n)} rows; changed {len(changed)} ({len(changed - outside)} of "
      f"the former-CIK symbols, {len(outside)} others)")
print("every other changed row is one the recheck found changed:", outside <= expected)
print("recheck-changed rows of other symbols left unchanged:",
      sorted(k for k in expected if k[0] not in SYM and k not in outside))
for k in sorted(o.keys() - n.keys()):
    print("REMOVED", k[0], o[k][1], o[k][2], o[k][3], o[k][4])
add = sorted(n.keys() - o.keys())
print(f"added {len(add)}, all of former-CIK symbols: {all(k[0] in SYM for k in add)}")
print("statuses now:", pd.Series([v[3] for v in n.values()]).value_counts().to_dict())
