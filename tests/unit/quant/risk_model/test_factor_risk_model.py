"""Cross-section preparation, factor history, factor covariance, RiskModel (Stage 19
Tasks 3 and 5)."""

import dataclasses
import time

import numpy as np
import pandas as pd
import pytest
from factor_risk_world import SECTORS, STYLES, cross_sections, make_world
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from atlas.core.config import AtlasConfig, FactorModelConfig
from atlas.core.errors import DataError
from atlas.quant.risk_model.factor_model import (
    MARKET,
    STYLE_NAMES,
    FactorHistory,
    FactorSet,
    RiskModel,
    build_risk_model,
    factor_covariance,
    fit_factor_history,
    newey_west_covariance,
    prepare_exposures,
    standardize_styles,
)
from atlas.quant.risk_model.psd import condition_number

CFG = FactorModelConfig()
FACTORS = FactorSet.from_config(CFG)
SMALL = AtlasConfig(quant={"risk_model": {"factor_model": {"min_cross_section": 50}}})


def test_factor_set_matches_the_world() -> None:
    assert STYLE_NAMES == STYLES
    assert FACTORS.sectors == SECTORS
    assert FACTORS.names == (MARKET, *SECTORS, *STYLES)
    assert len(FACTORS.names) == 20


# --- standardisation --------------------------------------------------------------------------


