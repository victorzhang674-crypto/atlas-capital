"""Stage 21 Task 2: the Gaussian HMM recovers synthetic states; labels are ordered by
volatility and stable across refits; filtered probabilities match hmmlearn."""

import numpy as np
import pandas as pd
import pytest
from hmmlearn.hmm import GaussianHMM
from regime_synthetic import regime_features

from atlas.core.errors import DataError
from atlas.quant.regime.hmm import HMMFit, filter_probabilities, fit_hmm, state_labels


def _accuracy(probs: pd.DataFrame, truth: np.ndarray) -> float:
    return float(np.mean(probs.to_numpy().argmax(axis=1) == truth))


@pytest.fixture(scope="module")
def two_state() -> tuple[pd.DataFrame, np.ndarray, HMMFit]:
    feats, truth = regime_features(1500, 2, seed=7)
    return feats, truth, fit_hmm(feats, 2, seed=1)


def test_state_labels() -> None:
    assert state_labels(2) == ("calm", "turbulent")
    assert state_labels(3) == ("calm", "normal", "turbulent")
    with pytest.raises(ValueError):
        state_labels(4)


def test_two_state_recovery_above_85_percent(two_state) -> None:  # type: ignore[no-untyped-def]
    feats, truth, fit = two_state
    probs = filter_probabilities(fit, feats)
    assert list(probs.columns) == ["calm", "turbulent"]
    assert _accuracy(probs, truth) > 0.85


def test_three_state_recovery_above_85_percent() -> None:
    feats, truth = regime_features(2500, 3, seed=11)
    fit = fit_hmm(feats, 3, seed=2)
    assert _accuracy(filter_probabilities(fit, feats), truth) > 0.85


def test_states_are_ordered_by_volatility(two_state) -> None:  # type: ignore[no-untyped-def]
    fit = two_state[2]
    assert fit.labels == ("calm", "turbulent")
    assert np.all(np.diff(fit.order_values) > 0)
    # the turbulent state's mean vol (original units) is near the true turbulent mean 0.32
    assert fit.order_values[-1] == pytest.approx(0.32, abs=0.02)
    assert fit.order_values[0] == pytest.approx(0.11, abs=0.02)


def test_order_does_not_depend_on_hmmlearn_internal_order() -> None:
    """Different seeds can make hmmlearn number the states differently; after ordering the
    calm state is always first."""
    feats, truth = regime_features(1500, 2, seed=7)
    for seed in range(6):
        fit = fit_hmm(feats, 2, seed=seed, n_init=1)
        assert _accuracy(filter_probabilities(fit, feats), truth) > 0.85


def test_filtered_last_row_equals_hmmlearn_predict_proba(two_state) -> None:  # type: ignore[no-untyped-def]
    """Filtered P(state_t | x_1..t) is the smoothed posterior of the last row of x_1..t."""
    feats, _, fit = two_state
    model = GaussianHMM(n_components=2, covariance_type="full")
    model.startprob_ = fit.startprob
    model.transmat_ = fit.transmat
    model.means_ = fit.means
    model.covars_ = fit.covars
    x = ((feats[list(fit.columns)] - fit.mean) / fit.scale).to_numpy()
    probs = filter_probabilities(fit, feats)
    for t in (1, 2, 50, 377, 1000, 1500):
        expected = model.predict_proba(x[:t])[-1]
        np.testing.assert_allclose(probs.iloc[t - 1].to_numpy(), expected, atol=1e-8)


def test_filtered_probabilities_sum_to_one(two_state) -> None:  # type: ignore[no-untyped-def]
    feats, _, fit = two_state
    probs = filter_probabilities(fit, feats)
    np.testing.assert_allclose(probs.sum(axis=1).to_numpy(), 1.0, atol=1e-12)
    assert probs.index.equals(feats.index)


