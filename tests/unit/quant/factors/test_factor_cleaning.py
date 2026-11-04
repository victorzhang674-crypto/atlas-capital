"""Stage 16 Task 3 of the brief: the cleaning pipeline (winsor bounds, z mean≈0/std≈1,
sector-median fill with flags, sector-demeaned sums ≈ 0, optional size neutralisation)."""

import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas.core.config import FactorsConfig
from atlas.quant.factors import (
    CleanedFactors,
    clean_factors,
    fill_missing,
    neutralize,
    winsorize,
    zscore,
)

TOL = 1e-9


def _frame(seed: int = 0, n: int = 60, cols: tuple[str, ...] = ("a", "b", "size")) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = {c: rng.standard_t(3, n) * (i + 1) + i for i, c in enumerate(cols)}
    return pd.DataFrame(data, index=pd.Index([f"S{i:03d}" for i in range(n)], name="symbol"))


def _sectors(index: pd.Index, k: int = 3) -> pd.Series:
    names = ["Energy", "Financials", "Utilities", None]
    return pd.Series([names[i % k] for i in range(len(index))], index=index, dtype="object")


# --- winsorize -------------------------------------------------------------------------------


def test_winsor_bounds_and_order() -> None:
    raw = _frame()
    raw.iloc[3, 0] = np.nan
    out = winsorize(raw, 0.01, 0.99)
    for col in raw.columns:
        x = raw[col].dropna()
        lo, hi = x.quantile(0.01), x.quantile(0.99)
        y = out[col].dropna()
        assert y.min() >= lo - TOL and y.max() <= hi + TOL
        inside = (x >= lo) & (x <= hi)
        assert (y[inside] == x[inside]).all()  # values inside the bounds are untouched
        assert (np.diff(y[x.sort_values().index].to_numpy()) >= -TOL).all()  # order kept
    assert math.isnan(out.iloc[3, 0])


def test_winsor_clips_an_outlier() -> None:
    x = pd.DataFrame({"a": [*range(99), 1e9]}, dtype="float64")
    out = winsorize(x, 0.01, 0.99)
    assert out["a"].max() == pytest.approx(x["a"].quantile(0.99))
    assert out["a"].min() == pytest.approx(x["a"].quantile(0.01))


def test_winsor_no_clip_at_zero_one() -> None:
    raw = _frame()
    pd.testing.assert_frame_equal(winsorize(raw, 0.0, 1.0), raw)


def test_winsor_rejects_bad_bounds() -> None:
    with pytest.raises(ValueError, match="lower"):
        winsorize(_frame(), 0.5, 0.5)


# --- zscore ----------------------------------------------------------------------------------


def test_zscore_mean_zero_std_one() -> None:
    raw = _frame()
    raw.iloc[:5, 1] = np.nan
    z = zscore(raw)
    for col in z.columns:
        assert abs(z[col].mean()) < TOL
        assert z[col].std(ddof=0) == pytest.approx(1.0, abs=TOL)
    assert z.iloc[:5, 1].isna().all()


def test_zscore_constant_and_single_value_columns() -> None:
    frame = pd.DataFrame({"c": [3.0, 3.0, 3.0], "one": [np.nan, 5.0, np.nan], "none": [np.nan] * 3})
    z = zscore(frame)
    assert list(z["c"]) == [0.0, 0.0, 0.0]  # no dispersion: no information, exposure 0
    assert z["one"].iloc[1] == 0.0 and z["one"].isna().sum() == 2
    assert z["none"].isna().all()


# --- fill_missing ----------------------------------------------------------------------------


def test_fill_with_sector_median_and_flag() -> None:
    idx = pd.Index(list("ABCDEFGHI"), name="symbol")
    values = [1.0, 3.0, np.nan, 10.0, np.nan, 7.0, np.nan, np.nan, 20.0]
    frame = pd.DataFrame({"a": values}, index=idx)
    sectors = pd.Series(["X", "X", "X", "Y", "Y", None, "Z", None, None], index=idx, dtype="object")
    filled, flags = fill_missing(frame, sectors)
    # Z has no values: 0 (the mean of a z-score); H has no sector: the overall median (7.0),
    # not the median of the other sector-less names F and I (13.5)
    assert list(filled["a"]) == [1.0, 3.0, 2.0, 10.0, 10.0, 7.0, 0.0, 7.0, 20.0]
    assert list(flags["a"]) == [False, False, True, False, True, False, True, True, False]
    assert flags.dtypes.eq("bool").all()


