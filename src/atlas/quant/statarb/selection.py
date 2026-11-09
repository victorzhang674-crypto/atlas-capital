"""Pair selection for statistical arbitrage (Stage 23).

On each formation date (monthly, point in time) the candidates are the members of
`quant.statarb.universe`; a candidate needs a finite return on every session of the formation
window and a GICS sector. Pairs are formed inside sectors only. With `cfg = quant.statarb`,
on log total-return price indices (`log_price_panel`):

1. **Correlation prefilter:** daily log returns over the last `corr_window` sessions,
   correlation > `min_corr`.
2. **Engle-Granger** over the last `formation` sessions: the order-free p-value
   `coint_pvalue` (the larger of the two orientations' MacKinnon p-values) < `max_pvalue`.
3. **Hedge ratio:** OLS of log y on log x (`y` the alphabetically first symbol), β > 0.
4. **Half-life** of the spread `s = log y - β log x` (AR(1)) in
   [`min_half_life`, `max_half_life`] sessions.
5. **Hurst exponent** of the spread (lagged differences, lags 2..`hurst_max_lag`) <
   `max_hurst`.

Pairs passing every filter are ranked by p-value (then half-life, then id) and accepted while
each name is in at most `max_pairs_per_name` pairs and there are at most `max_pairs`.
Half-lives are in sessions; prices are log total-return indices (shift-invariant statistics,
so the index's base does not matter).
"""

import math
import warnings
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import combinations

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint

from atlas.core.config import StatArbConfig
from atlas.core.logging import get_logger
from atlas.core.pit import guard_not_label
from atlas.data.view import DataView
from atlas.quant.alpha.multifactor import member_sectors

_log = get_logger(__name__)

PAIR_SEP = "~"
FILTERS: tuple[str, ...] = ("corr", "coint", "hedge_ratio", "half_life", "hurst")


def pair_id(a: str, b: str) -> str:
    """The pair's id `y~x`, `y` the alphabetically first of the two symbols."""
    if a == b:
        raise ValueError(f"a pair needs two different symbols, got the same {a!r} twice")
    y, x = sorted((a, b))
    return f"{y}{PAIR_SEP}{x}"


def _as_array(values: Sequence[float] | np.ndarray | pd.Series) -> np.ndarray:
    return np.asarray(values, dtype="float64")


def half_life(spread: Sequence[float] | np.ndarray | pd.Series) -> float:
    """Half-life of mean reversion of `spread` in sessions: OLS of `Δs_t = a + b s_{t-1}`,
    `phi = 1 + b`, half-life `-ln 2 / ln phi` for `0 < phi < 1`; `inf` for `phi >= 1` (no
    mean reversion), NaN for `phi <= 0` or a constant spread. At least 3 finite values."""
    s = _as_array(spread)
    if len(s) < 3 or not np.isfinite(s).all():
        raise ValueError(f"half_life needs at least 3 finite values, got {len(s)}")
    lag, delta = s[:-1], np.diff(s)
    if np.ptp(lag) == 0:
        return math.nan
    b = np.polyfit(lag, delta, 1)[0]
    phi = 1.0 + float(b)
    if phi >= 1.0:
        return math.inf
    if phi <= 0.0:
        return math.nan
    return -math.log(2.0) / math.log(phi)


def hurst_exponent(series: Sequence[float] | np.ndarray | pd.Series, max_lag: int) -> float:
    """Hurst exponent of `series` by lagged differences: the slope of log std(x_{t+τ} - x_t)
    on log τ for τ = 2..`max_lag`. About 0.5 for a random walk, below for a mean-reverting
    series, above for a persistent one; NaN when a lag has no variation."""
    x = _as_array(series)
    if max_lag < 3:
        raise ValueError(f"max_lag must be >= 3, got {max_lag}")
    if len(x) <= 2 * max_lag or not np.isfinite(x).all():
        raise ValueError(f"hurst_exponent needs more than {2 * max_lag} finite values")
    lags = np.arange(2, max_lag + 1)
    sd = np.array([np.std(x[lag:] - x[:-lag]) for lag in lags])
    if (sd <= 0).any():
        return math.nan
    return float(np.polyfit(np.log(lags), np.log(sd), 1)[0])


def hedge_ratio(
    log_y: Sequence[float] | np.ndarray | pd.Series, log_x: Sequence[float] | np.ndarray | pd.Series
) -> tuple[float, float]:
    """OLS `log y = alpha + beta log x`: `(beta, alpha)`."""
    y, x = _as_array(log_y), _as_array(log_x)
    if len(y) != len(x) or len(y) < 3:
        raise ValueError(f"hedge_ratio needs two equal series of >= 3 values: {len(y)}, {len(x)}")
    beta, alpha = np.polyfit(x, y, 1)
    return float(beta), float(alpha)


