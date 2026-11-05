"""Stage 21 Task 5: `DataView` → `RegimeState` (`regime_at`, `regime_history`), one code path,
point in time, on the synthetic crash world (`regime_world.py`)."""

import inspect
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from regime_world import RegimeWorld, add_future_rows, build_regime_world, world_config

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, FundConfig, QuantConfig
from atlas.core.errors import DataError
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.regime.loader import regime_at, regime_history
from atlas.quant.regime.state import RegimeState

CAL = TradingCalendar()


def _world(root: Path) -> RegimeWorld:
    return build_regime_world(Lake(root / "lake", registry=DEFAULT_REGISTRY.copy()))


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> RegimeWorld:
    return _world(tmp_path_factory.mktemp("regime_world"))


@pytest.fixture(scope="module")
def crash_history(world: RegimeWorld) -> list[RegimeState]:
    return regime_history(world.lake, date(2020, 2, 10), date(2020, 4, 9), world.config)


def _at(world: RegimeWorld, day: date, config: AtlasConfig | None = None) -> RegimeState:
    return regime_at(DataView(world.lake, day, config or world.config))


def test_regime_at_takes_only_a_view() -> None:
    assert list(inspect.signature(regime_at).parameters) == ["view"]


def test_state_is_dated_by_the_decision_day(world: RegimeWorld) -> None:
    s = _at(world, date(2019, 5, 6))  # a Monday
    assert s.as_of == date(2019, 5, 6)
    assert s.data_date == date(2019, 5, 3)  # the previous session's close


def test_calm_market(world: RegimeWorld) -> None:
    s = _at(world, date(2019, 5, 6))
    assert s.vol_regime in ("LOW", "NORMAL")
    assert s.trend_regime == "UP"
    assert s.stress_flag is False
    assert s.macro_quadrant in ("Goldilocks", "Reflation")
    assert s.credit_source == "proxy"  # the OAS starts only in 2019-10
    assert s.hmm_probs is not None and s.p_turbulent is not None and s.p_turbulent < 0.5
    assert s.risk_scalar >= 0.9
    assert s.missing == ()


def test_crash_is_flagged(crash_history: list[RegimeState]) -> None:
    by_day = {s.as_of: s for s in crash_history}
    for day in (date(2020, 3, 16), date(2020, 3, 18), date(2020, 3, 20)):
        s = by_day[day]
        assert s.vol_regime == "CRISIS"
        assert s.stress_flag is True
        assert s.trend_regime == "DOWN"
        assert s.p_turbulent is not None and s.p_turbulent > 0.5
        assert s.risk_scalar <= 0.5
    calm = by_day[date(2020, 2, 13)]
    assert calm.risk_scalar > by_day[date(2020, 3, 18)].risk_scalar


def test_history_has_one_state_per_decision_day(crash_history: list[RegimeState]) -> None:
    days = [s.as_of for s in crash_history]
    assert days == list(CAL.sessions(date(2020, 2, 10), date(2020, 4, 9)))
    assert all(s.data_date == CAL.offset(s.as_of, -1) for s in crash_history)


def test_history_equals_regime_at_day_by_day(
    world: RegimeWorld, crash_history: list[RegimeState]
) -> None:
    for s in crash_history:
        assert _at(world, s.as_of) == s, s.as_of


def test_nfci_is_used_only_after_its_release(world: RegimeWorld) -> None:
    """NFCI of Friday 2020-02-21 (+0.4) is released Wednesday 2020-02-26 08:30."""
    assert _at(world, date(2020, 2, 25)).nfci == pytest.approx(-0.5)
    assert _at(world, date(2020, 2, 26)).nfci == pytest.approx(-0.5)  # released after 08:00
    assert _at(world, date(2020, 2, 27)).nfci == pytest.approx(0.4)


def test_monthly_release_is_used_only_after_its_release(world: RegimeWorld) -> None:
    """The March 2020 production collapse is released on 2020-04-15."""
    before, after = _at(world, date(2020, 4, 15)), _at(world, date(2020, 4, 16))
    assert before.growth_score is not None and after.growth_score is not None
    assert after.growth_score < before.growth_score


def test_future_rows_and_a_later_split_change_nothing(tmp_path: Path) -> None:
    w = _world(tmp_path)
    start, end = date(2020, 6, 1), date(2020, 6, 30)
    before = regime_history(w.lake, start, end, w.config)
    add_future_rows(w)
    after = regime_history(w.lake, start, end, w.config)
    assert before == after
    assert regime_at(DataView(w.lake, end, w.config)) == before[-1]


