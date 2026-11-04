"""Covariance estimators: input checks, history and missing-data policy, sample and EWMA
(Stage 18, brief task 1), PSD repair and conditioning inside the estimators."""

import dataclasses
from datetime import date

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from risk_world import (
    factor_sigma,
    frobenius,
    mvn_returns,
    names,
    sessions,
    standard_errors,
)

from atlas.core.errors import DataError
from atlas.quant.risk_model.covariance import (
    METHODS,
    CovResult,
    EWMACovariance,
    LedoitWolfCovariance,
    OASCovariance,
    SampleCovariance,
    corr_from_cov,
    cov_from_corr,
    covariance_estimator,
)
from atlas.quant.risk_model.volatility import ewma_lambda


def _frame(t: int = 100, n: int = 3, seed: int = 0) -> pd.DataFrame:
    sigma = np.diag(np.linspace(0.01, 0.03, n) ** 2)
    return mvn_returns(sigma, t, seed)


# --- Task 4: CovResult, input checks, history and missing-data policy -----------------------


def test_corr_from_cov_and_back() -> None:
    cov = pd.DataFrame([[4.0, 2.0], [2.0, 9.0]], index=["A", "B"], columns=["A", "B"])
    corr = corr_from_cov(cov)
    assert isinstance(corr, pd.DataFrame) and list(corr.index) == ["A", "B"]
    assert np.allclose(corr.to_numpy(), [[1.0, 1 / 3], [1 / 3, 1.0]])
    back = cov_from_corr(corr, pd.Series([2.0, 3.0], index=["A", "B"]))
    assert np.allclose(back.to_numpy(), cov.to_numpy())
    assert np.allclose(corr_from_cov(cov.to_numpy()), corr.to_numpy())
    with pytest.raises(ValueError, match="variance"):
        corr_from_cov(np.diag([1.0, 0.0]))
    with pytest.raises(ValueError, match="vol"):
        cov_from_corr(np.eye(2), np.array([1.0, 2.0, 3.0]))


@pytest.mark.parametrize(
    "case", ["array", "text", "inf", "unsorted", "duplicate_index", "duplicate_names", "empty"]
)
def test_estimate_validates_input(case: str) -> None:
    good = _frame()
    bad: object
    if case == "array":
        bad = good.to_numpy()
    elif case == "text":
        bad = good.assign(S01=["x"] * len(good))
    elif case == "inf":
        bad = good.copy()
        bad.iloc[5, 1] = np.inf
    elif case == "unsorted":
        bad = good.iloc[::-1]
    elif case == "duplicate_index":
        bad = pd.concat([good.iloc[:50], good.iloc[49:]])
    elif case == "duplicate_names":
        bad = good.set_axis(["A", "A", "B"], axis=1)
    else:
        bad = good.iloc[:0]
    with pytest.raises(ValueError):
        SampleCovariance(min_history=10).estimate(bad)  # type: ignore[arg-type]


def test_min_history_drops_short_names() -> None:
    frame = _frame(t=100, n=2)
    frame["NEW"] = np.nan
    frame.iloc[60:, 2] = np.random.default_rng(1).normal(0, 0.02, 40)
    res = SampleCovariance(min_history=63).estimate(frame)
    assert res.symbols == ("S00", "S01")
    assert dict(res.dropped) == {"NEW": "history"}
    assert res.n_obs == 100
    assert res.end == date(2015, 5, 21)  # the last of 100 business days from 2015-01-02
    assert res.method == "sample"
    # The default comes from quant.risk_model.min_history (63).
    assert SampleCovariance().min_history == 63


def test_pairwise_overlap_drops_a_name() -> None:
    frame = _frame(t=200, n=3)
    frame.iloc[100:, 1] = np.nan  # S01: first 100 sessions only
    frame.iloc[:100, 2] = np.nan  # S02: last 100 sessions only -> no common session with S01
    res = SampleCovariance(min_history=63).estimate(frame)
    # S01 and S02 are tied (one short pair each, 100 returns each): the later name goes.
    assert res.symbols == ("S00", "S01")
    assert dict(res.dropped) == {"S02": "overlap"}
    # A name in more short pairs goes first, whatever its name.
    frame["S03"] = frame["S02"]
    frame.iloc[:100, 3] = np.random.default_rng(5).normal(0, 0.02, 100)
    frame.iloc[100:, 3] = np.nan  # S03 overlaps S00 and S01 fully, S02 not at all
    res = SampleCovariance(min_history=63).estimate(frame)
    assert dict(res.dropped) == {"S02": "overlap"}
    assert res.symbols == ("S00", "S01", "S03")


