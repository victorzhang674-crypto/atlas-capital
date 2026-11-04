"""Volatility forecasts: annualisation, EWMA and realised vol, the blend and the entry points
(Stage 18)."""

import math

import numpy as np
import pandas as pd
import pytest
from risk_world import factor_sigma, mvn_returns, sessions
from structlog.testing import capture_logs

from atlas.core.config import BlendWeights
from atlas.core.errors import DataError
from atlas.quant.risk_model.covariance import EWMACovariance
from atlas.quant.risk_model.volatility import (
    VolForecast,
    annualise_variance,
    annualise_vol,
    blend_vol,
    deannualise_vol,
    ewma_lambda,
    ewma_variance_path,
    ewma_vol,
    forecast_vol,
    forecast_vols,
    garch_vol,
    realised_vol,
)

P = 252  # quant.features.sessions_per_year


# --- Task 8: annualisation, EWMA and realised vol -------------------------------------------


def test_annualisation_helpers() -> None:
    assert annualise_vol(0.01) == pytest.approx(0.01 * math.sqrt(P))
    assert annualise_variance(1e-4) == pytest.approx(1e-4 * P)
    assert deannualise_vol(annualise_vol(0.013)) == pytest.approx(0.013)
    assert annualise_vol(0.01, periods=12) == pytest.approx(0.01 * math.sqrt(12))
    s = pd.Series([0.01, 0.02], index=["A", "B"])
    out = annualise_vol(s)
    assert isinstance(out, pd.Series) and list(out.index) == ["A", "B"]
    assert np.allclose(out.to_numpy(), s.to_numpy() * math.sqrt(P))
    cov = pd.DataFrame([[1e-4, 2e-5], [2e-5, 4e-4]], index=["A", "B"], columns=["A", "B"])
    ann = annualise_variance(cov)
    assert isinstance(ann, pd.DataFrame) and np.allclose(ann.to_numpy(), cov.to_numpy() * P)
    arr = annualise_vol(np.array([0.01, 0.02]))
    assert isinstance(arr, np.ndarray) and np.allclose(
        arr, [0.01 * math.sqrt(P), 0.02 * math.sqrt(P)]
    )
    for bad in (0, -1, 2.5):
        with pytest.raises(ValueError, match="periods"):
            annualise_vol(0.01, periods=bad)  # type: ignore[arg-type]


def test_ewma_lambda_from_half_life() -> None:
    lam = ewma_lambda(half_life=17)
    assert lam**17 == pytest.approx(0.5)  # the weight halves every half-life
    assert ewma_lambda(0.97) == 0.97
    with pytest.raises(ValueError, match="not both"):
        ewma_lambda(0.9, 10)


def test_ewma_variance_path_recursion() -> None:
    r = pd.Series([np.nan, 0.01, -0.02, np.nan, 0.03], index=sessions(5))
    path = ewma_variance_path(r, 0.5)
    # S_t = λ S_{t-1} + r_t², W_t = λ W_{t-1} + 1 (a missing return decays but adds nothing).
    expected = [np.nan, 1e-4, 4.5e-4 / 1.5, 2.25e-4 / 0.75, (1.125e-4 + 9e-4) / 1.375]
    assert np.allclose(path.to_numpy(), expected, rtol=1e-12, equal_nan=True)
    assert path.index.equals(r.index)
    assert path.iloc[1] == pytest.approx(r.iloc[1] ** 2)  # the first value is r₁²


def test_ewma_variance_path_no_look_ahead() -> None:
    r = mvn_returns(np.array([[4e-4]]), 300, seed=21).iloc[:, 0]
    full = ewma_variance_path(r, 0.94)
    for k in (1, 50, 299):
        assert np.allclose(
            ewma_variance_path(r.iloc[:k], 0.94).to_numpy(), full.iloc[:k].to_numpy()
        )
    changed = r.copy()
    changed.iloc[200:] *= 10.0  # rewrite the future
    after = ewma_variance_path(changed, 0.94)
    assert after.iloc[:200].equals(full.iloc[:200])
    assert not np.isclose(after.iloc[200], full.iloc[200])


def test_ewma_vol_matches_covariance_diagonal() -> None:
    frame = mvn_returns(factor_sigma(3, seed=22), 300, seed=23)
    cov = EWMACovariance(lam=0.94).estimate(frame)
    assert cov.adjustments == ()
    for name in frame.columns:
        f = ewma_vol(frame[name], 0.94)
        assert isinstance(f, VolForecast)
        assert f.variance == pytest.approx(cov.cov.loc[name, name], rel=1e-12)
        assert f.vol == pytest.approx(math.sqrt(f.variance))
        assert f.method == "ewma" and f.n_obs == 300 and f.fallback is None
        assert f.params["lambda"] == 0.94
    assert ewma_vol(frame["S00"]).params["lambda"] == 0.94  # quant.risk_model.ewma_lambda
    half = ewma_vol(frame["S00"], half_life=10)
    assert half.params["lambda"] == pytest.approx(0.5**0.1)


def test_realised_vol_window() -> None:
    r = mvn_returns(np.array([[4e-4]]), 100, seed=24).iloc[:, 0]
    f = realised_vol(r)  # quant.risk_model.volatility.realised_window = 21
    assert f.vol == pytest.approx(math.sqrt(np.mean(r.to_numpy()[-21:] ** 2)))
    assert f.method == "realised" and f.params["window"] == 21 and f.n_obs == 100
    holed = r.copy()
    holed.iloc[-3] = np.nan
    g = realised_vol(holed, window=10)
    assert g.vol == pytest.approx(math.sqrt(np.nanmean(holed.to_numpy()[-10:] ** 2)))
    with pytest.raises(ValueError, match="window"):
        realised_vol(r, window=1)


