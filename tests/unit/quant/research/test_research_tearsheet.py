"""Stage 17 Task 9 (brief task 5, part 1): the `SignalTearSheet`."""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from research_world import CAL, ResearchWorld, build_research_world

from atlas.core.config import AtlasConfig
from atlas.core.errors import LookAheadError
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.quant.research import (
    FIGURES,
    ForwardReturns,
    SignalTearSheet,
    build_tearsheet,
    forward_returns,
    ic_series,
    quantile_analysis,
    research_signal,
    vector_backtest,
)

CFG = AtlasConfig(quant={"research": {"horizons": [1, 5], "min_names": 15, "min_group_names": 5}})


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> ResearchWorld:
    lake = Lake(tmp_path_factory.mktemp("sheet") / "lake", registry=DEFAULT_REGISTRY.copy())
    return build_research_world(lake, n_names=60, n_sessions=140, rho=0.1, seed=19)


@pytest.fixture(scope="module")
def inputs(world: ResearchWorld) -> tuple[pd.DataFrame, ForwardReturns]:
    dates = list(world.sessions[1:-6:2])
    labels = forward_returns(world.factory, dates, list(world.symbols), config=CFG)
    signal = world.signal.loc[pd.DatetimeIndex(pd.to_datetime(dates))]
    return signal, labels


@pytest.fixture(scope="module")
def sheet(world: ResearchWorld, inputs: tuple[pd.DataFrame, ForwardReturns]) -> SignalTearSheet:
    signal, labels = inputs
    sectors = pd.Series(dict(world.sectors), dtype="object")
    return build_tearsheet("planted", signal, labels, sectors=sectors, config=CFG)


def test_build_tearsheet_summary_sections(
    sheet: SignalTearSheet, inputs: tuple[pd.DataFrame, ForwardReturns]
) -> None:
    signal, labels = inputs
    s = sheet.summary
    assert {
        "signal", "kind", "start", "end", "n_dates", "n_symbols", "headline_horizon", "labels",
        "ic", "decay", "quantiles", "by_sector", "by_regime", "backtest", "settings",
    } <= set(s)  # fmt: skip
    assert s["signal"] == "planted" and s["n_dates"] == len(signal)
    assert s["start"] == str(signal.index[0].date()) and s["end"] == str(signal.index[-1].date())
    assert s["labels"]["horizons"] == [1, 5] and s["labels"]["blocked"] == []
    assert s["headline_horizon"] == 1  # dates two sessions apart: the largest horizon <= 2
    json.loads(json.dumps(s, allow_nan=False))  # strict JSON: NaN written as null
    for h in (1, 5):
        for method in ("pearson", "spearman"):
            want = ic_series(signal, labels.horizon(h), method=method, config=CFG).mean()
            assert s["ic"][f"h{h}"][method]["mean"] == pytest.approx(want)
        qa = quantile_analysis(signal, labels, h, config=CFG)
        assert s["quantiles"][f"h{h}"]["spread_mean"] == pytest.approx(qa.spread_mean)
        assert s["quantiles"][f"h{h}"]["monotonicity"] == pytest.approx(qa.monotonicity)
        assert list(s["quantiles"][f"h{h}"]["mean_by_bucket"]) == ["1", "2", "3", "4", "5"]
    assert s["ic"]["h1"]["pearson"]["mean"] == pytest.approx(0.1, abs=0.02)  # planted 0.1
    end = pd.Timestamp(CAL.offset(signal.index[-1].date(), 2))
    bt = vector_backtest(signal, labels.daily.loc[:end], config=CFG)
    assert s["backtest"]["sharpe"] == pytest.approx(bt.summary["sharpe"])
    assert s["decay"]["ic_by_horizon"]["spearman"]["1"] == pytest.approx(
        s["ic"]["h1"]["spearman"]["mean"]
    )
    ac = s["decay"]["rank_autocorrelation"]
    assert list(ac) == ["1", "2", "3", "6", "12"]
    assert s["decay"]["turnover_proxy"] == pytest.approx(1 - ac["1"])
    sectors = s["by_sector"]["h1"]
    assert set(sectors["groups"]) == {"Energy", "Health Care", "Information Technology"}
    assert s["by_sector"]["names_without_sector"] == 15
    assert s["settings"]["n_quantiles"] == 5
    # the last book is held for one evaluation period (2 sessions), not to the labels' end
    last = signal.index[-1].date()
    assert sheet.backtest.returns.index[-1] == pd.Timestamp(CAL.offset(last, 2))
    assert s["backtest_end"] == str(CAL.offset(last, 2))


