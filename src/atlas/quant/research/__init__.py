"""Signal research toolkit (Stage 17): does a signal predict returns?

`labels` builds forward-return labels (evaluation only, guarded against use as features);
`signals` builds a point-in-time signal panel from a Stage 15 feature or a Stage 16 factor;
`ic` measures information coefficients and decay; `quantiles` the quantile spreads;
`vector_backtest` runs a simple long-short backtest with costs (its engine,
`weights_backtest`, takes target weights: Stage 23); `tearsheet` puts it together
(`SignalTearSheet`, saved by `atlas research signal`); `holdout` tells whether a window reads
returns after `backtest.in_sample_end` (the hold-out, spec A7).
"""

from atlas.quant.research.holdout import HoldoutCheck, holdout_check
from atlas.quant.research.ic import (
    METHODS,
    SUMMARY_FIELDS,
    ICSummary,
    Method,
    ic_by_group,
    ic_by_regime,
    ic_decay,
    ic_series,
    newey_west_tstat,
    nw_lags,
    rank_autocorrelation,
    session_step,
    summarize_ic,
)
from atlas.quant.research.labels import (
    ForwardReturns,
    ViewFactory,
    check_dates,
    check_horizons,
    compound_forward,
    forward_returns,
)
from atlas.quant.research.quantiles import (
    QuantileAnalysis,
    daily_quantile_returns,
    held_rows,
    monotonicity,
    quantile_analysis,
    quantile_buckets,
    quantile_returns,
)
from atlas.quant.research.signals import (
    FREQUENCIES,
    Frequency,
    SignalKind,
    SignalPanel,
    evaluation_dates,
    signal_kind,
    signal_panel,
)
from atlas.quant.research.tearsheet import (
    FIGURES,
    HEADLINE_METHOD,
    SignalTearSheet,
    build_tearsheet,
    headline_horizon,
    research_signal,
)
from atlas.quant.research.vector_backtest import (
    REBALANCES,
    WEIGHTINGS,
    BacktestResult,
    Rebalance,
    Weighting,
    long_short_weights,
    rebalance_dates,
    vector_backtest,
    weights_backtest,
)

__all__ = [
    "FIGURES",
    "FREQUENCIES",
    "HEADLINE_METHOD",
    "METHODS",
    "REBALANCES",
    "SUMMARY_FIELDS",
    "WEIGHTINGS",
    "BacktestResult",
    "ForwardReturns",
    "Frequency",
    "HoldoutCheck",
    "ICSummary",
    "Method",
    "QuantileAnalysis",
    "Rebalance",
    "SignalKind",
    "SignalPanel",
    "SignalTearSheet",
    "ViewFactory",
    "Weighting",
    "build_tearsheet",
    "check_dates",
    "check_horizons",
    "compound_forward",
    "daily_quantile_returns",
    "evaluation_dates",
    "forward_returns",
    "headline_horizon",
    "held_rows",
    "holdout_check",
    "ic_by_group",
    "ic_by_regime",
    "ic_decay",
    "ic_series",
    "long_short_weights",
    "monotonicity",
    "newey_west_tstat",
    "nw_lags",
    "quantile_analysis",
    "quantile_buckets",
    "quantile_returns",
    "rank_autocorrelation",
    "rebalance_dates",
    "research_signal",
    "session_step",
    "signal_kind",
    "signal_panel",
    "summarize_ic",
    "vector_backtest",
    "weights_backtest",
]