def _raw(n: int = 300, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    idx = pd.Index([f"S{i:03d}" for i in range(n)], name="symbol")
    raw = pd.DataFrame(rng.normal(size=(n, len(STYLES))), index=idx, columns=list(STYLES))
    return raw, pd.Series(np.exp(rng.normal(22, 1, n)), index=idx)


def test_standardize_styles_matches_a_manual_computation() -> None:
    raw, caps = _raw()
    raw.iloc[0, 0] = 50.0  # an outlier, clipped at the 99% quantile
    out, dropped, filled = standardize_styles(raw, caps, CFG)
    assert dropped == {} and filled == 0
    lo, hi = np.quantile(raw["momentum"], [0.01, 0.99])
    x = raw["momentum"].clip(lo, hi).to_numpy()
    want = (x - (caps.to_numpy() @ x) / caps.sum()) / x.std()
    np.testing.assert_allclose(out["momentum"].to_numpy(), want, atol=1e-12)
    w = caps / caps.sum()
    for col in out.columns:
        assert abs(float((w * out[col]).sum())) < 1e-12
        assert abs(float(out[col].std(ddof=0)) - 1.0) < 1e-12


def test_missing_style_becomes_neutral_and_is_counted() -> None:
    raw, caps = _raw()
    raw.iloc[3, 1] = np.nan
    raw.iloc[4, [0, 2]] = np.nan  # two missing: still kept (max_missing_styles = 2)
    out, dropped, filled = standardize_styles(raw, caps, CFG)
    assert dropped == {} and filled == 3
    assert out.iloc[3, 1] == 0.0 and out.iloc[4, 0] == 0.0 and out.iloc[4, 2] == 0.0
    assert not out.isna().any().any()


def test_too_many_missing_styles_drops_the_name() -> None:
    raw, caps = _raw()
    raw.iloc[7, [0, 1, 2]] = np.nan
    out, dropped, _ = standardize_styles(raw, caps, CFG)
    assert dropped == {raw.index[7]: "exposures"}
    assert raw.index[7] not in out.index and len(out) == len(raw) - 1


def test_degenerate_style_raises() -> None:
    raw, caps = _raw(50)
    raw["value"] = 1.0
    with pytest.raises(DataError, match="value"):
        standardize_styles(raw, caps, CFG)
    raw["value"] = np.nan
    with pytest.raises(DataError, match="value"):
        standardize_styles(raw, caps, CFG)


def test_standardize_validation() -> None:
    raw, caps = _raw(50)
    with pytest.raises(ValueError, match="columns"):
        standardize_styles(raw.drop(columns="value"), caps, CFG)
    with pytest.raises(ValueError, match="aligned"):
        standardize_styles(raw, caps.iloc[:-1], CFG)
    with pytest.raises(ValueError, match="finite"):
        standardize_styles(raw.mask(raw > 100).replace(raw.iloc[0, 0], np.inf), caps, CFG)


def test_unknown_sector_and_bad_cap_dropped_and_design_columns() -> None:
    w = make_world(n=120, t=2, seed=1)
    sectors = w.sectors.copy()
    sectors.iloc[0] = None
    sectors.iloc[1] = "Not A Sector"
    caps = w.caps.copy()
    caps.iloc[2] = np.nan
    caps.iloc[3] = -5.0
    prep = prepare_exposures(w.styles, sectors, caps, FACTORS, CFG)
    assert prep.dropped == {
        w.symbols[0]: "sector",
        w.symbols[1]: "sector",
        w.symbols[2]: "cap",
        w.symbols[3]: "cap",
    }
    assert list(prep.design.columns) == list(FACTORS.names)
    assert (prep.design[MARKET] == 1.0).all()
    assert (prep.design[list(SECTORS)].sum(axis=1) == 1.0).all()
    assert len(prep.design) == 116 and prep.caps.index.equals(prep.design.index)


# --- factor history -----------------------------------------------------------------------------


def test_fit_factor_history_shapes() -> None:
    w = make_world(n=200, t=60, seed=2)
    cs = cross_sections(w)
    cs[5] = dataclasses.replace(
        cs[5], returns=cs[5].returns.where(cs[5].returns.index != w.symbols[0])
    )
    hist = fit_factor_history(cs, FACTORS, SMALL.quant.risk_model.factor_model)
    assert isinstance(hist, FactorHistory)
    assert hist.factor_returns.shape == (60, 20) and list(hist.factor_returns.columns) == list(
        FACTORS.names
    )
    assert hist.residuals.shape == (60, 200)
    assert np.isnan(hist.residuals.iloc[5][w.symbols[0]])  # no return that day -> no residual
    assert hist.residuals.iloc[6].notna().all()
    assert (hist.n_names == [200] * 5 + [199] + [200] * 54).all()
    assert hist.r2.between(0, 1).all()
    assert hist.factor_returns.index.equals(pd.DatetimeIndex(w.dates, name="date"))
    assert hist.skipped == ()


def test_fit_factor_history_short_cross_section() -> None:
    w = make_world(n=120, t=4, seed=3)
    cs = cross_sections(w)
    bad = dataclasses.replace(cs[2], returns=cs[2].returns.where(np.arange(120) < 40))
    cfg = SMALL.quant.risk_model.factor_model
    with pytest.raises(DataError, match=r"min_cross_section"):
        fit_factor_history([*cs[:2], bad, cs[3]], FACTORS, cfg)
    hist = fit_factor_history([*cs[:2], bad, cs[3]], FACTORS, cfg, on_short="skip")
    assert hist.skipped == (cs[2].day,) and len(hist.factor_returns) == 3


def test_fit_factor_history_rejects_unsorted_or_empty() -> None:
    w = make_world(n=120, t=3, seed=3)
    cs = cross_sections(w)
    cfg = SMALL.quant.risk_model.factor_model
    with pytest.raises(ValueError, match="ascending"):
        fit_factor_history([cs[1], cs[0]], FACTORS, cfg)
    with pytest.raises(DataError, match="no cross-section"):
        fit_factor_history([], FACTORS, cfg)


# --- factor covariance --------------------------------------------------------------------------


def test_newey_west_known_example() -> None:
    f = np.array([[1.0, 2.0], [2.0, 0.0], [-1.0, 1.0], [0.0, -1.0]])
    frame = pd.DataFrame(f, columns=["a", "b"])
    g0 = np.array([[1.5, 0.25], [0.25, 1.5]])
    g1 = np.array([[0.0, 4.0 / 3.0], [1.0, -1.0 / 3.0]])
    want = g0 + 0.5 * (g1 + g1.T)  # Bartlett weight 1 - 1/2 for L = 1
    np.testing.assert_allclose(
        newey_west_covariance(frame, half_life=1e12, lags=1), want, rtol=1e-9
    )
    np.testing.assert_allclose(newey_west_covariance(frame, half_life=1e12, lags=0), g0, rtol=1e-9)


def test_half_life_weights() -> None:
    frame = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    got = newey_west_covariance(frame, half_life=1, lags=0)
    assert got[0, 0] == pytest.approx((0.25 * 1 + 0.5 * 4 + 1.0 * 9) / 1.75, rel=1e-12)


def test_nan_pairs_use_the_common_observations() -> None:
    frame = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [np.nan, 1.0, 2.0]})
    got = newey_west_covariance(frame, half_life=1, lags=0)
    # b is missing on the oldest row: its weights are 1/2 and 1
    assert got[1, 1] == pytest.approx((0.5 * 1 + 1.0 * 4) / 1.5, rel=1e-12)
    assert got[0, 1] == pytest.approx((0.5 * 2 * 1 + 1.0 * 3 * 2) / 1.5, rel=1e-12)
    assert got[0, 0] == pytest.approx((0.25 * 1 + 0.5 * 4 + 1.0 * 9) / 1.75, rel=1e-12)