def test_label_stability_across_expanding_refits() -> None:
    """Refits on expanding windows keep the same label for each true regime."""
    feats, truth = regime_features(2400, 2, seed=5)
    for end in (800, 1200, 1600, 2000, 2400):
        fit = fit_hmm(feats.iloc[:end], 2, seed=3)
        probs = filter_probabilities(fit, feats.iloc[:end])
        pred = probs.to_numpy().argmax(axis=1)
        assert np.mean(pred == truth[:end]) > 0.85
        for k in (0, 1):  # the majority label of each true regime is the same at every refit
            assert np.bincount(pred[truth[:end] == k], minlength=2).argmax() == k


def test_fit_is_deterministic_and_records_its_window(two_state) -> None:  # type: ignore[no-untyped-def]
    feats, _, fit = two_state
    again = fit_hmm(feats, 2, seed=1)
    np.testing.assert_array_equal(fit.means, again.means)
    np.testing.assert_array_equal(fit.transmat, again.transmat)
    assert fit.n_obs == 1500
    assert fit.fit_start == feats.index[0] and fit.fit_end == feats.index[-1]
    np.testing.assert_allclose(fit.mean, feats.mean().to_numpy())
    np.testing.assert_allclose(fit.scale, feats.std(ddof=0).to_numpy())
    np.testing.assert_allclose(fit.transmat.sum(axis=1), 1.0)
    assert np.isfinite(fit.log_likelihood)


def test_fit_arrays_are_read_only(two_state) -> None:  # type: ignore[no-untyped-def]
    fit = two_state[2]
    with pytest.raises(ValueError):
        fit.means[0, 0] = 1.0


def test_best_of_several_starts_is_kept() -> None:
    feats, _ = regime_features(800, 2, seed=9)
    single = [fit_hmm(feats, 2, seed=s, n_init=1).log_likelihood for s in range(4)]
    assert fit_hmm(feats, 2, seed=0, n_init=4).log_likelihood == pytest.approx(max(single))


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda f: f.assign(ret=np.nan), DataError),
        (lambda f: f.iloc[:10], DataError),
        (lambda f: f.assign(slope=1.0), DataError),
        (lambda f: f.drop(columns="vol"), ValueError),
        (lambda f: f.iloc[::-1], ValueError),
    ],
)
def test_fit_rejects_bad_features(mutate, error) -> None:  # type: ignore[no-untyped-def]
    feats, _ = regime_features(300, 2, seed=1)
    with pytest.raises(error):
        fit_hmm(mutate(feats), 2, seed=0)


def test_filter_rejects_missing_values(two_state) -> None:  # type: ignore[no-untyped-def]
    feats, _, fit = two_state
    bad = feats.copy()
    bad.iloc[3, 0] = np.nan
    with pytest.raises(DataError):
        filter_probabilities(fit, bad)


def test_bad_state_count() -> None:
    feats, _ = regime_features(300, 2, seed=1)
    with pytest.raises(ValueError):
        fit_hmm(feats, 1, seed=0)


def test_degenerate_starts_are_skipped_and_all_degenerate_raises() -> None:
    """A state that holds (almost) no rows is degenerate: with an impossible share every start
    is rejected (DataError); with the default share the same data fits."""
    feats, _ = regime_features(600, 2, seed=1)
    assert fit_hmm(feats, 2, seed=0, n_init=1).n_states == 2
    with pytest.raises(DataError, match="degenerate"):
        fit_hmm(feats, 2, seed=0, n_init=2, min_state_share=0.49)


def test_collapsed_state_is_rejected() -> None:
    """One-regime data asked for 3 states: a start whose extra state collapses is skipped."""
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2015-01-01", periods=700)
    feats = pd.DataFrame(
        rng.normal(size=(700, 4)), index=idx, columns=["ret", "vol", "credit", "slope"]
    )
    feats.iloc[650:] = rng.normal(size=(50, 4)) * 5  # a few wild rows
    try:
        fit = fit_hmm(feats, 3, seed=21, n_init=3, min_state_share=0.05)
    except DataError:
        return  # every start degenerate: reported, never a singular fit
    probs = filter_probabilities(fit, feats)
    assert np.isfinite(probs.to_numpy()).all()
    assert all(np.linalg.eigvalsh(c).min() > 1e-10 for c in fit.covars)
