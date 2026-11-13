"""Stage 22 Task 2: the cross-sectional multi-factor model (planted alpha, PIT, contract)."""

import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from alpha_world import (
    CAL,
    EQUITY_AS_OF,
    build_equity_world,
    equity_config,
    planted_components,
    planted_returns,
)
from factor_world import FactorWorld

from atlas.core.errors import ConfigError, LookAheadError
from atlas.core.pit import AsOf, bar_available_at, tag_label
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.alpha import (
    AlphaModel,
    MultiFactorModel,
    calibration_slope,
    check_alpha_frame,
    clean_component,
    combine,
    component_frame,
    grinold_alpha,
    style_scores,
)
from atlas.quant.factors import FactorPanel, factor_panel, style_composites
from atlas.quant.features import compute_features

# --- pure pieces --------------------------------------------------------------------------


def test_clean_component_is_sector_neutral_zscore() -> None:
    rng = np.random.default_rng(3)
    idx = pd.Index([f"S{i}" for i in range(60)], name="symbol")
    sectors = pd.Series([("A", "B", "C", None)[i % 4] for i in range(60)], index=idx, dtype=object)
    raw = pd.Series(rng.standard_normal(60) + np.where(sectors == "A", 5.0, 0.0), index=idx)
    raw.iloc[0] = 1e6  # an outlier is winsorised
    raw.iloc[5] = np.nan
    out = clean_component(raw, sectors, lower=0.01, upper=0.99, min_names=20)
    known = sectors.notna() & out.notna()
    sums = out[known].groupby(sectors[known]).sum()
    assert np.allclose(sums.to_numpy(), 0.0, atol=1e-9)
    assert out[known].std(ddof=0) == pytest.approx(1.0)
    assert math.isnan(out.iloc[5])
    assert out.abs().max() < 10


def test_clean_component_too_few_names_is_all_nan() -> None:
    idx = pd.Index([f"S{i}" for i in range(10)], name="symbol")
    out = clean_component(
        pd.Series(np.arange(10.0), index=idx),
        pd.Series("A", index=idx, dtype=object),
        lower=0.01,
        upper=0.99,
        min_names=20,
    )
    assert out.isna().all()


def test_style_scores_ignore_stage16_fills() -> None:
    """A value Stage 16 filled from its sector median is not a measurement: a name whose
    value factors were all filled has no value style; partly filled -> the known ones only."""
    names = ["A", "B", "C"]
    exposures = pd.DataFrame(
        {
            "earnings_yield": [1.0, 0.5, -1.5],
            "book_yield": [0.8, 0.2, -1.0],
            "roe": [0.3, -0.3, 0.0],
        },
        index=pd.Index(names, name="symbol"),
    )
    filled = pd.DataFrame(False, index=exposures.index, columns=exposures.columns)
    filled.loc["A", ["earnings_yield", "book_yield"]] = True  # A: no value data at all
    filled.loc["B", "book_yield"] = True  # B: earnings yield only
    panel = FactorPanel(
        as_of=AsOf.for_session(date(2024, 3, 1)),
        raw=exposures,
        exposures=exposures,
        filled=filled,
        market=pd.DataFrame(index=exposures.index),
        sectors=pd.Series([None] * 3, index=exposures.index, dtype=object),
        config_hash="test",
    )
    out = style_scores(panel)
    assert math.isnan(out.loc["A", "value"])
    assert not math.isnan(out.loc["A", "quality"])  # roe was measured
    unmasked = style_composites(exposures)
    assert not math.isnan(unmasked.loc["A", "value"])
    masked = exposures.mask(filled)
    expected = style_composites(masked)
    pd.testing.assert_frame_equal(out, expected)


def test_combine_recovers_planted_composite() -> None:
    p = planted_components()
    res = combine(p.components, p.sectors, p.weights, min_coverage=0.6, min_names=20)
    assert np.corrcoef(res.z, p.z)[0, 1] > 0.999
    assert np.allclose(res.coverage, 1.0)
    sums = res.z.groupby(p.sectors).sum()
    assert np.allclose(sums.to_numpy(), 0.0, atol=1e-9)
    assert res.z.std(ddof=0) == pytest.approx(1.0)