def test_newey_west_raises_long_run_variance_for_ar1() -> None:
    rng = np.random.default_rng(0)
    e, x = rng.normal(size=40000), np.zeros(40000)
    for t in range(1, 40000):
        x[t] = 0.5 * x[t - 1] + e[t]
    frame = pd.DataFrame({"a": x})
    nw0 = newey_west_covariance(frame, half_life=1e12, lags=0)[0, 0]
    nw5 = newey_west_covariance(frame, half_life=1e12, lags=5)[0, 0]
    assert abs(nw0 - 4 / 3) < 0.1 and abs(nw5 - 3.125) < 0.25  # long-run variance is 4
    assert abs(nw5 - 4.0) < abs(nw0 - 4.0)


def test_factor_covariance_repairs_and_conditions() -> None:
    f = np.array([[1.0, 2.0], [2.0, 0.0], [-1.0, 1.0], [0.0, -1.0]])
    frame = pd.DataFrame(np.tile(f, (20, 1)), columns=["a", "b"])  # 80 rows
    res = factor_covariance(frame, half_life=1e12, lags=1, min_history=63, max_condition=1e5)
    eig = np.linalg.eigvalsh(res.cov)
    assert eig.min() > 0 and condition_number(res.cov) <= 1e5 * (1 + 1e-9)
    assert res.n_days == 80 and res.factors == ("a", "b")
    short = frame.iloc[:62]
    with pytest.raises(DataError, match="63"):
        factor_covariance(short, half_life=90, lags=2, min_history=63, max_condition=1e5)


def test_factor_covariance_indefinite_input_is_repaired() -> None:
    res = factor_covariance(
        _inconsistent_pairs(), half_life=1e12, lags=0, min_history=63, max_condition=1e5
    )
    assert np.linalg.eigvalsh(res.cov).min() > 0 and np.allclose(res.cov, res.cov.T)


def test_factor_covariance_needs_data_for_every_pair() -> None:
    frame = pd.DataFrame({"a": np.arange(100.0), "b": [np.nan] * 100})
    with pytest.raises(DataError, match="b"):
        factor_covariance(frame, half_life=30, lags=0, min_history=63, max_condition=1e5)


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(0, 10_000), nan_rate=st.sampled_from([0.0, 0.05, 0.3]), lags=st.integers(0, 4)
)
def test_factor_covariance_is_always_positive_definite(
    seed: int, nan_rate: float, lags: int
) -> None:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(120, 6)) * rng.uniform(0.001, 0.02, 6)
    x[rng.random(x.shape) < nan_rate] = np.nan
    res = factor_covariance(
        pd.DataFrame(x), half_life=40, lags=lags, min_history=63, max_condition=1e5
    )
    assert np.allclose(res.cov, res.cov.T, atol=1e-18)
    assert np.linalg.eigvalsh(res.cov).min() > 0
    assert condition_number(res.cov) <= 1e5 * (1 + 1e-6)


