"""Statistical regime: a Gaussian hidden Markov model (hmmlearn) on daily market features
(Stage 21).

Features are a frame indexed by trading session (ascending), one float column per feature, no
missing values (the caller drops incomplete rows). The loader uses `ret` (benchmark simple daily
return, fraction), `vol` (21-session realised vol, annualised fraction), `credit` (credit-spread
proxy change, log-return units, positive = widening) and `slope` (10y - 2y Treasury, percentage
points).

- `fit_hmm` standardises the features with the training window's mean and population std,
  fits `n_init` seeded EM starts and keeps the best log-likelihood. States are **ordered by
  volatility**: ascending mean of the `order_by` feature (`vol`) in original units, so the
  labels (`state_labels`: calm / [normal] / turbulent) mean the same thing after every refit.
- `filter_probabilities` runs the forward algorithm: row `s` is P(state_s | rows ≤ s), the
  **filtered** probability, which never uses a later row (unlike smoothed posteriors).
- `expanding_probabilities` is the point-in-time schedule: the fit used for a decision day `t`
  in calendar month M is fitted on the rows of the sessions before M's first day (an expanding
  window from the first row, refitted once a month); the probability for `t` is the filtered
  probability of the last session before `t`.
"""

import math
import warnings
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from threadpoolctl import threadpool_limits

from atlas.core.config import RegimeHMMConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger

_log = get_logger(__name__)

_LABELS: dict[int, tuple[str, ...]] = {
    2: ("calm", "turbulent"),
    3: ("calm", "normal", "turbulent"),
}


def state_labels(n_states: int) -> tuple[str, ...]:
    """The state labels for `n_states` (2 or 3), calmest first."""
    if n_states not in _LABELS:
        raise ValueError(f"n_states must be 2 or 3, got {n_states}")
    return _LABELS[n_states]


@contextmanager
def _quiet_single_thread() -> Iterator[None]:
    """Run an hmmlearn fit on one BLAS / OpenMP thread (multithreaded reductions in the
    k-means initialisation change the result in the last bits; one thread makes a seeded fit
    bitwise reproducible) and silence three known third-party messages: hmmlearn 0.3.3's
    NumPy 2.5 deprecation (`a.shape = ...` in `hmmlearn.utils`), joblib's physical-core probe
    in a sandbox, and EM's divide-by-zero RuntimeWarnings on empty states (the fit's
    log-likelihood is checked instead)."""
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Setting the shape on a NumPy array", category=DeprecationWarning
        )
        warnings.filterwarnings(
            "ignore", message="Could not find the number of physical cores", category=UserWarning
        )
        warnings.simplefilter("ignore", category=RuntimeWarning)
        yield


def _frozen(array: np.ndarray) -> np.ndarray:
    out = np.array(array, dtype="float64", copy=True)
    out.setflags(write=False)
    return out


@dataclass(frozen=True, eq=False)
class HMMFit:
    """A fitted, volatility-ordered Gaussian HMM. Arrays are read-only and in the
    **standardised** feature space (`(x - mean) / scale`), states ordered as `labels`:
    `startprob` (K), `transmat` (K x K, rows sum to 1), `means` (K x D), `covars`
    (K x D x D, full). `mean` / `scale` (D) are the training window's feature mean and
    population std in original units; `order_values` (K) the state means of the ordering
    feature in original units (ascending). `fit_start` / `fit_end` are the first and last
    sessions used, `n_obs` the rows, `log_likelihood` the training log-likelihood (nats),
    `seed` the seed of the kept start."""

    columns: tuple[str, ...]
    labels: tuple[str, ...]
    mean: np.ndarray
    scale: np.ndarray
    startprob: np.ndarray
    transmat: np.ndarray
    means: np.ndarray
    covars: np.ndarray
    order_by: str
    order_values: np.ndarray
    fit_start: pd.Timestamp
    fit_end: pd.Timestamp
    n_obs: int
    log_likelihood: float
    seed: int

    @property
    def n_states(self) -> int:
        """The number of states."""
        return len(self.labels)


def _check_features(features: pd.DataFrame, columns: Sequence[str] | None = None) -> np.ndarray:
    if not isinstance(features, pd.DataFrame):
        raise TypeError("features must be a DataFrame indexed by session")
    if not features.index.is_monotonic_increasing or features.index.has_duplicates:
        raise ValueError("features must be indexed by unique, ascending sessions")
    if columns is not None:
        missing = [c for c in columns if c not in features.columns]
        if missing:
            raise ValueError(f"features lack the columns {missing}")
        features = features[list(columns)]
    values = features.to_numpy(dtype="float64")
    if not np.isfinite(values).all():
        raise DataError("features hold missing or non-finite values: drop those rows first")
    return values


