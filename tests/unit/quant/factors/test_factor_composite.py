"""Stage 16 Task 4 of the brief: the composite builder `composite(factors, weights)` and
the equal-weight style composites."""

import math

import numpy as np
import pandas as pd
import pytest

from atlas.quant.factors import FACTORS, GROUPS, composite, style_composites

IDX = pd.Index(["A", "B", "C", "D"], name="symbol")


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {"x": [1.0, -1.0, 0.5, np.nan], "y": [0.0, 2.0, -2.0, np.nan], "z": [3.0, 1.0, 2.0, 4.0]},
        index=IDX,
    )


def test_weighted_sum_known_values() -> None:
    out = composite(_frame(), {"x": 2.0, "y": 1.0}, standardize=False)
    assert out.name == "composite"
    assert list(out.index) == list(IDX)
    assert out["A"] == pytest.approx((2 * 1.0 + 1 * 0.0) / 3)
    assert out["B"] == pytest.approx((2 * -1.0 + 1 * 2.0) / 3)
    assert math.isnan(out["D"])  # neither factor known


def test_negative_weight_flips() -> None:
    out = composite(_frame(), {"x": 1.0, "y": -1.0}, standardize=False)
    assert out["A"] == pytest.approx((1.0 - 0.0) / 2)
    assert out["C"] == pytest.approx((0.5 + 2.0) / 2)


def test_missing_factor_renormalised() -> None:
    frame = _frame()
    frame.loc["A", "y"] = np.nan
    out = composite(frame, {"x": 2.0, "y": 1.0}, standardize=False)
    assert out["A"] == pytest.approx(1.0)  # only x known: weight 2 / 2


def test_zero_weight_factor_ignored() -> None:
    out = composite(_frame(), {"x": 1.0, "z": 0.0}, standardize=False)
    pd.testing.assert_series_equal(out, _frame()["x"].rename("composite"))


def test_standardized() -> None:
    out = composite(_frame(), {"x": 1.0, "z": 1.0})
    valid = out.dropna()
    assert abs(valid.mean()) < 1e-12
    assert valid.std(ddof=0) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("weights", "match"),
    [
        ({"nope": 1.0}, "unknown factor"),
        ({}, "at least one"),
        ({"x": 0.0, "y": 0.0}, "not all be zero"),
        ({"x": float("nan")}, "finite"),
    ],
)
def test_bad_weights(weights: dict[str, float], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        composite(_frame(), weights)


def test_style_composites() -> None:
    rng = np.random.default_rng(3)
    names = [f.name for f in FACTORS]
    frame = pd.DataFrame(rng.standard_normal((30, len(names))), columns=names)
    styles = style_composites(frame)
    assert list(styles.columns) == list(GROUPS)
    value = [f.name for f in FACTORS if f.group == "value"]
    pd.testing.assert_series_equal(
        styles["value"], composite(frame, dict.fromkeys(value, 1.0)), check_names=False
    )
    pd.testing.assert_series_equal(
        styles["size"], composite(frame, {"size": 1.0}), check_names=False
    )