# --- the model ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fitted():
    w = make_world(n=500, t=750, seed=31)
    hist = fit_factor_history(cross_sections(w), FACTORS, CFG)
    return w, hist


def _model(w, hist, day=None) -> RiskModel:
    day = w.dates[-1].date() if day is None else day
    return build_risk_model(hist, w.styles, w.sectors, w.caps, day, SMALL)


def test_cov_equals_xfxt_plus_d(fitted) -> None:
    w, hist = fitted
    m = _model(w, hist)
    x, f, d = m.exposures.to_numpy(), m.factor_cov.to_numpy(), m.specific_var.to_numpy()
    cov = m.cov()
    np.testing.assert_allclose(cov.to_numpy(), x @ f @ x.T + np.diag(d), rtol=1e-12, atol=1e-18)
    assert list(cov.index) == list(m.symbols) == list(cov.columns)
    assert m.as_of == w.dates[-1].date() and m.n_days == 750
    assert m.factors == FACTORS.names


def test_cov_is_positive_definite(fitted) -> None:
    w, hist = fitted
    m = _model(w, hist)
    assert np.linalg.eigvalsh(m.cov().to_numpy()).min() > 0
    np.testing.assert_allclose(m.vol().to_numpy(), np.sqrt(np.diag(m.cov().to_numpy())))
    assert (m.specific_var > 0).all()


def test_cov_subset_and_unknown_symbol(fitted) -> None:
    w, hist = fitted
    m = _model(w, hist)
    pick = [w.symbols[5], w.symbols[1]]
    sub = m.cov(pick)
    full = m.cov()
    pd.testing.assert_frame_equal(sub, full.loc[pick, pick])
    with pytest.raises(DataError, match="NOPE"):
        m.cov(["NOPE"])


def test_risk_model_is_read_only(fitted) -> None:
    w, hist = fitted
    m = _model(w, hist)
    before = m.cov().copy()
    exposures, factor_cov, specific_var = m.exposures, m.factor_cov, m.specific_var
    exposures.iloc[:, :] = 0.0
    factor_cov.iloc[:, :] = 0.0
    specific_var.iloc[:] = 0.0
    pd.testing.assert_frame_equal(m.cov(), before)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.as_of = w.dates[0].date()  # type: ignore[misc]
    with pytest.raises(ValueError, match="read-only"):
        m.x[0, 0] = 5.0


def test_build_reports_dropped_names(fitted) -> None:
    w, hist = fitted
    sectors = w.sectors.copy()
    sectors[w.symbols[0]] = None
    caps = w.caps.copy()
    caps[w.symbols[1]] = np.nan
    styles = w.styles.copy()
    styles.loc[w.symbols[2], ["value", "growth", "quality"]] = np.nan
    extra = pd.DataFrame(0.0, index=["NEWCO"], columns=styles.columns)
    m = build_risk_model(
        hist,
        pd.concat([styles, extra]),
        pd.concat([sectors, pd.Series({"NEWCO": SECTORS[0]})]),
        pd.concat([caps, pd.Series({"NEWCO": 1e10})]),
        w.dates[-1].date(),
        SMALL,
    )
    assert m.dropped == {
        w.symbols[0]: "sector",
        w.symbols[1]: "cap",
        w.symbols[2]: "exposures",
        "NEWCO": "history",
    }
    assert len(m.symbols) == 497


def test_model_is_point_in_time(fitted) -> None:
    w, hist = fitted
    day = w.dates[600].date()
    a = _model(w, hist, day)
    later = hist.factor_returns.index > pd.Timestamp(day)
    fr, rs = hist.factor_returns.copy(), hist.residuals.copy()
    fr.loc[later] = 9.0
    rs.loc[later] = 9.0
    tampered = dataclasses.replace(hist, factor_returns=fr, residuals=rs)
    b = _model(w, tampered, day)
    assert np.array_equal(a.factor_cov.to_numpy(), b.factor_cov.to_numpy())
    assert np.array_equal(a.specific_var.to_numpy(), b.specific_var.to_numpy())
    assert a.n_days == 601


