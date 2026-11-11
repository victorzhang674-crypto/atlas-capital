"""`atlas risk-model build` (Phase 2 checkpoint, Tier 1; Stage 19 code): stores the daily
factor returns and the month-end model snapshots. The build itself is `build_and_store`
(tested in `tests/unit/quant/risk_model` and end to end in `tests/integration`); these tests
pin what the command passes to it and what it reports."""

import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from factor_world import build_factor_world
from typer.testing import CliRunner

import atlas.cli.risk_model_cmd as risk_model_cmd
from atlas.cli.app import app
from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.quant.risk_model.factor_model import FactorHistory, FactorSet
from atlas.quant.risk_model.store import RiskModelStore

BASE_ENV = {"COLUMNS": "200", "NO_COLOR": "1"}
SETTINGS = {"fund": {"benchmark": "AAA"}, "quant": {"factors": {"min_names": 5}}}
CFG = AtlasConfig.model_validate(SETTINGS)


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    world = Lake(tmp_path_factory.mktemp("cli_risk") / "lake", registry=DEFAULT_REGISTRY.copy())
    build_factor_world(world)  # bars to 2024-02-29
    return world


def invoke(lake: Lake, tmp_path: Path, *args: str) -> object:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "fund.yaml").write_text(yaml.safe_dump(SETTINGS))
    env = {**BASE_ENV, "ATLAS__DATA__LAKE_PATH": str(lake.root)}
    return CliRunner().invoke(app, ["--config-dir", str(config_dir), "risk-model", *args], env=env)


def _history(start: date, end: date, skipped: tuple[date, ...] = ()) -> FactorHistory:
    days = pd.DatetimeIndex(pd.to_datetime(list(TradingCalendar().sessions(start, end))))
    factors = FactorSet.from_config(CFG.quant.risk_model.factor_model)
    rng = np.random.default_rng(0)
    return FactorHistory(
        factors=factors,
        factor_returns=pd.DataFrame(
            rng.normal(scale=0.01, size=(len(days), len(factors.names))),
            index=days,
            columns=list(factors.names),
        ),
        residuals=pd.DataFrame(index=days),
        r2=pd.Series(0.4, index=days),
        n_names=pd.Series(12, index=days),
        skipped=skipped,
    )


def _capture(monkeypatch: pytest.MonkeyPatch, skipped: tuple[date, ...] = ()) -> dict[str, object]:
    seen: dict[str, object] = {}

    def fake(
        source: Lake,
        store: RiskModelStore,
        start: date,
        end: date,
        config: AtlasConfig,
        **kw: object,
    ) -> tuple[FactorHistory, tuple[date, ...]]:
        seen.update(lake=source, store=store, start=start, end=end, config=config, **kw)
        return _history(start, end, skipped), (date(2024, 1, 31), end)

    monkeypatch.setattr(risk_model_cmd, "build_and_store", fake)
    return seen


def test_help() -> None:
    result = CliRunner().invoke(app, ["risk-model", "build", "--help"], env=BASE_ENV)
    text = _plain(result.output)
    assert result.exit_code == 0
    for option in ("--from", "--to", "--store", "--etfs"):
        assert option in text


def test_build_passes_the_window_clipped_to_the_data(
    lake: Lake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The world's last bar is 2024-02-29, so that is the last session regressed."""
    seen = _capture(monkeypatch, skipped=(date(2024, 1, 3),))
    store_root = tmp_path / "store"
    result = invoke(lake, tmp_path, "build", "--from", "2023-12-01", "--to", "2024",
                    "--store", str(store_root))  # fmt: skip
    assert result.exit_code == 0, result.output
    assert (seen["start"], seen["end"]) == (date(2023, 12, 1), date(2024, 2, 29))
    assert isinstance(seen["lake"], Lake) and seen["lake"].root == lake.root
    store = seen["store"]
    assert isinstance(store, RiskModelStore) and store.lake.root == store_root
    text = _plain(result.output)
    assert "61 sessions" in text and "1 skipped" in text and "mean R2 0.400" in text
    assert "2 snapshots" in text and "2024-01-31" in text and "2024-02-29" in text


def test_to_is_the_last_session_regressed_when_the_lake_has_it(
    lake: Lake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--to 2024-01-31` regresses 2024-01-31 itself (its bar is in the lake), so a month-end
    given as `--to` gets its snapshot; a non-session `--to` ends on the session before it."""
    seen = _capture(monkeypatch)
    result = invoke(lake, tmp_path, "build", "--from", "2024-01-02", "--to", "2024-01-31")
    assert result.exit_code == 0, result.output
    assert seen["end"] == date(2024, 1, 31)
    result = invoke(lake, tmp_path, "build", "--from", "2024-01-02", "--to", "2024-02-03")
    assert result.exit_code == 0, result.output
    assert seen["end"] == date(2024, 2, 2)  # 2024-02-03 is a Saturday


def test_etfs_default_to_the_configured_universe_and_can_be_left_out(
    lake: Lake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _capture(monkeypatch)
    result = invoke(lake, tmp_path, "build", "--from", "2024-01-02", "--to", "2024-02-29")
    assert result.exit_code == 0, result.output
    etfs = seen["etfs"]
    assert isinstance(etfs, list | tuple) and "SPY" in etfs and "TLT" in etfs
    assert isinstance(seen["store"], RiskModelStore) and seen["store"].lake.root == lake.root
    result = invoke(lake, tmp_path, "build", "--from", "2024-01-02", "--to", "2024-02-29",
                    "--no-etfs")  # fmt: skip
    assert result.exit_code == 0, result.output
    assert seen["etfs"] is None


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


def test_a_failed_build_exits_1(
    lake: Lake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> object:
        raise DataError("no names with factor exposures in the window")

    monkeypatch.setattr(risk_model_cmd, "build_and_store", boom)
    result = invoke(lake, tmp_path, "build", "--from", "2024-01-02", "--to", "2024-02-29")
    assert result.exit_code == 1
    assert "Risk model build failed" in _plain(result.output)