def fit_hmm(
    features: pd.DataFrame,
    n_states: int,
    *,
    seed: int,
    n_init: int = 5,
    n_iter: int = 200,
    tol: float = 1.0e-4,
    order_by: str = "vol",
    min_state_share: float = 0.01,
) -> HMMFit:
    """Fit a full-covariance Gaussian HMM with `n_states` (2 or 3) states on every row of
    `features` (see the module docstring), from `n_init` EM starts seeded `seed + i`, and
    return the best one with its states ordered by the mean of `order_by` (ascending).

    A start is **degenerate** — and skipped — when a state's covariance is not positive
    definite or the state holds less than `min_state_share` of the rows (mean posterior
    probability), e.g. a collapsed, unvisited state.

    Raises `ValueError` for bad arguments (unknown `order_by`, unsorted index) and `DataError`
    for unusable data (missing values, a constant feature, fewer than 10 rows per free
    parameter of a state, every start degenerate)."""
    if not isinstance(features, pd.DataFrame):
        raise TypeError("features must be a DataFrame indexed by session")
    labels = state_labels(n_states)
    if n_init < 1:
        raise ValueError(f"n_init must be at least 1, got {n_init}")
    if order_by not in features.columns:
        raise ValueError(f"order_by column {order_by!r} is not a feature")
    columns = tuple(map(str, features.columns))
    x = _check_features(features)
    n, d = x.shape
    if n < 10 * n_states * (d + 1):
        raise DataError(f"{n} rows are too few to fit a {n_states}-state HMM on {d} features")
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    if np.any(scale <= 1e-12 * np.maximum(1.0, np.abs(mean))):
        flat = [c for c, s in zip(columns, scale, strict=True) if s <= 0 or not np.isfinite(s)]
        raise DataError(f"constant feature(s) cannot be standardised: {flat or columns}")
    z = (x - mean) / scale
    best: tuple[float, int, GaussianHMM] | None = None
    for i in range(n_init):
        model = GaussianHMM(
            n_components=n_states,
            covariance_type="full",
            n_iter=n_iter,
            tol=tol,
            random_state=seed + i,
        )
        with _quiet_single_thread():
            model.fit(z)
            score = float(model.score(z))
            reason = _degenerate(model, z, min_state_share) if math.isfinite(score) else "score"
        if reason:
            _log.info("regime.hmm_start_skipped", seed=seed + i, reason=reason)
            continue
        if best is None or score > best[0]:
            best = (score, seed + i, model)
    if best is None:
        raise DataError(f"every HMM start ({n_init}) was degenerate or failed")
    score, used, model = best
    j = columns.index(order_by)
    order_std = np.asarray(model.means_)[:, j]
    order = np.argsort(order_std, kind="stable")
    covars = np.asarray(model.covars_)
    return HMMFit(
        columns=columns,
        labels=labels,
        mean=_frozen(mean),
        scale=_frozen(scale),
        startprob=_frozen(np.asarray(model.startprob_)[order]),
        transmat=_frozen(np.asarray(model.transmat_)[np.ix_(order, order)]),
        means=_frozen(np.asarray(model.means_)[order]),
        covars=_frozen(covars[order]),
        order_by=order_by,
        order_values=_frozen(order_std[order] * scale[j] + mean[j]),
        fit_start=pd.Timestamp(features.index[0]),
        fit_end=pd.Timestamp(features.index[-1]),
        n_obs=n,
        log_likelihood=score,
        seed=used,
    )


def _degenerate(model: GaussianHMM, z: np.ndarray, min_state_share: float) -> str:
    """Why a fitted start is unusable ('' when it is fine): a covariance that is not
    positive definite, or a state with a mean posterior share below `min_state_share`."""
    for k, cov in enumerate(np.asarray(model.covars_)):
        if not np.all(np.isfinite(cov)) or np.linalg.eigvalsh(cov).min() <= 1e-10:
            return f"covariance of state {k} is not positive definite"
    share = np.asarray(model.predict_proba(z)).mean(axis=0)
    if share.min() < min_state_share:
        return f"state share {share.min():.4f} below {min_state_share}"
    return ""


def _log_emissions(fit: HMMFit, z: np.ndarray) -> np.ndarray:
    """log N(z_t; means_k, covars_k) for every row t and state k (T x K)."""
    out = np.empty((z.shape[0], fit.n_states))
    d = z.shape[1]
    for k in range(fit.n_states):
        chol = np.linalg.cholesky(fit.covars[k])
        diff = np.linalg.solve(chol, (z - fit.means[k]).T)
        log_det = 2.0 * np.log(np.diag(chol)).sum()
        out[:, k] = -0.5 * (d * math.log(2 * math.pi) + log_det + (diff**2).sum(axis=0))
    return out


