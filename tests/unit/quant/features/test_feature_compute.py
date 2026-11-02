"""Stage 15 Task 5: `PricePanel`, `compute_panel` and `compute_features` on `tiny_universe`."""

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from atlas.core.config import AtlasConfig
from atlas.data.view import DataView
from atlas.quant.features.registry import (
    FeatureRegistry,
    PricePanel,
    compute_features,
    compute_panel,
)
from tiny_universe import TinyUniverse

DAYS = [date(2024, 2, 26), date(2024, 2, 27), date(2024, 2, 28), date(2024, 2, 29)]


def _bars(rows: list[tuple[Any, ...]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "adj_close"]
    )


def _test_registry() -> FeatureRegistry:
    reg = FeatureRegistry()

    @reg.feature("one_day", lookback=2, unit="fraction")
    def one_day(panel: PricePanel) -> pd.DataFrame:
        """Daily return."""
        return panel.returns

    @reg.feature("excess", lookback=2, unit="fraction", benchmark=True)
    def excess(panel: PricePanel) -> pd.DataFrame:
        """Daily return minus the benchmark's."""
        return panel.returns.sub(panel.benchmark_returns, axis=0)

    @reg.feature("level", lookback=30, unit="usd")
    def level(panel: PricePanel) -> pd.DataFrame:
        """30-session mean close."""
        return panel.close.rolling(30).mean()

    @reg.feature("xs_rank", lookback=2, kind="xs", unit="ratio")
    def xs_rank(panel: PricePanel) -> pd.DataFrame:
        """Cross-sectional rank of the daily return."""
        return panel.returns.rank(axis=1, pct=True)

    return reg


REG = _test_registry()


# --- PricePanel ---------------------------------------------------------------------------------


def test_panel_from_bars_adjusts_ohlc() -> None:
    bars = _bars(
        [  # a 2:1 split on 02-28: our adj_close halves the earlier bars
            ("AAA", DAYS[0], 99.0, 101.0, 98.0, 100.0, 10, 50.0),
            ("AAA", DAYS[1], 101.0, 103.0, 100.0, 102.0, 20, 51.0),
            ("AAA", DAYS[2], 50.5, 52.0, 50.0, 51.0, 40, 51.0),
            ("AAA", DAYS[3], 51.0, 53.0, 50.5, 52.0, 30, 52.0),
        ]
    )
    panel = PricePanel.from_bars(bars, DAYS, ["AAA"])
    assert panel.close["AAA"].tolist() == [50.0, 51.0, 51.0, 52.0]
    assert panel.open["AAA"].tolist() == [49.5, 50.5, 50.5, 51.0]
    assert panel.high["AAA"].tolist() == [50.5, 51.5, 52.0, 53.0]
    assert panel.low["AAA"].tolist() == [49.0, 50.0, 50.0, 50.5]
    assert panel.dollar_volume["AAA"].tolist() == [1000.0, 2040.0, 2040.0, 1560.0]  # raw
    assert panel.returns["AAA"].tolist()[1:] == pytest.approx([0.02, 0.0, 52 / 51 - 1])
    assert list(panel.index) == list(pd.to_datetime(DAYS))
    assert panel.index.name == "date"
    assert panel.benchmark_close is None
    assert panel.benchmark_returns.isna().all()


def test_panel_missing_bar_and_symbol_are_nan() -> None:
    bars = _bars(
        [
            ("AAA", DAYS[0], 1.0, 1.0, 1.0, 10.0, 1, 10.0),
            ("AAA", DAYS[2], 1.0, 1.0, 1.0, 11.0, 1, 11.0),
            ("AAA", DAYS[3], 1.0, 1.0, 1.0, 12.1, 1, 12.1),
            ("SPY", DAYS[3], 1.0, 1.0, 1.0, 400.0, 1, 400.0),
        ]
    )
    panel = PricePanel.from_bars(bars, DAYS, ["AAA", "ZZZ"], benchmark="SPY")
    assert panel.close["AAA"].isna().tolist() == [False, True, False, False]
    assert panel.returns["AAA"].isna().tolist() == [True, True, True, False]
    assert panel.close["ZZZ"].isna().all()
    assert panel.benchmark_close is not None
    assert panel.benchmark_close.isna().tolist() == [True, True, True, False]
    assert "SPY" not in panel.symbols


