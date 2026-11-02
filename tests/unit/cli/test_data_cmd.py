"""Stage 14 Task 7: `atlas data sync|update|status|quality` through Typer's CliRunner, with
the fake world's sources patched in (no network)."""

import json
import re
import shutil
import tomllib
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import respx
import yaml
from sync_helpers import (
    ETFS,
    MEMBERS,
    REPO_ROOT,
    START,
    TODAY,
    World,
    WorldPrices,
    sec_router,
    world_lexicon_file,
)
from typer.testing import CliRunner

from atlas.cli import data_cmd
from atlas.cli.app import app
from atlas.core.audit import AuditLog
from atlas.core.clock import SimClock
from atlas.core.errors import ConfigError, DataError
from atlas.core.storage import Database, Lake
from atlas.data.sync import SyncState

ENV = {"COLUMNS": "200", "NO_COLOR": "1"}


@pytest.fixture(autouse=True)
def sec() -> Iterator[respx.MockRouter]:
    yield from sec_router()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """A working directory with a copy of `config/` (ETFs SPY/QQQ, a two-series macro
    catalog), the test lexicon at its configured path, no `.env`, and the CLI's sources and
    clock replaced by the fake world's."""
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    universe = tmp_path / "config" / "universe.yaml"
    body = yaml.safe_load(universe.read_text())
    body["macro_etfs"]["tickers"] = list(ETFS)
    universe.write_text(yaml.safe_dump(body))
    (tmp_path / "config" / "macro.yaml").write_text(
        "series:\n"
        '  CPIAUCSL: {kind: release, release_time: "08:30", description: "CPI"}\n'
        '  DGS10: {kind: market, description: "10-year yield"}\n'
    )
    (tmp_path / "data" / "reference").mkdir(parents=True)
    lexicon = world_lexicon_file(tmp_path / "data" / "reference")
    monkeypatch.chdir(tmp_path)
    for name in ("FRED_API_KEY", "SEC_USER_AGENT", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    fake = World(lexicon_file=lexicon)
    monkeypatch.setattr(data_cmd, "build_sources", lambda cfg, lake: fake.sources())
    monkeypatch.setattr(data_cmd, "make_clock", lambda: SimClock(start=TODAY))
    return fake


def invoke(*args: str) -> object:
    return CliRunner().invoke(app, list(args), env=ENV, catch_exceptions=False)


def run_dirs(root: Path) -> list[Path]:
    runs = root / "data" / "runs"
    return sorted(p for p in runs.iterdir() if p.is_dir()) if runs.exists() else []


def _plain(text: str) -> str:
    """`text` without ANSI escape sequences (Rich styling)."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_help_lists_commands() -> None:
    # Help text is compared unstyled: under GitHub Actions (or FORCE_COLOR) Typer forces Rich's
    # terminal mode, which styles every option name.
    result = CliRunner().invoke(app, ["--help"], env=ENV)
    assert result.exit_code == 0
    assert "data" in _plain(result.output)
    data_help = CliRunner().invoke(app, ["data", "--help"], env=ENV)
    assert data_help.exit_code == 0
    for command in ("sync", "update", "status", "quality"):
        assert command in _plain(data_help.output)
    sync_help = CliRunner().invoke(app, ["data", "sync", "--help"], env=ENV)
    for option in ("--from", "--to", "--datasets", "--symbols"):
        assert option in _plain(sync_help.output)


def test_sync_runs_and_prints_summary(world: World, tmp_path: Path) -> None:
    result = invoke("data", "sync", "--from", START.isoformat())
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    out = result.output  # type: ignore[attr-defined]
    for step in ("universe", "prices", "fundamentals", "macro", "news", "quality"):
        assert step in out
    assert "Sync summary" in out
    lake = Lake(tmp_path / "data")
    assert set(lake.read_table("prices_daily")["symbol"]) == {*MEMBERS, *ETFS}
    state = SyncState.load(tmp_path / "data" / "cache" / "sync_state.json")
    assert state.runs[-1]["command"] == "sync"
    (run,) = run_dirs(tmp_path)
    log = [json.loads(line) for line in (run / "sync.log").read_text().splitlines()]
    assert {line.get("run_id") for line in log} == {run.name}


def test_sync_writes_audit_events(world: World, tmp_path: Path) -> None:
    invoke("data", "sync", "--from", START.isoformat(), "--datasets", "universe,prices")
    (run,) = run_dirs(tmp_path)
    path = run / "audit.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [e["event_type"] for e in events] == [
        "RUN_STARTED",
        "DATA_SYNC",
        "DATA_SYNC",
        "RUN_FINISHED",
    ]
    assert events[0]["payload"]["command"] == "data sync"
    assert events[0]["payload"]["datasets"] == ["universe", "prices"]
    assert [e["payload"]["step"] for e in events[1:3]] == ["universe", "prices"]
    assert events[-1]["payload"]["ok"] is True
    assert AuditLog.verify_chain(path)


def mirrored(root: Path, run_id: str) -> list[tuple[str, str]]:
    """`(event_id, hash)` of `run_id`'s rows in the lake's DuckDB `audit_events`, in order."""
    with Database(Lake(root / "data"), read_only=True) as db:
        rows = db.query(
            "SELECT event_id, hash FROM audit_events WHERE run_id = ? ORDER BY seq", [run_id]
        )
    return list(zip(rows["event_id"], rows["hash"], strict=True))


def logged(path: Path) -> list[tuple[str, str]]:
    return [(e["event_id"], e["hash"]) for e in map(json.loads, path.read_text().splitlines())]


def test_sync_mirrors_audit_log_into_duckdb(world: World, tmp_path: Path) -> None:
    # Phase 0 checkpoint: the DuckDB copy anchors the JSONL hash chain (Stage 03 backlog).
    result = invoke("data", "sync", "--from", START.isoformat(), "--datasets", "universe")
    assert result.exit_code == 0  # type: ignore[attr-defined]
    (run,) = run_dirs(tmp_path)
    assert mirrored(tmp_path, run.name) == logged(run / "audit.jsonl")
    lines = [json.loads(line) for line in (run / "sync.log").read_text().splitlines()]
    assert [line["inserted"] for line in lines if line["event"] == "audit.mirror"] == [3]


def test_audit_mirror_failure_exit_1(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: Database, path: Path) -> int:
        raise DataError(f"audit log {path} fails hash chain verification; not mirrored")

    monkeypatch.setattr(Database, "mirror_audit_log", broken)
    result = invoke("data", "sync", "--from", START.isoformat(), "--datasets", "universe")
    assert result.exit_code == 1  # type: ignore[attr-defined]
    assert "not mirrored" in result.output  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "args",
    [
        ("--datasets", "prices,options"),
        ("--from", "2024-13-01"),
        ("--from", "2024-05-01"),
        ("--symbols", "AA PL"),
    ],
)
def test_sync_bad_arguments_exit_2(world: World, args: tuple[str, ...]) -> None:
    result = CliRunner().invoke(app, ["data", "sync", *args], env=ENV)
    assert result.exit_code == 2, result.output
    assert world.prices.calls == []


def test_sync_step_error_exit_1(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_key() -> None:
        raise ConfigError("FRED_API_KEY is not set; add it to .env")

    monkeypatch.setattr(data_cmd, "build_sources", lambda cfg, lake: world.sources(fred=no_key))
    result = invoke("data", "sync", "--from", START.isoformat(), "--datasets", "macro")
    assert result.exit_code == 1  # type: ignore[attr-defined]
    assert "FRED_API_KEY" in result.output  # type: ignore[attr-defined]


def test_sync_interrupt_records_crash(world: World, tmp_path: Path) -> None:
    world.prices = WorldPrices(crash_on=1)
    result = CliRunner().invoke(
        app, ["data", "sync", "--from", START.isoformat(), "--datasets", "universe,prices"], env=ENV
    )
    assert result.exit_code != 0
    (run,) = run_dirs(tmp_path)
    events = [json.loads(line) for line in (run / "audit.jsonl").read_text().splitlines()]
    assert events[-1]["event_type"] == "RUN_FINISHED"
    assert events[-1]["payload"]["ok"] is False
    assert "KeyboardInterrupt" in events[-1]["payload"]["crashed"]
    assert mirrored(tmp_path, run.name) == logged(run / "audit.jsonl")  # crashes too


def test_update_incremental(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(data_cmd, "make_clock", lambda: SimClock(start=date(2024, 3, 1)))
    assert (
        invoke(
            "data", "sync", "--from", START.isoformat(), "--datasets", "universe,prices"
        ).exit_code
        == 0
    )  # type: ignore[attr-defined]
    world.prices.calls.clear()
    monkeypatch.setattr(data_cmd, "make_clock", lambda: SimClock(start=TODAY))
    result = invoke("data", "update", "--datasets", "prices")
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    assert {start for _, start, _ in world.prices.calls} == {date(2024, 2, 25)}


def test_update_without_state_exit_1(world: World) -> None:
    result = invoke("data", "update")
    assert result.exit_code == 1  # type: ignore[attr-defined]
    assert "atlas data sync" in result.output  # type: ignore[attr-defined]


def test_status_prints_tables_and_coverage(world: World, tmp_path: Path) -> None:
    invoke("data", "sync", "--from", START.isoformat())
    before = run_dirs(tmp_path)
    result = invoke("data", "status")
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    out = result.output  # type: ignore[attr-defined]
    for word in ("prices_daily", "fundamentals_facts", "Coverage", "current members", "Quality"):
        assert word in out
    assert "2/3" in out  # fundamentals: AAPL, MSFT of AAPL, MSFT, NEW
    assert run_dirs(tmp_path) == before  # read-only: no run directory


def test_status_on_empty_lake(world: World) -> None:
    result = invoke("data", "status")
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    assert "universe" in result.output  # type: ignore[attr-defined]


def test_quality_as_of_prints_issues(world: World, tmp_path: Path) -> None:
    invoke("data", "sync", "--from", START.isoformat(), "--datasets", "universe,prices")
    before = run_dirs(tmp_path)
    result = invoke("data", "quality", "--as-of", "2024-03-01")
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    assert "Quality report for 2024-03-01" in result.output  # type: ignore[attr-defined]
    assert run_dirs(tmp_path) == before
    assert not Lake(tmp_path / "data").has_table("data_quality_issues")


def test_quality_persist(world: World, tmp_path: Path) -> None:
    invoke("data", "sync", "--from", START.isoformat(), "--datasets", "universe,prices")
    result = invoke("data", "quality", "--as-of", "2024-03-01", "--persist")
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    assert Lake(tmp_path / "data").has_table("data_quality_issues")
    assert len(run_dirs(tmp_path)) == 2  # the sync's and the persisting quality run's


def test_bad_config_exit_1(world: World, tmp_path: Path) -> None:
    (tmp_path / "config" / "fund.yaml").write_text("data:\n  sync:\n    news_window_days: 0\n")
    result = invoke("data", "status")
    assert result.exit_code == 1  # type: ignore[attr-defined]
    assert "news_window_days" in result.output  # type: ignore[attr-defined]


def test_entry_point_declared() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert project["project"]["scripts"] == {"atlas": "atlas.cli.app:main"}


def test_summary_not_truncated_without_terminal(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)  # it makes Rich act as a terminal
    result = CliRunner().invoke(
        app, ["data", "sync", "--from", START.isoformat(), "--datasets", "universe,prices"],
        env={"NO_COLOR": "1"},
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    header = next(line for line in result.output.splitlines() if "step" in line and "keys" in line)
    assert "skipped" in header and "seconds" in header
    assert "…" not in header
