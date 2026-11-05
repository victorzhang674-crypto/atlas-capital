"""Risk and performance metrics library (Stage 20): the single source of every risk and
performance number the fund reports or limits on. Later stages import these numbers from
here only.

Units: returns are simple per-session (daily) fractions in a Series indexed by session
(ascending); weights are fractions of NAV by symbol (shorts negative); covariances are daily;
`periods` = sessions a year (`quant.features.sessions_per_year`, 252). Each function's
docstring states the units of its result. Not enough data raises `DataError`; an undefined
ratio (zero denominator) is NaN, never +-inf.

- `performance`: `cumulative_return`, `annualised_return` (CAGR), `annualised_mean`,
  `annualised_vol`, `sharpe_ratio`, `sortino_ratio`, `downside_risk`, `calmar_ratio`,
  `omega_ratio`, `drawdown_series`, `max_drawdown` (positive), `drawdown_periods`,
  `max_drawdown_duration`, `ulcer_index`, `skewness`, `excess_kurtosis`, `hit_rate`,
  `profit_factor`, `tail_ratio`, `rolling_metric` / `rolling_sharpe` / `rolling_vol` /
  `rolling_max_drawdown`, `performance_summary`.
- `relative`: `beta`, `alpha` (CAPM), `tracking_error`, `information_ratio`, `active_return`,
  `up_capture`, `down_capture`, `up_down_capture`, `correlation`, `rolling_beta`,
  `rolling_correlation`, `relative_summary`.
- `var`: `var_cvar` / `value_at_risk` / `conditional_value_at_risk` on a return or P&L series
  (historical, normal, Cornish-Fisher; horizon scaling), `portfolio_var` (weights + returns:
  historical simulation; weights + covariance: normal or Monte Carlo), `portfolio_returns`,
  `simulate_portfolio_returns`, `scale_to_horizon`; VaR / CVaR are positive losses.
- `contributions`: `risk_contributions` (marginal, component, percent; Euler) from a
  covariance or a Stage 19 `RiskModel`.
- `liquidity`: `days_to_liquidate`, `liquidity_profile`, `liquidity_from_view` (ADV at a
  participation rate; 1/3/5-session liquid shares).
- `exposure`: `exposure_summary` (gross, net, long, short, beta-adjusted net, sector nets),
  `group_net`, `group_gross`, `beta_adjusted_net`.
"""

from atlas.quant.metrics.contributions import RiskContributions, risk_contributions
from atlas.quant.metrics.exposure import (
    UNCLASSIFIED,
    ExposureSummary,
    beta_adjusted_net,
    exposure_summary,
    group_gross,
    group_net,
)
from atlas.quant.metrics.liquidity import (
    LiquidityProfile,
    days_to_liquidate,
    liquidity_from_view,
    liquidity_profile,
)
from atlas.quant.metrics.performance import (
    DrawdownPeriod,
    annualised_mean,
    annualised_return,
    annualised_vol,
    calmar_ratio,
    cumulative_return,
    downside_risk,
    drawdown_periods,
    drawdown_series,
    excess_kurtosis,
    hit_rate,
    max_drawdown,
    max_drawdown_duration,
    omega_ratio,
    performance_summary,
    profit_factor,
    rolling_max_drawdown,
    rolling_metric,
    rolling_sharpe,
    rolling_vol,
    sharpe_ratio,
    skewness,
    sortino_ratio,
    tail_ratio,
    ulcer_index,
    wealth_index,
)
from atlas.quant.metrics.relative import (
    active_return,
    alpha,
    beta,
    correlation,
    down_capture,
    information_ratio,
    relative_summary,
    rolling_beta,
    rolling_correlation,
    tracking_error,
    up_capture,
    up_down_capture,
)
from atlas.quant.metrics.var import (
    COVARIANCE_METHODS,
    METHODS,
    SERIES_METHODS,
    VaRResult,
    conditional_value_at_risk,
    cornish_fisher_cvar,
    cornish_fisher_monotone,
    cornish_fisher_quantile,
    cornish_fisher_var,
    normal_cvar,
    normal_var,
    portfolio_returns,
    portfolio_var,
    scale_to_horizon,
    simulate_portfolio_returns,
    value_at_risk,
    var_cvar,
)

__all__ = [
    "COVARIANCE_METHODS",
    "METHODS",
    "SERIES_METHODS",
    "UNCLASSIFIED",
    "DrawdownPeriod",
    "ExposureSummary",
    "LiquidityProfile",
    "RiskContributions",
    "VaRResult",
    "active_return",
    "alpha",
    "annualised_mean",
    "annualised_return",
    "annualised_vol",
    "beta",
    "beta_adjusted_net",
    "calmar_ratio",
    "conditional_value_at_risk",
    "cornish_fisher_cvar",
    "cornish_fisher_monotone",
    "cornish_fisher_quantile",
    "cornish_fisher_var",
    "correlation",
    "cumulative_return",
    "days_to_liquidate",
    "down_capture",
    "downside_risk",
    "drawdown_periods",
    "drawdown_series",
    "excess_kurtosis",
    "exposure_summary",
    "group_gross",
    "group_net",
    "hit_rate",
    "information_ratio",
    "liquidity_from_view",
    "liquidity_profile",
    "max_drawdown",
    "max_drawdown_duration",
    "normal_cvar",
    "normal_var",
    "omega_ratio",
    "performance_summary",
    "portfolio_returns",
    "portfolio_var",
    "profit_factor",
    "relative_summary",
    "risk_contributions",
    "rolling_beta",
    "rolling_correlation",
    "rolling_max_drawdown",
    "rolling_metric",
    "rolling_sharpe",
    "rolling_vol",
    "scale_to_horizon",
    "sharpe_ratio",
    "simulate_portfolio_returns",
    "skewness",
    "sortino_ratio",
    "tail_ratio",
    "tracking_error",
    "ulcer_index",
    "up_capture",
    "up_down_capture",
    "value_at_risk",
    "var_cvar",
    "wealth_index",
]
