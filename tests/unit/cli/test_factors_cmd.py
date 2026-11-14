"""`atlas factors build` (Phase 2 checkpoint, Tier 1; Stage 16 code): fills the lake's
`factor_exposures` cache on month-ends."""

import re
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml
from factor_world import build_factor_world
from typer.testing import CliRunner

import atlas.cli.factors_cmd as factors_cmd
from atlas.cli.app import app
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.factors import FactorStore, factor_panel

BASE_ENV = {"COLUMNS": "200", "NO_COLOR": "1"}
SETTINGS = {"fund": {"benchmark": "AAA"}, "quant": {"factors": {"min_names": 5}}}
CFG = AtlasConfig.model_validate(SETTINGS)
JAN, FEB = date(2024, 1, 31), date(2024, 2, 29)


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    """The Stage 16 synthetic world: 12 companies, bars to 2024-02-29."""
    world = Lake(tmp_path_factory.mktemp("cli_factors") / "lake", registry=DEFAULT_REGISTRY.copy())
    build_factor_world(world)
    return world


def invoke(lake: Lake, tmp_path: Path, *args: str) -> object:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "fund.yaml").write_text(yaml.safe_dump(SETTINGS))
    env = {**BASE_ENV, "ATLAS__DATA__LAKE_PATH": str(lake.root)}
    return CliRunner().invoke(app, ["--config-dir", str(config_dir), "factors", *args], env=env)


def test_help() -> None:
    result = CliRunner().invoke(app, ["factors", "build", "--help"], env=BASE_ENV)
    text = _plain(result.output)
    assert result.exit_code == 0
    for option in ("--from", "--to", "--store", "--refresh"):
        assert option in text


def test_build_fills_the_cache_and_skips_stored_dates(lake: Lake, tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    args = ("build", "--from", "2024-01-02", "--to", "2024-02-29", "--store", str(store_root))
    result = invoke(lake, tmp_path, *args)
    assert result.exit_code == 0, result.output
    text = _plain(result.output)
    assert "2 month-ends" in text and "2 written" in text and "0 already stored" in text
    # a store on another lake is read with the lake its panels were computed from: the
    # fingerprint of that data is checked on every read
    store = FactorStore(Lake(store_root), CFG, data_lake=lake)
    assert store.dates() == (JAN, FEB)
    got = store.read(FEB)
    want = factor_panel(DataView(lake, FEB, CFG))
    assert got is not None
    pd.testing.assert_frame_equal(got.exposures, want.exposures, check_names=False)
    assert not lake.has_table("factor_exposures")  # the data lake itself was only read
    again = invoke(lake, tmp_path, *args)
    assert again.exit_code == 0, again.output
    assert "0 written" in _plain(again.output) and "2 already stored" in _plain(again.output)


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    def fake(source: Lake, dates: object, **kwargs: object) -> tuple[date, ...]:
        seen.update(lake=source, dates=tuple(dates), **kwargs)  # type: ignore[call-overload]
        return ()

    monkeypatch.setattr(factors_cmd, "build_factor_cache", fake)
    return seen


def test_end_is_clipped_to_the_data(
    lake: Lake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The world's last bar is 2024-02-29: `--to 2024` stops at February's month-end."""
    seen = _capture(monkeypatch)
    result = invoke(lake, tmp_path, "build", "--from", "2023-11-01", "--to", "2024")
    assert result.exit_code == 0, result.output
    assert seen["dates"] == (date(2023, 11, 30), date(2023, 12, 29), JAN, FEB)
    assert seen["refresh"] is False


def test_default_store_is_the_data_lake_and_refresh_is_passed(
    lake: Lake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _capture(monkeypatch)
    result = invoke(lake, tmp_path, "build", "--from", "2024-01-02", "--to", "2024-01-31",
                    "--refresh")  # fmt: skip
    assert result.exit_code == 0, result.output
    assert seen["dates"] == (JAN,) and seen["refresh"] is True
    store = seen["store"]
    assert isinstance(store, FactorStore) and store.lake.root == lake.root


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--from", "2024-03-10", "--to", "2024-03-01"], "after"),
        (["--from", "March", "--to", "2024"], "not a year"),
    ],
)
def test_bad_arguments(lake: Lake, tmp_path: Path, args: list[str], message: str) -> None:
    result = invoke(lake, tmp_path, "build", *args)
    assert result.exit_code == 2
    assert message in _plain(result.output)


def test_a_window_without_a_month_end_exits_1(lake: Lake, tmp_path: Path) -> None:
    result = invoke(lake, tmp_path, "build", "--from", "2024-02-01", "--to", "2024-02-15")
    assert result.exit_code == 1
    assert "no month-end" in _plain(result.output)


def test_a_failed_build_exits_1(
    lake: Lake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> tuple[date, ...]:
        raise DataError("no usable names")

    monkeypatch.setattr(factors_cmd, "build_factor_cache", boom)
    result = invoke(lake, tmp_path, "build", "--from", "2024-01-02", "--to", "2024-02-29")
    assert result.exit_code == 1
    assert "Factor build failed" in _plain(result.output) and "no usable names" in result.output