def test_constant_name_dropped() -> None:
    frame = _frame()
    frame["FLAT"] = 0.001
    res = SampleCovariance(min_history=10).estimate(frame)
    assert dict(res.dropped) == {"FLAT": "constant"}
    assert "FLAT" not in res.symbols


def test_nothing_left_raises_data_error() -> None:
    with pytest.raises(DataError, match="history"):
        SampleCovariance(min_history=200).estimate(_frame(t=100))
    frame = _frame(t=100, n=2)
    frame.iloc[:50, 0] = np.nan
    frame.iloc[50:, 1] = np.nan
    with pytest.raises(DataError, match="complete"):
        SampleCovariance(min_history=40, missing="complete").estimate(frame)


def test_complete_policy_uses_common_sessions() -> None:
    frame = _frame(t=100, n=2)
    frame.iloc[:10, 1] = np.nan
    res = SampleCovariance(min_history=63, missing="complete").estimate(frame)
    assert res.n_obs == 90
    expected = np.cov(frame.iloc[10:].to_numpy(), rowvar=False, ddof=1)
    assert np.allclose(res.cov.to_numpy(), expected, rtol=1e-12, atol=0)


def test_cov_result_is_read_only() -> None:
    res = SampleCovariance(min_history=10).estimate(_frame())
    before = res.cov.iloc[0, 0]
    cov, vol, corr = res.cov, res.vol, res.corr  # a caller holding the frames edits them
    cov.iloc[0, 0] = 99.0
    vol.iloc[0] = 99.0
    corr.iloc[0, 1] = 99.0
    assert res.cov.iloc[0, 0] == before
    assert res.vol.iloc[0] == pytest.approx(np.sqrt(before))
    assert res.corr.iloc[0, 1] != 99.0
    assert not res.matrix.flags.writeable
    with pytest.raises(ValueError):
        res.matrix[0, 0] = 1.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.method = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        res.dropped["X"] = "history"  # type: ignore[index]
    assert isinstance(res, CovResult)
    assert list(res.cov.index) == list(res.symbols) == names(3)
    assert res.vol.name == "vol"
    assert np.allclose(np.diag(res.corr.to_numpy()), 1.0)
    assert sessions(3)[0] == pd.Timestamp("2015-01-02")


# --- Task 5: sample and EWMA against a known covariance (brief task 1) ----------------------


def _n_eff(weights: np.ndarray) -> float:
    return float(weights.sum() ** 2 / (weights**2).sum())


def test_sample_recovers_known_sigma() -> None:
    sigma = factor_sigma(5, seed=0)
    res = SampleCovariance().estimate(mvn_returns(sigma, 5000, seed=11))
    err = np.abs(res.cov.to_numpy() - sigma)
    assert (err <= 4 * standard_errors(sigma, 5000)).all(), err / standard_errors(sigma, 5000)
    assert np.allclose(res.vol.to_numpy(), np.sqrt(np.diag(sigma)), rtol=0.03)
    assert res.adjustments == () and res.shrinkage is None


def test_sample_matches_pandas_pairwise() -> None:
    frame = mvn_returns(factor_sigma(5, seed=2), 300, seed=3)
    holes = np.random.default_rng(4).random(frame.shape) < 0.1
    frame = frame.mask(holes)
    res = SampleCovariance(min_history=63).estimate(frame)
    assert res.adjustments == ()  # PSD as estimated: nothing repaired
    assert np.allclose(res.cov.to_numpy(), frame.cov().to_numpy(), rtol=1e-12, atol=0)
    assert res.n_obs == 300


def test_ewma_known_weights() -> None:
    # Weights 0.25, 0.5, 1 (oldest to latest) for λ = 0.5, normalised by 1.75; zero mean.
    frame = pd.DataFrame({"X": [1.0, 2.0, 3.0], "Y": [1.0, -1.0, 0.5]}, index=sessions(3))
    res = EWMACovariance(lam=0.5, min_history=2).estimate(frame)
    assert res.adjustments == ()
    assert res.cov.loc["X", "X"] == pytest.approx((0.25 * 1 + 0.5 * 4 + 9) / 1.75)
    assert res.cov.loc["Y", "Y"] == pytest.approx((0.25 * 1 + 0.5 * 1 + 0.25) / 1.75)
    assert res.cov.loc["X", "Y"] == pytest.approx((0.25 * 1 - 0.5 * 2 + 1.5) / 1.75)
    assert res.method == "ewma" and res.shrinkage is None
    # A missing return keeps its decay slot but adds nothing (pairwise).
    holed = frame.copy()
    holed.loc[holed.index[1], "Y"] = np.nan
    res = EWMACovariance(lam=0.5, min_history=2).estimate(holed)
    assert res.adjustments == ()
    assert res.cov.loc["Y", "Y"] == pytest.approx((0.25 * 1 + 0.25) / 1.25)
    assert res.cov.loc["X", "Y"] == pytest.approx((0.25 * 1 + 1.5) / 1.25)
    assert res.cov.loc["X", "X"] == pytest.approx((0.25 * 1 + 0.5 * 4 + 9) / 1.75)