def test_panel_rejects_misaligned_frames() -> None:
    idx = pd.DatetimeIndex(pd.to_datetime(DAYS), name="date")
    ok = pd.DataFrame(1.0, index=idx, columns=["AAA"])
    with pytest.raises(ValueError, match="dollar_volume"):
        PricePanel(ok, ok, ok, ok, ok.iloc[1:])
    with pytest.raises(ValueError, match="benchmark_close"):
        PricePanel(ok, ok, ok, ok, ok, benchmark_close=pd.Series(1.0, index=idx[1:]))


def test_panel_memo_computes_once() -> None:
    idx = pd.DatetimeIndex(pd.to_datetime(DAYS), name="date")
    ok = pd.DataFrame(1.0, index=idx, columns=["AAA"])
    panel = PricePanel(ok, ok, ok, ok, ok)
    calls: list[int] = []

    def compute() -> pd.DataFrame:
        calls.append(1)
        return ok * 2

    assert panel.memo("x", compute) is panel.memo("x", compute)
    assert calls == [1]


# --- compute_panel ------------------------------------------------------------------------------


def _panel(n: int = 40, symbols: tuple[str, ...] = ("AAA", "BBB")) -> PricePanel:
    idx = pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=n), name="date")
    rng = np.random.default_rng(0)
    close = pd.DataFrame(
        100 * np.exp(np.cumsum(rng.normal(0, 0.01, (n, len(symbols))), axis=0)),
        index=idx,
        columns=list(symbols),
    )
    return PricePanel(close, close, close, close, close * 1e6)


def test_compute_panel_aligned_float64_and_inf_to_nan() -> None:
    reg = FeatureRegistry()

    @reg.feature("ints", lookback=1, unit="ratio")
    def ints(panel: PricePanel) -> pd.DataFrame:
        """Integer ones."""
        return pd.DataFrame(1, index=panel.index, columns=panel.symbols)

    @reg.feature("infs", lookback=1, unit="ratio")
    def infs(panel: PricePanel) -> pd.DataFrame:
        """Division by zero."""
        return panel.close / 0.0

    out = compute_panel(_panel(), registry=reg)
    assert list(out) == ["ints", "infs"]
    assert all(frame.dtypes.eq("float64").all() for frame in out.values())
    assert out["infs"].isna().all().all()


def test_compute_panel_rejects_misaligned_result() -> None:
    reg = FeatureRegistry()

    @reg.feature("short", lookback=1, unit="ratio")
    def short(panel: PricePanel) -> pd.DataFrame:
        """Drops a row."""
        return panel.close.iloc[1:]

    with pytest.raises(ValueError, match="short"):
        compute_panel(_panel(), registry=reg)


# --- compute_features on tiny_universe ------------------------------------------------------


def test_compute_features_wide(tiny_universe: TinyUniverse) -> None:
    view = tiny_universe.view()
    out = compute_features(
        view, ["aaa", "BBB", "AAA"], ["one_day", "level"], history=5, registry=REG
    )
    assert isinstance(out, dict)
    assert list(out) == ["one_day", "level"]
    for frame in out.values():
        assert list(frame.columns) == ["AAA", "BBB"]
        assert list(frame.index) == list(pd.to_datetime(view.sessions(5)))
    rets = view.returns(["AAA", "BBB"], 5)
    pd.testing.assert_frame_equal(out["one_day"], rets, check_names=False, check_freq=False)
    closes = view.prices(["AAA"], 30)["adj_close"]
    assert out["level"].iloc[-1]["AAA"] == pytest.approx(closes.mean(), rel=1e-12)


def test_compute_features_long_matches_wide(tiny_universe: TinyUniverse) -> None:
    view = tiny_universe.view()
    names = ["one_day", "level"]
    wide = compute_features(view, ["AAA", "BBB"], names, history=3, registry=REG)
    long = compute_features(view, ["AAA", "BBB"], names, history=3, layout="long", registry=REG)
    assert isinstance(long, pd.DataFrame)
    assert list(long.columns) == names
    assert long.index.names == ["date", "symbol"]
    assert len(long) == 3 * 2
    for name in names:
        for (day, symbol), value in long[name].items():
            assert value == wide[name].loc[day, symbol]


