"""Covariance estimators with a minimum-history policy, missing-data handling, PSD repair and a
condition-number check (Stage 18).

Units: `returns` are simple daily returns (fractions), one column per name, indexed by session
in strictly ascending order. Every result is **daily**: `cov` in return² per session, `vol` the
daily standard deviation. Annualise with `atlas.quant.risk_model.volatility.annualise_*`.

Estimators (`CovarianceEstimator.estimate(returns) -> CovResult`):

- `SampleCovariance`: demeaned, divided by `n - 1`.
- `EWMACovariance`: RiskMetrics, zero mean; the return `k` sessions before the last row has
  weight `λ^k`, weights normalised to sum 1 (`λ` = `quant.risk_model.ewma_lambda`, or
  `0.5^(1/half_life)`).
- `LedoitWolfCovariance`: Ledoit & Wolf (2004), "Honey, I shrunk the sample covariance
  matrix": shrinkage of `S` (demeaned, divided by `n`) towards the constant-correlation target.
- `OASCovariance`: Chen, Wiesel, Eldar & Hero (2010), oracle approximating shrinkage (eq. 23)
  of `S` towards `tr(S)/N · I`.

Policy shared by every estimator (`quant.risk_model`):

1. A name with fewer than `min_history` returns is dropped (`history`); a constant name (zero
   variance) is dropped (`constant`). Under `missing="pairwise"`, while some pair shares fewer
   than `min_history` sessions, the name in most such pairs is dropped (`overlap`; ties: fewer
   returns, then the later name). Under `missing="complete"` only the sessions where every
   remaining name has a return are used (fewer than `min_history` → `DataError`; the other
   sessions keep their positions, so EWMA decay still counts them). Dropped
   names are never estimated (no estimate → no new risk) and are reported in
   `CovResult.dropped`. Nothing left → `DataError`.
2. Pairwise moments use each pair's common sessions (the sample covariance equals pandas'
   `DataFrame.cov()`); for Ledoit-Wolf and OAS, `T` is then the mean pair count (exact for
   complete data, an approximation otherwise).
3. The estimate is symmetrised; if it is not PSD it is replaced by `nearest_psd(…,
   keep_diagonal=True)` (variances kept; Ledoit-Wolf and OAS repair their pairwise sample
   matrix *before* shrinking, so the shrunk matrix stays positive definite); if its
   condition number exceeds `max_condition` its eigenvalues are floored at
   `λmax / max_condition`. Each step is recorded in
   `CovResult.adjustments` and logged. Every result is positive definite.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Literal

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.quant.risk_model.psd import condition_number, floor_condition, is_psd, nearest_psd
from atlas.quant.risk_model.volatility import ewma_lambda

if TYPE_CHECKING:
    from atlas.data.view import DataView

_log = get_logger(__name__)

Missing = Literal["pairwise", "complete"]

METHODS: tuple[str, ...] = ("sample", "ewma", "ledoit_wolf", "oas")


# --- correlation <-> covariance ------------------------------------------------------------


def corr_from_cov[M: (np.ndarray, pd.DataFrame)](cov: M) -> M:
    """The correlation matrix of covariance `cov` (labels kept); `ValueError` when a variance
    is not positive."""
    a = np.asarray(cov.to_numpy(dtype="float64") if isinstance(cov, pd.DataFrame) else cov)
    d = np.diag(a)
    if not (d > 0).all():
        raise ValueError("every variance (diagonal entry) must be positive")
    s = np.sqrt(d)
    out = a / np.outer(s, s)
    np.fill_diagonal(out, 1.0)
    if isinstance(cov, pd.DataFrame):
        return pd.DataFrame(out, index=cov.index, columns=cov.columns)
    return out


def cov_from_corr[M: (np.ndarray, pd.DataFrame)](corr: M, vol: np.ndarray | pd.Series) -> M:
    """The covariance `corr · vol_i · vol_j` (labels of `corr` kept); `vol` in the same order."""
    a = np.asarray(corr.to_numpy(dtype="float64") if isinstance(corr, pd.DataFrame) else corr)
    v = np.asarray(vol, dtype="float64")
    if v.shape != (a.shape[0],):
        raise ValueError(f"vol must have one entry per row of corr, got {v.shape} for {a.shape}")
    out = a * np.outer(v, v)
    if isinstance(corr, pd.DataFrame):
        return pd.DataFrame(out, index=corr.index, columns=corr.columns)
    return out


# --- result ---------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class CovResult:
    """A daily covariance estimate. `cov`, `corr` (DataFrames) and `vol` (Series) are fresh
    copies built from the read-only `matrix`, so editing them never changes the result.
    Results compare (and hash) by identity: compare `matrix` with `np.array_equal`.

    `n_obs` is the number of sessions used (with at least one return under `pairwise`, with
    every return under `complete`); `end` is the last session of the input; `shrinkage` is
    the shrinkage intensity (Ledoit-Wolf, OAS) or `None`; `adjustments` lists the repairs
    applied (`nearest_psd`, `condition_floor`); `dropped` maps each name that was not
    estimated to the reason (`history`, `overlap`, `constant`).
    """

    symbols: tuple[str, ...]
    matrix: np.ndarray = field(repr=False)
    method: str
    n_obs: int
    end: date | None
    shrinkage: float | None
    condition_number: float
    adjustments: tuple[str, ...]
    dropped: Mapping[str, str]

    def __post_init__(self) -> None:
        arr = np.array(self.matrix, dtype="float64", copy=True)
        arr.flags.writeable = False
        object.__setattr__(self, "matrix", arr)
        object.__setattr__(self, "dropped", MappingProxyType(dict(self.dropped)))

    @property
    def cov(self) -> pd.DataFrame:
        """Daily covariance (return² per session), names x names."""
        names = list(self.symbols)
        return pd.DataFrame(self.matrix.copy(), index=names, columns=names)

    @property
    def vol(self) -> pd.Series:
        """Daily volatility (standard deviation of daily returns) per name."""
        return pd.Series(np.sqrt(np.diag(self.matrix)), index=list(self.symbols), name="vol")

    @property
    def corr(self) -> pd.DataFrame:
        """Correlation matrix, names x names (unit diagonal)."""
        return corr_from_cov(self.cov)


# --- input checks and the history policy ----------------------------------------------------


def _validate(returns: object) -> pd.DataFrame:
    """`returns` if it is a non-empty DataFrame of finite-or-NaN numbers with unique names and
    a unique, strictly ascending index; `ValueError` otherwise."""
    if not isinstance(returns, pd.DataFrame):
        raise ValueError(f"returns must be a DataFrame, got {type(returns).__name__}")
    if returns.empty:
        raise ValueError("returns is empty")
    if not returns.columns.is_unique:
        raise ValueError("returns has duplicate column names")
    bad = [
        c
        for c in returns.columns
        if pd.api.types.is_bool_dtype(returns[c]) or not pd.api.types.is_numeric_dtype(returns[c])
    ]
    if bad:
        raise ValueError(f"returns must be numeric, not: {bad}")
    if not (returns.index.is_unique and returns.index.is_monotonic_increasing):
        raise ValueError("returns must be indexed by session, unique and strictly ascending")
    values = returns.to_numpy(dtype="float64")
    if np.isinf(values).any():
        raise ValueError("returns has infinite values")
    return returns


def _end(index: pd.Index) -> date | None:
    last = index[-1]
    if isinstance(last, pd.Timestamp):
        return last.date()
    return last if isinstance(last, date) else None


def _overlap_drops(mask: np.ndarray, names: list[str], min_history: int) -> list[str]:
    """Names to drop until every pair shares at least `min_history` sessions: repeatedly the
    name in most short pairs (ties: fewer returns, then the later name)."""
    keep = list(range(len(names)))
    out: list[str] = []
    while len(keep) > 1:
        m = mask[:, keep].astype("float64")  # counts, not a boolean product
        common = m.T @ m
        short = common < min_history
        np.fill_diagonal(short, False)
        counts = short.sum(axis=1)
        if not counts.any():
            break
        own = m.sum(axis=0)
        worst = max(range(len(keep)), key=lambda i: (counts[i], -own[i], names[keep[i]]))
        out.append(names[keep[worst]])
        del keep[worst]
    return out


# --- estimators -----------------------------------------------------------------------------


class CovarianceEstimator(ABC):
    """Base class: `estimate(returns) -> CovResult` applies the shared policy (module
    docstring) around the estimator's `_estimate`. Settings default to `quant.risk_model` of
    `config` (default `AtlasConfig()`)."""

    method: ClassVar[str]

    def __init__(
        self,
        *,
        config: AtlasConfig | None = None,
        min_history: int | None = None,
        missing: Missing | None = None,
        max_condition: float | None = None,
    ) -> None:
        cfg = (config if config is not None else AtlasConfig()).quant.risk_model
        self.min_history = cfg.min_history if min_history is None else min_history
        self.missing: Missing = cfg.missing if missing is None else missing
        self.max_condition = cfg.max_condition if max_condition is None else max_condition
        if isinstance(self.min_history, bool) or not isinstance(self.min_history, int):
            raise ValueError(f"min_history must be an int, got {self.min_history!r}")
        if self.min_history < 2:
            raise ValueError(f"min_history must be at least 2, got {self.min_history}")
        if self.missing not in ("pairwise", "complete"):
            raise ValueError(f"missing must be 'pairwise' or 'complete', got {self.missing!r}")
        if not (np.isfinite(self.max_condition) and self.max_condition > 1):
            raise ValueError(f"max_condition must be finite and > 1, got {self.max_condition}")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(min_history={self.min_history}, missing={self.missing!r}, "
            f"max_condition={self.max_condition:g})"
        )

    def estimate(self, returns: pd.DataFrame) -> CovResult:
        """The daily covariance of the names in `returns` (see the module docstring)."""
        frame = _validate(returns)
        names = [str(c) for c in frame.columns]
        x = frame.to_numpy(dtype="float64", copy=True)  # never a view of the caller's frame
        dropped: dict[str, str] = {}

        present = ~np.isnan(x)
        for j, name in enumerate(names):
            col = x[present[:, j], j]
            if len(col) < self.min_history:
                dropped[name] = "history"
            elif col.max() == col.min():
                dropped[name] = "constant"
        keep = [j for j, name in enumerate(names) if name not in dropped]

        if self.missing == "complete":
            while True:  # a name constant on the common sessions is dropped, which may add rows
                if not keep:
                    raise DataError(
                        f"no name has {self.min_history} returns (min_history); dropped: {dropped}"
                    )
                rows = present[:, keep].all(axis=1)
                if rows.sum() < self.min_history:
                    raise DataError(
                        f"only {int(rows.sum())} complete sessions, fewer than min_history "
                        f"({self.min_history})"
                    )
                flat = [j for j in keep if np.ptp(x[rows, j]) == 0.0]
                if not flat:
                    break
                dropped.update({names[j]: "constant" for j in flat})
                keep = [j for j in keep if j not in flat]
            x[~rows] = np.nan  # sessions keep their positions (EWMA decay slots)
        else:
            for name in _overlap_drops(
                present[:, keep], [names[j] for j in keep], self.min_history
            ):
                dropped[name] = "overlap"
            keep = [j for j, name in enumerate(names) if name not in dropped]
            if not keep:
                raise DataError(
                    f"no name has {self.min_history} returns (min_history); dropped: {dropped}"
                )
        x = x[:, keep]
        symbols = tuple(names[j] for j in keep)
        n_obs = int((~np.isnan(x)).any(axis=1).sum())

        raw, shrinkage, repaired = self._estimate(x)
        if not np.isfinite(raw).all():
            raise DataError(
                f"{self.method} estimate is not finite (e.g. EWMA weights that underflow to 0 "
                "for names seen only long ago): no covariance"
            )
        zero = [symbols[i] for i in np.flatnonzero(np.diag(raw) <= 0.0)]
        if zero:  # the condition floor would invent a variance for them: no guess
            raise DataError(f"{self.method} estimate has a zero variance for {zero}: no covariance")
        cov, adjustments, cond = self._finalise(raw, repaired)
        if dropped or adjustments:
            _log.warning(
                "covariance_adjusted",
                method=self.method,
                names=len(symbols),
                dropped=dropped,
                adjustments=list(adjustments),
            )
        return CovResult(
            symbols=symbols,
            matrix=cov,
            method=self.method,
            n_obs=n_obs,
            end=_end(frame.index),
            shrinkage=shrinkage,
            condition_number=cond,
            adjustments=adjustments,
            dropped=dropped,
        )

    def _finalise(
        self, raw: np.ndarray, repaired: bool
    ) -> tuple[np.ndarray, tuple[str, ...], float]:
        """Symmetrise, repair to PSD (variances kept), floor the condition number. `repaired`:
        the estimator already repaired its sample matrix (recorded as `nearest_psd`)."""
        cov = (raw + raw.T) / 2.0
        adjustments: list[str] = ["nearest_psd"] if repaired else []
        if not is_psd(cov):
            cov = nearest_psd(cov, keep_diagonal=True)
            if not repaired:
                adjustments.append("nearest_psd")
        cond = condition_number(cov)
        if cond > self.max_condition:
            cov = floor_condition(cov, self.max_condition)
            adjustments.append("condition_floor")
            cond = condition_number(cov)
        return cov, tuple(adjustments), cond

    @abstractmethod
    def _estimate(self, x: np.ndarray) -> tuple[np.ndarray, float | None, bool]:
        """Raw covariance of `x` (sessions x names, NaN = missing; every name has at least
        `min_history` returns and, unless `complete`, every pair that many common sessions),
        the shrinkage intensity (or `None`) and whether the estimator repaired its sample
        matrix to PSD itself."""


def _pairwise(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """`(X0, M, C)`: `x` with NaN as 0, the presence mask (float) and the pair counts `M'M`."""
    mask = (~np.isnan(x)).astype("float64")
    return np.nan_to_num(x, nan=0.0), mask, mask.T @ mask


class SampleCovariance(CovarianceEstimator):
    """Sample covariance, demeaned over each pair's common sessions, divided by `n - 1`
    (pandas' `DataFrame.cov()` for pairwise data)."""

    method: ClassVar[str] = "sample"

    def _estimate(self, x: np.ndarray) -> tuple[np.ndarray, float | None, bool]:
        x0, mask, common = _pairwise(x)
        cross = x0.T @ x0
        sums = x0.T @ mask  # (i, j): sum of name i's returns over the sessions name j has
        cov = (cross - sums * sums.T / common) / (common - 1.0)
        return cov, None, False


class EWMACovariance(CovarianceEstimator):
    """RiskMetrics EWMA covariance, zero mean: the return `k` sessions before the last row has
    weight `λ^k`; each pair's weights over its common sessions are normalised to sum 1 (a
    missing return keeps its decay slot but adds nothing). `λ` is `lam`, or
    `0.5 ** (1 / half_life)`, or `quant.risk_model.ewma_lambda` (not both `lam` and
    `half_life`)."""

    method: ClassVar[str] = "ewma"

    def __init__(
        self,
        lam: float | None = None,
        half_life: float | None = None,
        *,
        config: AtlasConfig | None = None,
        min_history: int | None = None,
        missing: Missing | None = None,
        max_condition: float | None = None,
    ) -> None:
        super().__init__(
            config=config, min_history=min_history, missing=missing, max_condition=max_condition
        )
        self.lam = ewma_lambda(lam, half_life, config=config)

    def _estimate(self, x: np.ndarray) -> tuple[np.ndarray, float | None, bool]:
        x0, mask, _ = _pairwise(x)
        weights = self.lam ** np.arange(len(x) - 1, -1, -1, dtype="float64")
        cross = (x0 * weights[:, None]).T @ x0
        total = (mask * weights[:, None]).T @ mask
        with np.errstate(invalid="ignore", divide="ignore"):  # 0/0 → NaN → DataError above
            return cross / total, None, False


def _moments(
    x: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, bool]:
    """Demeaned moments for the shrinkage estimators: `(X0, M, C, S, T, repaired)` with each
    column demeaned over its own returns (NaN as 0), the mask, the pair counts,
    `S = X0'X0 / C` (divided by the count, as in the papers), `T` = the mean pair count, and
    whether `S` (pairwise, so possibly not PSD) was replaced by its nearest PSD matrix with
    the variances kept. Repairing `S` before shrinking keeps the shrunk matrix positive
    definite; repairing after would land on the PSD boundary."""
    x0, mask, common = _pairwise(x - np.nanmean(x, axis=0))
    s = (x0.T @ x0) / common
    s = (s + s.T) / 2.0
    if is_psd(s):
        return x0, mask, common, s, float(common.mean()), False
    return x0, mask, common, nearest_psd(s, keep_diagonal=True), float(common.mean()), True


class LedoitWolfCovariance(CovarianceEstimator):
    """Ledoit & Wolf (2004) shrinkage towards the constant-correlation target.

    `S` divided by `T`; target `F_ii = s_ii`, `F_ij = r̄ √(s_ii s_jj)` with `r̄` the mean
    sample correlation; intensity `δ = max(0, min(1, (pi_hat - rho_hat) / gamma_hat / T))`
    with `pi_hat = Σ_ij AsyVar(√T s_ij)`,
    `rho_hat = Σ_i pi_hat_ii + r̄ Σ_{i≠j} √(s_jj / s_ii) ϑ_{ii,ij}` and
    `gamma_hat = ‖F - S‖²_F`. Result `δ F + (1 - δ) S`; `shrinkage` = `δ`."""

    method: ClassVar[str] = "ledoit_wolf"

    def _estimate(self, x: np.ndarray) -> tuple[np.ndarray, float | None, bool]:
        x0, mask, common, s, t, repaired = _moments(x)
        n = s.shape[0]
        if n == 1:
            return s, 0.0, repaired
        var = np.diag(s)
        sd = np.sqrt(var)
        rbar = float(((s / np.outer(sd, sd)).sum() - n) / (n * (n - 1)))
        target = rbar * np.outer(sd, sd)
        np.fill_diagonal(target, var)
        sq = x0**2
        moment = (x0.T @ x0) / common  # the raw pairwise S: pi and theta are moments about it
        pi = (sq.T @ sq) / common - moment**2
        theta = ((x0**3).T @ x0 - moment * (sq.T @ mask)) / common
        np.fill_diagonal(theta, 0.0)
        rho = float(np.trace(pi)) + rbar * float((np.outer(1.0 / sd, sd) * theta).sum())
        gamma = float(((target - s) ** 2).sum())
        if gamma == 0.0:  # S is already the target: the intensity does not matter
            return s, 0.0, repaired
        delta = min(1.0, max(0.0, (float(pi.sum()) - rho) / gamma / t))
        return delta * target + (1.0 - delta) * s, delta, repaired


class OASCovariance(CovarianceEstimator):
    """Oracle approximating shrinkage (Chen, Wiesel, Eldar & Hero 2010, eq. 23) towards
    `tr(S)/N · I`: `rho = min(1, ((1 - 2/N) tr(S²) + tr²(S)) / ((T + 1 - 2/N)(tr(S²) - tr²(S)/N)))`
    (`rho = 1` when `S` is already a scaled identity). `S` divided by `T`; `shrinkage` = `rho`."""

    method: ClassVar[str] = "oas"

    def _estimate(self, x: np.ndarray) -> tuple[np.ndarray, float | None, bool]:
        _, _, _, s, t, repaired = _moments(x)
        p = s.shape[0]
        tr = float(np.trace(s))
        tr2 = float((s**2).sum())  # tr(S²) for a symmetric S
        num = (1.0 - 2.0 / p) * tr2 + tr**2
        den = (t + 1.0 - 2.0 / p) * (tr2 - tr**2 / p)
        rho = 1.0 if den <= 0.0 else min(1.0, num / den)
        return (1.0 - rho) * s + rho * (tr / p) * np.eye(p), rho, repaired


_ESTIMATORS: dict[str, type[CovarianceEstimator]] = {
    cls.method: cls
    for cls in (SampleCovariance, EWMACovariance, LedoitWolfCovariance, OASCovariance)
}


def covariance_estimator(
    method: str | None = None, *, config: AtlasConfig | None = None, **kwargs: object
) -> CovarianceEstimator:
    """The estimator for `method` (`sample`, `ewma`, `ledoit_wolf`, `oas`; default
    `quant.risk_model.default_method` of `config`). `kwargs` go to its constructor
    (`min_history`, `missing`, `max_condition`; `lam` / `half_life` for `ewma`)."""
    cfg = config if config is not None else AtlasConfig()
    name = cfg.quant.risk_model.default_method if method is None else method
    if name not in _ESTIMATORS:
        raise ValueError(f"unknown covariance method {name!r}; expected one of {METHODS}")
    return _ESTIMATORS[name](config=cfg, **kwargs)  # type: ignore[arg-type]


def covariance_from_view(
    view: "DataView",
    symbols: Sequence[str],
    *,
    method: str | None = None,
    lookback: int | None = None,
    **kwargs: object,
) -> CovResult:
    """The covariance of `symbols`' daily returns known to `view`: `view.returns(symbols,
    lookback)` (default `quant.risk_model.lookback` sessions of the view's config) estimated
    by `covariance_estimator(method, config=view.config, **kwargs)`. Same units and rules as
    the estimator (names with too little history are dropped and listed in `dropped`)."""
    cfg = view.config
    n = cfg.quant.risk_model.lookback if lookback is None else lookback
    return covariance_estimator(method, config=cfg, **kwargs).estimate(view.returns(symbols, n))