def test_ewma_half_life_equals_lambda() -> None:
    frame = mvn_returns(factor_sigma(3, seed=4), 200, seed=5)
    by_half_life = EWMACovariance(half_life=10)
    assert by_half_life.lam == pytest.approx(0.5**0.1)
    a = by_half_life.estimate(frame).cov
    b = EWMACovariance(lam=0.5**0.1).estimate(frame).cov
    assert np.allclose(a.to_numpy(), b.to_numpy(), rtol=1e-14, atol=0)
    assert ewma_lambda(half_life=10) == pytest.approx(0.5**0.1)
    assert ewma_lambda() == 0.94  # quant.risk_model.ewma_lambda
    assert EWMACovariance().lam == 0.94
    for bad in ({"half_life": 0}, {"half_life": -3.0}, {"lam": 1.0}, {"lam": 0.0}):
        with pytest.raises(ValueError):
            ewma_lambda(**bad)


def test_ewma_recovers_known_sigma() -> None:
    sigma = factor_sigma(5, seed=6)
    lam = ewma_lambda(half_life=500)
    t = 5000
    res = EWMACovariance(lam=lam).estimate(mvn_returns(sigma, t, seed=7))
    n_eff = _n_eff(lam ** np.arange(t))
    err = np.abs(res.cov.to_numpy() - sigma)
    assert (err <= 4 * standard_errors(sigma, n_eff)).all()
    # RiskMetrics λ = 0.94: noisy on one path (about 32 effective sessions), unbiased on average.
    sigma3 = factor_sigma(3, seed=8)
    reps, t = 800, 200
    mean = np.mean(
        [
            EWMACovariance().estimate(mvn_returns(sigma3, t, seed=100 + r)).matrix
            for r in range(reps)
        ],
        axis=0,
    )
    tol = 4 * standard_errors(sigma3, _n_eff(0.94 ** np.arange(t))) / np.sqrt(reps)
    assert (np.abs(mean - sigma3) <= tol).all()


def test_ewma_tracks_a_variance_shift() -> None:
    # 400 calm sessions, then 100 at 4x the variance: on every path EWMA (λ = 0.94) is nearer the
    # new covariance than the sample estimate, and on average about half as far.
    sigma = factor_sigma(4, seed=9)
    ratios = []
    for s in range(20):
        calm = mvn_returns(sigma, 400, seed=10 + 2 * s)
        stressed = mvn_returns(4 * sigma, 100, seed=11 + 2 * s)
        frame = pd.DataFrame(
            np.vstack([calm.to_numpy(), stressed.to_numpy()]),
            index=sessions(500),
            columns=calm.columns,
        )
        ewma = frobenius(EWMACovariance().estimate(frame).matrix, 4 * sigma)
        sample = frobenius(SampleCovariance().estimate(frame).matrix, 4 * sigma)
        ratios.append(ewma / sample)
    assert max(ratios) < 1.0
    assert np.mean(ratios) < 0.75


def test_ewma_rejects_lambda_and_half_life_together() -> None:
    with pytest.raises(ValueError, match="not both"):
        EWMACovariance(lam=0.9, half_life=10)


# --- Task 7: PSD repair and condition check inside the estimators; factory; all PSD ---------


