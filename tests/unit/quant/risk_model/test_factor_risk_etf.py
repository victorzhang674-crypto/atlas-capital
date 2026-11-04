"""ETF exposures by time-series regression on the factor returns (Stage 19 Task 7)."""

import numpy as np
import pandas as pd
import pytest
from factor_risk_world import SECTORS, STYLES, FactorWorld, cross_sections, make_world

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.quant.risk_model.etf_mapping import EtfExposures, etf_exposures, extend_model
from atlas.quant.risk_model.factor_model import (
    FactorSet,
    RiskModel,
    build_risk_model,
    fit_factor_history,
)
from atlas.quant.risk_model.regression import sector_constraint

CFG = AtlasConfig()


@pytest.fixture(scope="module")
def world() -> FactorWorld:
    return make_world(n=300, t=400, seed=41)


def _null_free(world: FactorWorld, beta: np.ndarray) -> np.ndarray:
    """`beta` minus its component along the sector-constraint direction: the factor returns
    satisfy `sum_s share_s f_s = 0`, so only this part of a beta is identified."""
    c = sector_constraint(world.exposures, world.caps, SECTORS).to_numpy()
    return beta - (beta @ c) / (c @ c) * c


def _planted(world: FactorWorld, beta: np.ndarray, noise: float, seed: int) -> pd.Series:
    rng = np.random.default_rng(seed)
    f = world.factor_returns.to_numpy()
    return pd.Series(f @ beta + rng.normal(scale=noise, size=len(f)), index=world.dates)


def test_etf_betas_recover_planted_exposures(world: FactorWorld) -> None:
    rng = np.random.default_rng(1)
    k = len(world.factor_names)
    betas = {f"E{i}": _null_free(world, rng.normal(scale=0.5, size=k)) for i in range(5)}
    etf = pd.DataFrame(
        {name: _planted(world, b, 0.002, i) for i, (name, b) in enumerate(betas.items())}
    )
    out = etf_exposures(etf, world.factor_returns, CFG)
    assert isinstance(out, EtfExposures)
    for name, b in betas.items():
        np.testing.assert_allclose(out.betas.loc[name].to_numpy(), b, atol=0.06)
        assert out.r2[name] > 0.5
    assert list(out.betas.columns) == world.factor_names
    assert out.dropped == {} and (out.n_obs == 252).all()


def test_etf_specific_variance(world: FactorWorld) -> None:
    beta = _null_free(world, np.random.default_rng(2).normal(scale=0.5, size=20))
    etf = pd.DataFrame({"E": _planted(world, beta, 0.003, 7)})
    out = etf_exposures(etf, world.factor_returns, CFG)
    assert np.sqrt(out.specific_var["E"]) == pytest.approx(0.003, rel=0.1)


def test_etf_specific_variance_uses_degrees_of_freedom(world: FactorWorld) -> None:
    """Residual variance = SSE / (n - K - 1), checked against an independent least-squares fit
    (ridge ~ 0)."""
    cfg = AtlasConfig.model_validate(
        {"quant": {"risk_model": {"factor_model": {"etf": {"ridge": 1e-10}}}}}
    )
    beta = _null_free(world, np.random.default_rng(9).normal(scale=0.5, size=20))
    etf = pd.DataFrame({"E": _planted(world, beta, 0.004, 11)})
    out = etf_exposures(etf, world.factor_returns, cfg)
    f = world.factor_returns.tail(252).to_numpy()
    y = etf["E"].tail(252).to_numpy()
    fc, yc = f - f.mean(axis=0), y - y.mean()
    coef, *_ = np.linalg.lstsq(fc, yc, rcond=None)
    sse = float(np.sum((yc - fc @ coef) ** 2))
    assert out.specific_var["E"] == pytest.approx(sse / (252 - 20 - 1), rel=1e-6)


def test_etf_specific_variance_is_floored(world: FactorWorld) -> None:
    beta = _null_free(world, np.random.default_rng(3).normal(scale=0.5, size=20))
    etf = pd.DataFrame({"E": _planted(world, beta, 0.0, 7)})  # an exact factor mix
    out = etf_exposures(etf, world.factor_returns, CFG)
    assert out.specific_var["E"] == pytest.approx(
        CFG.quant.risk_model.factor_model.specific_floor_vol**2
    )


def test_etf_uses_only_the_window(world: FactorWorld) -> None:
    beta = _null_free(world, np.random.default_rng(4).normal(scale=0.5, size=20))
    etf = pd.DataFrame({"E": _planted(world, beta, 0.002, 8)})
    a = etf_exposures(etf, world.factor_returns, CFG)
    old = etf.copy()
    old.iloc[:140] = 5.0  # before the last 252 of 400 sessions
    fr = world.factor_returns.copy()
    fr.iloc[:140] = 5.0
    b = etf_exposures(old, fr, CFG)
    pd.testing.assert_frame_equal(a.betas, b.betas)
    assert a.specific_var["E"] == b.specific_var["E"]