def test_fill_leaves_an_all_missing_column() -> None:
    idx = pd.Index(["A", "B"], name="symbol")
    frame = pd.DataFrame({"a": [np.nan, np.nan]}, index=idx)
    filled, flags = fill_missing(frame, pd.Series(["X", "X"], index=idx))
    assert filled["a"].isna().all() and not flags["a"].any()


# --- neutralize ------------------------------------------------------------------------------


def _known(sectors: pd.Series) -> np.ndarray:
    return sectors.notna().to_numpy()


def test_sector_demeaned_sums_are_zero() -> None:
    raw = zscore(_frame())
    sectors = _sectors(raw.index, k=4)  # every fourth name has no sector
    known = _known(sectors)
    out = neutralize(raw, sectors)
    sums = out[known].groupby(sectors[known].to_numpy()).sum()
    assert (sums.abs() < TOL).all().all()
    # demeaning, not rescaling: within-sector differences are unchanged ...
    a = raw["a"][known] - raw["a"][known].groupby(sectors[known].to_numpy()).transform("mean")
    pd.testing.assert_series_equal(out["a"][known], a, check_names=False)
    # ... and a name without a sector is measured against the whole cross-section
    pd.testing.assert_series_equal(
        out["a"][~known], raw["a"][~known] - raw["a"].mean(), check_names=False
    )


def test_unknown_sector_names_are_not_each_others_peers() -> None:
    # Stage 16 audit: on the real lake every former S&P 500 member has no sector, so grouping
    # them would compare each future leaver with the others (information from after the
    # as-of). A sector-less name must react to another sector-less name exactly as to any
    # other name of the cross-section.
    raw = zscore(_frame())
    sectors = _sectors(raw.index, k=4)
    unknown = list(raw.index[~_known(sectors)])
    target, other_unknown, known_name = unknown[0], unknown[1], raw.index[0]
    base = neutralize(raw, sectors).loc[target, "a"]
    bumped_unknown, bumped_known = raw.copy(), raw.copy()
    bumped_unknown.loc[other_unknown, "a"] += 5.0
    bumped_known.loc[known_name, "a"] += 5.0
    via_unknown = neutralize(bumped_unknown, sectors).loc[target, "a"]
    via_known = neutralize(bumped_known, sectors).loc[target, "a"]
    assert via_unknown == pytest.approx(via_known, abs=1e-12)
    assert via_unknown == pytest.approx(base - 5.0 / len(raw), abs=1e-12)


def test_size_neutral_residual_orthogonal_to_size_and_sectors() -> None:
    raw = zscore(_frame())
    sectors = _sectors(raw.index, k=4)
    known = _known(sectors)
    out = neutralize(raw, sectors, size=raw["size"], exclude=("size",))
    size = raw["size"].to_numpy()
    groups = sectors[known].to_numpy()
    for col in ("a", "b"):
        assert abs(float(out[col].to_numpy()[known] @ size[known])) < 1e-8
        assert (out[col][known].groupby(groups).sum().abs() < 1e-8).all()
        # sector-less names: residual of a market-wide fit on [1, size]
        design = np.column_stack([np.ones(len(size)), size])
        coef, *_ = np.linalg.lstsq(design, raw[col].to_numpy(), rcond=None)
        want = raw[col].to_numpy()[~known] - design[~known] @ coef
        assert np.allclose(out[col].to_numpy()[~known], want, atol=1e-12)
    # size itself is only sector-demeaned
    want_size = raw["size"][known] - raw["size"][known].groupby(groups).transform("mean")
    pd.testing.assert_series_equal(out["size"][known], want_size, check_names=False)


def test_no_known_sector_is_market_demeaning() -> None:
    raw = zscore(_frame())
    sectors = pd.Series([None] * len(raw), index=raw.index, dtype="object")
    out = neutralize(raw, sectors)
    pd.testing.assert_frame_equal(out, raw - raw.mean())


def test_neutralize_keeps_nan_rows_and_columns() -> None:
    raw = zscore(_frame())
    raw.loc[raw.index[0], "a"] = np.nan
    raw["empty"] = np.nan
    out = neutralize(raw, _sectors(raw.index))
    assert math.isnan(out.loc[raw.index[0], "a"])
    assert out["empty"].isna().all()