def test_a_late_market_row_is_not_used_before_it_is_published(tmp_path: Path) -> None:
    """VIX for 2019-05-01 first published on 2019-05-08: the 2019-05-02 decision reads the
    2019-04-30 value, in the history and in a one-day view."""
    from regime_world import late_row

    w = _world(tmp_path)
    w.lake.delete_rows(
        "macro_observations", [("series_id", "=", "VIXCLS"), ("obs_date", "=", date(2019, 5, 1))]
    )
    w.lake.write_table(
        "macro_observations",
        late_row("VIXCLS", date(2019, 5, 1), 99.0, date(2019, 5, 8)),
        mode="upsert",
    )
    rows = w.lake.read_table(
        "macro_observations",
        filters=[("series_id", "=", "VIXCLS"), ("obs_date", "=", date(2019, 4, 30))],
    )
    prior = float(rows["value"].iloc[0])
    hist = {
        s.as_of: s for s in regime_history(w.lake, date(2019, 4, 29), date(2019, 5, 10), w.config)
    }
    assert hist[date(2019, 5, 2)].vix == pytest.approx(prior)
    assert _at(w, date(2019, 5, 2)).vix == pytest.approx(prior)
    assert all(s.vix != 99.0 for s in hist.values())


def test_a_revision_is_used_only_after_it_is_published(tmp_path: Path) -> None:
    """A later VIX vintage (2019-05-01 revised to 99, published 2019-05-20): each day of the
    history equals a one-day view, before and after the revision; no reading is ever 99."""
    from regime_world import late_row

    w = _world(tmp_path)
    w.lake.write_table(
        "macro_observations",
        late_row("VIXCLS", date(2019, 5, 1), 99.0, date(2019, 5, 20)),
        mode="upsert",
    )
    states = regime_history(w.lake, date(2019, 5, 1), date(2019, 5, 31), w.config)
    hist = {s.as_of: s for s in states}
    for day in (date(2019, 5, 2), date(2019, 5, 17), date(2019, 5, 20), date(2019, 5, 31)):
        assert _at(w, day) == hist[day], day
    assert all(s.vix != 99.0 for s in hist.values())


def test_early_days_have_no_hmm(world: RegimeWorld) -> None:
    s = _at(world, date(2017, 11, 1))  # fewer than hmm.min_obs rows before the month
    assert s.hmm_probs is None and s.hmm_labels == ()
    assert "hmm" in s.missing


def test_missing_components_are_reported(tmp_path: Path) -> None:
    w = _world(tmp_path)
    w.lake.delete_rows(
        "macro_observations",
        [("series_id", "in", ["VIXCLS", "NFCI", "INDPRO", "PAYEMS", "ICSA", "CPILFESL"])],
    )
    w.lake.delete_rows("index_membership", [("index_id", "=", "SP500")])
    s = _at(w, date(2019, 5, 6))
    assert s.vix is None and s.breadth is None and s.macro_quadrant is None and s.nfci is None
    assert set(s.missing) >= {"vix", "breadth", "macro_quadrant", "nfci"}
    assert s.vol_regime in ("LOW", "NORMAL", "HIGH", "CRISIS")  # from realised vol alone


def test_missing_benchmark_raises(world: RegimeWorld) -> None:
    cfg = AtlasConfig(
        fund=FundConfig(benchmark="QQQ"), quant=QuantConfig(regime=world_config().quant.regime)
    )
    with pytest.raises(DataError, match="QQQ"):
        regime_at(DataView(world.lake, date(2019, 5, 6), cfg))


def test_too_early_for_a_volatility_regime_raises(world: RegimeWorld) -> None:
    with pytest.raises(DataError):
        _at(world, date(2017, 3, 1))


def test_history_arguments(world: RegimeWorld) -> None:
    with pytest.raises(ValueError):
        regime_history(world.lake, date(2020, 3, 2), date(2020, 3, 1), world.config)
    with pytest.raises(DataError):
        regime_history(world.lake, date(2020, 3, 7), date(2020, 3, 8), world.config)  # a weekend


def test_history_is_deterministic(world: RegimeWorld) -> None:
    a = regime_history(world.lake, date(2020, 3, 2), date(2020, 3, 13), world.config)
    b = regime_history(world.lake, date(2020, 3, 2), date(2020, 3, 13), world.config)
    assert a == b
    assert pd.Series([s.risk_scalar for s in a]).between(0.25, 1.0).all()


def test_a_dividend_inside_the_range_changes_only_rounding(tmp_path: Path) -> None:
    """The history reads prices through its last day's view, which back-adjusts for a dividend
    paid inside the range; a one-day view before the dividend does not. Every price input is a
    ratio inside a window, so labels and the scalar agree and the readings agree to rounding."""
    from regime_world import add_dividend

    w = _world(tmp_path)
    for symbol in ("SPY", "HYG", "S03"):
        add_dividend(w, symbol, date(2020, 3, 20), 1.5)
    states = regime_history(w.lake, date(2020, 3, 2), date(2020, 3, 31), w.config)
    hist = {s.as_of: s for s in states}
    for day in (date(2020, 3, 2), date(2020, 3, 12), date(2020, 3, 19)):
        one, h = _at(w, day).model_dump(), hist[day].model_dump()
        for key, value in one.items():
            numeric = isinstance(value, float) or (
                isinstance(value, tuple) and bool(value) and isinstance(value[0], float)
            )
            if numeric:
                assert h[key] == pytest.approx(value, rel=1e-9, abs=1e-12), (day, key)
            else:
                assert h[key] == value, (day, key)
