"""Stage 17 Task 4 (brief task 1): forward-return labels, aligned t -> t+h, and their guard."""

import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from research_world import CAL, ResearchWorld, build_research_world

from atlas.core.errors import LookAheadError
from atlas.core.pit import guard_not_label, is_label
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.quant.research import ForwardReturns, compound_forward, forward_returns


def _lake(tmp_path: Path) -> Lake:
    return Lake(tmp_path / "lake", registry=DEFAULT_REGISTRY.copy())


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> ResearchWorld:
    return build_research_world(_lake(tmp_path_factory.mktemp("labels")))


@pytest.fixture(scope="module")
def labels(world: ResearchWorld) -> ForwardReturns:
    dates = list(world.sessions[0:-2:3])
    return forward_returns(world.factory, dates, list(world.symbols), horizons=(1, 5))


def _ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(d)


# --- compound_forward (pure) ------------------------------------------------------------------


def _daily() -> pd.DataFrame:
    index = pd.DatetimeIndex(pd.to_datetime([f"2024-01-0{d}" for d in (2, 3, 4, 5, 8)]))
    return pd.DataFrame(
        {"A": [np.nan, 0.10, -0.05, 0.02, 0.01], "B": [np.nan, 0.0, 0.20, np.nan, 0.03]},
        index=index.rename("date"),
    )


def test_compound_forward_known_values() -> None:
    daily = _daily()
    dates = [daily.index[0], daily.index[1]]
    one = compound_forward(daily, dates, 1)
    assert one.loc[dates[0], "A"] == pytest.approx(0.10)
    assert one.loc[dates[1], "A"] == pytest.approx(-0.05)
    assert one.loc[dates[1], "B"] == pytest.approx(0.20)
    two = compound_forward(daily, dates, 2)
    assert two.loc[dates[0], "A"] == pytest.approx(1.10 * 0.95 - 1)
    assert two.loc[dates[1], "A"] == pytest.approx(0.95 * 1.02 - 1)
    three = compound_forward(daily, dates, 3)
    assert three.loc[dates[0], "A"] == pytest.approx(1.10 * 0.95 * 1.02 - 1)
    assert list(three.index) == dates
    assert list(three.columns) == ["A", "B"]


def test_compound_forward_nan_window_and_end() -> None:
    daily = _daily()
    dates = list(daily.index[:4])
    two = compound_forward(daily, dates, 2)
    assert math.isnan(two.loc[dates[1], "B"])  # window 01-04, 01-05 contains a NaN
    assert two.loc[dates[0], "B"] == pytest.approx(1.0 * 1.2 - 1)
    assert math.isnan(two.loc[dates[3], "A"])  # window passes the end of the grid
    assert two.loc[dates[2], "A"] == pytest.approx(1.02 * 1.01 - 1)
    with pytest.raises(ValueError, match="not in the daily grid"):
        compound_forward(daily, [pd.Timestamp("2024-01-06")], 1)
    with pytest.raises(ValueError, match="horizon"):
        compound_forward(daily, dates, 0)


# --- forward_returns through the lake ----------------------------------------------------------


def test_forward_returns_align_t_to_t_plus_h(world: ResearchWorld, labels: ForwardReturns) -> None:
    closes = world.closes
    checked = 0
    for h in (1, 5):
        frame = labels.horizon(h)
        assert list(frame.columns) == list(world.symbols)
        for t in labels.dates:
            i = world.sessions.index(t)
            for symbol in world.symbols:
                got = frame.loc[_ts(t), symbol]
                if i + h < len(world.sessions):
                    want = closes[symbol].iloc[i + h] / closes[symbol].iloc[i] - 1
                    assert got == pytest.approx(want, rel=1e-9, abs=1e-12)
                    checked += 1
                else:
                    assert math.isnan(got)
    assert checked > 1000


def test_forward_returns_skip_the_decision_day(
    world: ResearchWorld, labels: ForwardReturns
) -> None:
    one = labels.horizon(1)
    for t in labels.dates[:-1]:
        i = world.sessions.index(t)
        nxt = _ts(world.sessions[i + 1])
        np.testing.assert_allclose(one.loc[_ts(t)].to_numpy(), world.returns.loc[nxt].to_numpy())
        if i > 0:  # the decision day's own return is not part of its label
            own = world.returns.loc[_ts(t)].to_numpy()
            assert not np.allclose(one.loc[_ts(t)].to_numpy(), own)


