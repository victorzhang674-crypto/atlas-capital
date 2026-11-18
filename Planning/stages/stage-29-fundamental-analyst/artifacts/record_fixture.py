"""Build the Stage 29 recorded fixture: AAPL's fundamental analyst run as of 2024-05-01.

Run from the worktree root with the worktree's venv, one step at a time:

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/record_fixture.py STEP

Steps (each prints what it did; the main lake is only read):

- `peers`   AAPL and its Information Technology peers (S&P 500 members on the day) on the
            main lake -> `$TMPDIR/s29/peers.txt`.
- `slice`   `atlas data slice` of those symbols from the main lake (since 2017-01-01) into
            `$TMPDIR/s29/scratch` (a scratch lake).
- `reparse` the scratch lake's `fundamentals_facts` re-parsed offline (no network: archive
            only, `refresh=False`) from the main lake's raw SEC archive with the current
            concept map, which adds `accounts_receivable` (the main lake has no rows for it).
- `fixture` `atlas data slice` of the scratch lake into the fixture's `lake/` (rows known at
            the as-of, since 2017-01-01).
- `compare` AAPL's prefetch on the main lake and on the fixture lake, item by item.
- `live`    the live run (DeepSeek, quick tier) on a scratch copy of the fixture lake with the
            cache in `read_write`; writes `run.json`, `expected_report.json` and exports the
            cache. Needs `DEEPSEEK_API_KEY`.
- `export`  `atlas llm cache export` of the run lake's cache into the fixture (a separate
            process: the live run holds the lake's DuckDB lock until it exits).
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
MAIN_LAKE = Path.home() / "Projects" / "atlas-capital" / "data"
WORK = Path(os.environ["TMPDIR"]) / "s29"
SCRATCH = WORK / "scratch"
RUN_LAKE = WORK / "run-lake"
FIXTURE = ROOT / "tests" / "fixtures" / "recorded" / "stage-29-fundamental-aapl-2024-05-01"
SYMBOL, DAY, SINCE = "AAPL", date(2024, 5, 1), "2017-01-01"
TABLES = "corporate_actions,fundamentals_facts,index_membership,instruments,prices_daily"
"""What the fundamental analyst reads (prices, splits, filings, sectors, membership)."""


def _config(lake: Path, **llm: object):  # noqa: ANN202
    from atlas.core.config import load_config

    overrides: dict[str, object] = {"data": {"lake_path": str(lake)}}
    if llm:
        overrides["llm"] = llm
    return load_config("dev", overrides=overrides, config_dir=ROOT / "config")


def _symbols() -> list[str]:
    return (WORK / "peers.txt").read_text().split()


def _cli(*args: str, lake: Path) -> None:
    env = dict(os.environ, ATLAS__DATA__LAKE_PATH=str(lake), COLUMNS="160", NO_COLOR="1")
    out = subprocess.run(["atlas", *args], env=env, cwd=ROOT, capture_output=True, text=True)
    print(out.stdout + out.stderr)
    if out.returncode != 0:
        sys.exit(out.returncode)


def peers() -> None:
    from atlas.core.storage import Lake
    from atlas.data.view import DataView

    config = _config(MAIN_LAKE)
    view = DataView(Lake(MAIN_LAKE), DAY, config)
    sector = view.instrument(SYMBOL).sector
    members = view.universe(config.quant.factors.universe)
    found = []
    for s in members:
        try:
            if view.instrument(s).sector == sector:
                found.append(s)
        except Exception as exc:  # noqa: BLE001 - reported, not hidden
            print(f"no instrument for {s}: {exc}")
    found = sorted(set(found) | {SYMBOL})
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "peers.txt").write_text(" ".join(found) + "\n")
    print(f"{SYMBOL} sector {sector}: {len(found)} symbols ({len(members)} members): {found}")


def slice_main() -> None:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    _cli("data", "slice", "--symbols", ",".join(_symbols()), "--as-of", DAY.isoformat(),
         "--out", str(SCRATCH), "--since", SINCE, "--tables", TABLES, lake=MAIN_LAKE)  # fmt: skip


def reparse() -> None:
    import httpx

    from atlas.core.storage import Lake
    from atlas.data.fundamentals.edgar_client import EdgarClient, build_cik_map
    from atlas.data.fundamentals.ingest import ingest_fundamentals

    config = _config(SCRATCH)
    lake = Lake(SCRATCH)
    sec = lake.raw_dir / "sec"
    if not sec.exists():
        sec.symlink_to(MAIN_LAKE / "raw" / "sec")  # read-only use of the archive
    offline = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    client = EdgarClient(
        config.data.fundamentals, user_agent="atlas fixture build (offline)", lake=lake,
        http=offline,
    )  # fmt: skip
    cik_map = build_cik_map(client, config)
    before = len(lake.read_table("fundamentals_facts"))
    started = time.monotonic()
    report = ingest_fundamentals(lake, client, _symbols(), cik_map=cik_map, config=config,
                                 refresh=False)  # fmt: skip
    after = lake.read_table("fundamentals_facts")
    ar = after[after["tag"].isin(["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"])]
    print(f"re-parsed in {time.monotonic() - started:.1f} s: {report}")
    print(f"fundamentals_facts rows {before} -> {len(after)}; receivables rows {len(ar)} "
          f"({ar['symbol'].nunique()} symbols)")  # fmt: skip
    sec.unlink()


def fixture() -> None:
    target = FIXTURE / "lake"
    if target.exists():
        shutil.rmtree(target)
    _cli("data", "slice", "--symbols", ",".join(_symbols()), "--as-of", DAY.isoformat(),
         "--out", str(target), "--since", SINCE, "--tables", TABLES, lake=SCRATCH)  # fmt: skip
    for empty in ("raw", "cache", "runs"):
        sub = target / empty
        if sub.exists() and not any(sub.iterdir()):
            sub.rmdir()
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(f"fixture lake: {size / 1e6:.2f} MB")


def compare() -> None:
    from atlas.agents.tools.fundamental_tools import prefetch
    from atlas.core.storage import Lake
    from atlas.data.view import DataView

    sides = {}
    for name, root in (("main", MAIN_LAKE), ("fixture", FIXTURE / "lake")):
        config = _config(root)
        started = time.monotonic()
        sides[name] = {e.id: e for e in prefetch(DataView(Lake(root), DAY, config), SYMBOL)}
        print(f"{name}: {len(sides[name])} items in {time.monotonic() - started:.1f} s")
    main, fix = sides["main"], sides["fixture"]
    same = [i for i in main if i in fix and main[i] == fix[i]]
    print(f"equal items: {len(same)}")
    for i in sorted(set(main) | set(fix)):
        if i in same:
            continue
        print(f"--- {i}")
        print(f"  main:    {main[i].text if i in main else '(absent)'}")
        print(f"  fixture: {fix[i].text if i in fix else '(absent)'}")


def live() -> None:
    from atlas.agents.context import AgentContext
    from atlas.agents.research import FundamentalAnalyst
    from atlas.agents.trace import TraceStore
    from atlas.core.storage import Lake
    from atlas.data.view import DataView
    from atlas.llm import build_gateway

    if RUN_LAKE.exists():
        shutil.rmtree(RUN_LAKE)
    shutil.copytree(FIXTURE / "lake", RUN_LAKE)
    config = _config(RUN_LAKE, cache={"mode": "read_write"})
    lake = Lake(RUN_LAKE)
    agent = FundamentalAnalyst(build_gateway(config, lake=lake), config=config, traces=TraceStore())
    started = time.monotonic()
    out = agent.run(AgentContext(DataView(lake, DAY, config), inputs={"symbol": SYMBOL}))
    trace = out.trace
    run = {
        "symbol": SYMBOL,
        "as_of": DAY.isoformat(),
        "recorded_on": date.today().isoformat(),
        "provider": config.llm.provider,
        "model": config.llm.tiers.quick,
        "prompt_version": agent.prompt_version,
        "status": trace.status,
        "llm_calls": trace.llm_calls,
        "tool_calls": [c.name for c in trace.tool_calls],
        "evidence_items": len(trace.evidence_ids),
        "cited": list(trace.cited_ids),
        "problems": list(out.problems),
        "usage": trace.usage.model_dump(mode="json"),
        "cost_usd": trace.cost_usd,
        "seconds": round(time.monotonic() - started, 1),
    }
    (FIXTURE / "run.json").write_text(json.dumps(run, indent=2) + "\n")
    (FIXTURE / "expected_report.json").write_text(out.output.model_dump_json(indent=2) + "\n")
    print(json.dumps(run, indent=2))
    print(out.output.model_dump_json(indent=2))


def export() -> None:
    """A separate process: the live run's process holds the run lake's DuckDB lock."""
    _cli("llm", "cache", "export", str(FIXTURE / "llm_cache.jsonl"), "--force", lake=RUN_LAKE)


STEPS = {"peers": peers, "slice": slice_main, "reparse": reparse, "fixture": fixture,
         "compare": compare, "live": live, "export": export}  # fmt: skip

if __name__ == "__main__":
    STEPS[sys.argv[1]]()