def test_build_needs_history_before_as_of(fitted) -> None:
    w, hist = fitted
    with pytest.raises(DataError, match="63"):
        build_risk_model(hist, w.styles, w.sectors, w.caps, w.dates[30].date(), SMALL)


@pytest.mark.timing
def test_build_time_500_names_under_2s(fitted) -> None:
    w, hist = fitted
    _model(w, hist)  # warm-up (imports, BLAS threads)
    start = time.perf_counter()
    m = _model(w, hist)
    elapsed = time.perf_counter() - start
    assert len(m.symbols) == 500
    assert elapsed < 2.0, f"build took {elapsed:.2f}s"


def test_factor_cov_recovers_the_truth(fitted) -> None:
    w, hist = fitted
    m = _model(w, hist)
    est, true = m.factor_cov.to_numpy(), w.true_factor_cov.to_numpy()
    d_est, d_true = np.sqrt(np.diag(est)), np.sqrt(np.diag(true))
    assert float(np.median(np.abs(d_est / d_true - 1))) < 0.2
    assert np.corrcoef(d_est, d_true)[0, 1] > 0.9


def test_calibration_bias_statistic_near_one(fitted) -> None:
    """Predicted vs realised vol on 250 out-of-sample sessions: measured (seeds 31-33, 20
    portfolios each) long/short 1.01-1.03, long-only 0.97-1.02, single names 0.96-1.03,
    cap-weighted market 0.95-1.05; the bounds below are the plan's [0.9, 1.1]."""
    from atlas.quant.risk_model.analytics import bias_statistic

    w, hist = fitted
    day_idx = 500
    m = _model(w, hist, w.dates[day_idx].date())
    rng = np.random.default_rng(5)
    cov = m.cov().to_numpy()
    out = {"long_short": [], "single": []}
    for kind in out:
        for _ in range(20):
            if kind == "long_short":
                port = rng.normal(size=500)
                port = port / np.abs(port).sum()
            else:
                port = np.zeros(500)
                port[rng.integers(500)] = 1.0
            p = pd.Series(port, index=m.symbols)
            vol = float(np.sqrt(port @ cov @ port))
            realised = w.returns.iloc[day_idx + 1 :] @ p
            out[kind].append(bias_statistic(realised, pd.Series(vol, index=realised.index)))
    for kind, values in out.items():
        assert 0.9 < float(np.mean(values)) < 1.1, (kind, np.mean(values))


def test_history_uses_sqrt_cap_weights() -> None:
    from atlas.quant.risk_model.regression import constrained_wls, sector_constraint

    w = make_world(n=200, t=3, seed=61)
    cs = cross_sections(w)
    hist = fit_factor_history(cs, FACTORS, SMALL.quant.risk_model.factor_model)
    prep = prepare_exposures(cs[1].raw_styles, cs[1].sectors, cs[1].caps, FACTORS, CFG)
    con = sector_constraint(prep.design, prep.caps, FACTORS.sectors)
    direct = constrained_wls(
        cs[1].returns.loc[prep.design.index], prep.design, np.sqrt(prep.caps), con
    )
    np.testing.assert_allclose(
        hist.factor_returns.iloc[1].to_numpy(), direct.factor_returns.to_numpy(), atol=1e-14
    )


def test_newey_west_is_exactly_symmetric() -> None:
    rng = np.random.default_rng(3)
    x = rng.normal(size=(150, 5))
    x[rng.random(x.shape) < 0.1] = np.nan
    c = newey_west_covariance(pd.DataFrame(x), half_life=40, lags=3)
    assert np.array_equal(c, c.T)


def _inconsistent_pairs() -> pd.DataFrame:
    """Pairwise-complete correlations a~b = 1, b~c = 1, a~c = -1: not a valid covariance."""
    z = np.random.default_rng(7).normal(size=100) * 0.01
    nan = np.full(100, np.nan)
    a = np.concatenate([z, nan, z])
    b = np.concatenate([z, z, nan])
    c = np.concatenate([nan, z, -z])
    return pd.DataFrame({"a": a, "b": b, "c": c})


