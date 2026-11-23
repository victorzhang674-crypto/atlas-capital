"""Stage 29 follow-up 5: on a scratch copy of the main lake, add OKTA's and VYLR's data (the
S&P 500 members since October 2026 that had no fundamentals) and replace every `filing_text`
row whose final extraction differs from the stored one; prove nothing else changed; leave a
copy ready to swap in (`main_lake_followup5_swap.sh`, run from the owner's Terminal panel: the
sandbox cannot write the main checkout). Run from the worktree root with its venv, one step at
a time:

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_followup5.py STEP

- `copy`    scratch copy in `$TMPDIR/s29f5/main-copy`: the curated tables except the news ones,
            the DuckDB file, `cache/sync_state.json`; `raw/fred` linked; `raw/sec/<dataset>/`
            and `raw/prices` as real folders of per-file links to the main archive, so a new
            answer replaces its link and the main archive is never written.
- `before`  digests of the factor and risk stores -> `$TMPDIR/s29f5/stores-before.json`.
- `new`     OKTA and VYLR: `atlas data sync --datasets prices` for the two symbols up to
            2026-10-30 (the main lake's last price day) and `--datasets fundamentals` up to
            today (VYLR's checkpoint was "empty" before its first 10-Q), run as CLI commands
            before this step (see the worklog); this step sets their first and last trade
            dates from `prices_daily` (the rule of the universe step) and runs `ingest_earnings` and
            `ingest_earnings_reactions` for them: the sync's `events` steps that are per symbol
            (the upcoming-dates snapshot is skipped for a past end; the FRED macro calendar is
            market-wide and not part of this change).
- `text`    every `filing_text` row the final full re-extraction (`$TMPDIR/s29f5/full-final`)
            found different: replaced by that extraction (status, source, document, URL, the
            document's SHA-256 is kept when the document is the same, else recomputed from
            EDGAR's bytes), keyed by symbol and accession.
- `check`   every changed table against the main lake, row by row: only the intended rows
            differ.
- `after`   the store digests after the rebuilds (`atlas factors build`, `atlas risk-model build
            --cached-factors` on the scratch copy, run in between), compared with `before`.
"""

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parents[4]
MAIN = Path.home() / "Projects" / "atlas-capital" / "data"
WORK = Path(os.environ["TMPDIR"]) / "s29f5"
COPY = WORK / "main-copy"
SKIP = {"news", "news_sentiment"}
NEW = ["OKTA", "VYLR"]
TABLES = ["prices_daily", "corporate_actions", "instruments", "fundamentals_facts", "events",
          "earnings_reactions", "filing_text"]  # fmt: skip
STORES = {
    "factor_exposures": (["date", "symbol", "factor"], ["value", "raw", "filled"]),
    "factor_returns": (["date", "factor"], None),
    "risk_model_snapshots": (["date", "component", "row", "column"], None),
}


def _lake(root: Path):  # noqa: ANN202
    import atlas.cli.app  # noqa: F401  (registers every table)
    from atlas.core.storage import Lake

    return Lake(root)


def _config():  # noqa: ANN202
    from atlas.core.config import load_config

    return load_config("dev", overrides={"data": {"lake_path": str(COPY)}}, config_dir=ROOT / "config")


def copy() -> None:
    if COPY.exists():
        shutil.rmtree(COPY)
    (COPY / "curated").mkdir(parents=True)
    for item in (MAIN / "curated").iterdir():
        if item.name in SKIP:
            continue
        target = COPY / "curated" / item.name
        shutil.copytree(item, target) if item.is_dir() else shutil.copy2(item, target)
    shutil.copy2(MAIN / "atlas.duckdb", COPY / "atlas.duckdb")
    (COPY / "raw").mkdir()
    links = 0
    for folder in (MAIN / "raw").iterdir():
        if folder.name == "fred" or not folder.is_dir():
            (COPY / "raw" / folder.name).symlink_to(folder)
            continue
        for f in folder.rglob("*"):
            if f.is_file():
                mine = COPY / "raw" / f.relative_to(MAIN / "raw")
                mine.parent.mkdir(parents=True, exist_ok=True)
                mine.symlink_to(f)
                links += 1
    for sub in ("cache", "runs"):
        (COPY / sub).mkdir()
    shutil.copy2(MAIN / "cache" / "sync_state.json", COPY / "cache" / "sync_state.json")
    size = sum(p.stat().st_size for p in (COPY / "curated").rglob("*") if p.is_file())
    print(f"copied {len(list((COPY / 'curated').iterdir()))} entries, {size / 1e6:.1f} MB; "
          f"{links} raw file links")


