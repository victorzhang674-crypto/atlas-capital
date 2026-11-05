"""Shared input handling of `atlas.quant.metrics` (Stage 20 Task 1)."""

import math

import numpy as np
import pandas as pd
import pytest
from metrics_reference import Reference

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.quant.metrics._common import (
    align,
    annual_to_daily,
    as_covariance,
    as_series,
    as_weights,
    is_constant,
    nan_if_zero,
    periods_per_year,
    risk_free_daily,
    sample_std,
    weights_on,
    window_length,
)

DAYS = pd.bdate_range("2024-01-01", periods=6)


def test_as_series_drops_nan_and_keeps_labels() -> None:
    s = pd.Series([0.01, np.nan, -0.02, 0.0], index=DAYS[:4])
    out = as_series(s)
    assert list(out.index) == [DAYS[0], DAYS[2], DAYS[3]]
    assert out.tolist() == [0.01, -0.02, 0.0]
    assert out.dtype == np.float64
    assert as_series([0.01, np.nan, 0.02]).index.tolist() == [0, 2]


@pytest.mark.parametrize(
    ("values", "match"),
    [
        (pd.Series([0.01, np.inf], index=DAYS[:2]), "infinite"),
        (pd.Series([0.01, -1.5], index=DAYS[:2]), "below -1"),
        (pd.Series([0.01, 0.02], index=[DAYS[1], DAYS[0]]), "sorted"),
        (pd.Series([0.01, 0.02], index=[DAYS[0], DAYS[0]]), "duplicate"),
        (pd.Series(["a", "b"], index=DAYS[:2]), "numeric"),
        (pd.DataFrame({"a": [0.01]}), "one series"),
        (np.zeros((2, 2)), "one-dimensional"),
    ],
)
def test_as_series_rejects_bad_input(values: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        as_series(values)  # type: ignore[arg-type]


def test_as_series_pnl_allows_large_losses_and_min_obs() -> None:
    assert as_series([-250.0, 100.0], pnl=True).tolist() == [-250.0, 100.0]
    assert as_series([-1.0]).tolist() == [-1.0]  # a total loss is a valid return
    with pytest.raises(DataError, match="at least 1"):
        as_series(pd.Series([np.nan, np.nan], index=DAYS[:2]))
    with pytest.raises(DataError, match="at least 2"):
        as_series([0.01], min_obs=2)


def test_periods_per_year() -> None:
    assert periods_per_year(None) == 252
    cfg = AtlasConfig.model_validate({"quant": {"features": {"sessions_per_year": 250}}})
    assert periods_per_year(None, cfg) == 250
    assert periods_per_year(12) == 12
    for bad in (0, -1, True, 2.5):
        with pytest.raises(ValueError, match="periods"):
            periods_per_year(bad)  # type: ignore[arg-type]


def test_annual_to_daily_is_geometric() -> None:
    d = annual_to_daily(0.05, 252)
    assert (1 + d) ** 252 == pytest.approx(1.05, rel=1e-12)
    assert annual_to_daily(0.0, 252) == 0.0
    with pytest.raises(ValueError, match="annual rate"):
        annual_to_daily(-1.0, 252)


def test_risk_free_daily_scalar_series_and_default() -> None:
    idx = DAYS[:3]
    assert risk_free_daily(None, idx, 252).tolist() == [0.0, 0.0, 0.0]
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"risk_free_annual": 0.04}}})
    assert risk_free_daily(None, idx, 252, cfg) == pytest.approx([annual_to_daily(0.04, 252)] * 3)
    assert risk_free_daily(0.0001, idx, 252).tolist() == [0.0001] * 3
    series = pd.Series([0.1, 0.2, 0.3, 0.4], index=DAYS[:4])
    assert risk_free_daily(series, idx, 252).tolist() == [0.1, 0.2, 0.3]
    with pytest.raises(DataError, match="no risk-free rate"):
        risk_free_daily(series, DAYS[2:6], 252)
    with pytest.raises(ValueError, match="risk_free"):
        risk_free_daily(math.inf, idx, 252)
    with pytest.raises(ValueError, match="risk_free"):
        risk_free_daily("1%", idx, 252)  # type: ignore[arg-type]


def test_align_inner_join() -> None:
    r = pd.Series([0.01, 0.02, np.nan, 0.04], index=DAYS[:4])
    b = pd.Series([0.1, 0.2, 0.3], index=DAYS[1:4])
    ra, ba = align(r, b)
    assert list(ra.index) == [DAYS[1], DAYS[3]] == list(ba.index)
    assert ra.tolist() == [0.02, 0.04] and ba.tolist() == [0.1, 0.3]
    with pytest.raises(DataError, match="common sessions"):
        align(r.iloc[:1], b)
    with pytest.raises(ValueError, match="both be Series"):
        align(r, [0.1, 0.2, 0.3, 0.4])
    with pytest.raises(ValueError, match="same length"):
        align([0.1, 0.2], [0.1, 0.2, 0.3])
    xa, ya = align([0.1, np.nan, 0.3, 0.4], [0.2, 0.2, np.nan, 0.5])
    assert xa.tolist() == [0.1, 0.4] and ya.tolist() == [0.2, 0.5]  # positions 0 and 3


