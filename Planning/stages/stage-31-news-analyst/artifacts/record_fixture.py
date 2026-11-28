"""Build the Stage 31 recorded fixture: AAPL's news analyst run as of 2024-05-01.

Run from the worktree root with the worktree's venv, one step at a time:

    uv run python Planning/stages/stage-31-news-analyst/artifacts/record_fixture.py STEP

Steps (each prints what it did; the main lake is only read):

- `members` AAPL's GICS-sector members of the S&P 500 on the day, AAPL and SPY (the reaction
            benchmark) -> `$TMPDIR/s31/symbols.txt`. The news analyst reads the sector peers'
            news, so the slice holds the whole sector (its members are the slice's universe).
- `fixture` `slice_lake` of the main lake in three parts (each table with the `since` it
            needs), merged into the fixture's `lake/`:
            news since 2024-04-01 (the 21-day window + 48 h of dedup context),
            events, macro_events and earnings_reactions since 2022-01-01 (the last 8 reactions
            and the earlier report dates an estimated earnings date is projected from),
            prices_daily since 2024-03-01 (the 20-session ADV that ranks the peers),
            corporate_actions since 2023-01-01, instruments and index_membership whole.
- `compare` AAPL's prefetch on the main lake and on the fixture lake, item by item: equal.
- `live`    the live run (DeepSeek, quick tier) on a scratch copy of the fixture lake with the
            cache in `read_write`; writes `run.json`, `expected_report.json`. Needs
            `DEEPSEEK_API_KEY` in the environment (never printed).
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
WORK = Path(os.environ["TMPDIR"]) / "s31"
RUN_LAKE = WORK / "run-lake"
FIXTURE = ROOT / "tests" / "fixtures" / "recorded" / "stage-31-news-aapl-2024-05-01"
SYMBOL, DAY = "AAPL", date(2024, 5, 1)

PARTS = (
    ("news", date(2024, 4, 1), ("news",)),
    ("events", date(2022, 1, 1), ("events", "macro_events", "earnings_reactions")),
    ("prices", date(2024, 3, 1), ("prices_daily",)),
    ("actions", date(2023, 1, 1), ("corporate_actions",)),
    ("static", None, ("instruments", "index_membership")),
)
"""(name, `since`, tables): what each table needs (see the module docstring)."""


def _config(lake: Path, **llm: object):  # noqa: ANN202
    from atlas.core.config import load_config

    overrides: dict[str, object] = {"data": {"lake_path": str(lake)}}
    if llm:
        overrides["llm"] = llm
    return load_config("dev", overrides=overrides, config_dir=ROOT / "config")


def _symbols() -> list[str]:
    return (WORK / "symbols.txt").read_text().split()


def _cli(*args: str, lake: Path) -> None:
    env = dict(os.environ, ATLAS__DATA__LAKE_PATH=str(lake), COLUMNS="160", NO_COLOR="1")
    out = subprocess.run(["atlas", *args], env=env, cwd=ROOT, capture_output=True, text=True)
    print(out.stdout + out.stderr)
    if out.returncode != 0:
        sys.exit(out.returncode)


def members() -> None:
    from atlas.core.storage import Lake
    from atlas.data.view import DataView

    config = _config(MAIN_LAKE)
    view = DataView(Lake(MAIN_LAKE), DAY, config)
    sector = view.instrument(SYMBOL).sector
    found = view.universe(config.quant.factors.universe)
    peers = [s for s in found if view.instrument(s).sector == sector]
    symbols = sorted({*peers, SYMBOL, config.fund.benchmark})
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "symbols.txt").write_text(" ".join(symbols) + "\n")
    print(f"{SYMBOL} sector: {sector}; {len(found)} members on {DAY}, {len(peers)} in the sector")
    print(f"{len(symbols)} symbols with the benchmark: {' '.join(symbols)}")
    print(f"blocked on the main lake: {sorted(view.blocked)}")


def fixture() -> None:
    import atlas.data.view  # noqa: F401  (importing the readers registers every table schema)
    import atlas.data.universe  # noqa: F401
    from atlas.core.pit import AsOf
    from atlas.core.storage import Lake
    from atlas.data.slice import slice_lake

    target = FIXTURE / "lake"
    if target.exists():
        shutil.rmtree(target)
    scratch = WORK / "parts"
    if scratch.exists():
        shutil.rmtree(scratch)
    config = _config(MAIN_LAKE)
    source = Lake(MAIN_LAKE)
    as_of = AsOf.for_session(DAY, config=config.calendar)
    final = Lake(target, registry=source.registry)
    rows: dict[str, int] = {}
    since: dict[str, str | None] = {}
    for name, start, tables in PARTS:
        started = time.monotonic()
        report = slice_lake(
            source, scratch / name, _symbols(), as_of, since=start, tables=list(tables)
        )
        part = Lake(scratch / name, registry=source.registry)
        for table, n in report.rows.items():
            if n:
                final.write_table(table, part.read_table(table), mode="overwrite")
            rows[table] = n
            since[table] = start.isoformat() if start else None
        print(f"{name}: {report.rows} in {time.monotonic() - started:.1f} s")
    manifest = {
        "as_of": DAY.isoformat(),
        "cutoff": as_of.cutoff.isoformat(),
        "symbols": _symbols(),
        "since": since,
        "tables": rows,
    }
    (target / "slice.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(f"fixture lake: {size / 1e6:.2f} MB; rows {rows}")


def compare() -> None:
    from atlas.agents.tools.news_tools import prefetch
    from atlas.core.storage import Lake
    from atlas.data.view import DataView

    sides = {}
    for name, root in (("main", MAIN_LAKE), ("fixture", FIXTURE / "lake")):
        config = _config(root)
        started = time.monotonic()
        view = DataView(Lake(root), DAY, config)
        sides[name] = {e.id: e for e in prefetch(view, SYMBOL)}
        print(f"{name}: {len(sides[name])} items in {time.monotonic() - started:.1f} s; "
              f"blocked {sorted(view.blocked)}")  # fmt: skip
    main, fix = sides["main"], sides["fixture"]
    same = [i for i in main if i in fix and main[i] == fix[i]]
    print(f"equal items: {len(same)} of {len(set(main) | set(fix))}")
    for i in sorted(set(main) | set(fix)):
        if i in same:
            continue
        print(f"--- {i}")
        print(f"  main:    {main[i].text if i in main else '(absent)'}")
        print(f"  fixture: {fix[i].text if i in fix else '(absent)'}")
    for item in fix.values():
        print(f"\n[{item.id}] {item.as_of:%Y-%m-%d %H:%M}Z {item.text}")


def live() -> None:
    from atlas.agents.context import AgentContext
    from atlas.agents.research import NewsAnalyst
    from atlas.agents.trace import TraceStore
    from atlas.core.storage import Lake
    from atlas.data.view import DataView
    from atlas.llm import build_gateway

    if RUN_LAKE.exists():
        shutil.rmtree(RUN_LAKE)
    shutil.copytree(FIXTURE / "lake", RUN_LAKE)
    config = _config(RUN_LAKE, cache={"mode": "read_write"})
    lake = Lake(RUN_LAKE)
    agent = NewsAnalyst(build_gateway(config, lake=lake), config=config, traces=TraceStore())
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


STEPS = {"members": members, "fixture": fixture, "compare": compare, "live": live,
         "export": export}  # fmt: skip

if __name__ == "__main__":
    STEPS[sys.argv[1]]()
