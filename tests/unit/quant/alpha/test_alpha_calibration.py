"""Stage 22 Task 1: Grinold calibration and the point-in-time IC bookkeeping."""

import math
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from atlas.core.calendar import TradingCalendar
from atlas.core.errors import LookAheadError
from atlas.core.pit import tag_label
from atlas.quant.alpha import (
    calibration_slope,
    grinold_alpha,
    horizon_scale,
    ic_history,
    ic_weights,
    known_ic,
    specific_vol,
    trailing_ic,
)

CAL = TradingCalendar()


def test_horizon_scale() -> None:
    assert horizon_scale(21, 252) == pytest.approx(math.sqrt(12))
    assert horizon_scale(252, 252) == 1.0
    with pytest.raises(ValueError):
        horizon_scale(0, 252)


def test_grinold_known_values() -> None:
    z = pd.Series({"A": 1.0, "B": -2.0, "C": 0.5})
    sigma = pd.Series({"A": 0.20, "B": 0.30, "C": np.nan})
    out = grinold_alpha(z, 0.05, sigma, horizon=21, periods=252)
    assert out["A"] == pytest.approx(0.05 * 0.20 * 1.0 * math.sqrt(12))
    assert out["B"] == pytest.approx(0.05 * 0.30 * -2.0 * math.sqrt(12))
    assert math.isnan(out["C"])
    # at the annual horizon the formula is the brief's IC x sigma x z
    annual = grinold_alpha(z, 0.05, sigma, horizon=252, periods=252)
    assert annual["A"] == pytest.approx(0.05 * 0.20)


@pytest.mark.parametrize("ic", [math.nan, 1.0, -1.0, 2.0])
def test_grinold_rejects_bad_ic(ic: float) -> None:
    with pytest.raises(ValueError):
        grinold_alpha(pd.Series({"A": 1.0}), ic, pd.Series({"A": 0.2}), horizon=21, periods=252)


def test_grinold_rejects_negative_sigma() -> None:
    with pytest.raises(ValueError):
        grinold_alpha(pd.Series({"A": 1.0}), 0.05, pd.Series({"A": -0.2}), horizon=21, periods=252)


def test_grinold_is_calibrated_on_planted_returns() -> None:
    """Returns r = rho*s*z + sqrt(1-rho^2)*s*e over h sessions have IC rho with z; alpha
    annualised then de-annualised to h is the conditional mean, so the OLS slope is ~1."""
    rng = np.random.default_rng(22)
    n_dates, n_names, rho, h, periods = 60, 400, 0.08, 21, 252
    preds, reals = [], []
    for _ in range(n_dates):
        z = rng.standard_normal(n_names)
        z = (z - z.mean()) / z.std()
        sigma_ann = rng.uniform(0.15, 0.45, n_names)
        sigma_h = sigma_ann * math.sqrt(h / periods)
        e = rng.standard_normal(n_names)
        r_h = rho * sigma_h * z + math.sqrt(1 - rho**2) * sigma_h * e
        idx = pd.Index([f"S{i}" for i in range(n_names)])
        a = grinold_alpha(
            pd.Series(z, idx), rho, pd.Series(sigma_ann, idx), horizon=h, periods=periods
        )
        preds.append(a)
        reals.append(pd.Series(r_h * periods / h, idx))  # realised, annualised
    fit = calibration_slope(pd.concat(preds), pd.concat(reals))
    assert fit.slope == pytest.approx(1.0, abs=0.12)
    assert fit.n_obs == n_dates * n_names


def test_calibration_slope_known_values() -> None:
    x = pd.Series([1.0, 2.0, 3.0, 4.0])
    fit = calibration_slope(x, 2.0 * x + 1.0)
    assert fit.slope == pytest.approx(2.0)
    assert fit.intercept == pytest.approx(1.0)
    assert fit.n_obs == 4
    nan = calibration_slope(pd.Series([1.0, np.nan]), pd.Series([1.0, 2.0]))
    assert math.isnan(nan.slope) and nan.n_obs == 1