def test_window_length() -> None:
    assert window_length(None) == 63
    assert window_length(20) == 20
    for bad in (1, 0, True, 2.0):
        with pytest.raises(ValueError, match="window"):
            window_length(bad)  # type: ignore[arg-type]


def test_nan_if_zero() -> None:
    assert nan_if_zero(1.0, 2.0) == 0.5
    assert math.isnan(nan_if_zero(1.0, 0.0))
    assert math.isnan(nan_if_zero(0.0, 0.0))


def test_as_weights() -> None:
    w = as_weights(pd.Series({"A": 1, "B": -0.5}))
    assert w.dtype == np.float64 and w.tolist() == [1.0, -0.5]
    with pytest.raises(ValueError, match="Series"):
        as_weights([0.1])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="duplicate"):
        as_weights(pd.Series([0.1, 0.2], index=["A", "A"]))
    with pytest.raises(ValueError, match="finite"):
        as_weights(pd.Series({"A": np.nan}))


def test_as_covariance_validates() -> None:
    cov = pd.DataFrame([[4.0, 1.0], [1.0, 9.0]], index=["A", "B"], columns=["A", "B"])
    matrix, names = as_covariance(cov)
    assert names == ["A", "B"] and matrix.tolist() == [[4.0, 1.0], [1.0, 9.0]]
    assert as_covariance(np.eye(3))[1] == [0, 1, 2]
    singular = np.array([[1.0, 1.0], [1.0, 1.0]])  # PSD, rank 1: allowed
    assert as_covariance(singular)[0].shape == (2, 2)
    bad = [
        (pd.DataFrame([[1.0]], index=["A"], columns=["B"]), "same unique labels"),
        (np.ones((2, 3)), "square"),
        (np.array([[1.0, np.nan], [np.nan, 1.0]]), "finite"),
        (np.array([[1.0, 0.5], [0.4, 1.0]]), "symmetric"),
        (np.array([[1.0, 2.0], [2.0, 1.0]]), "positive semi-definite"),
        (np.array([[-1.0]]), "positive semi-definite"),
        (np.zeros((0, 0)), "empty"),
        ([[1.0]], "DataFrame"),
    ]
    for value, match in bad:
        with pytest.raises(ValueError, match=match):
            as_covariance(value)  # type: ignore[arg-type]


def test_weights_on() -> None:
    names: list[object] = ["A", "B", "C"]
    w = pd.Series({"C": 0.3, "A": 0.1, "Z": 0.0})
    assert weights_on(names, w).tolist() == [0.1, 0.0, 0.3]
    with pytest.raises(DataError, match="without an estimate"):
        weights_on(names, pd.Series({"A": 0.1, "Z": 0.2}))
    assert weights_on(names, pd.Series({"A": 0.1, "Z": 0.2}), strict=False).tolist() == [
        0.1, 0.0, 0.0
    ]  # fmt: skip
    assert weights_on(names, np.array([1.0, 2.0, 3.0])).tolist() == [1.0, 2.0, 3.0]
    with pytest.raises(ValueError, match="one value per name"):
        weights_on(names, np.array([1.0, 2.0]))
    with pytest.raises(ValueError, match="finite"):
        weights_on(names, np.array([1.0, np.nan, 3.0]))


def test_reference_fixture_loads(reference: Reference) -> None:
    assert len(reference.returns) == len(reference.benchmark) == 504
    assert reference.returns.index.is_monotonic_increasing
    assert reference.periods == 252 and reference.risk_free_daily == 0.0001
    assert len(reference.values) == 30


def test_audit_followup_uncovered_input_errors() -> None:  # post-DONE audit item 4
    with pytest.raises(ValueError, match="risk_free contains infinite"):
        risk_free_daily(pd.Series([0.0, np.inf, 0.0], index=DAYS[:3]), DAYS[:3], 252)
    with pytest.raises(ValueError, match="weights must be numeric"):
        as_weights(pd.Series({"A": "five percent"}))


def test_audit_followup_is_constant_and_sample_std() -> None:  # post-DONE audit
    b = np.random.default_rng(1).normal(0.0, 0.01, 100)
    assert is_constant(np.array([0.001] * 100)) and is_constant((b + 0.001) - b)
    assert is_constant(np.array([])) and is_constant(np.zeros(5))
    assert not is_constant(np.array([0.001, 0.001 * (1 + 1e-9)]))  # a real (tiny) spread
    assert not is_constant(np.array([1e-20, -1e-20]))  # scale-free: tiny but genuine
    assert sample_std(np.array([0.001] * 100)) == 0.0
    assert sample_std(np.array([0.01, 0.03])) == pytest.approx(float(np.std([0.01, 0.03], ddof=1)))