def test_compute_features_loads_lookback_plus_buffer(
    tiny_universe: TinyUniverse, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, tuple[str, ...], int]] = []
    prices, sessions = DataView.prices, DataView.sessions

    def spy_prices(self: DataView, symbols: list[str], lookback: int) -> pd.DataFrame:
        calls.append(("prices", tuple(symbols), lookback))
        return prices(self, symbols, lookback)

    def spy_sessions(self: DataView, lookback: int) -> tuple[date, ...]:
        calls.append(("sessions", (), lookback))
        return sessions(self, lookback)

    monkeypatch.setattr(DataView, "prices", spy_prices)
    monkeypatch.setattr(DataView, "sessions", spy_sessions)
    view = tiny_universe.view()
    buffer = view.config.quant.features.buffer_sessions
    compute_features(view, ["AAA"], ["one_day", "level"], history=4, registry=REG)
    n = 30 + buffer + 4 - 1
    assert sorted(calls) == [("prices", ("AAA",), n), ("sessions", (), n)]
    calls.clear()
    cfg = AtlasConfig.model_validate({"quant": {"features": {"buffer_sessions": 0}}})
    compute_features(tiny_universe.view(cfg), ["AAA"], ["one_day"], registry=REG)
    assert sorted(calls) == [("prices", ("AAA",), 2), ("sessions", (), 2)]


def test_benchmark_loaded_only_when_needed(
    tiny_universe: TinyUniverse, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[str, ...]] = []
    prices = DataView.prices

    def spy(self: DataView, symbols: list[str], lookback: int) -> pd.DataFrame:
        asked.append(tuple(symbols))
        return prices(self, symbols, lookback)

    monkeypatch.setattr(DataView, "prices", spy)
    view = tiny_universe.view()
    compute_features(view, ["AAA"], ["one_day"], registry=REG)
    out = compute_features(view, ["AAA"], ["excess"], history=3, registry=REG)
    assert asked == [("AAA",), ("AAA", "SPY")]
    assert isinstance(out, dict)
    assert list(out["excess"].columns) == ["AAA"]  # the benchmark is not an output column
    rets = view.returns(["AAA", "SPY"], 3)
    expected = (rets["AAA"] - rets["SPY"]).to_numpy()
    np.testing.assert_allclose(out["excess"]["AAA"].to_numpy(), expected, rtol=1e-12)


def test_blocked_symbol_left_out(tiny_universe: TinyUniverse) -> None:
    lake = tiny_universe.lake
    bars = lake.read_table("prices_daily", filters=[("symbol", "in", ["CCC", "SPY"])])
    bad = bars["date"] == date(2024, 2, 27)
    bars.loc[bad, ["open", "high", "low", "close", "adj_close"]] = 0.0  # nonpositive: critical
    lake.write_table("prices_daily", bars, mode="upsert")
    view = tiny_universe.view()
    assert {"CCC", "SPY"} <= view.blocked
    out = compute_features(view, ["AAA", "CCC"], ["one_day", "excess"], registry=REG)
    assert isinstance(out, dict)
    assert list(out["one_day"].columns) == ["AAA"]
    assert out["excess"]["AAA"].isna().all()  # blocked benchmark: NaN, never a guess


def test_xs_feature_sees_cross_section(tiny_universe: TinyUniverse) -> None:
    view = tiny_universe.view()
    two = compute_features(view, ["AAA", "BBB"], ["xs_rank"], registry=REG)
    four = compute_features(view, list(tiny_universe.stocks), ["xs_rank"], registry=REG)
    assert isinstance(two, dict) and isinstance(four, dict)
    assert sorted(two["xs_rank"].iloc[-1]) == [0.5, 1.0]
    assert sorted(four["xs_rank"].iloc[-1]) == [0.25, 0.5, 0.75, 1.0]


def test_compute_features_argument_errors(tiny_universe: TinyUniverse) -> None:
    view = tiny_universe.view()
    with pytest.raises(ValueError, match="unknown features"):
        compute_features(view, ["AAA"], ["nope"], registry=REG)
    for bad in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="history"):
            compute_features(view, ["AAA"], ["one_day"], history=bad, registry=REG)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="layout"):
        compute_features(view, ["AAA"], ["one_day"], layout="tall", registry=REG)  # type: ignore[call-overload]
    with pytest.raises(ValueError, match="sequence"):
        compute_features(view, "AAA", ["one_day"], registry=REG)


def test_compute_features_no_symbols_left(tiny_universe: TinyUniverse) -> None:
    out = compute_features(tiny_universe.view(), ["ZZZ"], ["one_day"], history=2, registry=REG)
    assert isinstance(out, dict)
    assert out["one_day"].shape == (2, 1)
    assert out["one_day"].isna().all().all()  # no bars: NaN, not an error