def test_pairwise_non_psd_is_repaired_keeping_variances() -> None:
    # A follows B on sessions 0-99, C follows B on 200-299, and A = -C on 100-199: pairwise
    # correlations near (A,B) 0.7, (B,C) 0.7, (A,C) -1, which no PSD matrix has.
    rng = np.random.default_rng(13)
    b = rng.normal(0, 0.02, 300)
    a = np.full(300, np.nan)
    c = np.full(300, np.nan)
    a[:100] = b[:100] + rng.normal(0, 0.002, 100)
    common = rng.normal(0, 0.02, 100)
    a[100:200] = common
    c[100:200] = -common + rng.normal(0, 0.002, 100)
    c[200:] = b[200:] + rng.normal(0, 0.002, 100)
    frame = pd.DataFrame({"A": a, "B": b, "C": c}, index=sessions(300))
    raw = frame.cov().to_numpy()
    assert np.linalg.eigvalsh(raw).min() < 0  # the pairwise estimate itself is broken
    res = SampleCovariance(min_history=63).estimate(frame)
    assert res.adjustments[0] == "nearest_psd"
    assert np.linalg.eigvalsh(res.matrix).min() > 0
    own = frame.var(ddof=1).to_numpy()  # each name's variance over all its own sessions
    assert np.allclose(np.diag(res.matrix), own, rtol=1e-4)


def test_singular_sample_is_floored() -> None:
    frame = mvn_returns(factor_sigma(30, seed=14), 20, seed=15)  # N > T: rank 19
    res = SampleCovariance(min_history=10).estimate(frame)
    assert res.adjustments == ("condition_floor",)
    assert res.condition_number <= 1e5 * (1 + 1e-9)
    eig = np.linalg.eigvalsh(res.matrix)
    assert eig.min() > 0
    assert res.condition_number == pytest.approx(eig.max() / eig.min())
    tight = SampleCovariance(min_history=10, max_condition=50.0).estimate(frame)
    assert tight.condition_number <= 50 * (1 + 1e-9)


def test_covariance_estimator_factory() -> None:
    kinds = {
        "sample": SampleCovariance,
        "ewma": EWMACovariance,
        "ledoit_wolf": LedoitWolfCovariance,
        "oas": OASCovariance,
    }
    assert set(kinds) == set(METHODS)
    for method, cls in kinds.items():
        est = covariance_estimator(method, min_history=20)
        assert type(est) is cls and est.method == method and est.min_history == 20
    assert type(covariance_estimator()) is LedoitWolfCovariance  # quant.risk_model.default_method
    assert covariance_estimator("ewma", half_life=10).lam == pytest.approx(0.5**0.1)  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="method"):
        covariance_estimator("shrunk")
    with pytest.raises(ValueError, match="missing"):
        covariance_estimator("sample", missing="drop")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="min_history"):
        covariance_estimator("sample", min_history=1)
    with pytest.raises(ValueError, match="max_condition"):
        covariance_estimator("sample", max_condition=1.0)
    with pytest.raises(TypeError):
        covariance_estimator("sample", lam=0.9)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    n=st.integers(1, 7),
    t=st.integers(8, 90),
    seed=st.integers(0, 10_000),
    hole_rate=st.floats(0.0, 0.4),
    collinear=st.booleans(),
)
def test_every_estimator_output_is_psd(
    n: int, t: int, seed: int, hole_rate: float, collinear: bool
) -> None:
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 1, (t, n)) * rng.uniform(0.005, 0.05, n)
    if collinear and n >= 2:
        x[:, -1] = x[:, 0] * 1.5 + rng.normal(0, 1e-9, t)  # an almost exact copy
    x[rng.random((t, n)) < hole_rate] = np.nan
    frame = pd.DataFrame(x, index=sessions(t), columns=names(n))
    for method in METHODS:
        try:
            res = covariance_estimator(method, min_history=5).estimate(frame)
        except DataError:
            continue  # not enough history: no estimate, by design
        m = res.matrix
        assert np.array_equal(m, m.T)
        eig = np.linalg.eigvalsh(m)
        assert eig.min() > 0
        assert eig.max() / eig.min() <= 1e5 * (1 + 1e-6)
        assert res.condition_number <= 1e5 * (1 + 1e-9)
        assert np.allclose(res.vol.to_numpy() ** 2, np.diag(m), rtol=1e-12)
        corr = res.corr.to_numpy()
        assert np.array_equal(np.diag(corr), np.ones(len(m)))
        assert np.abs(corr).max() <= 1 + 1e-9
        assert set(res.symbols) | set(res.dropped) == set(frame.columns)


# --- Task 11: package surface and look-ahead ------------------------------------------------

