"""Stage 29 Task 5: `atlas data slice --symbols … --as-of … --out DIR` through Typer's CliRunner
on the Stage 16 factor world (read-only on the lake; exit 0 / 1 / 2)."""

import json
import shutil
from pathlib import Path

import pytest
from factor_world import build_factor_world
from typer.testing import CliRunner

from atlas.cli.app import app
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.slice import MANIFEST

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def lake_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    lake = Lake(tmp_path / "lake", registry=DEFAULT_REGISTRY.copy())
    build_factor_world(lake)
    return {"COLUMNS": "200", "NO_COLOR": "1", "ATLAS__DATA__LAKE_PATH": str(tmp_path / "lake")}


def test_slice_writes_a_lake_and_prints_rows(lake_env: dict[str, str], tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        [
            "data",
            "slice",
            "--symbols",
            "AAA,bbb",
            "--as-of",
            "2024-03-01",
            "--out",
            str(out),
            "--since",
            "2023-01-01",
        ],
        env=lake_env,
    )
    assert result.exit_code == 0, result.output
    manifest = json.loads((out / MANIFEST).read_text())
    assert manifest["symbols"] == ["AAA", "BBB"] and manifest["since"] == "2023-01-01"
    assert "prices_daily" in result.output and "fundamentals_facts" in result.output
    assert "Slice of 2 symbol(s) at 2024-03-01" in result.output
    assert set(Lake(out).read_table("prices_daily")["symbol"]) == {"AAA", "BBB"}


def test_slice_tables_option(lake_env: dict[str, str], tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        [
            "data",
            "slice",
            "--symbols",
            "AAA",
            "--as-of",
            "2024-03-01",
            "--out",
            str(out),
            "--tables",
            "instruments",
        ],
        env=lake_env,
    )
    assert result.exit_code == 0, result.output
    assert json.loads((out / MANIFEST).read_text())["tables"] == {"instruments": 1}


@pytest.mark.parametrize(
    "args",
    [
        ["--symbols", "AAA", "--out", "x"],  # no --as-of
        ["--symbols", "AAA", "--as-of", "2024-13-01", "--out", "x"],
        ["--symbols", " , ", "--as-of", "2024-03-01", "--out", "x"],
        ["--symbols", "AAA", "--as-of", "2024-03-01"],  # no --out
    ],
)
def test_bad_arguments_exit_2(lake_env: dict[str, str], args: list[str]) -> None:
    assert CliRunner().invoke(app, ["data", "slice", *args], env=lake_env).exit_code == 2


def test_a_used_folder_or_unknown_table_exits_1(lake_env: dict[str, str], tmp_path: Path) -> None:
    used = tmp_path / "used"
    used.mkdir()
    (used / "file").write_text("x")
    base = ["data", "slice", "--symbols", "AAA", "--as-of", "2024-03-01"]
    result = CliRunner().invoke(app, [*base, "--out", str(used)], env=lake_env)
    assert result.exit_code == 1 and "not empty" in result.output
    result = CliRunner().invoke(
        app, [*base, "--out", str(tmp_path / "n"), "--tables", "nope"], env=lake_env
    )
    assert result.exit_code == 1 and "unknown table" in result.output


def test_help_lists_slice_and_its_options() -> None:
    env = {"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}
    data_help = CliRunner().invoke(app, ["data", "--help"], env=env)
    assert "slice" in data_help.output
    help_text = CliRunner().invoke(app, ["data", "slice", "--help"], env=env).output
    for option in ("--symbols", "--as-of", "--out", "--since", "--tables"):
        assert option in help_text
