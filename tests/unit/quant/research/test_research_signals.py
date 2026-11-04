"""Stage 17 Task 5: evaluation dates and the point-in-time signal panel."""

import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from factor_world import build_factor_world
from research_world import CAL, ResearchWorld, build_research_world

from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.factors import factor_panel
from atlas.quant.features import compute_features
from atlas.quant.research import SignalPanel, evaluation_dates, signal_kind, signal_panel


def _lake(path: Path) -> Lake:
    return Lake(path / "lake", registry=DEFAULT_REGISTRY.copy())


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> ResearchWorld:
    return build_research_world(_lake(tmp_path_factory.mktemp("signals")))


@pytest.fixture(scope="module")
def panel(world: ResearchWorld) -> SignalPanel:
    return signal_panel("reversal_5d", world.factory, [world.day(30), world.day(60), world.day(90)])


def test_evaluation_dates_frequencies() -> None:
    start, end = date(2024, 1, 1), date(2024, 3, 31)
    assert evaluation_dates(start, end, "daily") == CAL.sessions(start, end)
    assert evaluation_dates(start, end, "monthly") == (
        date(2024, 1, 2),
        date(2024, 2, 1),
        date(2024, 3, 1),
    )
    weekly = evaluation_dates(start, end, "weekly")
    assert weekly[:3] == (date(2024, 1, 2), date(2024, 1, 8), date(2024, 1, 16))  # 01-15 holiday
    weeks = {d.isocalendar()[:2] for d in CAL.sessions(start, end)}
    assert len(weekly) == len(weeks)
    for d in weekly:
        same_week = [
            s for s in CAL.sessions(start, end) if s.isocalendar()[:2] == d.isocalendar()[:2]
        ]
        assert d == min(same_week)
    assert evaluation_dates(date(2024, 1, 17), end, "monthly")[0] == date(2024, 1, 17)
    with pytest.raises(ValueError, match="start"):
        evaluation_dates(end, start, "monthly")
    with pytest.raises(ValueError, match="frequency"):
        evaluation_dates(start, end, "hourly")  # type: ignore[arg-type]


def test_signal_kind() -> None:
    assert signal_kind("momentum_12_1") == "technical"
    assert signal_kind("earnings_yield") == "fundamental"
    with pytest.raises(ValueError, match="unknown signal 'nope'"):
        signal_kind("nope")


def test_signal_panel_technical_matches_compute_features(
    world: ResearchWorld, panel: SignalPanel
) -> None:
    assert panel.name == "reversal_5d" and panel.kind == "technical"
    assert list(panel.values.index) == [pd.Timestamp(world.day(i)) for i in (30, 60, 90)]
    for i in (30, 60, 90):
        d = world.day(i)
        view = world.view(d)
        members = view.universe("sp500")
        want = compute_features(view, members, ["reversal_5d"])["reversal_5d"].iloc[-1]
        got = panel.values.loc[pd.Timestamp(d), members]
        np.testing.assert_allclose(got.to_numpy(), want.to_numpy())
        # the signal uses the close of t-1 at most: -(C(t-1)/C(t-6) - 1)
        c = world.closes["S000"]
        assert got["S000"] == pytest.approx(-(c.iloc[i - 1] / c.iloc[i - 6] - 1))


def test_signal_panel_uses_point_in_time_membership(
    world: ResearchWorld, panel: SignalPanel
) -> None:
    late = world.late
    before, after = pd.Timestamp(world.day(30)), pd.Timestamp(world.day(90))
    assert world.day(30) < world.late_start <= world.day(90)
    assert late not in panel.members[world.day(30)]
    assert late in panel.members[world.day(90)]
    assert math.isnan(panel.values.loc[before, late])
    assert not math.isnan(panel.values.loc[after, late])
    assert len(panel.members[world.day(30)]) == len(world.symbols) - 1


def test_signal_panel_ignores_future_rows(world: ResearchWorld, tmp_path: Path) -> None:
    other = build_research_world(_lake(tmp_path))
    bars = other.lake.read_table("prices_daily")
    future = bars["date"] >= world.day(60)  # the decision day's own bar is published after 08:00
    changed = bars[future].copy()
    step = changed["date"].map({d: i - 60 for i, d in enumerate(world.sessions)})
    scale = 1.37 + 0.03 * step.to_numpy(dtype="float64")  # changes levels and returns
    for col in ("open", "high", "low", "close", "adj_close"):
        changed[col] = changed[col] * scale
    other.lake.write_table("prices_daily", changed, mode="upsert")
    dates = [world.day(30), world.day(60)]
    a = signal_panel("reversal_5d", world.factory, dates)
    b = signal_panel("reversal_5d", other.factory, dates)
    pd.testing.assert_frame_equal(a.values, b.values)
    c = signal_panel("reversal_5d", other.factory, [world.day(66)])
    d = signal_panel("reversal_5d", world.factory, [world.day(66)])
    assert not np.allclose(c.values.to_numpy(), d.values.to_numpy())  # later dates do see them


def test_signal_panel_fundamental_uses_factor_exposures(tmp_path: Path) -> None:
    lake = _lake(tmp_path)
    build_factor_world(lake)
    dates = [date(2024, 2, 1), date(2024, 3, 1)]
    out = signal_panel("earnings_yield", lambda d: DataView(lake, d), dates)
    assert out.kind == "fundamental"
    for d in dates:
        want = factor_panel(DataView(lake, d)).exposures["earnings_yield"]
        got = out.values.loc[pd.Timestamp(d), list(want.index)]
        np.testing.assert_allclose(got.to_numpy(), want.to_numpy())


def test_signal_panel_sectors(world: ResearchWorld, panel: SignalPanel) -> None:
    assert list(panel.sectors.index) == list(panel.values.columns)
    for symbol, sector in panel.sectors.items():
        assert sector == world.sectors[str(symbol)]
    assert panel.sectors.isna().sum() == len(world.symbols) // 4


def test_signal_panel_unknown_name(world: ResearchWorld) -> None:
    calls: list[date] = []

    def factory(d: date) -> DataView:
        calls.append(d)
        return world.view(d)

    with pytest.raises(ValueError, match="unknown signal"):
        signal_panel("nope", factory, [world.day(30)])
    assert calls == []