def _digests(root: Path) -> dict[str, dict[str, object]]:
    lake = _lake(root)
    out: dict[str, dict[str, object]] = {}
    for name, (keys, values) in STORES.items():
        frame = lake.read_table(name)
        cols = keys + (values if values is not None else [c for c in frame.columns if c not in keys and c not in ("available_at", "data_fingerprint", "config_hash")])
        part = frame[cols].sort_values(keys, kind="stable").reset_index(drop=True)
        text = part.to_csv(index=False, float_format="%.17g")
        out[name] = {"rows": len(part), "dates": part["date"].nunique(),
                     "digest": hashlib.sha256(text.encode()).hexdigest()[:16]}
    return out


def before() -> None:
    digests = _digests(COPY)
    (WORK / "stores-before.json").write_text(json.dumps(digests, indent=2))
    print(json.dumps(digests, indent=2))


def new() -> None:
    from atlas.core.calendar import TradingCalendar
    from atlas.data.events import ingest_earnings, ingest_earnings_reactions
    from atlas.data.fundamentals import build_cik_map, build_edgar_client

    cfg = _config()
    lake = _lake(COPY)
    cal = TradingCalendar(config=cfg.calendar)
    # first and last trade dates from prices_daily, as `refresh_universe_reference` sets them
    # (the universe step, which also applies today's index changes, is not run here)
    prices = lake.read_table("prices_daily", filters=[("symbol", "in", NEW)], columns=["symbol", "date"])
    span = prices.groupby("symbol")["date"].agg(["min", "max"])
    inst = lake.read_table("instruments", filters=[("symbol", "in", NEW)])
    for col, agg in (("first_trade_date", "min"), ("last_trade_date", "max")):
        inst[col] = [span.loc[s, agg] if s in span.index else v for s, v in zip(inst["symbol"], inst[col])]
    lake.write_table("instruments", inst, mode="upsert")
    print(inst[["symbol", "first_trade_date", "last_trade_date"]].to_string(index=False))
    with build_edgar_client(cfg, lake) as client:
        cik_map = build_cik_map(client, cfg)
        for sym in NEW:
            print(sym, [(s.cik, str(s.since), str(s.until)) for s in cik_map.sources(sym)])
        e = ingest_earnings(lake, client, NEW, cik_map=cik_map, config=cfg, calendar=cal)
        print(f"earnings rows={e.rows} ok={e.symbols_ok} failed={dict(e.failure_reasons)} "
              f"skipped={e.skipped_filings}")
    r = ingest_earnings_reactions(lake, NEW, config=cfg, calendar=cal)
    print(f"reactions rows={r.rows} skipped={r.skipped}")
    for name in ["prices_daily", "fundamentals_facts", "events", "earnings_reactions", "filing_text"]:
        f = ds.dataset(str(COPY / "curated" / name), format="parquet").to_table(
            columns=["symbol"], filter=ds.field("symbol").isin(NEW)).to_pandas()
        print(name, f["symbol"].value_counts().to_dict())


def _final() -> pd.DataFrame:
    parts = sorted((WORK / "full-final").glob("part-*.parquet"))
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)


