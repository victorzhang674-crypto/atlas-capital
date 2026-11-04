"""ETF exposures for the factor risk model (Stage 19).

An ETF has no fundamentals, so its factor exposures are time-series betas: its daily returns
over the last `window` sessions regressed on the factor returns (intercept removed by
demeaning), with a small ridge penalty `ridge * tr(F'F) / K` because the cap-weighted sector
constraint makes the factor returns nearly collinear. Only the component of a beta orthogonal
to the constraint direction is identified; the ridge picks the minimum-norm one, which leaves
every risk number unchanged (the factor covariance has no variance along that direction).
The residual variance (degrees of freedom `n - K - 1`, floored at the model's specific-vol
floor) is the ETF's specific variance.

Units: daily simple returns; betas dimensionless; variances daily return**2.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.quant.risk_model.factor_model import RiskModel

_log = get_logger(__name__)


@dataclass(frozen=True)
class EtfExposures:
    """`betas` (ETFs x factors), `specific_var` (daily), `r2` and `n_obs` per ETF, and
    `dropped` (ETF -> `history`: fewer than `etf.min_obs` observations in the window)."""

    betas: pd.DataFrame
    specific_var: pd.Series
    r2: pd.Series
    n_obs: pd.Series
    dropped: dict[str, str] = field(default_factory=dict)


def etf_exposures(
    etf_returns: pd.DataFrame, factor_returns: pd.DataFrame, config: AtlasConfig | None = None
) -> EtfExposures:
    """Time-series betas of each column of `etf_returns` (daily simple returns, NaN =
    missing) on `factor_returns` over the last `etf.window` sessions of `factor_returns`
    (sessions where every factor has a return). An ETF with fewer than `etf.min_obs`
    observations is dropped; none left -> `DataError`."""
    cfg = config if config is not None else AtlasConfig()
    fm = cfg.quant.risk_model.factor_model
    ec = fm.etf
    values = etf_returns.to_numpy(dtype="float64")
    if np.isinf(values).any() or np.isinf(factor_returns.to_numpy(dtype="float64")).any():
        raise ValueError("returns must be finite or NaN")
    factors = factor_returns.dropna(how="any").tail(ec.window)
    common = factors.index.intersection(etf_returns.index)
    if len(common) == 0:
        raise ValueError("etf_returns and factor_returns share no dates")
    f_all = factors.loc[common]
    k = f_all.shape[1]
    betas: dict[str, np.ndarray] = {}
    spec: dict[str, float] = {}
    r2: dict[str, float] = {}
    n_obs: dict[str, int] = {}
    dropped: dict[str, str] = {}
    for name in etf_returns.columns:
        y_all = etf_returns.loc[common, name]
        ok = y_all.notna().to_numpy()
        n = int(ok.sum())
        if n < max(ec.min_obs, k + 2):
            dropped[str(name)] = "history"
            continue
        f = f_all.to_numpy()[ok]
        y = y_all.to_numpy()[ok]
        f = f - f.mean(axis=0)
        yc = y - y.mean()
        gram = f.T @ f
        penalty = ec.ridge * float(np.trace(gram)) / k
        beta = np.linalg.solve(gram + penalty * np.eye(k), f.T @ yc)
        resid = yc - f @ beta
        sse, sst = float(resid @ resid), float(yc @ yc)
        var = sse / (n - k - 1)
        betas[str(name)] = beta
        spec[str(name)] = max(var, fm.specific_floor_vol**2)
        r2[str(name)] = 1.0 - sse / sst if sst > 0 else 0.0
        n_obs[str(name)] = n
    if not betas:
        raise DataError(
            f"no ETF has {ec.min_obs} observations in the last {ec.window} sessions: "
            f"dropped {dict(list(dropped.items())[:5])} (every ETF)"
        )
    if dropped:
        _log.warning("risk_model.etf_dropped", dropped=dropped)
    idx = pd.Index(list(betas), name="symbol")
    return EtfExposures(
        betas=pd.DataFrame(
            np.array(list(betas.values())), index=idx, columns=list(factor_returns.columns)
        ),
        specific_var=pd.Series(spec, index=idx, name="specific_var"),
        r2=pd.Series(r2, index=idx, name="r2"),
        n_obs=pd.Series(n_obs, index=idx, name="n_obs"),
        dropped=dropped,
    )


def extend_model(model: RiskModel, etfs: EtfExposures) -> RiskModel:
    """`model` with the ETFs appended as extra rows (their betas as exposures, their residual
    variances as specific variances; ETFs `etfs` dropped are added to `dropped`). The factor
    covariance is unchanged. A name already in the
    model, or betas over other factors, raise `ValueError`."""
    if list(etfs.betas.columns) != list(model.factors):
        raise ValueError(
            f"ETF betas are over other factors than the model's: {list(etfs.betas.columns)}"
        )
    clash = [s for s in etfs.betas.index if s in set(model.symbols)]
    if clash:
        raise ValueError(f"{clash[:5]} already in the model")
    return RiskModel(
        as_of=model.as_of,
        symbols=(*model.symbols, *map(str, etfs.betas.index)),
        factors=model.factors,
        x=np.vstack([model.x, etfs.betas.to_numpy()]),
        f=model.f,
        d=np.concatenate([model.d, etfs.specific_var.reindex(etfs.betas.index).to_numpy()]),
        dropped={**model.dropped, **etfs.dropped},
        adjustments=model.adjustments,
        n_days=model.n_days,
        etfs=(*model.etfs, *map(str, etfs.betas.index)),
    )
