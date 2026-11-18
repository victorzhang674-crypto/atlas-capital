"""`atlas llm usage` (Phase 3 checkpoint, Tier 3 item 2): spend per day, run, agent and
model from the lake's usage ledger (`llm_usage`)."""

import re
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from atlas.cli.app import app
from atlas.core.storage import Database, Lake
from atlas.llm.cost import CostLedger, UsageRecord

BASE_ENV = {"COLUMNS": "200", "NO_COLOR": "1"}


def _plain(text: str) -> str:
    """Output without colour codes and without the table's box-drawing characters."""
    return re.sub(r"[│┃┌┐└┘├┤┬┴┼─━┏┓┗┛┡┩╇]", " ", re.sub(r"\x1b\[[0-9;]*m", "", text))


def _row(text: str, first: str) -> list[str]:
    """The cells of the table line whose first cell is `first`."""
    for line in _plain(text).splitlines():
        cells = line.split()
        if cells and cells[0] == first:
            return cells
    raise AssertionError(f"no line starts with {first!r}:\n{text}")


def _record(call_id: str, day: date, run_id: str | None, agent: str | None, cost: float):  # type: ignore[no-untyped-def]
    return UsageRecord(
        call_id=call_id,
        ts=datetime(day.year, day.month, day.day, 9, tzinfo=UTC),
        day=day,
        run_id=run_id,
        agent=agent,
        tier="quick",
        provider="openai_compatible",
        model="deepseek-flash",
        input_tokens=1000,
        output_tokens=200,
        cost_usd=cost,
        latency_ms=1.0,
    )


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A lake whose ledger has three paid calls over two days and two runs."""
    lake = Lake(tmp_path / "lake")
    with Database(lake) as database:
        ledger = CostLedger(database)
        ledger.record(_record("u1", date(2026, 11, 16), "01RUNA", "news", 0.0125))
        ledger.record(_record("u2", date(2026, 11, 16), "01RUNA", "technical", 0.25))
        ledger.record(_record("u3", date(2026, 11, 17), "01RUNB", "news", 0.5))
    return lake.root


def invoke(*args: str):  # type: ignore[no-untyped-def]
    return CliRunner().invoke(app, ["llm", "usage", *args], env=BASE_ENV)


def test_llm_help_lists_usage() -> None:
    result = CliRunner().invoke(app, ["llm", "--help"], env=BASE_ENV)
    assert result.exit_code == 0
    assert "usage" in _plain(result.output)


def test_usage_shows_the_total_and_spend_per_day_run_agent_and_model(store: Path) -> None:
    result = invoke("--store", str(store))
    assert result.exit_code == 0, result.output
    text = _plain(result.output)
    for title in ("per day", "per run", "per agent", "per model"):
        assert title in text
    assert _row(result.output, "total") == ["total", "3", "3", "0", "3,000", "600", "0.7625"]
    assert _row(result.output, "2026-11-16")[-1] == "0.2625"
    assert _row(result.output, "01RUNB")[-1] == "0.5000"
    assert _row(result.output, "news")[1:3] == ["2", "2"]
    assert _row(result.output, "openai_compatible/deepseek-flash")[-1] == "0.7625"


def test_usage_filters_by_run_and_day(store: Path) -> None:
    result = invoke("--store", str(store), "--run", "01RUNA", "--by", "agent")
    assert result.exit_code == 0, result.output
    text = _plain(result.output)
    assert "run 01RUNA" in text
    assert _row(result.output, "total")[-1] == "0.2625"
    assert "per agent" in text and "per day" not in text and "per model" not in text
    result = invoke("--store", str(store), "--day", "2026-11-17", "--by", "run", "--by", "model")
    assert result.exit_code == 0, result.output
    assert _row(result.output, "total")[-1] == "0.5000"
    assert "per run" in _plain(result.output) and "per model" in _plain(result.output)


def test_usage_with_nothing_recorded_says_so(store: Path) -> None:
    result = invoke("--store", str(store), "--run", "01NOSUCH")
    assert result.exit_code == 0, result.output
    assert "No LLM usage recorded" in _plain(result.output)
    assert "run 01NOSUCH" in _plain(result.output)


def test_usage_without_a_database_exits_1(tmp_path: Path) -> None:
    result = invoke("--store", str(tmp_path / "empty"))
    assert result.exit_code == 1
    assert "No LLM usage ledger" in _plain(result.output)


def _old_lake(tmp_path: Path) -> Path:
    """A lake whose database predates Stages 24 and 25: no `llm_usage`, no `llm_cache` (the main
    lake on 2026-11-18)."""
    root = tmp_path / "old"
    root.mkdir()
    with duckdb.connect(str(root / "atlas.duckdb")) as con:
        con.execute("CREATE TABLE macro_series (x INTEGER)")
    return root


def test_usage_on_a_lake_without_the_ledger_table_exits_1(tmp_path: Path) -> None:
    result = invoke("--store", str(_old_lake(tmp_path)))
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "No LLM usage ledger" in _plain(result.output)
    assert "llm_usage" in _plain(result.output)


@pytest.mark.parametrize(
    "args", [("stats",), ("export", "out.jsonl"), ("prune", "--older-than", "30d", "--dry-run")]
)
def test_cache_reads_on_a_lake_without_the_cache_table_exit_1(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    """Found with `atlas llm usage` on the main lake: the read-only cache commands raised
    DuckDB's CatalogException there (Stage 25)."""
    old = _old_lake(tmp_path)
    argv = [a if a != "out.jsonl" else str(tmp_path / a) for a in args]
    result = CliRunner().invoke(app, ["llm", "cache", *argv, "--store", str(old)], env=BASE_ENV)
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "No LLM cache" in _plain(result.output)
    assert not (tmp_path / "out.jsonl").exists()


def test_usage_with_an_unreadable_database_exits_1(tmp_path: Path) -> None:
    root = tmp_path / "broken"
    root.mkdir()
    (root / "atlas.duckdb").write_bytes(b"not a duckdb file" * 100)
    result = invoke("--store", str(root))
    assert result.exit_code == 1
    assert "Cannot open the usage ledger" in _plain(result.output)


@pytest.mark.parametrize(
    ("args", "hint"), [(("--day", "17/11/2026"), "--day"), (("--by", "sector"), "--by")]
)
def test_usage_bad_arguments_exit_2(store: Path, args: tuple[str, ...], hint: str) -> None:
    result = invoke("--store", str(store), *args)
    assert result.exit_code == 2
    assert hint in _plain(result.output)
