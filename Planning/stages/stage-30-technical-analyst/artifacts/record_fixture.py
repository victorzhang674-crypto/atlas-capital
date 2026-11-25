"""Build the Stage 30 recorded fixture: AAPL's technical analyst run as of 2024-05-01.

Run from the worktree root with the worktree's venv, one step at a time:

    uv run python Planning/stages/stage-30-technical-analyst/artifacts/record_fixture.py STEP

Steps (each prints what it did; the main lake is only read):

- `members` the S&P 500 members on the day (the snapshot's cross-section), AAPL and SPY (the
            beta benchmark) on the main lake -> `$TMPDIR/s30/symbols.txt`.
- `regime`  `regime_at` on the main lake for the day -> the fixture's `regime.json` (its
            inputs, every member's prices since 2009 and the FRED vintages, are far too large
            for a fixture; the replay passes it as `inputs["regime"]`, as the fund graph does).
- `fixture` `atlas data slice` of those symbols from the main lake into the fixture's `lake/`
            (rows known at the as-of, since `SINCE`).
- `compare` AAPL's prefetch on the main lake and on the fixture lake, item by item (same
            regime): they must be equal.
- `live`    the live run (DeepSeek, quick tier) on a scratch copy of the fixture lake with the
            cache in `read_write`; writes `run.json`, `expected_report.json`. Needs
            `DEEPSEEK_API_KEY`.
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
WORK = Path(os.environ["TMPDIR"]) / "s30"
RUN_LAKE = WORK / "run-lake"
FIXTURE = ROOT / "tests" / "fixtures" / "recorded" / "stage-30-technical-aapl-2024-05-01"
SYMBOL, DAY = "AAPL", date(2024, 5, 1)
SINCE = "2021-10-01"
"""Early enough for every feature of the last 252 sessions: the longest read is 253 sessions
of lookback + 63 buffer + 251 more sessions of history (about 2022-01-20)."""
TABLES = "corporate_actions,index_membership,instruments,prices_daily"
"""What the technical analyst reads (prices, splits and dividends, membership, instruments)."""


def _config(lake: Path, **llm: object):  # noqa: ANN202
    from atlas.core.config import load_config

    overrides: dict[str, object] = {"data": {"lake_path": str(lake)}}
    if llm:
        overrides["llm"] = llm
    return load_config("dev", overrides=overrides, config_dir=ROOT / "config")


def _symbols() -> list[str]:
    return (WORK / "symbols.txt").read_text().split()


def _regime():  # noqa: ANN202
    from atlas.quant.regime import RegimeState

    return RegimeState.model_validate_json((FIXTURE / "regime.json").read_text())


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
    found = view.universe(config.quant.features.snapshot.universe)
    symbols = sorted({*found, SYMBOL, config.fund.benchmark})
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "symbols.txt").write_text(" ".join(symbols) + "\n")
    print(f"{len(found)} members on {DAY}; {len(symbols)} symbols with {SYMBOL} and the benchmark")
    print(f"blocked on the main lake: {sorted(view.blocked)}")


def regime() -> None:
    from atlas.core.storage import Lake
    from atlas.data.view import DataView
    from atlas.quant.regime import regime_at

    config = _config(MAIN_LAKE)
    started = time.monotonic()
    state = regime_at(DataView(Lake(MAIN_LAKE), DAY, config))
    FIXTURE.mkdir(parents=True, exist_ok=True)
    (FIXTURE / "regime.json").write_text(state.model_dump_json(indent=2) + "\n")
    print(f"regime_at in {time.monotonic() - started:.1f} s:")
    print(state.model_dump_json(indent=2))


def fixture() -> None:
    target = FIXTURE / "lake"
    if target.exists():
        shutil.rmtree(target)
    _cli("data", "slice", "--symbols", ",".join(_symbols()), "--as-of", DAY.isoformat(),
         "--out", str(target), "--since", SINCE, "--tables", TABLES, lake=MAIN_LAKE)  # fmt: skip
    for empty in ("raw", "cache", "runs"):
        sub = target / empty
        if sub.exists() and not any(sub.iterdir()):
            sub.rmdir()
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(f"fixture lake: {size / 1e6:.2f} MB")


def compare() -> None:
    from atlas.agents.tools.technical_tools import prefetch
    from atlas.core.storage import Lake
    from atlas.data.view import DataView

    state = _regime()
    sides = {}
    for name, root in (("main", MAIN_LAKE), ("fixture", FIXTURE / "lake")):
        config = _config(root)
        started = time.monotonic()
        view = DataView(Lake(root), DAY, config)
        sides[name] = {e.id: e for e in prefetch(view, SYMBOL, state)}
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
        print(f"\n[{item.id}] {item.text}")


def live() -> None:
    from atlas.agents.context import AgentContext
    from atlas.agents.research import TechnicalAnalyst
    from atlas.agents.trace import TraceStore
    from atlas.core.storage import Lake
    from atlas.data.view import DataView
    from atlas.llm import build_gateway

    if RUN_LAKE.exists():
        shutil.rmtree(RUN_LAKE)
    shutil.copytree(FIXTURE / "lake", RUN_LAKE)
    config = _config(RUN_LAKE, cache={"mode": "read_write"})
    lake = Lake(RUN_LAKE)
    agent = TechnicalAnalyst(build_gateway(config, lake=lake), config=config, traces=TraceStore())
    started = time.monotonic()
    inputs = {"symbol": SYMBOL, "regime": _regime()}
    out = agent.run(AgentContext(DataView(lake, DAY, config), inputs=inputs))
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


STEPS = {"members": members, "regime": regime, "fixture": fixture, "compare": compare,
         "live": live, "export": export}  # fmt: skip

if __name__ == "__main__":
    STEPS[sys.argv[1]]()
