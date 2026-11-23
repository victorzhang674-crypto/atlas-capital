"""Stage 29 follow-up 4: rebuild, on a scratch copy of the main lake, what the former CIKs and the
MD&A extraction fixes change, prove nothing else changed, and leave a copy ready to swap in
(`main_lake_followup4_swap.sh`, run from the owner's Terminal panel: the sandbox cannot write
the main checkout). Run from the worktree root with the worktree's venv, one step at a time:

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_followup4.py STEP

- `copy`    scratch copy in `$TMPDIR/s29f4/main-copy`: the curated tables except the news ones,
            the DuckDB file; `raw/fred` linked; `raw/sec/<dataset>/` real folders of per-file
            links to the main archive, so a new EDGAR answer replaces its link and the main
            archive is never written.
- `before`  digests of the factor, risk and quality stores -> `$TMPDIR/s29f4/stores-before.json`.
- `cik`     for the symbols with former CIKs (and TFCF / TFCFA): their `fundamentals_facts`,
            SEC `events`, `earnings_reactions` and `filing_text` rows deleted, then
            `ingest_fundamentals`, `ingest_earnings`, `ingest_filing_text` and
            `ingest_earnings_reactions` again (archived answers reused, new ones fetched;
            documents through the recheck's gzip cache).
- `text`    the `filing_text` rows the recheck found changed (`$TMPDIR/s29f4/recheck.parquet`,
            made with the final extraction code) and every row of the former-CIK symbols
            deleted and fetched again (from the cache).
- `missing` the rows `text` left out because the main lake's archived submissions are older
            than its `filing_text` (see the step), fetched.
- `check`   every table against the main lake, symbol by symbol and row by row: only the
            intended rows differ.
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
WORK = Path(os.environ["TMPDIR"]) / "s29f4"
COPY = WORK / "main-copy"
SKIP = {"news", "news_sentiment"}
SYMBOLS = [  # config/cik_overrides.yaml entries with `former`, and Twenty-First Century Fox
    "APA", "AVGO", "BG", "BLK", "CI", "DD", "DIS", "FTI", "GOOG", "GOOGL", "LIN", "LLL",
    "SKYD", "SNDK", "TFCF", "TFCFA", "VTRS", "XOM",
]  # fmt: skip
TABLES = ["fundamentals_facts", "events", "earnings_reactions", "filing_text"]
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


def _client(cfg, lake):  # noqa: ANN001, ANN202
    sys.path.insert(0, str(WORK))
    from caching import Caching  # the recheck's gzip document cache ($TMPDIR/s29f4/caching.py)

    from atlas.data.fundamentals import build_edgar_client

    real = build_edgar_client(cfg, lake)
    return real, Caching(real, WORK / "cache")


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
    (COPY / "raw" / "fred").symlink_to(MAIN / "raw" / "fred")
    links = 0
    for folder in (MAIN / "raw" / "sec").iterdir():
        mine = COPY / "raw" / "sec" / folder.name
        mine.mkdir(parents=True)
        for f in folder.iterdir():
            (mine / f.name).symlink_to(f)
            links += 1
    for sub in ("cache", "runs"):
        (COPY / sub).mkdir()
    shutil.copy2(MAIN / "cache" / "sync_state.json", COPY / "cache" / "sync_state.json")
    size = sum(p.stat().st_size for p in (COPY / "curated").rglob("*") if p.is_file())
    print(f"copied {len(list((COPY / 'curated').iterdir()))} entries, {size / 1e6:.1f} MB; "
          f"{links} raw/sec links")


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


def cik() -> None:
    from atlas.core.calendar import TradingCalendar
    from atlas.data.events import ingest_earnings, ingest_earnings_reactions
    from atlas.data.filings.text import ingest_filing_text
    from atlas.data.fundamentals import build_cik_map, ingest_fundamentals

    cfg = _config()
    lake = _lake(COPY)
    cal = TradingCalendar(config=cfg.calendar)
    deleted = {
        "fundamentals_facts": lake.delete_rows("fundamentals_facts", [("symbol", "in", SYMBOLS)]),
        "events": lake.delete_rows(
            "events", [("symbol", "in", SYMBOLS), ("source", "in", ["sec_8k", "sec_10q", "sec_10k"])]
        ),
        "earnings_reactions": lake.delete_rows("earnings_reactions", [("symbol", "in", SYMBOLS)]),
        "filing_text": lake.delete_rows("filing_text", [("symbol", "in", SYMBOLS)]),
    }
    print("deleted", deleted)
    real, client = _client(cfg, lake)
    with real:
        cik_map = build_cik_map(real, cfg)
        for sym in SYMBOLS:
            print(sym, [(s.cik, str(s.since), str(s.until)) for s in cik_map.sources(sym)])
        f = ingest_fundamentals(lake, real, SYMBOLS, cik_map=cik_map, config=cfg, calendar=cal, refresh=False)
        print(f"fundamentals rows={f.rows} ok={len(f.symbols_ok)} failed={dict(f.failure_reasons)}")
        e = ingest_earnings(lake, real, SYMBOLS, cik_map=cik_map, config=cfg, calendar=cal, refresh=False)
        print(f"earnings rows={e.rows} ok={len(e.symbols_ok)} failed={dict(e.failure_reasons)} "
              f"skipped={e.skipped_filings}")
        t = ingest_filing_text(lake, client, SYMBOLS, cik_map=cik_map, config=cfg, calendar=cal, refresh=False)
        print(f"filing_text rows={t.rows} ok={len(t.symbols_ok)} failed={dict(t.failure_reasons)} "
              f"statuses={dict(t.statuses)}")
    r = ingest_earnings_reactions(lake, SYMBOLS, config=cfg, calendar=cal)
    print(f"reactions rows={r.rows} skipped={r.skipped}")


def text() -> None:
    from atlas.core.calendar import TradingCalendar
    from atlas.data.filings.text import ingest_filing_text
    from atlas.data.fundamentals import build_cik_map

    changed = pd.read_parquet(WORK / "recheck.parquet")
    changed = changed[~changed["same"] & ~changed["symbol"].isin(SYMBOLS)]
    print("changed rows to fetch again:", len(changed), "of", changed["symbol"].nunique(), "symbols")
    cfg = _config()
    lake = _lake(COPY)
    n = lake.delete_rows("filing_text", [("accn", "in", sorted(set(changed["accn"])))])
    # the former-CIK symbols' rows were extracted by `cik` before the last rules: again, too
    n += lake.delete_rows("filing_text", [("symbol", "in", SYMBOLS)])
    print("deleted", n)
    real, client = _client(cfg, lake)
    with real:
        cik_map = build_cik_map(real, cfg)
        syms = sorted(set(changed["symbol"]) | set(SYMBOLS))
        rep = ingest_filing_text(lake, client, syms, cik_map=cik_map, config=cfg,
                                 calendar=TradingCalendar(config=cfg.calendar), refresh=False)
    print(f"filing_text rows={rep.rows} ok={len(rep.symbols_ok)} failed={dict(rep.failure_reasons)} "
          f"statuses={dict(rep.statuses)}")


def _table(root: Path, name: str, columns: list[str] | None = None) -> pd.DataFrame:
    return ds.dataset(str(root / "curated" / name), format="parquet").to_table(columns=columns).to_pandas()


def _row_digests(frame: pd.DataFrame) -> pd.Series:
    return pd.util.hash_pandas_object(frame.astype(str), index=False)


def check() -> None:
    lake = _lake(COPY)
    for name in TABLES:
        schema = lake.table_schema(name)
        keys = list(schema.primary_key)
        cols = [c for c in schema.column_names if c != "text"] + (["text"] if name == "filing_text" else [])
        old = _table(MAIN, name, cols)
        new = _table(COPY, name, cols)
        old["_d"], new["_d"] = _row_digests(old[cols]).values, _row_digests(new[cols]).values
        o = old.set_index(keys)["_d"]
        n = new.set_index(keys)["_d"]
        gone = o.index.difference(n.index)
        added = n.index.difference(o.index)
        both = o.index.intersection(n.index)
        changed = both[(o.loc[both] != n.loc[both]).to_numpy()]
        sym = keys.index("symbol")

        def syms(index: pd.Index) -> list[str]:
            return sorted({k[sym] for k in index})

        outside = sorted(set(syms(gone) + syms(added) + syms(changed)) - set(SYMBOLS))
        print(f"{name}: main {len(o)} rows, copy {len(n)} rows; removed {len(gone)}, added "
              f"{len(added)}, changed {len(changed)}; symbols touched outside the former-CIK "
              f"set: {len(outside)} {outside[:40]}")
        if name != "filing_text":
            for s in SYMBOLS:
                a, b = (old["symbol"] == s).sum(), (new["symbol"] == s).sum()
                if a != b:
                    print(f"  {s}: {a} -> {b}")


def after() -> None:
    old = json.loads((WORK / "stores-before.json").read_text())
    new = _digests(COPY)
    (WORK / "stores-after.json").write_text(json.dumps(new, indent=2))
    for name in STORES:
        print(name, "before", old[name], "after", new[name],
              "EQUAL" if old[name] == new[name] else "CHANGED")




def missing() -> None:
    """`text` re-ingested from the main lake's archived submissions, which predate the filings
    the follow-up 3 sync stored (it archived its submissions in its own scratch lake): those
    rows were left out. With that sync's submissions copied into the scratch copy's
    `raw/sec/submissions/` (done in the shell before this step, as regular files, so the swap
    brings them into the main archive), the missing rows of every symbol `cik` or `text`
    touched are fetched (only accessions not stored are read)."""
    from atlas.core.calendar import TradingCalendar
    from atlas.data.filings.text import ingest_filing_text
    from atlas.data.fundamentals import build_cik_map

    changed = pd.read_parquet(WORK / "recheck.parquet")
    syms = sorted(set(changed.loc[~changed["same"], "symbol"]) | set(SYMBOLS))
    cfg = _config()
    lake = _lake(COPY)
    real, client = _client(cfg, lake)
    with real:
        rep = ingest_filing_text(lake, client, syms, cik_map=build_cik_map(real, cfg), config=cfg,
                                 calendar=TradingCalendar(config=cfg.calendar), refresh=False)
    print(f"filing_text rows={rep.rows} ok={len(rep.symbols_ok)} failed={dict(rep.failure_reasons)} "
          f"statuses={dict(rep.statuses)}")


if __name__ == "__main__":
    {"copy": copy, "before": before, "cik": cik, "text": text, "missing": missing, "check": check,
     "after": after}[sys.argv[1]]()
