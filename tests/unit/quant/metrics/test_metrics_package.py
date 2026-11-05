"""The public API of `atlas.quant.metrics` and its single-source-of-truth rule (Stage 20 Task 9)."""

import ast
import inspect
from pathlib import Path

import atlas.quant.metrics as metrics

SRC = Path(metrics.__file__).resolve().parents[2]  # src/atlas
METRICS_DIR = Path(metrics.__file__).resolve().parent

STAGE_20 = {
    "COVARIANCE_METHODS", "METHODS", "SERIES_METHODS", "UNCLASSIFIED", "DrawdownPeriod",
    "ExposureSummary", "LiquidityProfile", "RiskContributions", "VaRResult", "active_return",
    "alpha", "annualised_mean", "annualised_return", "annualised_vol", "beta",
    "beta_adjusted_net", "calmar_ratio", "conditional_value_at_risk", "cornish_fisher_cvar",
    "cornish_fisher_monotone", "cornish_fisher_quantile", "cornish_fisher_var", "correlation",
    "cumulative_return",
    "days_to_liquidate", "down_capture", "downside_risk", "drawdown_periods", "drawdown_series",
    "excess_kurtosis", "exposure_summary", "group_gross", "group_net", "hit_rate",
    "information_ratio", "liquidity_from_view", "liquidity_profile", "max_drawdown",
    "max_drawdown_duration", "normal_cvar", "normal_var", "omega_ratio", "performance_summary",
    "portfolio_returns", "portfolio_var", "profit_factor", "relative_summary",
    "risk_contributions", "rolling_beta", "rolling_correlation", "rolling_max_drawdown",
    "rolling_metric", "rolling_sharpe", "rolling_vol", "scale_to_horizon", "sharpe_ratio",
    "simulate_portfolio_returns", "skewness", "sortino_ratio", "tail_ratio", "tracking_error",
    "ulcer_index", "up_capture", "up_down_capture", "value_at_risk", "var_cvar", "wealth_index",
}  # fmt: skip

# Words a docstring uses to state the units of a result.
UNIT_WORDS = (
    "fraction", "unitless", "session", "currency", "nav", "annual", "daily", "usd", "loss",
    "vol",
)  # fmt: skip


def test_public_api_is_exported() -> None:
    assert set(metrics.__all__) == STAGE_20
    assert len(metrics.__all__) == len(set(metrics.__all__))
    for name in STAGE_20:
        assert getattr(metrics, name) is not None


def test_every_module_states_units() -> None:
    for path in sorted(METRICS_DIR.glob("*.py")):
        doc = ast.get_docstring(ast.parse(path.read_text()))
        assert doc, path.name
        if path.name != "__init__.py":
            assert "Units:" in doc, f"{path.name} module docstring has no Units: section"


def test_every_public_callable_documents_its_units() -> None:
    missing = []
    for name in metrics.__all__:
        obj = getattr(metrics, name)
        if not callable(obj):
            continue
        doc = (inspect.getdoc(obj) or "").lower()
        if not doc or not any(word in doc for word in UNIT_WORDS):
            missing.append(name)
    assert missing == []


METRIC_FUNCTIONS = {
    name
    for name in STAGE_20
    if callable(getattr(metrics, name)) and not inspect.isclass(getattr(metrics, name))
}
# Formulas that compute a fund-level drawdown or annualised risk number from returns.
FORBIDDEN_SNIPPETS = ("maximum.accumulate", "cummax(")


def _outside_metrics() -> list[Path]:
    return [p for p in sorted(SRC.rglob("*.py")) if METRICS_DIR not in p.parents]


def test_no_other_module_defines_a_library_metric() -> None:
    clashes = []
    for path in _outside_metrics():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
                node.name in METRIC_FUNCTIONS or node.name.lstrip("_") in METRIC_FUNCTIONS
            ):
                clashes.append(f"{path.relative_to(SRC)}:{node.lineno} {node.name}")
    # Stage 18's annualisation helpers and Stage 19's factor-model decomposition live in
    # risk_model and have different names; nothing outside metrics re-implements a metric.
    assert clashes == []


def test_no_other_module_computes_a_running_peak_drawdown() -> None:
    hits = []
    for path in _outside_metrics():
        text = path.read_text()
        hits += [f"{path.relative_to(SRC)}: {s}" for s in FORBIDDEN_SNIPPETS if s in text]
    assert hits == []


def test_research_backtest_summary_uses_the_library() -> None:
    text = (SRC / "quant" / "research" / "vector_backtest.py").read_text()
    assert "from atlas.quant.metrics" in text


def test_every_public_name_is_tested() -> None:  # audit finding 4: "every metric tested"
    tests = Path(__file__).resolve().parent
    text = "\n".join(
        p.read_text() for p in tests.glob("test_metrics_*.py") if p.name != Path(__file__).name
    )
    untested = sorted(name for name in STAGE_20 if name not in text)
    assert untested == []


# --- post-DONE audit: constant series whose mean does not round exactly ------------------------


def test_audit_followup_constant_series_are_undefined_not_huge() -> None:
    import math

    import numpy as np
    import pandas as pd

    days = pd.bdate_range("2020-01-01", periods=100)
    flat = pd.Series([0.001] * 100, index=days)  # x - mean(x) is ~1e-19, not exactly 0
    assert float(np.std(flat.to_numpy())) > 0.0  # the rounding noise this test is about
    b = pd.Series(np.random.default_rng(1).normal(0.0, 0.01, 100), index=days)
    assert math.isnan(metrics.sharpe_ratio(flat))
    assert metrics.annualised_vol(flat) == 0.0
    assert math.isnan(metrics.skewness(flat)) and math.isnan(metrics.excess_kurtosis(flat))
    assert math.isnan(metrics.beta(b, flat)) and math.isnan(metrics.alpha(b, flat))
    assert math.isnan(metrics.correlation(b, flat))
    assert metrics.tracking_error(b + 0.001, b) == 0.0
    assert math.isnan(metrics.information_ratio(b + 0.001, b))
    normal = metrics.var_cvar(flat, method="normal")
    assert normal.var == 0.0 and normal.cvar == 0.0
    cf = metrics.var_cvar(flat, method="cornish_fisher")
    assert cf.var == 0.0 and cf.cvar == 0.0 and cf.adjustments == ()
    rf = pd.Series(np.linspace(0.0, 0.001, 100), index=days)
    assert not math.isnan(metrics.sharpe_ratio(flat, risk_free=rf))  # excess not constant
