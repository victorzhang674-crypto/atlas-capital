"""Shrinkage estimators: Ledoit-Wolf (constant-correlation target) and OAS (Stage 18, brief
task 2)."""

import numpy as np
import pandas as pd
import pytest
from risk_world import (
    constant_corr_sigma,
    factor_sigma,
    frobenius,
    mvn_returns,
    sessions,
    standard_errors,
)

from atlas.quant.risk_model.covariance import (
    LedoitWolfCovariance,
    OASCovariance,
    SampleCovariance,
)

LOOSE = {"max_condition": 1e15}  # compare the estimators' own maths, not the condition floor


def _lw_literal(x: np.ndarray) -> tuple[float, np.ndarray]:
    """Ledoit & Wolf (2004), "Honey, I shrunk the sample covariance matrix", written out with
    loops: S divided by T, constant-correlation target F, intensity max(0, min(1, κ/T))."""
    t, n = x.shape
    x = x - x.mean(axis=0)
    s = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            s[i, j] = sum(x[k, i] * x[k, j] for k in range(t)) / t
    sd = [s[i, i] ** 0.5 for i in range(n)]
    rbar = sum(s[i, j] / (sd[i] * sd[j]) for i in range(n) for j in range(n) if i != j)
    rbar /= n * (n - 1)
    f = np.array(
        [[s[i, i] if i == j else rbar * sd[i] * sd[j] for j in range(n)] for i in range(n)]
    )
    pi = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            pi[i, j] = sum((x[k, i] * x[k, j] - s[i, j]) ** 2 for k in range(t)) / t

    def theta(i: int, j: int) -> float:  # asymptotic covariance of √T s_ii and √T s_ij
        return sum((x[k, i] ** 2 - s[i, i]) * (x[k, i] * x[k, j] - s[i, j]) for k in range(t)) / t

    rho = sum(pi[i, i] for i in range(n))
    for i in range(n):
        for j in range(n):
            if i != j:
                rho += (
                    rbar
                    / 2
                    * (
                        (s[j, j] / s[i, i]) ** 0.5 * theta(i, j)
                        + (s[i, i] / s[j, j]) ** 0.5 * theta(j, i)
                    )
                )
    gamma = sum((f[i, j] - s[i, j]) ** 2 for i in range(n) for j in range(n))
    kappa = (pi.sum() - rho) / gamma
    delta = max(0.0, min(1.0, kappa / t))
    return delta, delta * f + (1 - delta) * s


def test_ledoit_wolf_matches_literal_formula() -> None:
    for seed in (0, 1, 2):
        frame = mvn_returns(factor_sigma(4, seed=seed), 30, seed=20 + seed)
        res = LedoitWolfCovariance(min_history=20, **LOOSE).estimate(frame)
        delta, expected = _lw_literal(frame.to_numpy())
        assert res.shrinkage == pytest.approx(delta, rel=1e-12, abs=1e-15)
        assert 0 < delta < 1  # an interior intensity: the formula, not the clamp, is checked
        assert np.allclose(res.cov.to_numpy(), expected, rtol=1e-12, atol=0)
        assert res.method == "ledoit_wolf"


def test_ledoit_wolf_negative_intensity_is_clamped_to_zero() -> None:
    # A one-factor sample of 3 names over 9 sessions whose estimated kappa / T is -5.78
    # (found by search, worklog Task 12): the intensity is clamped to 0, the result is S.
    rng = np.random.default_rng(8224)
    n, t = int(rng.integers(3, 5)), int(rng.integers(5, 30))
    base = rng.normal(size=(t, 1))
    x = base * rng.uniform(0.5, 2, n) + rng.normal(size=(t, n)) * 0.3
    assert (n, t) == (3, 9)
    frame = pd.DataFrame(x, index=sessions(t), columns=["A", "B", "C"])
    res = LedoitWolfCovariance(min_history=9, **LOOSE).estimate(frame)
    delta, expected = _lw_literal(x)
    assert delta == 0.0 and res.shrinkage == 0.0
    xc = x - x.mean(axis=0)
    assert np.allclose(res.cov.to_numpy(), xc.T @ xc / t, rtol=1e-12, atol=0)
    assert np.allclose(res.cov.to_numpy(), expected, rtol=1e-12, atol=0)


