"""GARCH(1,1) volatility forecasts via `arch`, with the EWMA fallback (Stage 18, brief task 4)."""

import math
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
from risk_world import simulate_garch
from structlog.testing import capture_logs

from atlas.core.errors import DataError
from atlas.quant.risk_model import volatility
from atlas.quant.risk_model.volatility import ewma_vol, garch_vol

TRUE = {"omega": 0.02, "alpha": 0.08, "beta": 0.90}  # percent units, as `arch` fits them


@pytest.fixture(scope="module")
def path() -> pd.Series:
    return simulate_garch(TRUE["omega"], TRUE["alpha"], TRUE["beta"], 6000, seed=31)


def test_garch_recovers_simulated_parameters(path: pd.Series) -> None:
    f = garch_vol(path)
    assert f.method == "garch" and f.fallback is None and f.n_obs == 6000
    alpha, beta = f.params["alpha"], f.params["beta"]
    assert alpha == pytest.approx(TRUE["alpha"], abs=0.03)
    assert beta == pytest.approx(TRUE["beta"], abs=0.04)
    assert alpha + beta == pytest.approx(TRUE["alpha"] + TRUE["beta"], abs=0.02)
    assert f.params["persistence"] == pytest.approx(alpha + beta)
    omega_pct = f.params["omega"] * 1e4  # params are in daily fractions (return²)
    assert omega_pct == pytest.approx(TRUE["omega"], rel=0.5)
    long_run = f.params["omega"] / (1 - alpha - beta)
    assert f.params["long_run_variance"] == pytest.approx(long_run)
    # The long-run vol is near the simulated unconditional vol (√(0.02/0.02) = 1% a day).
    assert math.sqrt(long_run) == pytest.approx(0.01, rel=0.25)


def test_garch_forecast_is_the_one_step_recursion(path: pd.Series) -> None:
    from arch import arch_model

    pct = path.to_numpy() * 100.0
    res = arch_model(pct, mean="Zero", vol="GARCH", p=1, q=1, dist="normal", rescale=False).fit(
        disp="off"
    )
    omega, alpha, beta = (float(res.params[k]) for k in ("omega", "alpha[1]", "beta[1]"))
    sigma2_t = float(np.asarray(res.conditional_volatility)[-1]) ** 2
    by_hand = (
        omega + alpha * pct[-1] ** 2 + beta * sigma2_t
    ) / 1e4  # sigma²_{T+1} = ω + alpha r_T² + β sigma_T²
    f = garch_vol(path)
    assert f.variance == pytest.approx(by_hand, rel=1e-6)
    assert f.vol == pytest.approx(math.sqrt(by_hand), rel=1e-6)


def _fake_fit(result: object = None, error: Exception | None = None) -> Any:
    calls: list[np.ndarray] = []

    def fit(pct: np.ndarray) -> object:
        calls.append(pct)
        if error is not None:
            raise error
        return result

    fit.calls = calls  # type: ignore[attr-defined]
    return fit


def _result(flag: int = 0, omega: float = 0.02, alpha: float = 0.08, beta: float = 0.9) -> object:
    params = pd.Series({"omega": omega, "alpha[1]": alpha, "beta[1]": beta})
    return SimpleNamespace(convergence_flag=flag, params=params)


def _assert_fell_back(series: pd.Series, reason: str, logs: list[dict[str, Any]]) -> None:
    f = garch_vol(series)
    expected = ewma_vol(series)
    assert f.fallback is not None and reason in f.fallback
    assert f.method == "ewma"
    assert f.variance == expected.variance
    assert f.params["lambda"] == 0.94
    assert any(e["event"] == "garch_fallback" and e["log_level"] == "warning" for e in logs)


def test_garch_falls_back_on_non_convergence(
    path: pd.Series, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(volatility, "_fit_garch", _fake_fit(_result(flag=1)))
    with capture_logs() as logs:
        _assert_fell_back(path, "did not converge", logs)


@pytest.mark.parametrize(
    "error", [RuntimeError("boom"), np.linalg.LinAlgError("singular"), ValueError("bad")]
)
def test_garch_falls_back_when_the_optimiser_raises(
    path: pd.Series, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr(volatility, "_fit_garch", _fake_fit(error=error))
    with capture_logs() as logs:
        _assert_fell_back(path, "fit failed", logs)


@pytest.mark.parametrize(
    "params",
    [
        {"alpha": 0.3, "beta": 0.75},  # alpha + β ≥ 1: no long-run variance
        {"alpha": 0.1, "beta": 0.9},  # exactly integrated
        {"omega": 0.0},
        {"alpha": -0.01},
        {"beta": float("nan")},
    ],
)
def test_garch_falls_back_on_non_stationary_parameters(
    path: pd.Series, monkeypatch: pytest.MonkeyPatch, params: dict[str, float]
) -> None:
    monkeypatch.setattr(volatility, "_fit_garch", _fake_fit(_result(**params)))
    with capture_logs() as logs:
        _assert_fell_back(path, "invalid parameters", logs)


def test_garch_falls_back_on_short_history(
    path: pd.Series, monkeypatch: pytest.MonkeyPatch
) -> None:
    fit = _fake_fit(error=AssertionError("the fit must not run"))
    monkeypatch.setattr(volatility, "_fit_garch", fit)
    short = path.iloc[-200:]  # >= min_history (63), < garch_min_obs (252)
    with capture_logs() as logs:
        _assert_fell_back(short, "garch_min_obs", logs)
    assert fit.calls == []
    with pytest.raises(DataError, match="min_history"):
        garch_vol(path.iloc[-40:])  # below min_history: no forecast at all


def test_garch_ignores_nan_returns(path: pd.Series, monkeypatch: pytest.MonkeyPatch) -> None:
    holed = path.iloc[-1000:].copy()
    holed.iloc[[10, 500, 998]] = np.nan
    clean = holed.dropna()
    a, b = garch_vol(holed), garch_vol(clean)
    assert a.n_obs == 997 and a.fallback is None
    assert a.variance == pytest.approx(b.variance, rel=1e-12)
    fit = _fake_fit(_result(flag=1))
    monkeypatch.setattr(volatility, "_fit_garch", fit)
    garch_vol(holed)
    (sent,) = fit.calls
    assert len(sent) == 997 and not np.isnan(sent).any()
    assert np.allclose(sent, clean.to_numpy() * 100.0)  # percent units
