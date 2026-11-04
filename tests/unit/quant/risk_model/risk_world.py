"""Synthetic worlds for the Stage 18 risk-model tests: known covariance matrices, multivariate
normal daily returns and GARCH(1,1) paths, all seeded."""

import numpy as np
import pandas as pd


def names(n: int) -> list[str]:
    return [f"S{i:02d}" for i in range(n)]


def sessions(t: int, start: str = "2015-01-02") -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.bdate_range(start, periods=t), name="date")


def vols(n: int, seed: int) -> np.ndarray:
    """Daily vols between 1% and 3%."""
    return np.random.default_rng(seed).uniform(0.01, 0.03, size=n)


def from_corr(corr: np.ndarray, vol: np.ndarray) -> np.ndarray:
    return corr * np.outer(vol, vol)


def factor_sigma(n: int, k: int = 3, seed: int = 0) -> np.ndarray:
    """A covariance from `k` factors plus specific risk: correlations mostly 0.1-0.6."""
    rng = np.random.default_rng(seed)
    loadings = rng.uniform(0.2, 0.6, size=(n, k)) * rng.choice([1.0, 1.0, 1.0, -1.0], (n, k))
    common = loadings @ loadings.T
    specific = rng.uniform(0.3, 0.8, size=n)
    raw = common + np.diag(specific)
    d = np.sqrt(np.diag(raw))
    return from_corr(raw / np.outer(d, d), vols(n, seed + 1))


def constant_corr_sigma(n: int, rho: float = 0.3, seed: int = 0) -> np.ndarray:
    """Heterogeneous vols, every correlation `rho` (the Ledoit-Wolf target's shape)."""
    corr = np.full((n, n), rho)
    np.fill_diagonal(corr, 1.0)
    return from_corr(corr, vols(n, seed))


def mvn_returns(sigma: np.ndarray, t: int, seed: int) -> pd.DataFrame:
    """`t` sessions of zero-mean multivariate normal daily returns with covariance `sigma`."""
    rng = np.random.default_rng(seed)
    draws = rng.multivariate_normal(np.zeros(len(sigma)), sigma, size=t, method="cholesky")
    return pd.DataFrame(draws, index=sessions(t), columns=names(len(sigma)))


def standard_errors(sigma: np.ndarray, t: float) -> np.ndarray:
    """Standard error of each sample covariance entry under normality:
    √((sigma_ii sigma_jj + sigma_ij²) / t)."""
    d = np.diag(sigma)
    return np.sqrt((np.outer(d, d) + sigma**2) / t)


def frobenius(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


def simulate_garch(omega: float, alpha: float, beta: float, t: int, seed: int) -> pd.Series:
    """A zero-mean GARCH(1,1) path with normal shocks; parameters in percent units (as `arch`
    fits them), returned as daily fractions. 500 burn-in sessions are discarded."""
    rng = np.random.default_rng(seed)
    burn = 500
    z = rng.standard_normal(t + burn)
    var = omega / (1.0 - alpha - beta)
    out = np.empty(t + burn)
    for i in range(t + burn):
        out[i] = np.sqrt(var) * z[i]
        var = omega + alpha * out[i] ** 2 + beta * var
    return pd.Series(out[burn:] / 100.0, index=sessions(t), name="G")