def test_planted_alpha_ic_and_calibration() -> None:
    """Planted IC rho at 21 sessions: the model's composite IC ~ rho and its Grinold
    expected returns are calibrated (realised on predicted slope ~1)."""
    rng = np.random.default_rng(7)
    rho, h, periods = 0.06, 21, 252
    ics, preds, reals = [], [], []
    for seed in range(40):
        p = planted_components(seed=100 + seed)
        res = combine(p.components, p.sectors, p.weights, min_coverage=0.6, min_names=20)
        r = planted_returns(p, rho=rho, horizon=h, periods=periods, rng=rng)
        ics.append(np.corrcoef(res.z.rank(), r.rank())[0, 1])
        preds.append(grinold_alpha(res.z, rho, p.sigma, horizon=h, periods=periods))
        reals.append(r * periods / h)
    assert np.mean(ics) == pytest.approx(rho, abs=0.02)
    fit = calibration_slope(pd.concat(preds), pd.concat(reals))
    assert fit.slope == pytest.approx(1.0, abs=0.25)


def test_combine_renormalises_and_drops_low_coverage() -> None:
    p = planted_components(n_names=100)
    comps = p.components.copy()
    comps.loc[comps.index[:10], "momentum"] = np.nan  # coverage 0.7 with momentum 0.3
    comps.loc[comps.index[10:20], ["momentum", "value", "quality"]] = np.nan  # coverage 0.3
    res = combine(comps, p.sectors, p.weights, min_coverage=0.6, min_names=20)
    assert res.coverage.iloc[:10].tolist() == pytest.approx([0.7] * 10)
    assert res.z.iloc[:10].notna().all()
    assert res.z.iloc[10:20].isna().all()
    assert res.z.iloc[20:].notna().all()


def test_combine_leaves_out_zero_weight_components() -> None:
    """A component with weight 0 changes neither the score nor the coverage: a name missing
    only that component keeps full coverage and the same z as without the column."""
    p = planted_components(n_names=100)
    weights = {**p.weights, "momentum": 0.0}
    comps = p.components.copy()
    comps.loc[comps.index[:10], "momentum"] = np.nan
    res = combine(comps, p.sectors, weights, min_coverage=0.6, min_names=20)
    without = {k: w for k, w in weights.items() if k != "momentum"}
    rest = comps.drop(columns="momentum")
    ref = combine(rest, p.sectors, without, min_coverage=0.6, min_names=20)
    assert np.allclose(res.coverage, 1.0)
    pd.testing.assert_series_equal(res.z, ref.z)


def test_combine_too_few_names_is_empty() -> None:
    p = planted_components(n_names=12)
    res = combine(p.components, p.sectors, p.weights, min_coverage=0.6, min_names=20)
    assert res.z.isna().all()


def test_combine_rejects_labels() -> None:
    p = planted_components(n_names=50)
    with pytest.raises(LookAheadError):
        combine(tag_label(p.components), p.sectors, p.weights, min_coverage=0.6, min_names=20)


# --- the model on a lake world ------------------------------------------------------------


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> FactorWorld:
    return build_equity_world(Lake(tmp_path_factory.mktemp("alpha_eq")))


def _view(world: FactorWorld, **mf: object) -> DataView:
    return DataView(world.lake, EQUITY_AS_OF, equity_config(**mf))


def test_model_is_an_alpha_model_and_meets_contract(world: FactorWorld) -> None:
    model = MultiFactorModel()
    assert isinstance(model, AlphaModel)
    assert model.name == "multifactor"
    out = model.predict(_view(world))
    check_alpha_frame(out)
    assert len(out) >= 10
    assert out.index.is_monotonic_increasing


