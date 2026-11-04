"""Persistence of daily factor returns and model snapshots (Stage 19 Task 8)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
from factor_risk_world import cross_sections, make_world

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.quant.risk_model.factor_model import (
    FactorHistory,
    FactorSet,
    RiskModel,
    build_risk_model,
    fit_factor_history,
)
from atlas.quant.risk_model.store import (
    FACTOR_RETURNS,
    RISK_MODEL_SNAPSHOTS,
    RiskModelStore,
    risk_model_config_hash,
)

CFG = AtlasConfig.model_validate(
    {"quant": {"risk_model": {"factor_model": {"min_cross_section": 50}}}}
)
CAL = TradingCalendar(config=CFG.calendar)


def after(day: date) -> date:
    """The first decision day that knows the bars of session `day`."""
    return CAL.next_session(day)


@pytest.fixture(scope="module")
def fitted() -> tuple[FactorHistory, RiskModel, pd.DataFrame]:
    w = make_world(n=150, t=120, seed=51)
    fm = CFG.quant.risk_model.factor_model
    hist = fit_factor_history(cross_sections(w), FactorSet.from_config(fm), fm)
    model = build_risk_model(hist, w.styles, w.sectors, w.caps, w.dates[-1].date(), CFG)
    return hist, model, w.returns


def test_schemas() -> None:
    assert FACTOR_RETURNS.name == "factor_returns" and FACTOR_RETURNS.primary_key == (
        "date",
        "factor",
    )
    assert RISK_MODEL_SNAPSHOTS.primary_key == ("date", "component", "row", "column")
    assert "factor_returns" in DEFAULT_REGISTRY and "risk_model_snapshots" in DEFAULT_REGISTRY


def test_factor_returns_round_trip(tmp_lake: Lake, fitted) -> None:
    hist, _model, _ = fitted
    store = RiskModelStore(tmp_lake, CFG)
    assert store.write_factor_returns(hist) == 120 * 20
    last = hist.factor_returns.index[-1].date()
    got = store.read_factor_returns(after(last))
    assert got is not None
    pd.testing.assert_frame_equal(got, hist.factor_returns, check_freq=False)
    diag = store.read_diagnostics(after(last))
    assert diag is not None
    np.testing.assert_allclose(diag["r2"].to_numpy(), hist.r2.to_numpy())
    assert (diag["n_names"] == hist.n_names.to_numpy()).all()


def test_snapshot_round_trip_equals_model(tmp_lake: Lake, fitted) -> None:
    _, model, _ = fitted
    store = RiskModelStore(tmp_lake, CFG)
    rows = store.write_snapshot(model)
    assert rows > len(model.symbols) * len(model.factors)
    got = store.read_snapshot(after(model.as_of))
    assert got is not None
    assert (
        got.as_of == model.as_of and got.symbols == model.symbols and got.factors == model.factors
    )
    np.testing.assert_array_equal(got.x, model.x)
    np.testing.assert_array_equal(got.f, model.f)
    np.testing.assert_array_equal(got.d, model.d)
    assert dict(got.dropped) == dict(model.dropped) and got.n_days == model.n_days
    assert got.adjustments == model.adjustments and got.etfs == model.etfs
    pd.testing.assert_frame_equal(got.cov(), model.cov())
    assert store.snapshot_dates() == (model.as_of,)


def test_read_is_point_in_time(tmp_lake: Lake, fitted) -> None:
    hist, model, _ = fitted
    store = RiskModelStore(tmp_lake, CFG)
    store.write_factor_returns(hist)
    store.write_snapshot(model)
    first = hist.factor_returns.index[0].date()
    # a decision on the morning of session D does not know D's close
    assert store.read_factor_returns(first) is None
    early = store.read_factor_returns(after(first))
    assert early is not None and len(early) == 1 and early.index[0] == pd.Timestamp(first)
    assert store.read_snapshot(model.as_of) is None
    assert store.read_snapshot(date(2015, 1, 5)) is None
    assert store.read_snapshot(after(model.as_of)) is not None
    later = store.read_factor_returns(after(model.as_of))
    assert later is not None and len(later) == 120


def test_snapshot_read_returns_latest_on_or_before(tmp_lake: Lake, fitted) -> None:
    _hist, model, _ = fitted
    store = RiskModelStore(tmp_lake, CFG)
    store.write_snapshot(model)
    older = RiskModel(
        as_of=date(2015, 3, 31),
        symbols=model.symbols,
        factors=model.factors,
        x=model.x,
        f=model.f,
        d=model.d * 2,
    )
    store.write_snapshot(older)
    assert store.read_snapshot(date(2015, 4, 15)).as_of == date(2015, 3, 31)  # type: ignore[union-attr]
    assert store.read_snapshot(date(2030, 1, 1)).as_of == model.as_of  # type: ignore[union-attr]
    assert store.snapshot_dates() == (date(2015, 3, 31), model.as_of)


def test_other_config_hash_not_read(tmp_lake: Lake, fitted) -> None:
    hist, model, _ = fitted
    other = AtlasConfig.model_validate(
        {"quant": {"risk_model": {"factor_model": {"min_cross_section": 50, "shrinkage_q": 2.0}}}}
    )
    assert risk_model_config_hash(other) != risk_model_config_hash(CFG)
    RiskModelStore(tmp_lake, other).write_snapshot(model)
    RiskModelStore(tmp_lake, other).write_factor_returns(hist)
    store = RiskModelStore(tmp_lake, CFG)
    assert store.read_snapshot(after(model.as_of)) is None and store.snapshot_dates() == ()
    assert store.read_factor_returns(after(model.as_of)) is None


def test_write_replaces_a_day(tmp_lake: Lake, fitted) -> None:
    _, model, _ = fitted
    store = RiskModelStore(tmp_lake, CFG)
    store.write_snapshot(model)
    smaller = RiskModel(
        as_of=model.as_of,
        symbols=model.symbols[:10],
        factors=model.factors,
        x=model.x[:10],
        f=model.f,
        d=model.d[:10] * 3,
    )
    store.write_snapshot(smaller)
    got = store.read_snapshot(after(model.as_of))
    assert got is not None and got.symbols == model.symbols[:10]
    np.testing.assert_array_equal(got.d, smaller.d)


def test_read_without_tables(tmp_lake: Lake) -> None:
    store = RiskModelStore(tmp_lake, CFG)
    assert store.read_snapshot(date(2024, 1, 31)) is None
    assert store.read_factor_returns(date(2024, 1, 31)) is None
    assert store.read_diagnostics(date(2024, 1, 31)) is None
    assert store.snapshot_dates() == ()


def test_rewriting_factor_returns_replaces_the_range(tmp_lake: Lake, fitted) -> None:
    hist, _, _ = fitted
    store = RiskModelStore(tmp_lake, CFG)
    store.write_factor_returns(hist)
    changed = FactorHistory(
        factors=hist.factors,
        factor_returns=hist.factor_returns + 0.001,
        residuals=hist.residuals,
        r2=hist.r2,
        n_names=hist.n_names,
    )
    store.write_factor_returns(changed)
    got = store.read_factor_returns(after(hist.factor_returns.index[-1].date()))
    assert got is not None
    pd.testing.assert_frame_equal(got, changed.factor_returns, check_freq=False)


def test_datetime_as_of_rejected(tmp_lake: Lake) -> None:
    from datetime import datetime

    with pytest.raises(TypeError, match="trading day"):
        RiskModelStore(tmp_lake, CFG).read_snapshot(datetime(2024, 1, 31))  # type: ignore[arg-type]
