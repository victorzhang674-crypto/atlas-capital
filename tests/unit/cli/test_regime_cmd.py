"""Stage 21 gap fix: `atlas regime build` persists the daily regime history."""

import re
from datetime import date
from pathlib import Path

import pytest
from regime_world import RegimeWorld, build_regime_world, world_config
from typer.testing import CliRunner

from atlas.cli.app import app
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.quant.regime import RegimeStore, regime_history

BASE_ENV = {"COLUMNS": "200", "NO_COLOR": "1"}


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> RegimeWorld:
    root = tmp_path_factory.mktemp("cli_regime") / "lake"
    return build_regime_world(Lake(root, registry=DEFAULT_REGISTRY.copy()))


def _config_dir(tmp_path: Path) -> Path:
    """A config directory whose fund.yaml carries the test world's regime settings."""
    import yaml

    d = tmp_path / "config"
    d.mkdir()
    regime = world_config().quant.regime.model_dump(mode="json")
    (d / "fund.yaml").write_text(yaml.safe_dump({"quant": {"regime": regime}}))
    return d


def invoke(world: RegimeWorld, tmp_path: Path, *args: str) -> object:
    env = {**BASE_ENV, "ATLAS__DATA__LAKE_PATH": str(world.lake.root)}
    return CliRunner().invoke(
        app, ["--config-dir", str(_config_dir(tmp_path)), "regime", *args], env=env
    )


def test_help() -> None:
    result = CliRunner().invoke(app, ["regime", "build", "--help"], env=BASE_ENV)
    text = _plain(result.output)
    assert result.exit_code == 0
    for option in ("--from", "--to", "--store"):
        assert option in text


def test_build_persists_the_history(world: RegimeWorld, tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    result = invoke(world, tmp_path, "build", "--from", "2020-03-02", "--to", "2020-03-13",
                    "--store", str(store_root))  # fmt: skip
    assert result.exit_code == 0, result.output
    text = _plain(result.output)
    assert "10 decision days" in text and "CRISIS" in text
    store = RegimeStore(Lake(store_root), world.config)
    expected = regime_history(world.lake, date(2020, 3, 2), date(2020, 3, 13), world.config)
    assert store.read(date(2020, 3, 2), date(2020, 3, 13)) == expected


def test_default_store_is_the_data_lake(tmp_path: Path) -> None:
    w = build_regime_world(Lake(tmp_path / "lake", registry=DEFAULT_REGISTRY.copy()))
    result = invoke(w, tmp_path, "build", "--from", "2020-03-02", "--to", "2020-03-06")
    assert result.exit_code == 0, result.output
    assert RegimeStore(Lake(w.lake.root), w.config).dates()[-1] == date(2020, 3, 6)


def test_end_is_clipped_to_the_data(world: RegimeWorld, tmp_path: Path) -> None:
    """The world's last bar is 2020-06-30: `--to 2020` ends on the decision of 2020-07-01."""
    store_root = tmp_path / "store"
    result = invoke(world, tmp_path, "build", "--from", "2020-06-22", "--to", "2020",
                    "--store", str(store_root))  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "ends 2020-07-01" in _plain(result.output)
    assert RegimeStore(Lake(store_root), world.config).dates()[-1] == date(2020, 7, 1)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--from", "2020-03-10", "--to", "2020-03-01"], "after"),
        (["--from", "March", "--to", "2020"], "not a year"),
    ],
)
def test_bad_arguments(world: RegimeWorld, tmp_path: Path, args: list[str], message: str) -> None:
    result = invoke(world, tmp_path, "build", *args)
    assert result.exit_code == 2
    assert message in _plain(result.output)


def test_a_failed_build_exits_1(world: RegimeWorld, tmp_path: Path) -> None:
    result = invoke(world, tmp_path, "build", "--from", "2017-02-01", "--to", "2017-02-10",
                    "--store", str(tmp_path / "s"))  # fmt: skip
    assert result.exit_code == 1
    assert "failed" in _plain(result.output).lower()