def test_short_etf_dropped_and_missing_returns_skipped(world: FactorWorld) -> None:
    beta = _null_free(world, np.random.default_rng(5).normal(scale=0.5, size=20))
    good = _planted(world, beta, 0.002, 9)
    young = good.copy()
    young.iloc[:-60] = np.nan  # 60 observations < min_obs (126)
    gappy = good.copy()
    gappy.iloc[-200:-150] = np.nan  # 202 observations in the window: kept
    out = etf_exposures(
        pd.DataFrame({"OK": good, "YOUNG": young, "GAPPY": gappy}), world.factor_returns, CFG
    )
    assert out.dropped == {"YOUNG": "history"}
    assert list(out.betas.index) == ["OK", "GAPPY"]
    assert out.n_obs["GAPPY"] == 202


def test_etf_validation(world: FactorWorld) -> None:
    etf = pd.DataFrame({"E": _planted(world, np.zeros(20), 0.01, 1)})
    with pytest.raises(ValueError, match="finite"):
        etf_exposures(etf.replace(etf.iloc[0, 0], np.inf), world.factor_returns, CFG)
    with pytest.raises(DataError, match="every ETF"):
        etf_exposures(etf.iloc[-50:], world.factor_returns, CFG)
    with pytest.raises(ValueError, match="dates"):
        etf_exposures(etf.set_axis(etf.index + pd.Timedelta(days=3000)), world.factor_returns, CFG)


def test_sector_etf_loads_on_its_sector(world: FactorWorld) -> None:
    target = SECTORS[4]
    members = world.sectors.index[world.sectors == target]
    w = world.caps[members] / world.caps[members].sum()
    etf = pd.DataFrame({"XLF": world.returns[members] @ w})
    out = etf_exposures(etf, world.factor_returns, CFG)
    b = out.betas.loc["XLF"]
    assert b[list(SECTORS)].idxmax() == target
    assert b["market"] == pytest.approx(1.0, abs=0.08)
    assert out.r2["XLF"] > 0.9


@pytest.fixture(scope="module")
def model(world: FactorWorld) -> RiskModel:
    fm = CFG.quant.risk_model.factor_model
    hist = fit_factor_history(
        cross_sections(world),
        FactorSet.from_config(fm),
        fm.model_copy(update={"min_cross_section": 50}),
    )
    cfg = AtlasConfig(quant={"risk_model": {"factor_model": {"min_cross_section": 50}}})
    return build_risk_model(
        hist, world.styles, world.sectors, world.caps, world.dates[-1].date(), cfg
    )


def test_extend_model_adds_rows(world: FactorWorld, model: RiskModel) -> None:
    beta = _null_free(world, np.random.default_rng(6).normal(scale=0.5, size=20))
    etf = pd.DataFrame(
        {"AAA": _planted(world, beta, 0.002, 3), "BBB": _planted(world, beta * 0.5, 0.004, 4)}
    )
    out = etf_exposures(etf, world.factor_returns, CFG)
    ext = extend_model(model, out)
    assert ext.symbols == (*model.symbols, "AAA", "BBB") and ext.etfs == ("AAA", "BBB")
    assert ext.factors == model.factors and ext.as_of == model.as_of and ext.n_days == model.n_days
    pd.testing.assert_frame_equal(ext.cov(list(model.symbols)), model.cov())
    x = out.betas.loc["AAA"].to_numpy()
    want = x @ model.f @ x + out.specific_var["AAA"]
    assert ext.cov(["AAA"]).iloc[0, 0] == pytest.approx(want, rel=1e-12)
    cross = ext.cov([model.symbols[0], "AAA"]).iloc[0, 1]
    assert cross == pytest.approx(model.x[0] @ model.f @ x, rel=1e-12)
    assert np.linalg.eigvalsh(ext.cov().to_numpy()).min() > 0


def test_extend_model_rejects_name_clashes(world: FactorWorld, model: RiskModel) -> None:
    clash = EtfExposures(
        betas=pd.DataFrame(0.0, index=[model.symbols[0]], columns=list(model.factors)),
        specific_var=pd.Series(1e-4, index=[model.symbols[0]]),
        r2=pd.Series(0.5, index=[model.symbols[0]]),
        n_obs=pd.Series(252, index=[model.symbols[0]]),
        dropped={},
    )
    with pytest.raises(ValueError, match="already in the model"):
        extend_model(model, clash)
    bad = EtfExposures(
        betas=pd.DataFrame(0.0, index=["X"], columns=list(model.factors)[:-1]),
        specific_var=pd.Series(1e-4, index=["X"]),
        r2=pd.Series(0.5, index=["X"]),
        n_obs=pd.Series(252, index=["X"]),
        dropped={},
    )
    with pytest.raises(ValueError, match="factors"):
        extend_model(model, bad)


def test_world_constants_are_consistent() -> None:
    assert len(STYLES) == 8


def test_extend_model_reports_dropped_etfs(world: FactorWorld, model: RiskModel) -> None:
    beta = _null_free(world, np.random.default_rng(12).normal(scale=0.5, size=20))
    good = _planted(world, beta, 0.002, 13)
    young = good.copy()
    young.iloc[:-30] = np.nan
    out = etf_exposures(pd.DataFrame({"OLD": good, "YOUNG": young}), world.factor_returns, CFG)
    ext = extend_model(model, out)
    assert ext.dropped["YOUNG"] == "history" and "YOUNG" not in ext.symbols
    assert {k: v for k, v in ext.dropped.items() if k != "YOUNG"} == dict(model.dropped)