def test_component_frame_equals_pure_pipeline(world: FactorWorld) -> None:
    view = _view(world)
    cfg = view.config.quant.alpha.multifactor
    panel = component_frame(view)
    members = view.universe("sp500")
    feats = compute_features(
        view, members, ["momentum_12_1", "vol_63d", "ret_21d", "idio_vol_252d"]
    )
    fp = factor_panel(view, members)
    styles = style_composites(fp.exposures.mask(fp.filled)).reindex(members)
    fc = view.config.quant.factors
    expected = {
        "momentum": feats["momentum_12_1"].iloc[-1],
        "value": styles["value"],
        "quality": styles["quality"],
        "low_vol": -feats["vol_63d"].iloc[-1],
        "reversal": -feats["ret_21d"].iloc[-1],
    }
    for name, raw in expected.items():
        want = clean_component(
            raw.reindex(members),
            panel.sectors,
            lower=fc.winsor_lower,
            upper=fc.winsor_upper,
            min_names=cfg.min_names,
        )
        pd.testing.assert_series_equal(
            panel.values[name], want.reindex(panel.values.index), check_names=False
        )
    pd.testing.assert_series_equal(
        panel.resid_vol, feats["idio_vol_252d"].iloc[-1].reindex(members), check_names=False
    )


def test_predict_equals_grinold_on_components(world: FactorWorld) -> None:
    view = _view(world)
    cfg = view.config.quant.alpha.multifactor
    panel = component_frame(view)
    res = combine(
        panel.values,
        panel.sectors,
        {k: c.weight for k, c in cfg.components.items()},
        min_coverage=cfg.min_coverage,
        min_names=cfg.min_names,
    )
    exp = grinold_alpha(res.z, cfg.ic_prior, panel.resid_vol, horizon=21, periods=252)
    out = MultiFactorModel().predict(view)
    pd.testing.assert_series_equal(out["score"], res.z.reindex(out.index), check_names=False)
    pd.testing.assert_series_equal(out["exp_return_ann"], exp.reindex(out.index), check_names=False)
    want_conf = (res.coverage * cfg.prior_confidence).reindex(out.index)
    pd.testing.assert_series_equal(out["confidence"], want_conf, check_names=False)


def test_resid_vol_override_and_missing_names(world: FactorWorld) -> None:
    view = _view(world)
    base = MultiFactorModel().predict(view)
    override = pd.Series(0.2, index=base.index[:5])
    out = MultiFactorModel(resid_vol=override).predict(view)
    assert list(out.index) == list(base.index[:5])
    ratio = out["exp_return_ann"] / out["score"]
    assert np.allclose(ratio, 0.03 * 0.2 * math.sqrt(12))


def test_predict_is_point_in_time(tmp_path_factory: pytest.TempPathFactory) -> None:
    lake = Lake(tmp_path_factory.mktemp("alpha_pit"))
    world = build_equity_world(lake)
    before = MultiFactorModel().predict(_view(world))
    # bars after the view's last known session (not known at the as-of) change nothing
    bars = lake.read_table("prices_daily")
    last = bars["date"].max()
    later = bars[bars["date"] == last].copy()
    later["date"] = CAL.next_session(last)
    later["close"] = later["close"] * 3.0
    later["adj_close"] = later["close"]
    later["available_at"] = pd.Timestamp(bar_available_at(CAL.next_session(last), CAL))
    lake.write_table("prices_daily", later, mode="upsert")
    after = MultiFactorModel().predict(_view(world))
    pd.testing.assert_frame_equal(before, after)


def test_ic_weighting_uses_only_known_ics(world: FactorWorld) -> None:
    view = _view(world, weighting="ic", ic_min_obs=3)
    day = view.day
    # 6 monthly ICs: known ones favour value only; one not-yet-known IC favours momentum
    dates = [CAL.offset(day, -21 * k - 22) for k in range(6, 0, -1)]
    hist = pd.DataFrame(
        {"momentum": -0.05, "value": 0.05, "quality": 0.0, "low_vol": -0.01, "reversal": 0.0},
        index=pd.DatetimeIndex(dates, name="date"),
    )
    unknown_day = CAL.offset(day, -10)  # its 21-session label is not closed at `day`
    hist.loc[pd.Timestamp(unknown_day)] = [5.0, -5.0, 0.0, 0.0, 0.0]
    out = MultiFactorModel(ic_history=hist).predict(view)
    panel = component_frame(view)
    value_only = combine(
        panel.values,
        panel.sectors,
        {"value": 1.0, "momentum": 0.0, "quality": 0.0, "low_vol": 0.0, "reversal": 0.0},
        min_coverage=0.6,
        min_names=5,
    )
    common = out.index
    assert np.allclose(out["score"], value_only.z.reindex(common))