# --- clean_factors ---------------------------------------------------------------------------


def _check_invariants(result: CleanedFactors, sectors: pd.Series) -> None:
    """Names with a known sector: mean 0, std 1, every sector sums to 0. Names without one
    are scored on the same scale, relative to the whole cross-section."""
    known = sectors.reindex(result.exposures.index).notna().to_numpy()
    basis = known if known.any() else np.ones(len(known), dtype=bool)
    groups = sectors.reindex(result.exposures.index).fillna("-").to_numpy()[basis]
    for col in result.exposures.columns:
        x = result.exposures[col]
        if x.isna().all():
            continue
        assert x.notna().all()  # filled
        xk = x[basis]
        assert abs(xk.mean()) < 1e-8
        std = xk.std(ddof=0)
        assert std == pytest.approx(1.0, abs=1e-8) or std < 1e-12
        if known.any():
            assert (xk.groupby(groups).sum().abs() < 1e-7).all()


def test_clean_pipeline_invariants() -> None:
    raw = _frame(seed=4)
    raw.iloc[[2, 9, 17], 0] = np.nan
    sectors = _sectors(raw.index, k=4)
    result = clean_factors(raw, sectors, FactorsConfig(min_names=10))
    assert list(result.exposures.columns) == list(raw.columns)
    assert list(result.exposures.index) == list(raw.index)
    assert result.filled.iloc[[2, 9, 17], 0].all() and result.filled.sum().sum() == 3
    _check_invariants(result, sectors)


def test_clean_size_neutral_invariants() -> None:
    raw = _frame(seed=5)
    sectors = _sectors(raw.index, k=4)
    known = _known(sectors)
    result = clean_factors(raw, sectors, FactorsConfig(min_names=10, size_neutral=True))
    _check_invariants(result, sectors)
    size = result.exposures["size"].to_numpy()[known]
    for col in ("a", "b"):
        assert abs(float(result.exposures[col].to_numpy()[known] @ size)) < 1e-7


def test_clean_too_few_names_left_nan() -> None:
    raw = _frame(n=30)
    raw.iloc[5:, 1] = np.nan  # 5 valid values of b
    result = clean_factors(raw, _sectors(raw.index), FactorsConfig(min_names=20))
    assert result.exposures["b"].isna().all()
    assert not result.filled["b"].any()
    assert result.exposures["a"].notna().all()


def test_clean_winsorises_before_scoring() -> None:
    # 1,000 names: the 99th percentile (linear interpolation) lies among the regular values;
    # with 100 names it would interpolate halfway to the outlier itself.
    idx = pd.Index([f"S{i}" for i in range(1000)], name="symbol")
    raw = pd.DataFrame({"a": [*range(999), 1e12]}, index=idx, dtype="float64")
    sectors = pd.Series(["X"] * 1000, index=idx, dtype="object")
    z = clean_factors(raw, sectors, FactorsConfig(min_names=10)).exposures["a"]
    assert z.max() < 2.0  # the outlier no longer dominates the scale


def test_clean_rejects_misaligned_sectors() -> None:
    raw = _frame(n=30)
    with pytest.raises(ValueError, match="sectors"):
        clean_factors(raw, _sectors(raw.index[:10]), FactorsConfig(min_names=10))


@settings(max_examples=60, deadline=None)
@given(
    n=st.integers(min_value=25, max_value=80),
    k=st.integers(min_value=1, max_value=4),
    missing=st.floats(min_value=0.0, max_value=0.4),
    seed=st.integers(min_value=0, max_value=10_000),
    size_neutral=st.booleans(),
)
def test_cleaning_invariants_property(
    n: int, k: int, missing: float, seed: int, size_neutral: bool
) -> None:
    rng = np.random.default_rng(seed)
    raw = _frame(seed=seed, n=n)
    raw = raw.mask(rng.random(raw.shape) < missing)
    sectors = _sectors(raw.index, k=k)
    cfg = FactorsConfig(min_names=5, size_neutral=size_neutral)
    result = clean_factors(raw, sectors, cfg)
    _check_invariants(result, sectors)
    assert (result.filled == (raw.isna() & result.exposures.notna())).all().all()
