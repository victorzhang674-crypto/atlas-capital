"""Independent re-derivations of the metrics that have no empyrical reference (Stage 20
post-DONE audit): each test computes the number by a different algorithm than the library."""

import math
from collections import defaultdict

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from atlas.quant.metrics import (
    cornish_fisher_cvar,
    cornish_fisher_var,
    exposure_summary,
    liquidity_profile,
    risk_contributions,
)

# --- Cornish-Fisher: simulate the transformed variable ---------------------------------------


@pytest.mark.parametrize(("skew", "kurt"), [(-0.6, 3.0), (0.3, 1.0), (-0.3, 5.0)])
@pytest.mark.parametrize("conf", [0.95, 0.975, 0.99])
def test_cornish_fisher_matches_simulation(skew: float, kurt: float, conf: float) -> None:
    """VaR / CVaR of X = mu + sigma z_cf(Z), Z ~ N(0, 1), measured empirically on a stratified
    sample of 2,000,000 normal points z_i = Phi^-1((i - 1/2) / n) (no sampling luck: a random
    seed's 2.5% quantile can sit 3 standard errors off, which the audit run showed)."""
    mu, sigma = 0.0003, 0.012
    n = 2_000_000
    z = stats.norm.ppf((np.arange(1, n + 1) - 0.5) / n)
    zcf = z + (z**2 - 1) * skew / 6 + (z**3 - 3 * z) * kurt / 24 - (2 * z**3 - 5 * z) * skew**2 / 36
    x = mu + sigma * zcf
    q = np.quantile(x, 1 - conf)
    assert cornish_fisher_var(mu, sigma, skew, kurt, conf) == pytest.approx(-q, rel=1e-4)
    cvar, floored = cornish_fisher_cvar(mu, sigma, skew, kurt, conf)
    assert not floored
    assert cvar == pytest.approx(-float(x[x <= q].mean()), rel=1e-3)


# --- contributions: covariance form ----------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_contributions_equal_the_covariance_form(seed: int) -> None:
    """component_i = w_i cov(r_i, r_p) / sigma_p on a sample, with r_p = R w."""
    rng = np.random.default_rng(seed)
    n, t = 8, 400
    mix = rng.normal(size=(n, n)) * 0.01
    data = rng.standard_normal((t, n)) @ mix.T
    w = rng.normal(size=n) * 0.1
    names = [f"S{i}" for i in range(n)]
    cov = pd.DataFrame(np.cov(data, rowvar=False, ddof=1), index=names, columns=names)
    res = risk_contributions(pd.Series(w, index=names), cov)
    rp = data @ w
    sigma_p = float(np.std(rp, ddof=1))
    assert res.vol == pytest.approx(sigma_p, rel=1e-10)
    for i, name in enumerate(names):
        cov_ip = float(np.cov(data[:, i], rp, ddof=1)[0, 1])
        assert res.component[name] == pytest.approx(w[i] * cov_ip / sigma_p, rel=1e-9, abs=1e-15)


# --- liquidity: day-by-day liquidation -------------------------------------------------------


def _simulate(positions: dict[str, float], adv: dict[str, float], rate: float, horizon: int):
    """Sell min(remaining, rate x ADV) of every name each session; record what is left."""
    remaining = {s: abs(v) for s, v in positions.items()}
    left_after: list[dict[str, float]] = []
    for _ in range(horizon):
        for s in remaining:
            cap = rate * adv.get(s, 0.0) if adv.get(s, 0.0) == adv.get(s, 0.0) else 0.0
            remaining[s] = max(remaining[s] - max(cap, 0.0), 0.0)
        left_after.append(dict(remaining))
    return left_after


@pytest.mark.parametrize("seed", range(6))
def test_liquidity_profile_equals_a_liquidation_simulation(seed: int) -> None:
    rng = np.random.default_rng(seed)
    names = [f"N{i}" for i in range(25)]
    pos = {s: float(rng.choice([-1, 1]) * rng.uniform(1e5, 5e6)) for s in names}
    pos[names[0]] = 0.0
    adv = {s: float(rng.uniform(1e6, 3e7)) for s in names}
    adv[names[1]] = 0.0  # no market: never sold
    del adv[names[2]]  # no ADV at all
    rate = float(rng.choice([0.05, 0.10, 0.25]))
    prof = liquidity_profile(
        pd.Series(pos), pd.Series(adv), participation=rate, horizons=[1, 2, 3, 5, 10]
    )
    left = _simulate(pos, adv, rate, 10)
    held = [s for s in names if pos[s] != 0.0]
    gross = sum(abs(pos[s]) for s in names)
    for h in (1, 2, 3, 5, 10):
        done = sum(1 for s in held if left[h - 1][s] <= 1e-6 * abs(pos[s]))
        sold = sum(abs(pos[s]) - left[h - 1][s] for s in names)
        assert prof.names_liquid[h] == pytest.approx(done / len(held)), h
        assert prof.gross_liquid[h] == pytest.approx(sold / gross, rel=1e-12), h
    assert set(prof.no_adv) == {names[1], names[2]}
    for s in held:
        if s in prof.no_adv:
            assert math.isinf(prof.days[s])
        else:
            assert prof.days[s] == pytest.approx(abs(pos[s]) / (rate * adv[s]), rel=1e-12)


# --- exposure: plain loops -------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(6))
def test_exposures_equal_plain_loops(seed: int) -> None:
    rng = np.random.default_rng(seed)
    names = [f"N{i}" for i in range(30)]
    w = {s: float(rng.normal(0, 0.02)) if rng.random() > 0.1 else 0.0 for s in names}
    betas = {s: float(rng.uniform(0.2, 2.0)) for s in names}
    sectors = {s: str(rng.choice(["Energy", "Utilities", "Financials"])) for s in names[:-3]}
    res = exposure_summary(pd.Series(w), betas=pd.Series(betas), sectors=sectors)
    gross = sum(abs(v) for v in w.values())
    long_ = sum(v for v in w.values() if v > 0)
    short = sum(v for v in w.values() if v < 0)
    by_sector: dict[str, float] = defaultdict(float)
    for s, v in w.items():
        by_sector[sectors.get(s, "Unclassified")] += v
    assert res.gross == pytest.approx(gross) and res.net == pytest.approx(sum(w.values()))
    assert res.long == pytest.approx(long_) and res.short == pytest.approx(short)
    assert res.n_long == sum(v > 0 for v in w.values())
    assert res.n_short == sum(v < 0 for v in w.values())
    assert res.beta_adjusted_net == pytest.approx(sum(w[s] * betas[s] for s in names))
    assert res.sector_net is not None
    assert res.sector_net.to_dict() == pytest.approx(dict(by_sector))
    assert set(res.unclassified) == {s for s in names[-3:] if w[s] != 0.0}


def test_normal_cdf_reference() -> None:
    """The stdlib normal used by the library agrees with scipy at the limit confidences."""
    from atlas.quant.metrics import normal_cvar, normal_var

    for conf in (0.95, 0.975, 0.99):
        z = stats.norm.ppf(1 - conf)
        assert normal_var(0.0, 1.0, conf) == pytest.approx(-z, rel=1e-12)
        assert normal_cvar(0.0, 1.0, conf) == pytest.approx(
            stats.norm.expect(lambda v: -v, ub=z) / (1 - conf), rel=1e-8
        )
