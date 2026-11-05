"""Liquidity metrics (Stage 20 Task 7)."""

import math

import numpy as np
import pandas as pd
import pytest

from atlas.core.config import AtlasConfig
from atlas.quant.metrics.liquidity import (
    LiquidityProfile,
    days_to_liquidate,
    liquidity_from_view,
    liquidity_profile,
)
from tiny_universe import TinyUniverse

POS = pd.Series({"A": 1_000_000.0, "B": -500_000.0, "C": 2_000_000.0, "D": 0.0})  # USD
ADV = pd.Series({"A": 5_000_000.0, "B": 1_000_000.0, "C": 4_000_000.0, "D": 100.0})  # USD/day


def test_days_to_liquidate_hand_example() -> None:
    days = days_to_liquidate(POS, ADV)  # default participation limits.liquidation_adv_frac 0.10
    assert days.tolist() == pytest.approx([2.0, 5.0, 5.0, 0.0])
    assert list(days.index) == ["A", "B", "C", "D"] and days.name == "days_to_liquidate"
    assert days_to_liquidate(POS, ADV, participation=0.2).tolist() == pytest.approx(
        [1.0, 2.5, 2.5, 0.0]
    )
    cfg = AtlasConfig.model_validate({"limits": {"liquidation_adv_frac": 0.05}})
    assert days_to_liquidate(POS, ADV, config=cfg)["A"] == pytest.approx(4.0)


def test_short_positions_use_magnitude() -> None:
    assert days_to_liquidate(-POS, ADV).tolist() == pytest.approx([2.0, 5.0, 5.0, 0.0])


def test_profile_hand_example() -> None:
    prof = liquidity_profile(POS, ADV)
    assert isinstance(prof, LiquidityProfile)
    assert prof.participation == 0.10
    assert dict(prof.names_liquid) == pytest.approx({1: 0.0, 3: 1 / 3, 5: 1.0})
    # sellable in h sessions: min(|pos|, h x 10% ADV) = (0.5M, 0.1M, 0.4M) per session
    assert dict(prof.gross_liquid) == pytest.approx({1: 1.0 / 3.5, 3: 2.5 / 3.5, 5: 1.0})
    assert prof.max_days == pytest.approx(5.0)
    assert prof.no_adv == ()
    custom = liquidity_profile(POS, ADV, horizons=[1, 10], participation=0.25)
    assert dict(custom.names_liquid) == pytest.approx({1: 1 / 3, 10: 1.0})  # days 0.8, 2, 2
    with pytest.raises(TypeError):
        custom.names_liquid[99] = 1.0  # type: ignore[index]


def test_missing_or_zero_adv_is_infinite_and_listed() -> None:
    pos = pd.Series({"A": 1_000_000.0, "E": 10.0, "F": 10.0, "G": -10.0, "H": 0.0})
    adv = pd.Series({"A": 5_000_000.0, "F": 0.0, "G": np.nan, "H": np.nan})
    days = days_to_liquidate(pos, adv)
    assert days["A"] == pytest.approx(2.0) and days["H"] == 0.0
    assert all(math.isinf(days[s]) for s in ("E", "F", "G"))
    prof = liquidity_profile(pos, adv)
    assert prof.no_adv == ("E", "F", "G")
    assert prof.names_liquid[5] == pytest.approx(0.25)  # only A of the 4 held names
    assert prof.gross_liquid[5] == pytest.approx(1_000_000.0 / 1_000_030.0)
    assert math.isinf(prof.max_days)


def test_empty_book() -> None:
    prof = liquidity_profile(pd.Series({"A": 0.0}), ADV)
    assert prof.max_days == 0.0
    assert dict(prof.names_liquid) == {1: 1.0, 3: 1.0, 5: 1.0}
    assert dict(prof.gross_liquid) == {1: 1.0, 3: 1.0, 5: 1.0}


def test_validation() -> None:
    for bad in (0.0, 1.5, -0.1, True):
        with pytest.raises(ValueError, match="participation"):
            days_to_liquidate(POS, ADV, participation=bad)  # type: ignore[arg-type]
    for hs in ([], [0], [1.5]):
        with pytest.raises(ValueError, match="horizons"):
            liquidity_profile(POS, ADV, horizons=hs)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="adv must be a Series"):
        days_to_liquidate(POS, ADV.to_numpy())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="duplicate"):
        days_to_liquidate(POS, pd.Series([1.0, 2.0], index=["A", "A"]))
    with pytest.raises(ValueError, match="infinite"):
        days_to_liquidate(POS, ADV.replace(100.0, np.inf))
    with pytest.raises(ValueError, match="finite"):
        days_to_liquidate(pd.Series({"A": np.nan}), ADV)


def test_liquidity_from_view(tiny_universe: TinyUniverse) -> None:
    view = tiny_universe.view()
    pos = pd.Series({"AAA": 2_000_000.0, "SPY": -1_000_000.0, "ZZZ": 5_000.0})
    prof = liquidity_from_view(view, pos)
    adv = view.adv(["AAA", "SPY", "ZZZ"])["adv_usd"]  # limits.adv_window_days = 20
    want = liquidity_profile(pos, adv)
    pd.testing.assert_series_equal(prof.days, want.days)
    assert dict(prof.gross_liquid) == dict(want.gross_liquid)
    assert prof.no_adv == ("ZZZ",)
    short = liquidity_from_view(view, pos, lookback=5, participation=0.05, horizons=[1])
    adv5 = view.adv(["AAA", "SPY", "ZZZ"], 5)["adv_usd"]
    assert short.days["AAA"] == pytest.approx(2_000_000.0 / (0.05 * adv5["AAA"]))
    assert list(short.names_liquid) == [1]
