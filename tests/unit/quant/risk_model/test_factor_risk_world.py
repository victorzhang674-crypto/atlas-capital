"""The Stage 19 synthetic world is deterministic, follows its own model and satisfies the
cap-weighted sector constraint (Task 1)."""

import numpy as np
import pandas as pd
from factor_risk_world import SECTORS, STYLES, make_world


def test_world_shapes_and_determinism() -> None:
    a, b = make_world(n=120, t=40, seed=3), make_world(n=120, t=40, seed=3)
    assert a.returns.shape == (40, 120)
    assert a.exposures.shape == (120, 1 + len(SECTORS) + len(STYLES))
    assert list(a.factor_returns.columns) == ["market", *SECTORS, *STYLES]
    pd.testing.assert_frame_equal(a.returns, b.returns)
    pd.testing.assert_frame_equal(a.exposures, b.exposures)
    assert not a.returns.equals(make_world(n=120, t=40, seed=4).returns)
    assert a.sectors.nunique() == len(SECTORS)


def test_world_returns_follow_the_model() -> None:
    w = make_world(n=120, t=40, seed=1)
    implied = w.factor_returns.to_numpy() @ w.exposures.to_numpy().T + w.specific_returns.to_numpy()
    np.testing.assert_allclose(w.returns.to_numpy(), implied, atol=1e-14)


def test_world_factors_satisfy_the_sector_constraint() -> None:
    w = make_world(n=200, t=60, seed=2)
    shares = w.caps.groupby(w.sectors).sum() / w.caps.sum()
    sector_returns = w.factor_returns[list(SECTORS)]
    np.testing.assert_allclose(
        sector_returns.to_numpy() @ shares.reindex(list(SECTORS)).to_numpy(), 0.0, atol=1e-15
    )


def test_world_style_exposures_standardised() -> None:
    w = make_world(n=300, t=5, seed=5)
    weights = w.caps / w.caps.sum()
    for style in STYLES:
        x = w.exposures[style]
        assert abs(float((weights * x).sum())) < 1e-12
        assert abs(float(x.std(ddof=0)) - 1.0) < 1e-12


def test_world_true_cov_matches_the_projected_draws() -> None:
    w = make_world(n=100, t=20000, seed=7, factor_only=True, business_days=True)
    sample = np.cov(w.factor_returns.to_numpy().T, bias=True)
    scale = np.sqrt(np.outer(np.diag(w.true_factor_cov), np.diag(w.true_factor_cov)))
    assert np.max(np.abs(sample - w.true_factor_cov.to_numpy()) / scale) < 0.06