EXPORTS = {
    "METHODS", "VOL_METHODS", "CovResult", "CovarianceEstimator", "EWMACovariance",
    "LedoitWolfCovariance", "OASCovariance", "SampleCovariance", "VolForecast",
    "annualise_variance", "annualise_vol", "blend_vol", "condition_number", "corr_from_cov",
    "cov_from_corr", "covariance_estimator", "deannualise_vol", "ewma_lambda",
    "ewma_variance_path", "ewma_vol", "floor_condition", "forecast_vol", "forecast_vols",
    "garch_vol", "is_psd", "nearest_correlation", "nearest_psd", "realised_vol",
}  # fmt: skip


def test_package_exports() -> None:
    import atlas.quant.risk_model as rm

    assert set(rm.__all__) == EXPORTS
    for name in rm.__all__:
        assert getattr(rm, name) is not None


def test_package_import_does_not_load_arch() -> None:
    # `arch` (scipy, statsmodels) is imported only when a GARCH fit runs.
    import subprocess
    import sys

    code = "import sys, atlas.quant.risk_model; print('arch' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_estimate_uses_only_the_given_rows() -> None:
    frame = mvn_returns(factor_sigma(4, seed=16), 300, seed=17)
    cut = frame.index[199]
    future = frame.copy()
    future.iloc[200:] *= 25.0  # rewrite everything after the cut
    for method in METHODS:
        est = covariance_estimator(method)
        a = est.estimate(frame.loc[:cut])
        b = est.estimate(future.loc[:cut])
        assert np.array_equal(a.matrix, b.matrix)
        assert a.end == b.end == cut.date()
    with pytest.raises(ValueError, match="ascending"):
        EWMACovariance().estimate(frame.iloc[::-1])  # never silently weight the wrong end
    # EWMA weights the latest session most: a shock on the last row moves it more than on the first.
    base = EWMACovariance().estimate(frame).matrix
    first, last = frame.copy(), frame.copy()
    first.iloc[0] *= 10.0
    last.iloc[-1] *= 10.0
    moved_first = frobenius(EWMACovariance().estimate(first).matrix, base)
    moved_last = frobenius(EWMACovariance().estimate(last).matrix, base)
    assert moved_last > 1000 * moved_first


# --- review fixes (before verification) -----------------------------------------------------


def test_complete_policy_drops_names_constant_on_common_sessions() -> None:
    frame = _frame(t=200, n=3)
    frame.iloc[:100, 2] = np.nan
    frame.iloc[:100, 1] = np.random.default_rng(2).normal(0, 0.02, 100)
    frame.iloc[100:, 1] = 0.0  # S01 moves only before S02 starts: constant on the common rows
    res = SampleCovariance(min_history=63, missing="complete").estimate(frame)
    assert dict(res.dropped) == {"S01": "constant"}
    assert res.symbols == ("S00", "S02")
    assert res.n_obs == 100
    assert (np.diag(res.matrix) > 0).all()


def test_non_finite_estimate_raises_data_error() -> None:
    # λ = 0.5: the weights of sessions 1100+ sessions before the end underflow to 0, so S01,
    # present only then, has no EWMA weight left at all.
    frame = _frame(t=1200, n=2)
    frame.iloc[100:, 1] = np.nan
    with pytest.raises(DataError, match="not finite"):
        EWMACovariance(lam=0.5, min_history=63).estimate(frame)


def _broken_pairwise() -> pd.DataFrame:
    rng = np.random.default_rng(13)
    b = rng.normal(0, 0.02, 300)
    a = np.full(300, np.nan)
    c = np.full(300, np.nan)
    a[:100] = b[:100] + rng.normal(0, 0.002, 100)
    common = rng.normal(0, 0.02, 100)
    a[100:200] = common
    c[100:200] = -common + rng.normal(0, 0.002, 100)
    c[200:] = b[200:] + rng.normal(0, 0.002, 100)
    return pd.DataFrame({"A": a, "B": b, "C": c}, index=sessions(300))


@pytest.mark.parametrize("method", ["ledoit_wolf", "oas"])
def test_shrinkage_repairs_the_pairwise_sample_before_shrinking(method: str) -> None:
    # Repairing after shrinking would land on the PSD boundary (a zero eigenvalue) and need
    # the condition floor; repairing S first keeps the shrunk matrix positive definite.
    frame = _broken_pairwise()
    res = covariance_estimator(method, min_history=63).estimate(frame)
    assert res.adjustments == ("nearest_psd",)
    assert res.shrinkage is not None and res.shrinkage > 0
    assert res.condition_number < 1e5
    assert np.linalg.eigvalsh(res.matrix).min() > 0
    sample = SampleCovariance(min_history=63).estimate(frame)
    assert sample.adjustments == ("nearest_psd", "condition_floor")