def _logsumexp(values: np.ndarray, axis: int) -> np.ndarray:
    top = values.max(axis=axis, keepdims=True)
    return np.squeeze(top, axis=axis) + np.log(np.exp(values - top).sum(axis=axis))


def filter_probabilities(fit: HMMFit, features: pd.DataFrame) -> pd.DataFrame:
    """Forward-filtered state probabilities of every row of `features` under `fit`: row `s` is
    P(state_s | rows ≤ s) (fractions summing to 1), columns `fit.labels`, the same index. The
    sequence starts at the first row with the fit's initial distribution."""
    x = _check_features(features, fit.columns)
    z = (x - fit.mean) / fit.scale
    log_b = _log_emissions(fit, z)
    with np.errstate(divide="ignore"):
        log_a = np.log(fit.transmat)
        log_alpha = np.log(fit.startprob) + log_b[0]
    out = np.empty_like(log_b)
    log_alpha = log_alpha - _logsumexp(log_alpha, axis=0)
    out[0] = log_alpha
    for t in range(1, len(z)):
        log_alpha = _logsumexp(log_alpha[:, None] + log_a, axis=0) + log_b[t]
        log_alpha = log_alpha - _logsumexp(log_alpha, axis=0)
        out[t] = log_alpha
    return pd.DataFrame(np.exp(out), index=features.index, columns=list(fit.labels))


def month_start(day: date) -> pd.Timestamp:
    """The first calendar day of `day`'s month: the fit used for `day` is trained on the
    feature rows of the sessions before it."""
    return pd.Timestamp(day.year, day.month, 1)


def month_fit(
    features: pd.DataFrame,
    month: pd.Timestamp,
    config: RegimeHMMConfig,
    *,
    order_by: str = "vol",
) -> HMMFit | None:
    """The HMM fit for calendar month `month` (its first day): `fit_hmm` with `config` on the
    rows of `features` before `month` (the expanding window). `None` with fewer than
    `config.min_obs` such rows, or when every start is degenerate (logged
    `regime.hmm_fit_failed`)."""
    train = features.loc[pd.DatetimeIndex(features.index) < month]
    if len(train) < config.min_obs:
        return None
    try:
        fit = fit_hmm(
            train,
            config.n_states,
            seed=config.seed,
            n_init=config.n_init,
            n_iter=config.n_iter,
            tol=config.tol,
            order_by=order_by,
            min_state_share=config.min_state_share,
        )
    except DataError as exc:
        _log.warning("regime.hmm_fit_failed", month=month.date().isoformat(), error=str(exc))
        return None
    _log.info(
        "regime.hmm_refit",
        month=month.date().isoformat(),
        n_obs=fit.n_obs,
        fit_end=fit.fit_end.date().isoformat(),
        log_likelihood=round(fit.log_likelihood, 3),
    )
    return fit


def expanding_probabilities(
    features: pd.DataFrame,
    decisions: Sequence[date],
    config: RegimeHMMConfig,
    *,
    order_by: str = "vol",
) -> tuple[pd.DataFrame, dict[pd.Timestamp, HMMFit | None]]:
    """Point-in-time HMM probabilities for each decision day (see the module docstring).

    Returns (probabilities, fits): a frame indexed by the decision days (as Timestamps),
    columns `state_labels(config.n_states)`, NaN where the month has no fit (`month_fit`
    returned `None`) or no row precedes the day; and the fit of each month (key = the
    month's first day)."""
    labels = state_labels(config.n_states)
    days = sorted(set(decisions))
    out = pd.DataFrame(
        np.nan, index=pd.DatetimeIndex([pd.Timestamp(d) for d in days]), columns=list(labels)
    )
    fits: dict[pd.Timestamp, HMMFit | None] = {}
    index = pd.DatetimeIndex(features.index)
    for month in sorted({month_start(d) for d in days}):
        fit = month_fit(features, month, config, order_by=order_by)
        fits[month] = fit
        if fit is None:
            continue
        in_month = [d for d in days if month_start(d) == month]
        last = pd.Timestamp(max(in_month))
        probs = filter_probabilities(fit, features.loc[index < last])
        for day in in_month:
            known = probs.loc[probs.index < pd.Timestamp(day)]
            if len(known):
                out.loc[pd.Timestamp(day)] = known.iloc[-1].to_numpy()
    return out, fits