def test_vol_forecast_needs_min_history() -> None:
    short = mvn_returns(np.array([[4e-4]]), 40, seed=25).iloc[:, 0]
    for fn in (ewma_vol, realised_vol):
        with pytest.raises(DataError, match="min_history"):
            fn(short)
    assert ewma_vol(short, min_history=30).n_obs == 40
    flat = pd.Series(0.0, index=sessions(100))
    for fn in (ewma_vol, realised_vol):
        with pytest.raises(DataError, match="zero variance"):
            fn(flat)
    holes = pd.Series(np.nan, index=sessions(100))
    holes.iloc[-25:-1] = 0.01
    holes.iloc[-1] = np.nan
    with pytest.raises(DataError, match="min_history"):
        ewma_vol(holes)


@pytest.mark.parametrize("case", ["frame", "text", "inf", "unsorted", "duplicate"])
def test_vol_forecast_validates_input(case: str) -> None:
    good = mvn_returns(np.array([[4e-4]]), 100, seed=26).iloc[:, 0]
    bad: object
    if case == "frame":
        bad = good.to_frame()
    elif case == "text":
        bad = good.astype(str)
    elif case == "inf":
        bad = good.copy()
        bad.iloc[3] = np.inf
    elif case == "unsorted":
        bad = good.iloc[::-1]
    else:
        bad = pd.concat([good.iloc[:50], good.iloc[49:]])
    for fn in (ewma_vol, realised_vol):
        with pytest.raises(ValueError):
            fn(bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ewma_variance_path(bad, 0.94)  # type: ignore[arg-type]


# --- Task 10: the realised-vol blend and the forecast entry points --------------------------


@pytest.fixture(scope="module")
def long_path() -> pd.Series:
    from risk_world import simulate_garch

    return simulate_garch(0.02, 0.08, 0.90, 1500, seed=41)


def test_blend_is_variance_weighted(long_path: pd.Series) -> None:
    e, g, r = ewma_vol(long_path), garch_vol(long_path), realised_vol(long_path)
    assert g.fallback is None
    f = blend_vol(long_path)  # weights ewma 0.4, garch 0.4, realised 0.2 (config)
    assert f.variance == pytest.approx(0.4 * e.variance + 0.4 * g.variance + 0.2 * r.variance)
    assert f.method == "blend" and f.fallback is None and f.n_obs == 1500
    assert f.params["ewma_variance"] == e.variance
    assert f.params["garch_variance"] == g.variance
    assert f.params["realised_variance"] == r.variance
    assert (f.params["w_ewma"], f.params["w_garch"], f.params["w_realised"]) == (0.4, 0.4, 0.2)
    only_realised = blend_vol(long_path, weights=BlendWeights(ewma=0.0, garch=0.0, realised=1.0))
    assert only_realised.variance == pytest.approx(r.variance)


def test_blend_carries_garch_fallback(long_path: pd.Series) -> None:
    short = long_path.iloc[-100:]  # fewer than garch_min_obs: GARCH falls back to EWMA
    f = blend_vol(short)
    e, r = ewma_vol(short), realised_vol(short)
    assert f.fallback is not None and "garch_min_obs" in f.fallback
    assert f.variance == pytest.approx(0.8 * e.variance + 0.2 * r.variance)
    assert f.params["garch_variance"] == e.variance


def test_forecast_vol_dispatch(long_path: pd.Series) -> None:
    assert forecast_vol(long_path, "ewma") == ewma_vol(long_path)
    assert forecast_vol(long_path, "garch") == garch_vol(long_path)
    assert forecast_vol(long_path, "realised") == realised_vol(long_path)
    assert forecast_vol(long_path, "blend") == blend_vol(long_path)
    assert forecast_vol(long_path) == blend_vol(long_path)  # quant.risk_model.volatility.method
    with pytest.raises(ValueError, match="method"):
        forecast_vol(long_path, "arima")


def test_forecast_vols_per_column(long_path: pd.Series) -> None:
    frame = mvn_returns(factor_sigma(3, seed=42), 300, seed=43)
    frame.iloc[:260, 2] = np.nan  # S02: 40 returns, fewer than min_history
    with capture_logs() as logs:
        out = forecast_vols(frame, "ewma")
    assert list(out) == ["S00", "S01"]  # no forecast for S02: never a guess
    for name, f in out.items():
        assert f == ewma_vol(frame[name])
    assert any(e["event"] == "vol_forecast_skipped" and e["symbol"] == "S02" for e in logs)
    with pytest.raises(ValueError, match="DataFrame"):
        forecast_vols(long_path)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="duplicate"):
        forecast_vols(frame.set_axis(["A", "A", "B"], axis=1))


def test_vol_forecast_annual_vol(long_path: pd.Series) -> None:
    f = ewma_vol(long_path)
    assert f.annual_vol() == pytest.approx(f.vol * math.sqrt(P))
    assert f.annual_vol(periods=12) == pytest.approx(f.vol * math.sqrt(12))
    with pytest.raises(TypeError):
        f.params["lambda"] = 0.5  # type: ignore[index]
