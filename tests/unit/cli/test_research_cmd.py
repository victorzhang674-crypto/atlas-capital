"""Stage 17 Task 10 (brief task 5, part 2): `atlas research signal`."""

import json
import re
from datetime import date
from pathlib import Path

import pytest
import typer
from research_world import ResearchWorld, build_research_world
from typer.testing import CliRunner

from atlas.cli.app import app
from atlas.cli.research_cmd import parse_bound
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.quant.research import FIGURES

BASE_ENV = {"COLUMNS": "200", "NO_COLOR": "1"}


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> ResearchWorld:
    root = tmp_path_factory.mktemp("cli") / "lake"
    return build_research_world(Lake(root, registry=DEFAULT_REGISTRY.copy()))


def _env(lake_root: Path) -> dict[str, str]:
    return {**BASE_ENV, "ATLAS__DATA__LAKE_PATH": str(lake_root)}


def invoke(lake_root: Path, *args: str) -> object:
    return CliRunner().invoke(app, ["research", *args], env=_env(lake_root))


def test_research_signal_help() -> None:
    result = CliRunner().invoke(app, ["research", "--help"], env=BASE_ENV)
    assert result.exit_code == 0 and "signal" in _plain(result.output)
    result = CliRunner().invoke(app, ["research", "signal", "--help"], env=BASE_ENV)
    text = _plain(result.output)
    assert result.exit_code == 0
    for option in ("--name", "--from", "--to", "--frequency", "--universe", "--out"):
        assert option in text


def test_year_bounds_parse() -> None:
    assert parse_bound("2016") == date(2016, 1, 1)
    assert parse_bound("2021", end=True) == date(2021, 12, 31)
    assert parse_bound("2019-03-04") == date(2019, 3, 4)
    assert parse_bound("2019-03-04", end=True) == date(2019, 3, 4)
    for bad in ("16", "2019-13-01", "March"):
        with pytest.raises(typer.BadParameter):
            parse_bound(bad)


def test_research_signal_writes_tearsheet_to_lake_runs(world: ResearchWorld) -> None:
    root = world.lake.root
    args = [
        "signal",
        "--name",
        "reversal_5d",
        "--from",
        str(world.day(75)),
        "--to",
        str(world.day(110)),
    ]
    result = invoke(root, *args, "--frequency", "weekly")
    assert result.exit_code == 0, result.output
    out = root / "runs" / "research" / "reversal_5d"
    for figure in FIGURES:
        assert (out / f"{figure}.png").is_file()
    summary = json.loads((out / "tearsheet.json").read_text())
    assert summary["signal"] == "reversal_5d" and summary["frequency"] == "weekly"
    assert summary["labels"]["horizons"] == [1, 5, 21, 63]
    run = summary["run"]  # audit finding A: the command runs inside a run context
    assert len(run["run_id"]) == 26 and run["stage"] == "research.signal"
    assert run["mode"] == "dev" and run["config_fingerprint"]
    text = _plain(result.output)
    assert "IC spearman" in text and "Long-short backtest" in text and str(out) in text
    assert f"Run {run['run_id']}" in text
    log = [json.loads(line) for line in (out / "research.log").read_text().splitlines()]
    evaluated = [r for r in log if r["event"] == "research.signal_evaluated"]
    assert len(evaluated) == 1 and evaluated[0]["run_id"] == run["run_id"]
    assert all(r["run_id"] == run["run_id"] for r in log)


def test_research_signal_out_option(world: ResearchWorld, tmp_path: Path) -> None:
    out = tmp_path / "sheet"
    args = ["signal", "--name", "rsi_14", "--from", str(world.day(80)), "--to", str(world.day(100))]
    result = invoke(world.lake.root, *args, "--frequency", "daily", "--out", str(out))
    assert result.exit_code == 0, result.output
    assert json.loads((out / "tearsheet.json").read_text())["n_dates"] == 21


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--name", "nope", "--from", "2024", "--to", "2024"], "unknown signal"),
        (["--name", "reversal_5d", "--from", "2024-02-01", "--to", "2024-01-01"], "after"),
        (["--name", "reversal_5d", "--from", "2024-01-06", "--to", "2024-01-07"], "no trading"),
        (
            ["--name", "reversal_5d", "--from", "2024", "--to", "2024", "--frequency", "hourly"],
            "daily",
        ),
        (["--name", "reversal_5d", "--from", "24", "--to", "2024"], "YYYY"),
    ],
    ids=["bad-name", "from-after-to", "no-dates", "bad-frequency", "bad-date"],
)
def test_research_signal_bad_arguments_exit_2(
    world: ResearchWorld, args: list[str], message: str
) -> None:
    result = invoke(world.lake.root, "signal", *args)
    assert result.exit_code == 2
    assert message in _plain(result.output)


def test_research_signal_empty_lake_exit_1(tmp_path: Path) -> None:
    result = invoke(
        tmp_path / "empty", "signal", "--name", "reversal_5d", "--from", "2024-01", "--to", "2024"
    )
    assert result.exit_code == 2  # "2024-01" is not a date
    result = invoke(
        tmp_path / "empty",
        "signal",
        "--name",
        "reversal_5d",
        "--from",
        "2024-01-02",
        "--to",
        "2024-01-31",
    )
    assert result.exit_code == 1
    assert "Research failed" in _plain(result.output)