def test_tearsheet_regime_not_available_without_regimes(
    sheet: SignalTearSheet, inputs: tuple[pd.DataFrame, ForwardReturns]
) -> None:
    assert sheet.summary["by_regime"]["available"] is False
    signal, labels = inputs
    regimes = pd.Series(
        np.where(np.arange(len(signal)) % 2 == 0, "calm", "stress"), index=signal.index
    )
    with_regimes = build_tearsheet("planted", signal, labels, regimes=regimes, config=CFG)
    by_regime = with_regimes.summary["by_regime"]
    assert by_regime["available"] is True
    assert set(by_regime["h1"]) == {"calm", "stress"}
    assert (
        by_regime["h1"]["calm"]["n_dates"] + by_regime["h1"]["stress"]["n_dates"]
        == (with_regimes.summary["ic"]["h1"]["spearman"]["n_dates"])
    )


def test_tearsheet_save_writes_json_pngs_csvs(sheet: SignalTearSheet, tmp_path: Path) -> None:
    out = tmp_path / "out" / "planted"
    written = sheet.save(out)
    names = {p.name for p in written}
    assert {f"{f}.png" for f in FIGURES} <= names
    assert {
        "tearsheet.json",
        "ic_series.csv",
        "quantile_returns.csv",
        "backtest_returns.csv",
    } <= names
    for figure in FIGURES:
        assert (out / f"{figure}.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert json.loads((out / "tearsheet.json").read_text()) == json.loads(
        json.dumps(sheet.summary, allow_nan=False)
    )
    ic = pd.read_csv(out / "ic_series.csv", index_col="date", parse_dates=True)
    assert list(ic.columns) == ["h1_pearson", "h1_spearman", "h5_pearson", "h5_spearman"]
    bt = pd.read_csv(out / "backtest_returns.csv", index_col="date", parse_dates=True)
    assert list(bt.columns) == ["gross_return", "cost", "net_return", "cumulative"]
    assert bt["cumulative"].iloc[-1] == pytest.approx(sheet.summary["backtest"]["total_return"])
    assert sheet.to_dict() == sheet.summary and sheet.to_dict() is not sheet.summary


def test_research_signal_end_to_end(world: ResearchWorld) -> None:
    calls: list[object] = []
    sheet = research_signal(
        "reversal_5d",
        world.factory,
        world.day(75),
        world.day(130),
        frequency="weekly",
        config=CFG,
        progress=calls.append,
    )
    s = sheet.summary
    assert s["signal"] == "reversal_5d" and s["kind"] == "technical" and s["frequency"] == "weekly"
    assert len(calls) == s["n_dates"] > 5
    assert s["universe"] == "sp500"
    assert set(s["ic"]) == {"h1", "h5"} and set(s["quantiles"]) == {"h1", "h5"}
    assert s["headline_horizon"] == 5
    assert s["backtest"]["n_rebalances"] >= 2
    assert not math.isnan(s["ic"]["h1"]["spearman"]["mean"])
    assert s["by_sector"]["names_without_sector"] == 15


def test_build_tearsheet_rejects_labels(inputs: tuple[pd.DataFrame, ForwardReturns]) -> None:
    _, labels = inputs
    with pytest.raises(LookAheadError, match="forward-return label"):
        build_tearsheet("leak", labels.horizon(1), labels, config=CFG)


def test_unrealised_horizon_is_null_in_json(
    world: ResearchWorld, inputs: tuple[pd.DataFrame, ForwardReturns], tmp_path: Path
) -> None:
    # a horizon longer than the lake: no label is realised, every statistic is NaN -> null
    signal, _ = inputs
    cfg = AtlasConfig(quant={"research": {"horizons": [1, 200], "min_names": 15}})
    dates = [d.date() for d in signal.index]
    labels = forward_returns(world.factory, dates, list(world.symbols), config=cfg)
    sheet = build_tearsheet("planted", signal, labels, config=cfg)
    assert sheet.summary["ic"]["h200"]["pearson"]["mean"] is None
    assert sheet.summary["quantiles"]["h200"]["spread_mean"] is None
    json.loads(json.dumps(sheet.summary, allow_nan=False))
    sheet.save(tmp_path / "null")
    assert (
        json.loads((tmp_path / "null" / "tearsheet.json").read_text())["ic"]["h200"]["spearman"][
            "mean"
        ]
        is None
    )