def coint_pvalue(
    a: Sequence[float] | np.ndarray | pd.Series, b: Sequence[float] | np.ndarray | pd.Series
) -> float:
    """Order-free Engle-Granger p-value: the larger of statsmodels `coint(a, b)` and
    `coint(b, a)` (constant, lag length by AIC); NaN when the test cannot be run (e.g. a
    perfect fit)."""
    x, y = _as_array(a), _as_array(b)
    if len(x) != len(y):
        raise ValueError(f"coint_pvalue needs equal lengths, got {len(x)} and {len(y)}")
    values = []
    for first, second in ((x, y), (y, x)):
        try:
            with warnings.catch_warnings(), np.errstate(all="ignore"):
                warnings.simplefilter("ignore")
                p = float(coint(first, second, trend="c", autolag="aic")[1])
        except (ValueError, np.linalg.LinAlgError):
            return math.nan
        if not math.isfinite(p) or np.allclose(first, second):
            return math.nan
        values.append(p)
    return max(values)


@dataclass(frozen=True)
class PairStats:
    """The filter statistics of one candidate (NaN for those not computed) and the first
    failed filter (`None` when every filter passes)."""

    corr: float
    pvalue: float
    hedge_ratio: float
    intercept: float
    half_life: float
    hurst: float
    failed: str | None

    @property
    def passed(self) -> bool:
        """Whether every filter passed."""
        return self.failed is None


def _corr(log_y: np.ndarray, log_x: np.ndarray, window: int) -> float:
    ry, rx = np.diff(log_y)[-window:], np.diff(log_x)[-window:]
    if np.std(ry) == 0 or np.std(rx) == 0:
        return math.nan
    return float(np.corrcoef(ry, rx)[0, 1])


def evaluate_pair(
    log_y: Sequence[float] | np.ndarray | pd.Series,
    log_x: Sequence[float] | np.ndarray | pd.Series,
    config: StatArbConfig,
    *,
    check_corr: bool = True,
    corr: float | None = None,
) -> PairStats:
    """Run the filters (module docstring) in order on one formation window (`log_y`,
    `log_x`: log prices, equal length, the last `formation` values are used) and stop at the
    first failure. `corr` passes a precomputed correlation; `check_corr=False` skips the
    prefilter."""
    y, x = _as_array(log_y), _as_array(log_x)
    if len(y) != len(x):
        raise ValueError(f"evaluate_pair needs equal lengths, got {len(y)} and {len(x)}")
    cfg = config
    y, x = y[-cfg.formation :], x[-cfg.formation :]
    nan = math.nan
    rho = corr if corr is not None else _corr(y, x, cfg.corr_window)
    if check_corr and not rho > cfg.min_corr:
        return PairStats(rho, nan, nan, nan, nan, nan, "corr")
    p = coint_pvalue(y, x)
    if not p < cfg.max_pvalue:
        return PairStats(rho, p, nan, nan, nan, nan, "coint")
    beta, alpha = hedge_ratio(y, x)
    if not beta > 0:
        return PairStats(rho, p, beta, alpha, nan, nan, "hedge_ratio")
    spread = y - beta * x
    hl = half_life(spread)
    if not cfg.min_half_life <= hl <= cfg.max_half_life:
        return PairStats(rho, p, beta, alpha, hl, nan, "half_life")
    h = hurst_exponent(spread, cfg.hurst_max_lag)
    if not h < cfg.max_hurst:
        return PairStats(rho, p, beta, alpha, hl, h, "hurst")
    return PairStats(rho, p, beta, alpha, hl, h, None)


@dataclass(frozen=True)
class Pair:
    """A selected pair: `y`, `x` (y alphabetically first), its `sector`, the OLS
    `hedge_ratio` (β, log-price units: $β of x per $1 of y) and `intercept`, the spread's
    `half_life` (sessions) and `hurst`, the order-free Engle-Granger `pvalue`, the return
    `corr`, and the formation date `formed` (`None` outside a dated run)."""

    pair_id: str
    y: str
    x: str
    sector: str
    hedge_ratio: float
    intercept: float
    half_life: float
    hurst: float
    pvalue: float
    corr: float
    formed: date | None = None


def log_price_panel(returns: pd.DataFrame) -> pd.DataFrame:
    """Log total-return price indices `cumsum(log(1 + r))` of simple daily `returns`
    (sessions x symbols); a session with a missing return stays NaN."""
    logs = pd.DataFrame(
        np.log1p(returns.to_numpy(dtype="float64")), index=returns.index, columns=returns.columns
    )
    return logs.cumsum(skipna=True).where(logs.notna())