def test_oas_matches_paper_formula() -> None:
    # Chen, Wiesel, Eldar & Hero (2010), eq. (23), with S divided by T and target tr(S)/p · I.
    x = np.random.default_rng(3).normal(size=(40, 3)) * [0.01, 0.02, 0.03]
    t, p = x.shape
    xc = x - x.mean(axis=0)
    s = xc.T @ xc / t
    tr, tr2 = np.trace(s), np.trace(s @ s)
    rho = min(1.0, ((1 - 2 / p) * tr2 + tr**2) / ((t + 1 - 2 / p) * (tr2 - tr**2 / p)))
    expected = (1 - rho) * s + rho * tr / p * np.eye(p)
    frame = pd.DataFrame(x, index=sessions(t), columns=["A", "B", "C"])
    res = OASCovariance(min_history=6, **LOOSE).estimate(frame)
    assert 0 < rho < 1  # interior (0.224): the formula, not the cap, is checked
    assert res.shrinkage == pytest.approx(rho, rel=1e-12)
    assert np.allclose(res.cov.to_numpy(), expected, rtol=1e-12, atol=0)
    assert res.method == "oas"
    # A scaled-identity sample: nothing to estimate beyond the target, full shrinkage.
    eye = pd.DataFrame(
        [[1.0, 0.0], [-1.0, 0.0], [0.0, 1.0], [0.0, -1.0]], index=sessions(4), columns=["A", "B"]
    )
    assert OASCovariance(min_history=4, **LOOSE).estimate(eye).shrinkage == 1.0


def _errors(sigma: np.ndarray, t: int, seeds: range) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {"sample": [], "ledoit_wolf": [], "oas": []}
    for seed in seeds:
        frame = mvn_returns(sigma, t, seed=seed)
        for est in (
            SampleCovariance(min_history=20, **LOOSE),
            LedoitWolfCovariance(min_history=20, **LOOSE),
            OASCovariance(min_history=20, **LOOSE),
        ):
            out[est.method].append(frobenius(est.estimate(frame).matrix, sigma))
    return out


def _truth(kind: str, n: int) -> np.ndarray:
    if kind == "constant_correlation":
        return constant_corr_sigma(n, 0.3, seed=1)
    if kind == "factor":
        return factor_sigma(n, seed=1)
    return 0.02**2 * np.eye(n)  # spherical: OAS's own target


# N = 100 names, T = 50 sessions (N/T = 2), 20 paths. Thresholds sit below the measured values
# (worklog, Task 6): Ledoit-Wolf / sample mean error 0.47 (constant correlation) and 0.87
# (factor), wins 20/20 and 18/20; OAS / sample 0.87 (factor), wins 17/20.
@pytest.mark.parametrize(
    ("truth", "max_ratio", "min_wins"), [("constant_correlation", 0.6, 18), ("factor", 0.95, 15)]
)
def test_ledoit_wolf_beats_sample_when_n_over_t_large(
    truth: str, max_ratio: float, min_wins: int
) -> None:
    err = _errors(_truth(truth, 100), 50, range(20))
    wins = sum(lw < s for lw, s in zip(err["ledoit_wolf"], err["sample"], strict=True))
    assert wins >= min_wins
    assert np.mean(err["ledoit_wolf"]) < max_ratio * np.mean(err["sample"])


@pytest.mark.parametrize(
    ("truth", "max_ratio", "min_wins"), [("spherical", 0.5, 20), ("factor", 0.95, 14)]
)
def test_oas_beats_sample_when_n_over_t_large(truth: str, max_ratio: float, min_wins: int) -> None:
    err = _errors(_truth(truth, 100), 50, range(20))
    wins = sum(o < s for o, s in zip(err["oas"], err["sample"], strict=True))
    assert wins >= min_wins
    assert np.mean(err["oas"]) < max_ratio * np.mean(err["sample"])


def test_shrinkage_vanishes_with_long_history() -> None:
    sigma = factor_sigma(5, seed=3)
    frame = mvn_returns(sigma, 20000, seed=4)
    for est in (LedoitWolfCovariance(), OASCovariance()):
        res = est.estimate(frame)
        assert res.shrinkage is not None and res.shrinkage < 0.02, (est.method, res.shrinkage)
        assert (np.abs(res.matrix - sigma) <= 4 * standard_errors(sigma, 20000)).all()


def test_shrinkage_in_unit_interval() -> None:
    rng = np.random.default_rng(5)
    for n, t in ((2, 30), (10, 12), (30, 40), (5, 2000)):
        for seed in range(5):
            sigma = factor_sigma(n, seed=int(rng.integers(1000)))
            frame = mvn_returns(sigma, t, seed=seed)
            for est in (LedoitWolfCovariance(min_history=2), OASCovariance(min_history=2)):
                res = est.estimate(frame)
                assert res.shrinkage is not None and 0.0 <= res.shrinkage <= 1.0
    one = mvn_returns(np.array([[4e-4]]), 100, seed=1)
    assert LedoitWolfCovariance().estimate(one).shrinkage == 0.0  # one name: nothing to pool


def test_shrinkage_recovers_known_sigma() -> None:
    sigma = factor_sigma(5, seed=6)
    frame = mvn_returns(sigma, 5000, seed=7)
    for est in (LedoitWolfCovariance(), OASCovariance()):
        res = est.estimate(frame)
        err = np.abs(res.matrix - sigma)
        assert (err <= 4 * standard_errors(sigma, 5000)).all(), (est.method, err)
        assert res.adjustments == ()
