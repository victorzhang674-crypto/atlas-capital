"""Stage 21 Task 3: the HMM's expanding window is point in time (a fit for month M uses only
rows before M; a probability for day t only rows before t)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
from regime_synthetic import regime_features

from atlas.core.config import RegimeHMMConfig
from atlas.quant.regime.hmm import expanding_probabilities, fit_hmm

CFG = RegimeHMMConfig(n_states=2, min_obs=400, n_init=2)


@pytest.fixture(scope="module")
def feats() -> pd.DataFrame:
    return regime_features(1100, 2, seed=4)[0]


def _decisions(feats: pd.DataFrame, start: int, stop: int) -> list[date]:
    """Decision days = the sessions after the rows start..stop (the next row's date)."""
    return [d.date() for d in feats.index[start:stop]]


def test_one_fit_per_month_on_rows_before_the_month(feats: pd.DataFrame) -> None:
    days = _decisions(feats, 600, 700)
    _, fits = expanding_probabilities(feats, days, CFG)
    months = sorted({pd.Timestamp(d.year, d.month, 1) for d in days})
    assert list(fits) == months
    for month, fit in fits.items():
        assert fit is not None
        assert fit.fit_end < month  # every training row is before the month's first day
        expected = feats.loc[feats.index < month]
        assert fit.n_obs == len(expected) and fit.fit_start == feats.index[0]
        assert fit.fit_end == expected.index[-1]
        same = fit_hmm(
            expected, 2, seed=CFG.seed, n_init=CFG.n_init, n_iter=CFG.n_iter, tol=CFG.tol
        )
        np.testing.assert_array_equal(fit.means, same.means)


def test_probability_of_a_day_is_the_filtered_row_before_it(feats: pd.DataFrame) -> None:
    from atlas.quant.regime.hmm import filter_probabilities

    days = _decisions(feats, 650, 680)
    probs, fits = expanding_probabilities(feats, days, CFG)
    for day in days:
        fit = fits[pd.Timestamp(day.year, day.month, 1)]
        assert fit is not None
        prior = feats.loc[feats.index < pd.Timestamp(day)]
        expected = filter_probabilities(fit, prior).iloc[-1]
        np.testing.assert_allclose(probs.loc[pd.Timestamp(day)].to_numpy(), expected.to_numpy())


def test_future_rows_change_nothing(feats: pd.DataFrame) -> None:
    """Shift test: replace every row from session t on (incl. later months' fit windows) with
    noise; every probability for a decision day <= t is unchanged."""
    days = _decisions(feats, 600, 760)
    cut = pd.Timestamp(days[90])
    base, _ = expanding_probabilities(feats, days, CFG)
    rng = np.random.default_rng(0)
    noisy = feats.copy()
    later = noisy.index >= cut
    noisy.loc[later] = rng.normal(size=(int(later.sum()), noisy.shape[1])) * 5
    shifted, _ = expanding_probabilities(noisy, days, CFG)
    known = base.index <= cut
    pd.testing.assert_frame_equal(base.loc[known], shifted.loc[known])
    assert not base.loc[~known].equals(shifted.loc[~known])  # the test can see a change


def test_truncated_history_gives_the_same_probabilities(feats: pd.DataFrame) -> None:
    days = _decisions(feats, 600, 700)
    full, _ = expanding_probabilities(feats, days, CFG)
    cut = pd.Timestamp(days[50])
    trunc, _ = expanding_probabilities(
        feats.loc[feats.index < cut], [d for d in days if pd.Timestamp(d) <= cut], CFG
    )
    pd.testing.assert_frame_equal(full.loc[full.index <= cut], trunc)


def test_no_fit_before_min_obs(feats: pd.DataFrame) -> None:
    days = _decisions(feats, 300, 460)
    probs, fits = expanding_probabilities(feats, days, CFG)
    for month, fit in fits.items():
        rows = int((feats.index < month).sum())
        assert (fit is None) == (rows < CFG.min_obs)
    early = [pd.Timestamp(d) for d in days if fits[pd.Timestamp(d.year, d.month, 1)] is None]
    assert early and probs.loc[early].isna().all().all()
    late = probs.dropna()
    assert len(late) and np.allclose(late.sum(axis=1), 1.0)


def test_day_without_prior_rows_is_missing(feats: pd.DataFrame) -> None:
    cfg = RegimeHMMConfig(n_states=2, min_obs=50, n_init=1)
    probs, _ = expanding_probabilities(feats.iloc[:200], [date(2011, 6, 1)], cfg)
    assert probs.isna().all().all()


def test_failed_month_fit_is_missing_not_an_error(feats: pd.DataFrame) -> None:
    """Every start degenerate in a month → that month has no fit and NaN probabilities."""
    cfg = RegimeHMMConfig(n_states=2, min_obs=400, n_init=1, min_state_share=0.49)
    days = _decisions(feats, 600, 640)
    probs, fits = expanding_probabilities(feats, days, cfg)
    assert all(f is None for f in fits.values())
    assert probs.isna().all().all()