def text() -> None:
    from atlas.data.filings.text import FILING_TEXT

    cfg = _config()
    lake = _lake(COPY)
    final = _final()
    if (final["status"] == "error").any():
        raise SystemExit(f"{int((final['status'] == 'error').sum())} errors in the re-extraction")
    changed = final[~final["same"]]
    print("rows to replace:", len(changed), "of", changed["symbol"].nunique(), "symbols")
    stored = ds.dataset(str(COPY / "curated" / "filing_text"), format="parquet").to_table(
        filter=ds.field("accn").isin(list(changed["accn"]))).to_pandas()
    stored = stored.set_index(["symbol", "accn"])
    template = cfg.data.fundamentals.filing_document_url
    rows = []
    for r in changed.itertuples():
        old = stored.loc[(r.symbol, r.accn)]
        row = old.to_dict() | {"symbol": r.symbol, "accn": r.accn}
        row.update(status=r.status, source=r.source, document=r.new_document,
                   chars=int(r.chars), text=r.text)
        if r.new_document != old["document"]:
            folder = str(r.accn).replace("-", "")
            row["url"] = template.format(cik=int(r.cik), folder=folder, name=r.new_document)
            row["sha256"] = _sha256(cfg, lake, int(r.cik), r.accn, r.new_document)
        rows.append(row)
    frame = pd.DataFrame(rows)[[c.name for c in FILING_TEXT.columns]]
    lake.write_table(FILING_TEXT.name, frame, mode="upsert", keys=list(FILING_TEXT.primary_key))
    print("replaced", len(frame), "rows; by kind:",
          frame.assign(k=frame["source"]).groupby("k").size().to_dict())


_client = None


def _sha256(cfg, lake, cik: int, accn: str, name: str) -> str:  # noqa: ANN001
    """The SHA-256 of a document's bytes, as `ingest_filing_text` stores it (fetched)."""
    global _client
    from atlas.data.fundamentals import build_edgar_client

    if _client is None:
        _client = build_edgar_client(cfg, lake)
    return hashlib.sha256(_client.filing_document(cik, accn, name)).hexdigest()


def _table(root: Path, name: str, columns: list[str] | None = None) -> pd.DataFrame:
    return ds.dataset(str(root / "curated" / name), format="parquet").to_table(columns=columns).to_pandas()


def _row_digests(frame: pd.DataFrame) -> pd.Series:
    return pd.util.hash_pandas_object(frame.astype(str), index=False)


def check() -> None:
    lake = _lake(COPY)
    final = _final()
    intended = set(zip(final.loc[~final["same"], "symbol"], final.loc[~final["same"], "accn"]))
    for name in TABLES:
        schema = lake.table_schema(name)
        keys = list(schema.primary_key)
        cols = list(schema.column_names)
        old = _table(MAIN, name, cols)
        new_ = _table(COPY, name, cols)
        old["_d"], new_["_d"] = _row_digests(old[cols]).values, _row_digests(new_[cols]).values
        o = old.set_index(keys)["_d"]
        n = new_.set_index(keys)["_d"]
        gone = o.index.difference(n.index)
        added = n.index.difference(o.index)
        both = o.index.intersection(n.index)
        changed = both[(o.loc[both] != n.loc[both]).to_numpy()]
        sym = keys.index("symbol") if "symbol" in keys else None

        def syms(index: pd.Index) -> set[str]:
            if sym is None:
                return set()
            return {k[sym] if isinstance(k, tuple) else k for k in index}  # one-column keys

        outside = sorted((syms(gone) | syms(added) | syms(changed)) - set(NEW))
        line = (f"{name}: main {len(o)} rows, copy {len(n)} rows; removed {len(gone)}, added "
                f"{len(added)}, changed {len(changed)}")
        if name == "filing_text":
            acc = keys.index("accn")
            unintended = {(k[sym], k[acc]) for k in changed} - intended
            line += f"; changed rows not found different by the re-extraction: {len(unintended)}"
            line += f"; symbols added/removed outside OKTA, VYLR: {sorted(syms(gone) | syms(added) - set(NEW))}"
        else:
            line += f"; symbols touched outside OKTA, VYLR: {len(outside)} {outside[:20]}"
        print(line)
        for s in NEW:
            if sym is not None:
                a, b = (old["symbol"] == s).sum(), (new_["symbol"] == s).sum()
                print(f"  {s}: {a} -> {b}")


def after() -> None:
    old = json.loads((WORK / "stores-before.json").read_text())
    new_ = _digests(COPY)
    (WORK / "stores-after.json").write_text(json.dumps(new_, indent=2))
    for name in STORES:
        print(name, "before", old[name], "after", new_[name],
              "EQUAL" if old[name] == new_[name] else "CHANGED")


if __name__ == "__main__":
    {"copy": copy, "before": before, "new": new, "text": text, "check": check,
     "after": after}[sys.argv[1]]()