def test_forward_returns_missing_bar_is_nan(tmp_path: Path) -> None:
    w = build_research_world(_lake(tmp_path), n_names=25, n_sessions=80, drop=[("S001", 50)])
    dates = [w.day(i) for i in (45, 49, 50, 51)]
    lab = forward_returns(w.factory, dates, list(w.symbols), horizons=(1, 5))
    one, five = lab.horizon(1), lab.horizon(5)
    assert math.isnan(one.loc[_ts(w.day(49)), "S001"])  # C(t+1) missing
    assert math.isnan(one.loc[_ts(w.day(50)), "S001"])  # C(t) missing
    assert not math.isnan(one.loc[_ts(w.day(51)), "S001"])
    assert math.isnan(five.loc[_ts(w.day(45)), "S001"])  # window 46..50 contains the gap
    assert not math.isnan(five.loc[_ts(w.day(45)), "S002"])


def test_forward_returns_unrealised_is_nan(world: ResearchWorld) -> None:
    dates = [world.day(-6), world.day(-3)]
    lab = forward_returns(world.factory, dates, list(world.symbols), horizons=(5,))
    five = lab.horizon(5)
    assert five.loc[_ts(dates[0])].notna().all()
    assert five.loc[_ts(dates[1])].isna().all()  # ends after the lake's last bar


def test_label_view_is_after_last_label_session(
    world: ResearchWorld, labels: ForwardReturns
) -> None:
    last = CAL.offset(max(labels.dates), 5)
    assert labels.known_at.day == CAL.next_session(last)
    assert labels.horizons == (1, 5)
    assert labels.symbols == world.symbols
    assert labels.blocked == ()


def test_forward_returns_blocked_symbol_is_reported(tmp_path: Path) -> None:
    w = build_research_world(_lake(tmp_path), n_names=25, n_sessions=80, stale="S003")
    # the label view (after session 78) sees S003's frozen closes in its quality window
    lab = forward_returns(w.factory, [w.day(10), w.day(77)], list(w.symbols), horizons=(1,))
    assert lab.known_at.day == w.day(79)
    assert lab.blocked == ("S003",)
    assert lab.horizon(1)["S003"].isna().all()
    assert lab.horizon(1)["S004"].notna().all()


def test_labels_are_tagged_and_guarded(labels: ForwardReturns) -> None:
    assert is_label(labels)
    assert is_label(labels.horizon(1))
    assert is_label(labels.daily)
    assert is_label(labels.horizon(5).rank(axis=1))
    for obj in (labels, labels.horizon(1), labels.daily, labels.horizon(1) * 2):
        with pytest.raises(LookAheadError, match="forward-return label"):
            guard_not_label(obj)
    first = labels.horizon(1)
    first.iloc[0, 0] = 99.0  # a copy: the stored labels do not change
    assert labels.horizon(1).iloc[0, 0] != 99.0


def test_horizon_unknown_raises(labels: ForwardReturns) -> None:
    with pytest.raises(ValueError, match="horizon 21"):
        labels.horizon(21)


def test_forward_returns_validates_dates(world: ResearchWorld) -> None:
    syms = list(world.symbols)
    with pytest.raises(ValueError, match="dates"):
        forward_returns(world.factory, [], syms)
    with pytest.raises(ValueError, match="ascending"):
        forward_returns(world.factory, [world.day(5), world.day(4)], syms)
    with pytest.raises(ValueError, match="ascending"):
        forward_returns(world.factory, [world.day(5), world.day(5)], syms)
    with pytest.raises(ValueError, match="not a trading session"):
        forward_returns(world.factory, [date(2024, 1, 6)], syms)
    with pytest.raises(ValueError, match="symbols"):
        forward_returns(world.factory, [world.day(5)], [])
    with pytest.raises(ValueError, match="horizons"):
        forward_returns(world.factory, [world.day(5)], syms, horizons=(5, 1))
