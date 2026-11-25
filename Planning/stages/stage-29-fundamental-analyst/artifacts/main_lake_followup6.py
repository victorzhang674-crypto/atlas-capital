"""Stage 29 follow-up 6: bring the main lake's prices up to today on a scratch copy, prove only
new bars (and the trade dates and corporate actions they imply) changed, and leave a copy ready
to swap in (`main_lake_followup5_swap.sh` with this follow-up's backup name, run from the
owner's Terminal panel: the sandbox cannot write the main checkout). Run from the worktree root
with its venv, one step at a time:

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_followup6.py STEP

- `copy`    scratch copy in `$TMPDIR/s29f6/main-copy` (as follow-up 5's `copy`).
- (between `copy` and `dates`: `atlas data sync --datasets prices` on the copy, a CLI command,
  see the worklog.)
- `dates`   first and last trade dates of every instrument from `prices_daily`
            (`trade_date_ranges`, the rule the universe step applies; the universe step itself
            is not run: it would also apply today's index changes).
- `check`   prices, corporate actions and instruments against the main lake, row by row: no
            stored bar changes or disappears; new bars only after the main lake's last day.
"""

import os
import sys
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import main_lake_followup5 as f5  # noqa: E402  (copy, _lake, _table, _row_digests)

f5.WORK = Path(os.environ["TMPDIR"]) / "s29f6"
f5.COPY = f5.WORK / "main-copy"
MAIN, COPY = f5.MAIN, f5.COPY


def copy() -> None:
    f5.WORK.mkdir(parents=True, exist_ok=True)
    f5.copy()


def dates() -> None:
    from atlas.data.reference import INSTRUMENTS, trade_date_ranges

    lake = f5._lake(COPY)
    ranges = trade_date_ranges(lake).set_index("symbol")
    inst = lake.read_table(INSTRUMENTS.name)
    before = inst[["first_trade_date", "last_trade_date"]].copy()
    for col in ("first_trade_date", "last_trade_date"):
        inst[col] = [
            ranges.loc[s, col] if s in ranges.index else v for s, v in zip(inst["symbol"], inst[col])
        ]
        inst[col] = inst[col].astype(object).where(inst[col].notna(), None)
    lake.write_table(INSTRUMENTS.name, inst, mode="overwrite")
    changed = (before.astype(str) != inst[["first_trade_date", "last_trade_date"]].astype(str)).any(axis=1)
    print(f"instruments: {int(changed.sum())} of {len(inst)} rows got new trade dates; "
          f"last trade dates now {inst['last_trade_date'].dropna().max()}")


def check() -> None:
    lake = f5._lake(COPY)
    main_last = ds.dataset(str(MAIN / "curated" / "prices_daily"), format="parquet").to_table(
        columns=["date"]).to_pandas()["date"].max()
    print("main lake's last bar:", main_last)
    for name in ("prices_daily", "corporate_actions", "instruments"):
        schema = lake.table_schema(name)
        keys = list(schema.primary_key)
        cols = list(schema.column_names)
        old = f5._table(MAIN, name, cols)
        new = f5._table(COPY, name, cols)
        old["_d"], new["_d"] = f5._row_digests(old[cols]).values, f5._row_digests(new[cols]).values
        o = old.set_index(keys)["_d"]
        n = new.set_index(keys)["_d"]
        gone = o.index.difference(n.index)
        added = n.index.difference(o.index)
        both = o.index.intersection(n.index)
        changed = both[(o.loc[both] != n.loc[both]).to_numpy()]
        line = (f"{name}: main {len(o)} rows, copy {len(n)} rows; removed {len(gone)}, added "
                f"{len(added)}, changed {len(changed)}")
        date_col = "date" if "date" in keys else ("ex_date" if "ex_date" in keys else None)
        if date_col is not None and len(added):
            d = keys.index(date_col)
            days = sorted({k[d] for k in added})
            line += (f"; added rows' days {days[0]} .. {days[-1]} ({len(days)} days), "
                     f"{len({k[keys.index('symbol')] for k in added})} symbols; "
                     f"added on or before the main lake's last bar: {sum(1 for k in added if k[d] <= main_last)}")
        print(line)
        if name == "prices_daily":
            last = new.groupby("symbol")["date"].max()
            print("  symbols by last bar (top 5):", last.value_counts().head(5).to_dict())


if __name__ == "__main__":
    {"copy": copy, "dates": dates, "check": check}[sys.argv[1]]()