def _candidates(window: pd.DataFrame, sectors: pd.Series) -> dict[str, list[str]]:
    """Symbols with a finite value on every session of `window` and a sector, by sector."""
    complete = [s for s in window.columns if np.isfinite(window[s].to_numpy()).all()]
    dropped = sorted(set(window.columns) - set(complete))
    if dropped:
        _log.info("statarb.incomplete_window", n=len(dropped), symbols=dropped[:20])
    out: dict[str, list[str]] = {}
    for sym in sorted(complete):
        sector = sectors.get(sym) if sym in sectors.index else None
        if sector is None or (isinstance(sector, float) and math.isnan(sector)):
            _log.info("statarb.no_sector", symbol=sym)
            continue
        out.setdefault(str(sector), []).append(sym)
    return out


def select_pairs(
    log_prices: pd.DataFrame,
    sectors: pd.Series,
    *,
    config: StatArbConfig,
    formed: date | None = None,
) -> list[Pair]:
    """The pairs selected on the last `formation` rows of `log_prices` (sessions x symbols,
    oldest first) with `sectors` (symbol → GICS sector): module docstring. `ValueError` if
    fewer than `formation` rows; a forward-return label raises `LookAheadError`."""
    guard_not_label(log_prices, "price panel")
    cfg = config
    if len(log_prices) < cfg.formation:
        raise ValueError(
            f"select_pairs needs at least formation = {cfg.formation} sessions, "
            f"got {len(log_prices)}"
        )
    window = log_prices.iloc[-cfg.formation :]
    passed: list[Pair] = []
    tested = 0
    for sector, names in sorted(_candidates(window, sectors).items()):
        if len(names) < 2:
            continue
        values = window[names].to_numpy(dtype="float64")
        rets = np.diff(values, axis=0)[-cfg.corr_window :]
        with np.errstate(all="ignore"):
            corr = np.corrcoef(rets, rowvar=False)
        for i, j in combinations(range(len(names)), 2):
            rho = float(corr[i, j])
            if not rho > cfg.min_corr:
                continue
            tested += 1
            y, x = names[i], names[j]  # names are sorted: y < x
            stats = evaluate_pair(values[:, i], values[:, j], cfg, corr=rho)
            if stats.passed:
                passed.append(
                    Pair(
                        pair_id=pair_id(y, x),
                        y=y,
                        x=x,
                        sector=sector,
                        hedge_ratio=stats.hedge_ratio,
                        intercept=stats.intercept,
                        half_life=stats.half_life,
                        hurst=stats.hurst,
                        pvalue=stats.pvalue,
                        corr=stats.corr,
                        formed=formed,
                    )
                )
    passed.sort(key=lambda p: (p.pvalue, p.half_life, p.pair_id))
    chosen: list[Pair] = []
    uses: Counter[str] = Counter()
    for pair in passed:
        if len(chosen) >= cfg.max_pairs:
            break
        if uses[pair.y] >= cfg.max_pairs_per_name or uses[pair.x] >= cfg.max_pairs_per_name:
            continue
        chosen.append(pair)
        uses[pair.y] += 1
        uses[pair.x] += 1
    _log.info(
        "statarb.pairs_selected",
        formed=formed.isoformat() if formed else None,
        tested=tested,
        passed=len(passed),
        selected=len(chosen),
    )
    return chosen


def select_pairs_at(view: DataView, config: StatArbConfig | None = None) -> list[Pair]:
    """The pairs selected at `view.as_of` (formation date = the view's day): the members of
    `universe` at the as-of, `formation` sessions of returns known at the as-of (ending the
    session before), sectors from the instruments table. `config` overrides the view's
    `quant.statarb`."""
    cfg = config or view.config.quant.statarb
    members = sorted(view.universe(cfg.universe))
    if len(members) < 2:
        _log.info("statarb.no_members", day=view.day.isoformat(), n=len(members))
        return []
    returns = view.returns(members, cfg.formation)
    sectors = member_sectors(view, list(returns.columns))
    return select_pairs(log_price_panel(returns), sectors, config=cfg, formed=view.day)


__all__ = [
    "FILTERS",
    "PAIR_SEP",
    "Pair",
    "PairStats",
    "coint_pvalue",
    "evaluate_pair",
    "half_life",
    "hedge_ratio",
    "hurst_exponent",
    "log_price_panel",
    "pair_id",
    "select_pairs",
    "select_pairs_at",
]