def test_repairs_are_recorded() -> None:
    frame = _inconsistent_pairs()
    raw = newey_west_covariance(frame, half_life=1e12, lags=0)
    assert np.linalg.eigvalsh(raw).min() < -1e-6 * np.abs(raw).max()  # indefinite before repair
    res = factor_covariance(frame, half_life=1e12, lags=0, min_history=63, max_condition=1e5)
    assert "nearest_psd" in res.adjustments
    assert np.linalg.eigvalsh(res.cov).min() > 0
    base = np.random.default_rng(1).normal(size=(300, 3))
    dup = pd.DataFrame({"a": base[:, 0], "b": base[:, 0], "c": base[:, 1]})  # singular
    res = factor_covariance(dup, half_life=1e12, lags=0, min_history=63, max_condition=1e5)
    assert "condition_floor" in res.adjustments and np.linalg.eigvalsh(res.cov).min() > 0
    clean = factor_covariance(
        pd.DataFrame(base), half_life=1e12, lags=0, min_history=63, max_condition=1e5
    )
    assert clean.adjustments == ()


# --- audit follow-up --------------------------------------------------------------------------


def test_skip_also_covers_a_rank_deficient_day() -> None:
    w = make_world(n=120, t=4, seed=71)
    cs = cross_sections(w)
    styles = cs[2].raw_styles.copy()
    styles["value"] = styles["quality"]  # collinear styles: the regression cannot be fitted
    bad = dataclasses.replace(cs[2], raw_styles=styles)
    cfg = SMALL.quant.risk_model.factor_model
    with pytest.raises(DataError, match="rank"):
        fit_factor_history([*cs[:2], bad, cs[3]], FACTORS, cfg)
    hist = fit_factor_history([*cs[:2], bad, cs[3]], FACTORS, cfg, on_short="skip")
    assert hist.skipped == (cs[2].day,) and len(hist.factor_returns) == 3


def test_factor_covariance_needs_min_history_per_factor() -> None:
    rng = np.random.default_rng(2)
    frame = pd.DataFrame(rng.normal(size=(200, 3)) * 0.01, columns=["a", "b", "thin"])
    frame.loc[frame.index[:150], "thin"] = np.nan  # 50 observations < 63
    with pytest.raises(DataError, match="thin"):
        factor_covariance(frame, half_life=90, lags=2, min_history=63, max_condition=1e5)
    frame.loc[frame.index[130:150], "thin"] = 0.001  # 70 observations
    factor_covariance(frame, half_life=90, lags=2, min_history=63, max_condition=1e5)


def test_stale_history_is_rejected(fitted) -> None:
    w, hist = fitted
    early = hist.up_to(w.dates[600].date())
    stale_day = w.dates[606].date()  # 6 sessions after the history ends; the limit is 5
    with pytest.raises(DataError, match="stale"):
        build_risk_model(early, w.styles, w.sectors, w.caps, stale_day, SMALL)
    ok = build_risk_model(early, w.styles, w.sectors, w.caps, w.dates[605].date(), SMALL)
    assert ok.n_days == 601


def test_risk_model_validates_values() -> None:
    from datetime import date as _date

    good = dict(
        as_of=_date(2020, 1, 2),
        symbols=("A",),
        factors=("f",),
        x=np.ones((1, 1)),
        f=np.eye(1),
        d=np.ones(1),
    )
    RiskModel(**good)
    with pytest.raises(ValueError, match="finite"):
        RiskModel(**{**good, "x": np.full((1, 1), np.nan)})
    with pytest.raises(ValueError, match="finite"):
        RiskModel(**{**good, "f": np.full((1, 1), np.inf)})
    with pytest.raises(ValueError, match="symmetric"):
        RiskModel(
            **{
                **good,
                "factors": ("f", "g"),
                "x": np.ones((1, 2)),
                "f": np.array([[1.0, 0.5], [0.0, 1.0]]),
            }
        )
