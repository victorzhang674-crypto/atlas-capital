"""Stage 22 Task 3: time-series momentum on synthetic trending ETFs."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.alpha import AlphaModel, TSMOMModel, check_alpha_frame, direction, trend_blend
from atlas.quant.features import compute_features
from tiny_universe import bars_from_returns

CAL = TradingCalendar()
LAST = date(2024, 2, 29)
AS_OF = date(2024, 3, 1)
N = 400


def _alternating(n: int, up: float, down: float) -> np.ndarray:
    return np.array([up if i % 2 == 0 else down for i in range(n)])


def _paths() -> dict[str, np.ndarray]:
    mixed = np.concatenate([np.full(N - 1 - 100, 0.003), np.full(100, -0.002)])
    return {
        "SPY": _alternating(N - 1, 0.002, -0.001),  # steady up, low vol
        "QQQ": _alternating(N - 1, 0.006, -0.005),  # up, higher vol
        "TLT": _alternating(N - 1, -0.002, 0.001),  # steady down
        "GLD": mixed + _alternating(N - 1, 0.0002, -0.0002),  # up long, down recently
        "IWM": _alternating(99, 0.002, -0.001),  # 100 sessions: 21/63 lookbacks only
        "EFA": _alternating(29, 0.002, -0.001),  # 30 sessions: too few lookbacks
    }


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    lk = Lake(tmp_path_factory.mktemp("tsmom"))
    rng = np.random.default_rng(3)
    frames = []
    for sym, rets in _paths().items():
        days = list(CAL.sessions(CAL.offset(LAST, -len(rets)), LAST))
        frames.append(bars_from_returns(sym, rets, days, rng=rng, calendar=CAL))
    lk.write_table("prices_daily", pd.concat(frames, ignore_index=True), mode="upsert")
    return lk


def _view(lake: Lake, **tsmom: object) -> DataView:
    return DataView(lake, AS_OF, AtlasConfig.model_validate({"quant": {"alpha": {"tsmom": tsmom}}}))


def test_trend_blend_known_values() -> None:
    rets = pd.DataFrame(
        {
            21: [0.01, -0.01, 0.02, np.nan, 0.0],
            63: [0.02, -0.03, -0.01, 0.01, np.nan],
            126: [0.05, -0.02, -0.02, np.nan, np.nan],
            252: [0.10, -0.01, 0.10, np.nan, np.nan],
        },
        index=["A", "B", "C", "D", "E"],
    )
    b = trend_blend(rets, min_lookbacks=2)
    assert b["A"] == 1.0
    assert b["B"] == -1.0
    assert b["C"] == 0.0  # two up, two down
    assert np.isnan(b["D"]) and np.isnan(b["E"])  # one lookback only
    assert trend_blend(rets, min_lookbacks=1)["D"] == 1.0
    assert trend_blend(rets, min_lookbacks=1)["E"] == 0.0  # flat return: sign 0


def test_trending_series(lake: Lake) -> None:
    view = _view(lake)
    model = TSMOMModel()
    assert isinstance(model, AlphaModel) and model.name == "tsmom" and model.horizon == 21
    out = model.predict(view)
    check_alpha_frame(out)
    assert set(out.index) == {"GLD", "IWM", "QQQ", "SPY", "TLT"}  # EFA: too few lookbacks
    d = direction(out)
    assert d["SPY"] == 1 and d["QQQ"] == 1 and d["IWM"] == 1
    assert d["TLT"] == -1
    assert d["GLD"] == -1  # 21/63/126 down, 252 up -> blend -0.5
    assert out.loc["GLD", "confidence"] == pytest.approx(0.5)
    assert out.loc["SPY", "confidence"] == 1.0


def test_inverse_vol_scaling_and_expected_return(lake: Lake) -> None:
    view = _view(lake)
    cfg = view.config.quant.alpha.tsmom
    out = TSMOMModel().predict(view)
    vol = compute_features(view, list(out.index), ["vol_63d"])["vol_63d"].iloc[-1]
    blend = pd.Series({"SPY": 1.0, "QQQ": 1.0, "TLT": -1.0, "GLD": -0.5, "IWM": 1.0})
    for sym, b in blend.items():
        assert out.loc[sym, "exp_return_ann"] == pytest.approx(cfg.sharpe_prior * vol[sym] * b)
        want = float(np.clip(b * cfg.vol_target / vol[sym], -cfg.max_position, cfg.max_position))
        assert out.loc[sym, "score"] == pytest.approx(want)
    assert vol["QQQ"] > vol["SPY"]
    assert out.loc["SPY", "score"] == cfg.max_position  # low vol: capped
    assert 0 < out.loc["QQQ", "score"] < out.loc["SPY", "score"]  # higher vol, smaller position


def test_config_and_unknown_lookback(lake: Lake) -> None:
    out = TSMOMModel().predict(_view(lake, vol_target=0.01))
    assert out.loc["QQQ", "score"] < 0.2
    with pytest.raises(ValueError, match="ret_30d"):
        TSMOMModel().predict(_view(lake, lookbacks=[21, 30]))


def test_empty_universe(tmp_path_factory: pytest.TempPathFactory) -> None:
    empty = Lake(tmp_path_factory.mktemp("tsmom_empty"))
    out = TSMOMModel().predict(DataView(empty, AS_OF))
    assert out.empty
    check_alpha_frame(out)