def _monthly(start: date, n: int) -> list[date]:
    out, day = [], start
    for _ in range(n):
        out.append(day)
        day = CAL.offset(day, 21)
    return out


def test_known_ic_boundary() -> None:
    dates = _monthly(date(2020, 1, 2), 4)
    hist = pd.DataFrame({"a": [0.1, 0.2, 0.3, 0.4]}, index=pd.DatetimeIndex(dates, name="date"))
    label_end = CAL.offset(dates[1], 21)  # the label of dates[1] closes here
    at_end = known_ic(hist, label_end, 21, CAL)  # decision at 08:00 on that day: close unknown
    assert list(at_end.index.date) == [dates[0]]
    after = known_ic(hist, CAL.next_session(label_end), 21, CAL)
    assert list(after.index.date) == dates[:2]


def test_trailing_ic_window_and_min_obs() -> None:
    dates = _monthly(date(2015, 1, 2), 60)
    values = np.where(np.arange(60) < 30, -0.05, 0.05)
    hist = pd.DataFrame(
        {"a": values, "b": np.full(60, np.nan)}, index=pd.DatetimeIndex(dates, name="date")
    )
    day = CAL.next_session(CAL.offset(dates[-1], 21))
    tr = trailing_ic(hist, day, 21, window_months=12, min_obs=6, calendar=CAL)
    assert tr.loc["a", "mean"] == pytest.approx(0.05)  # only the last 12 months: all +0.05
    assert tr.loc["a", "n_obs"] >= 6
    assert math.isnan(tr.loc["b", "mean"]) and tr.loc["b", "n_obs"] == 0
    few = trailing_ic(hist, day, 21, window_months=12, min_obs=100, calendar=CAL)
    assert math.isnan(few.loc["a", "mean"])


def test_ic_weights_positive_proportional_and_fallback() -> None:
    tr = pd.DataFrame({"mean": [0.04, 0.02, -0.03, np.nan]}, index=["a", "b", "c", "d"])
    fixed = {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}
    w, source = ic_weights(tr, fixed)
    assert source == "ic"
    assert w == pytest.approx({"a": 2 / 3, "b": 1 / 3, "c": 0.0, "d": 0.0})
    none = pd.DataFrame({"mean": [-0.01, np.nan]}, index=["a", "b"])
    w2, source2 = ic_weights(none, {"a": 1.0, "b": 3.0})
    assert source2 == "fixed"
    assert w2 == pytest.approx({"a": 0.25, "b": 0.75})


def test_ic_history_matches_stage17_and_guards_labels() -> None:
    days = [date(2020, 1, 2), date(2020, 2, 3)]
    syms = [f"S{i}" for i in range(30)]
    rng = np.random.default_rng(1)
    comps = {
        d: pd.DataFrame({"x": rng.standard_normal(30), "y": rng.standard_normal(30)}, index=syms)
        for d in days
    }
    labels = tag_label(
        pd.DataFrame(rng.standard_normal((2, 30)), index=pd.DatetimeIndex(days), columns=syms)
    )
    hist = ic_history(comps, labels, min_names=20)
    assert list(hist.columns) == ["x", "y"]
    expected = np.corrcoef(comps[days[0]]["x"].rank(), labels.iloc[0].rank())[0, 1]
    assert hist.iloc[0]["x"] == pytest.approx(expected)
    with pytest.raises(LookAheadError):
        ic_history({d: tag_label(f) for d, f in comps.items()}, labels, min_names=20)


def test_specific_vol_from_risk_model() -> None:
    model = SimpleNamespace(specific_var=pd.Series({"A": 0.0004, "B": 0.0001}))
    out = specific_vol(model, periods=252)  # type: ignore[arg-type]
    assert out["A"] == pytest.approx(math.sqrt(0.0004 * 252))
    assert out.name == "resid_vol"
