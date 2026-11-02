"""Stage 15 Task 15: `TechnicalSnapshot` and `build_snapshots` (percentiles vs own history
and vs the universe)."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from atlas.core.config import AtlasConfig
from atlas.core.pit import AsOf
from atlas.quant.features import REGISTRY, FeatureRegistry, PricePanel, compute_features
from atlas.quant.features.snapshot import (
    FeatureReading,
    TechnicalSnapshot,
    build_snapshots,
    percentile_rank,
)
from tiny_universe import TinyUniverse, add_future_rows

RANKS = {"AAA": 1.0, "BBB": 2.0, "CCC": 3.0, "DDD": 4.0, "SPY": 2.0}


def _registry() -> FeatureRegistry:
    reg = FeatureRegistry()

    @reg.feature("counter", lookback=1, unit="ratio")
    def counter(panel: PricePanel) -> pd.DataFrame:
        """1, 2, 3, ... over the sessions with a bar (strictly rising)."""
        return panel.close.notna().cumsum().where(panel.close.notna())

    @reg.feature("constant", lookback=1, unit="ratio")
    def constant(panel: PricePanel) -> pd.DataFrame:
        """A per-symbol constant (AAA 1 < BBB 2 = SPY 2 < CCC 3 < DDD 4)."""
        return panel.close * 0.0 + pd.Series(RANKS).reindex(panel.symbols).to_numpy()

    @reg.feature("level", lookback=1, unit="usd")
    def level(panel: PricePanel) -> pd.DataFrame:
        """The close (USD)."""
        return panel.close

    @reg.feature("sparse", lookback=1, unit="ratio")
    def sparse(panel: PricePanel) -> pd.DataFrame:
        """Returns on every other session only."""
        mask = np.arange(len(panel.index)) % 2 == 0
        return panel.returns.where(pd.Series(mask, index=panel.index), axis=0)

    @reg.feature("gone", lookback=1, unit="ratio")
    def gone(panel: PricePanel) -> pd.DataFrame:
        """NaN on the last session."""
        out = panel.close.copy()
        out.iloc[-1] = np.nan
        return out

    return reg


REG = _registry()


def _config(**snapshot: object) -> AtlasConfig:
    return AtlasConfig.model_validate({"quant": {"features": {"snapshot": snapshot}}})


def test_percentile_rank_known_values() -> None:
    assert percentile_rank([1.0, 2.0, 3.0, 4.0], 4.0) == pytest.approx(3.5 / 4)
    assert percentile_rank([1.0, 2.0, 2.0, 3.0], 2.0) == pytest.approx(0.5)
    assert percentile_rank([1.0, math.nan, 3.0], 1.0) == pytest.approx(0.5 / 2)
    assert percentile_rank([5.0], 5.0) == pytest.approx(0.5)
    assert percentile_rank([], 1.0) is None
    assert percentile_rank([1.0, 2.0], math.nan) is None
    assert percentile_rank([1.0, 2.0], 9.0, min_count=3) is None


def test_snapshot_values_match_compute_features(tiny_universe: TinyUniverse) -> None:
    view = tiny_universe.view()
    stocks = list(tiny_universe.stocks)
    snaps = build_snapshots(view, ["CCC", "aaa"], universe=stocks)
    assert list(snaps) == ["CCC", "AAA"]
    values = compute_features(view, stocks, history=1)
    for symbol, snap in snaps.items():
        assert snap.symbol == symbol
        assert snap.as_of == view.as_of
        assert snap.session == date(2024, 2, 29)
        assert snap.history_sessions == 252
        assert snap.universe_size == 4
        assert [r.name for r in snap.readings] == list(REGISTRY.names())
        for reading in snap.readings:
            want = values[reading.name][symbol].iloc[-1]
            if math.isnan(want):
                assert reading.value is None
            else:
                assert reading.value == pytest.approx(want, rel=1e-12)
            assert reading.unit == REGISTRY.get(reading.name).unit
    beta = snaps["CCC"].value("beta_252d")
    assert beta is not None and abs(beta - 1.3) < 0.2  # tiny_universe's known beta, 252d window


def test_pct_own_known_answer(tiny_universe: TinyUniverse) -> None:
    snap = build_snapshots(tiny_universe.view(), ["AAA"], names=["counter"], registry=REG)["AAA"]
    reading = snap.reading("counter")
    assert reading.pct_own == pytest.approx((252 - 0.5) / 252)  # the latest is the highest
    cfg = _config(history_sessions=100, min_history=50)
    snap = build_snapshots(tiny_universe.view(cfg), ["AAA"], names=["counter"], registry=REG)["AAA"]
    assert snap.history_sessions == 100
    assert snap.reading("counter").pct_own == pytest.approx(99.5 / 100)


def test_pct_universe_known_answer(tiny_universe: TinyUniverse) -> None:
    view = tiny_universe.view(_config(min_universe=2))
    snaps = build_snapshots(view, ["AAA", "DDD", "SPY"], names=["constant"], registry=REG)
    assert snaps["AAA"].universe_size == 5  # four members plus SPY
    assert snaps["AAA"].reading("constant").pct_universe == pytest.approx(0.5 / 5)
    assert snaps["DDD"].reading("constant").pct_universe == pytest.approx(4.5 / 5)
    assert snaps["SPY"].reading("constant").pct_universe == pytest.approx(2.0 / 5)  # a tie
    assert snaps["AAA"].reading("constant").pct_own == pytest.approx(0.5)  # all equal
    too_few = build_snapshots(
        tiny_universe.view(_config(min_universe=6)), ["AAA"], names=["constant"], registry=REG
    )
    assert too_few["AAA"].reading("constant").pct_universe is None


def test_usd_features_have_no_universe_percentile(tiny_universe: TinyUniverse) -> None:
    view = tiny_universe.view(_config(min_universe=2))
    snap = build_snapshots(view, ["AAA"], names=["level", "constant"], registry=REG)["AAA"]
    assert snap.reading("level").pct_universe is None
    assert snap.reading("level").pct_own is not None
    assert snap.reading("constant").pct_universe is not None
    real = build_snapshots(view, ["BBB"], names=["sma_20", "ret_21d"])["BBB"]
    assert real.reading("sma_20").pct_universe is None
    assert real.reading("ret_21d").pct_universe is not None


def test_short_history_gives_none(tiny_universe: TinyUniverse) -> None:
    view = tiny_universe.view(_config(history_sessions=100, min_history=60))
    snap = build_snapshots(view, ["AAA"], names=["sparse", "counter"], registry=REG)["AAA"]
    assert snap.reading("sparse").pct_own is None  # 50 valid values < 60
    assert snap.reading("counter").pct_own is not None


def test_nan_value_gives_none(tiny_universe: TinyUniverse) -> None:
    view = tiny_universe.view(_config(min_universe=2))
    reading = build_snapshots(view, ["AAA"], names=["gone"], registry=REG)["AAA"].reading("gone")
    assert (reading.value, reading.pct_own, reading.pct_universe) == (None, None, None)


def test_snapshot_contract(tiny_universe: TinyUniverse) -> None:
    snap = build_snapshots(tiny_universe.view(), ["AAA"], universe=["AAA", "BBB"])["AAA"]
    assert isinstance(snap, TechnicalSnapshot)
    assert TechnicalSnapshot.model_validate_json(snap.model_dump_json()) == snap
    with pytest.raises(ValidationError):
        snap.symbol = "BBB"  # type: ignore[misc]
    with pytest.raises(ValueError, match="no reading 'nope'"):
        snap.reading("nope")
    with pytest.raises(ValidationError):
        FeatureReading(name="x", unit="ratio", value=math.nan, pct_own=None, pct_universe=None)
    with pytest.raises(ValidationError):
        FeatureReading(name="x", unit="ratio", value=1.0, pct_own=1.5, pct_universe=None)
    with pytest.raises(ValidationError):
        TechnicalSnapshot(
            symbol="AAA",
            as_of=AsOf.for_session(date(2024, 3, 1)),
            session=date(2024, 2, 29),
            history_sessions=252,
            universe_size=1,
            readings=(
                FeatureReading(name="x", unit="ratio", value=1.0, pct_own=None, pct_universe=None),
            )
            * 2,
        )  # a repeated feature name


def test_blocked_symbol_left_out(tiny_universe: TinyUniverse) -> None:
    lake = tiny_universe.lake
    bars = lake.read_table("prices_daily", filters=[("symbol", "=", "CCC")])
    bars.loc[bars["date"] == date(2024, 2, 27), ["open", "high", "low", "close", "adj_close"]] = 0.0
    lake.write_table("prices_daily", bars, mode="upsert")
    snaps = build_snapshots(tiny_universe.view(), ["AAA", "CCC"])
    assert list(snaps) == ["AAA"]
    assert snaps["AAA"].universe_size == 3  # CCC is out of the cross-section too


def test_snapshot_no_lookahead(tiny_universe: TinyUniverse) -> None:
    before = build_snapshots(tiny_universe.view(), list(tiny_universe.symbols))
    add_future_rows(tiny_universe)
    after = build_snapshots(tiny_universe.view(), list(tiny_universe.symbols))
    assert after == before


def test_default_universe_from_config(tiny_universe: TinyUniverse) -> None:
    snaps = build_snapshots(tiny_universe.view(), ["SPY"], names=["ret_1d"])
    assert snaps["SPY"].universe_size == 5  # the four sp500 members plus SPY
    with pytest.raises(ValueError, match="sequence"):
        build_snapshots(tiny_universe.view(), "SPY")
