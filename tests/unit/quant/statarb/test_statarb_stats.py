"""Stage 23 Task 2 (brief task 2): half-life and Hurst exponent."""

import math

import numpy as np
import pytest
from statarb_world import ou_spread

from atlas.quant.statarb.selection import half_life, hurst_exponent


def _ar1(phi: float, n: int, seed: int, sigma: float = 0.01) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.zeros(n)
    for t in range(1, n):
        out[t] = phi * out[t - 1] + rng.normal(0, sigma)
    return out


def test_half_life_of_an_exact_geometric_decay() -> None:
    phi = 0.9
    s = 5.0 * phi ** np.arange(60)  # s_t = phi s_{t-1}, no noise: Δs = (phi - 1) s_{t-1}
    assert half_life(s) == pytest.approx(-math.log(2) / math.log(phi), rel=1e-9)


@pytest.mark.parametrize("hl", [2.0, 5.0, 10.0, 30.0])
def test_half_life_recovers_planted_ou(hl: float) -> None:
    rng = np.random.default_rng(int(hl))
    s = ou_spread(20_000, hl, 0.01, rng)
    assert half_life(s) == pytest.approx(hl, rel=0.12)


def test_half_life_is_shift_invariant() -> None:
    s = _ar1(0.8, 2000, 1)
    assert half_life(s + 7.5) == pytest.approx(half_life(s), rel=1e-9)


def test_half_life_without_mean_reversion() -> None:
    explosive = 1.02 ** np.arange(100)
    assert half_life(explosive) == math.inf
    rng = np.random.default_rng(3)
    walk = np.cumsum(rng.normal(0, 0.01, 504))
    assert half_life(walk) > 60  # a random walk fails any sensible half-life bound
    alternating = np.array([1.0, -1.0] * 50)  # phi = -1: undefined
    assert math.isnan(half_life(alternating))
    assert math.isnan(half_life(np.ones(10)))


def test_half_life_input_checks() -> None:
    with pytest.raises(ValueError):
        half_life([1.0, 2.0])
    with pytest.raises(ValueError):
        half_life([1.0, np.nan, 2.0, 3.0])


def test_hurst_of_a_random_walk_is_about_one_half() -> None:
    values = []
    for seed in range(20):
        rng = np.random.default_rng(seed)
        values.append(hurst_exponent(np.cumsum(rng.normal(0, 0.01, 2000)), 50))
    assert np.mean(values) == pytest.approx(0.5, abs=0.05)


@pytest.mark.parametrize(("hl", "upper"), [(2.0, 0.15), (5.0, 0.25), (10.0, 0.35)])
def test_hurst_of_mean_reverting_spreads_is_low(hl: float, upper: float) -> None:
    rng = np.random.default_rng(10 + int(hl))
    assert hurst_exponent(ou_spread(2000, hl, 0.01, rng), 50) < upper


def test_hurst_of_a_persistent_series_is_above_one_half() -> None:
    # Increments follow an AR(1) with phi 0.6: trending (persistent) moves.
    increments = _ar1(0.6, 4000, 5)
    assert hurst_exponent(np.cumsum(increments), 50) > 0.6


def test_hurst_uses_lags_two_to_max_lag() -> None:
    """The documented definition (mutation check, 2026-11-09): the slope of log std of the
    lagged differences on log lag over lags 2..max_lag."""
    x = np.cumsum(np.random.default_rng(9).normal(size=300))
    lags = np.arange(2, 11)
    sd = [np.std(x[lag:] - x[:-lag]) for lag in lags]
    expected = np.polyfit(np.log(lags), np.log(sd), 1)[0]
    assert hurst_exponent(x, 10) == pytest.approx(expected, rel=1e-12)


def test_hurst_is_scale_and_shift_invariant() -> None:
    rng = np.random.default_rng(7)
    s = ou_spread(1000, 8.0, 0.01, rng)
    assert hurst_exponent(3.0 * s + 2.0, 40) == pytest.approx(hurst_exponent(s, 40), rel=1e-9)


def test_hurst_edge_cases() -> None:
    assert math.isnan(hurst_exponent(np.ones(200), 20))
    with pytest.raises(ValueError):
        hurst_exponent(np.arange(200.0), 2)
    with pytest.raises(ValueError):
        hurst_exponent(np.arange(40.0), 20)  # needs more than 2 x max_lag values
    with pytest.raises(ValueError):
        hurst_exponent(np.r_[np.arange(100.0), np.nan], 20)
