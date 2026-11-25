"""Stage 29 follow-up 7: the full data sync, on a scratch copy of the main lake. Every curated
table is checked against the main lake row by row before the copy is swapped in
(`main_lake_followup7_swap.sh`, run from the owner's Terminal panel: the sandbox cannot write
the main checkout). Run from the worktree root with its venv, one step at a time:

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_followup7.py STEP

- `copy`    scratch copy in `$TMPDIR/s29f7/main-copy` (as follow-up 5's `copy`, with the news
            and news-sentiment tables this time: the news and sentiment steps write them), and
            `raw/fred` as per-file links (`link_fred`), so the macro step can archive answers.
- (between `copy` and `check`: `atlas data sync` on the copy, a CLI command; see the worklog.)
- `members` for every current member (`index_membership` with no end date): its last price
            bar, last fundamentals fact, last filing and last SEC event in the copy, to find
            symbols a step checkpointed without data.
- `check`   every curated table against the main lake, row by row by primary key: rows removed,
            added and changed; for changed rows, which columns; for dated tables, the date range
            of added and changed rows; the symbols touched.
"""

import os
import sys
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import main_lake_followup5 as f5  # noqa: E402  (copy, _lake, _table, _row_digests)

f5.WORK = Path(os.environ["TMPDIR"]) / "s29f7"
f5.COPY = f5.WORK / "main-copy"
f5.SKIP = set()
MAIN, COPY = f5.MAIN, f5.COPY
DATE_NAMES = ("date", "event_date", "filed", "ex_date", "published_at", "period_end",
              "realtime_start", "observation_date", "release_date", "start_date", "as_of")  # fmt: skip


def copy() -> None:
    f5.WORK.mkdir(parents=True, exist_ok=True)
    f5.copy()
    link_fred()


def link_fred() -> None:
    """`raw/fred` as real folders of per-file links (follow-up 5's copy linked the whole folder,
    which the macro step cannot write into: its first run on this copy failed there)."""
    mine = COPY / "raw" / "fred"
    if mine.is_symlink():
        mine.unlink()
    links = 0
    for f in (MAIN / "raw" / "fred").rglob("*"):
        if f.is_file():
            target = mine / f.relative_to(MAIN / "raw" / "fred")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.symlink_to(f)
                links += 1
    print(f"raw/fred: {links} file links")


def _last(table: str, col: str, symbols: list[str]) -> pd.Series:
    path = COPY / "curated" / table
    if not path.exists():
        return pd.Series(dtype=object)
    frame = ds.dataset(str(path), format="parquet").to_table(
        columns=["symbol", col], filter=ds.field("symbol").isin(symbols)).to_pandas()
    return frame.groupby("symbol")[col].max()


def members() -> None:
    mem = f5._table(COPY, "index_membership")
    cur = sorted(set(mem.loc[mem["end_date"].isna(), "symbol"]))
    out = pd.DataFrame(index=cur)
    out["price"] = _last("prices_daily", "date", cur)
    out["fact_filed"] = _last("fundamentals_facts", "filed", cur)
    out["filing"] = _last("filing_text", "filed", cur)
    out["sec_event"] = _last("events", "event_date", cur)
    print(f"current members: {len(cur)}")
    for col in out.columns:
        s = out[col]
        print(f"{col}: none for {int(s.isna().sum())}; latest {s.max()}; by value (top 6): "
              f"{s.astype(str).value_counts().head(6).to_dict()}")
    stale = out[out["price"].astype(str) < str(out["price"].max())]
    print("members whose last bar is before the latest:", stale.index.tolist())
    old = out[out["fact_filed"].astype(str) < "2026-08-01"]
    print("members with no fact filed since 2026-08-01:", old.index.tolist())
    out.to_csv(f5.WORK / "members.csv")


def _date_col(schema, keys: list[str]) -> str | None:  # noqa: ANN001
    for name in (*keys, *schema.column_names):
        if name in DATE_NAMES:
            return name
    return None


def check() -> None:
    lake = f5._lake(COPY)
    names = sorted(p.name for p in (COPY / "curated").iterdir() if p.is_dir())
    for name in names:
        if not (MAIN / "curated" / name).exists():
            print(f"{name}: NEW table, {ds.dataset(str(COPY / 'curated' / name), format='parquet').count_rows()} rows")
            continue
        schema = lake.table_schema(name)
        keys = list(schema.primary_key)
        cols = list(schema.column_names)
        old = f5._table(MAIN, name, cols)
        new = f5._table(COPY, name, cols)
        old["_d"], new["_d"] = f5._row_digests(old[cols]).values, f5._row_digests(new[cols]).values
        o = old.set_index(keys)
        n = new.set_index(keys)
        gone = o.index.difference(n.index)
        added = n.index.difference(o.index)
        both = o.index.intersection(n.index)
        changed = both[(o.loc[both, "_d"] != n.loc[both, "_d"]).to_numpy()]
        line = (f"{name}: main {len(o)}, copy {len(n)}; removed {len(gone)}, added {len(added)}, "
                f"changed {len(changed)}")
        if len(o) == len(n) == len(both) and not len(changed):
            print(line + " (equal)")
            continue
        dcol = _date_col(schema, keys)

        def span(frame: pd.DataFrame) -> str:
            if dcol is None or frame.empty:
                return ""
            d = frame[dcol] if dcol in frame.columns else frame.index.get_level_values(dcol)
            d = pd.Series(d).dropna()
            return f"{dcol} {d.min()} .. {d.max()}" if len(d) else ""

        def symbols(index: pd.Index) -> list[str]:
            if "symbol" not in keys:
                return []
            vals = index.get_level_values("symbol") if isinstance(index, pd.MultiIndex) else index
            return sorted(set(vals))

        print(line)
        if len(added):
            a = n.loc[added].reset_index()
            print(f"  added: {span(a)}; symbols {len(symbols(added))}")
        if len(gone):
            g = o.loc[gone].reset_index()
            print(f"  removed: {span(g)}; symbols {symbols(gone)[:30]}")
        if len(changed):
            c_old, c_new = o.loc[changed], n.loc[changed]
            per_col = {c: int((c_old[c].astype(str) != c_new[c].astype(str)).sum())
                       for c in cols if c not in keys}
            per_col = {c: k for c, k in per_col.items() if k}
            print(f"  changed: {span(c_new.reset_index())}; columns {per_col}; "
                  f"symbols {len(symbols(changed))} {symbols(changed)[:20]}")


if __name__ == "__main__":
    {"copy": copy, "link_fred": link_fred, "members": members, "check": check}[sys.argv[1]]()