def test_ic_history_sets_ic_and_reliability(world: FactorWorld) -> None:
    view = _view(world, ic_min_obs=3)
    day = view.day
    dates = [CAL.offset(day, -21 * k - 22) for k in range(8, 0, -1)]
    ics = [0.05, 0.07, 0.03, 0.06, 0.04, 0.05, 0.08, 0.02]
    hist = pd.DataFrame({"composite": ics}, index=pd.DatetimeIndex(dates, name="date"))
    out = MultiFactorModel(ic_history=hist).predict(view)
    base = MultiFactorModel().predict(view)
    ratio = (out["exp_return_ann"] / base["exp_return_ann"]).dropna()
    assert np.allclose(ratio, np.mean(ics) / 0.03)
    assert (out["confidence"] >= base["confidence"]).all()  # t-stat >> t_full: reliability 1
    negative = MultiFactorModel(ic_history=-hist).predict(view)
    assert (negative["exp_return_ann"] == 0).all()  # floored at 0: no flipped book
    assert (negative["confidence"] == 0).all()
    with pytest.raises(LookAheadError):
        MultiFactorModel(ic_history=tag_label(hist)).predict(view)


def test_small_universe_gives_empty_frame(world: FactorWorld) -> None:
    out = MultiFactorModel().predict(_view(world, min_names=50))
    assert out.empty
    check_alpha_frame(out)


def test_unknown_feature_component_raises(world: FactorWorld) -> None:
    view = DataView(
        world.lake,
        EQUITY_AS_OF,
        equity_config(components={"x": {"kind": "feature", "source": "nope", "weight": 1.0}}),
    )
    with pytest.raises(ValueError, match="nope"):
        MultiFactorModel().predict(view)


def test_explicit_config_overrides_view(world: FactorWorld) -> None:
    view = _view(world)
    cfg = view.config.quant.alpha.multifactor.model_copy(update={"ic_prior": 0.06})
    a = MultiFactorModel(config=cfg).predict(view)
    b = MultiFactorModel().predict(view)
    assert np.allclose(a["exp_return_ann"], 2 * b["exp_return_ann"])
    assert MultiFactorModel(config=cfg).horizon == 21


def test_horizon_must_be_the_one_predict_uses(world: FactorWorld) -> None:  # Phase 2 checkpoint
    view = _view(world, horizon=42)
    with pytest.raises(ConfigError, match=r"horizon.*21.*42"):
        MultiFactorModel().predict(view)  # the model would say 21 and forecast for 42
    model = MultiFactorModel(config=view.config.quant.alpha.multifactor)
    assert model.horizon == 42
    check_alpha_frame(model.predict(view))


# --- Phase 2 checkpoint, Tier 3 item 4: style scores from the factor cache ----------------------


def test_predict_from_the_factor_cache_equals_a_fresh_prediction(
    world: FactorWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import atlas.quant.alpha.multifactor as multifactor_module
    from atlas.core.storage import DEFAULT_REGISTRY
    from atlas.quant.factors import FactorStore, factor_panel

    view = _view(world)
    fresh = MultiFactorModel().predict(view)
    store = FactorStore(Lake(tmp_path / "cache", registry=DEFAULT_REGISTRY.copy()), view.config)
    store.write(factor_panel(view))
    monkeypatch.setattr(
        multifactor_module, "factor_panel", lambda *a, **k: pytest.fail("the cache was not used")
    )
    cached = MultiFactorModel(factor_store=store).predict(view)
    pd.testing.assert_frame_equal(cached, fresh)


def test_predict_without_a_panel_of_that_day_computes_it(
    world: FactorWorld, tmp_path: Path
) -> None:
    from atlas.core.storage import DEFAULT_REGISTRY
    from atlas.quant.factors import FactorStore, factor_panel

    view = _view(world)
    earlier = DataView(world.lake, CAL.offset(EQUITY_AS_OF, -1), view.config)
    store = FactorStore(Lake(tmp_path / "cache", registry=DEFAULT_REGISTRY.copy()), view.config)
    store.write(factor_panel(earlier))  # yesterday's panel is not today's
    pd.testing.assert_frame_equal(
        MultiFactorModel(factor_store=store).predict(view), MultiFactorModel().predict(view)
    )
