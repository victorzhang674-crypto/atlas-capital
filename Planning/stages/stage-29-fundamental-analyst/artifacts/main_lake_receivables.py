"""Stage 29 follow-up (audit A5): give the main lake its `accounts_receivable` rows.

The main lake stores only the tags the concept map named when it was ingested, so the new
`accounts_receivable` concept has no rows there. This rebuilds, on a scratch copy, what a
re-ingest changes, proves it changes nothing else, and leaves a copy ready to swap in
(`main_lake_swap.sh`, run from the owner's Terminal panel: the sandbox cannot write the main
checkout). Run from the worktree root with the worktree's venv, one step at a time:

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_receivables.py STEP

- `copy`     scratch copy of the main lake in `$TMPDIR/s29/main-copy` (curated tables except the
             news ones, the DuckDB file; `raw/` symlinked read-only).
- `before`   digests of the stored factor exposures, factor returns and risk-model snapshots
             (values only) -> `$TMPDIR/s29/stores-before.json`.
- `reparse`  `ingest_fundamentals` of every symbol with facts, offline (`refresh=False`, an
             HTTP transport answering 404), with the current concept map.
- `check`    old facts vs new facts, row by row: every old row unchanged, every new row a
             receivables tag.
- `after`    the same store digests after the rebuilds (`atlas factors build`, `atlas
             risk-model build --cached-factors` on the scratch copy, run in between) and the
             comparison with `before`.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
MAIN = Path.home() / "Projects" / "atlas-capital" / "data"
WORK = Path(os.environ["TMPDIR"]) / "s29"
COPY = WORK / "main-copy"
SKIP = {"news", "news_sentiment"}
AR_TAGS = {"AccountsReceivableNetCurrent", "ReceivablesNetCurrent"}
STORES = {
    "factor_exposures": (["date", "symbol", "factor"], ["value", "raw", "filled"]),
    "factor_returns": (["date", "factor"], None),
    "risk_model_snapshots": (["date", "component", "row", "column"], None),
}


def _lake(root: Path):  # noqa: ANN202
    import atlas.cli.app  # noqa: F401  (registers every table)
    from atlas.core.storage import Lake

    return Lake(root)


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
    (COPY / "raw").symlink_to(MAIN / "raw")
    for sub in ("cache", "runs"):
        (COPY / sub).mkdir()
    size = sum(p.stat().st_size for p in (COPY / "curated").rglob("*") if p.is_file())
    print(f"copied {len(list((COPY / 'curated').iterdir()))} entries, {size / 1e6:.1f} MB")


def _store_digests(root: Path) -> dict[str, dict[str, object]]:
    lake = _lake(root)
    out: dict[str, dict[str, object]] = {}
    for name, (keys, values) in STORES.items():
        frame = lake.read_table(name)
        cols = keys + (values if values is not None else [c for c in frame.columns if c not in keys and c not in ("available_at", "data_fingerprint", "config_hash")])
        part = frame[cols].sort_values(keys, kind="stable").reset_index(drop=True)
        text = part.to_csv(index=False, float_format="%.17g")
        out[name] = {
            "rows": len(part),
            "columns": cols,
            "dates": sorted({str(d) for d in part["date"]}).__len__(),
            "digest": hashlib.sha256(text.encode()).hexdigest()[:16],
        }
    return out


def before() -> None:
    digests = _store_digests(COPY)
    (WORK / "stores-before.json").write_text(json.dumps(digests, indent=2))
    print(json.dumps(digests, indent=2))


def reparse() -> None:
    import httpx

    from atlas.core.config import load_config
    from atlas.data.fundamentals.edgar_client import EdgarClient, build_cik_map
    from atlas.data.fundamentals.ingest import ingest_fundamentals

    config = load_config("dev", overrides={"data": {"lake_path": str(COPY)}}, config_dir=ROOT / "config")
    lake = _lake(COPY)
    old = lake.read_table("fundamentals_facts")
    old.to_parquet(WORK / "facts-before.parquet")
    symbols = sorted(set(old["symbol"]))
    offline = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    client = EdgarClient(config.data.fundamentals, user_agent="atlas re-parse (offline)", lake=lake, http=offline)
    started = time.monotonic()
    report = ingest_fundamentals(
        lake, client, symbols, cik_map=build_cik_map(client, config), config=config, refresh=False
    )
    print(f"re-parsed {len(symbols)} symbols in {time.monotonic() - started:.0f} s: rows={report.rows} "
          f"ok={len(report.symbols_ok)} failed={len(report.symbols_failed)} {dict(report.failure_reasons)}")


def check() -> None:
    old = pd.read_parquet(WORK / "facts-before.parquet")
    new = _lake(COPY).read_table("fundamentals_facts")
    cols = list(old.columns)
    merged = old.merge(new, on=cols, how="outer", indicator=True)
    only_old = merged[merged["_merge"] == "left_only"]
    only_new = merged[merged["_merge"] == "right_only"]
    print(f"rows before {len(old)}, after {len(new)}, unchanged {int((merged['_merge'] == 'both').sum())}")
    print(f"rows gone or changed: {len(only_old)}")
    print(f"rows added: {len(only_new)}; their tags: {sorted(set(only_new['tag']))}")
    print(f"symbols with receivables rows: {only_new['symbol'].nunique()} of {old['symbol'].nunique()}")
    ok = only_old.empty and set(only_new["tag"]) <= AR_TAGS
    print("CHECK", "OK" if ok else "FAILED")
    if not ok:
        print(only_old.head(20).to_string())
        sys.exit(1)


def after() -> None:
    old = json.loads((WORK / "stores-before.json").read_text())
    new = _store_digests(COPY)
    (WORK / "stores-after.json").write_text(json.dumps(new, indent=2))
    same = True
    for name in STORES:
        equal = old[name] == new[name]
        same &= equal
        print(f"{name}: before {old[name]['rows']} rows / {old[name]['dates']} dates / {old[name]['digest']}; "
              f"after {new[name]['rows']} / {new[name]['dates']} / {new[name]['digest']}: {'EQUAL' if equal else 'DIFFERENT'}")
    print("STORES", "EQUAL" if same else "DIFFERENT")
    for name in ("factors", "risk_model"):
        print(subprocess.run(["tail", "-2", str(COPY / "runs" / name / "build.log")], capture_output=True, text=True).stdout)


STEPS = {"copy": copy, "before": before, "reparse": reparse, "check": check, "after": after}

if __name__ == "__main__":
    STEPS[sys.argv[1]]()
