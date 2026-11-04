"""Stage 19 Task 6 of the brief: the factor risk model on the real data lake (read only).

Needs a populated lake: set `ATLAS__DATA__LAKE_PATH` (the test is skipped when the lake or its
price/fundamental tables are missing, as in CI). By default it fits 2023 with quarterly
fundamental anchors (`anchor_months=3`: one `factor_panel` costs ~2 minutes on 500 names) and
checks the year-end model, its persistence and ETF exposures. With `ATLAS_RISK_MODEL_FULL=1` it
covers 2018-2024 and checks the bias statistics of out-of-sample portfolios at half-yearly
model dates (the evidence run with more portfolios is
`Planning/stages/stage-19-factor-risk-model/artifacts/real_lake_check.py`).
"""

import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, load_config
from atlas.core.logging import get_logger
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.risk_model import (
    FactorHistory,
    RiskModel,
    RiskModelStore,
    bias_statistic,
    build_model_from_view,
    etf_exposures_from_view,
    extend_model,
    fit_factor_history,
    load_cross_sections,
    portfolio_risk,
)
from atlas.quant.risk_model.factor_model import FactorSet

pytestmark = pytest.mark.integration

_log = get_logger(__name__)
_FULL = os.environ.get("ATLAS_RISK_MODEL_FULL") == "1"
START, END = (
    (date(2018, 1, 2), date(2024, 12, 31)) if _FULL else (date(2023, 1, 3), date(2023, 12, 29))
)


def _config() -> AtlasConfig:
    cfg = load_config()
    rm = cfg.quant.risk_model
    fm = rm.factor_model.model_copy(update={"anchor_months": 3})
    quant = cfg.quant.model_copy(update={"risk_model": rm.model_copy(update={"factor_model": fm})})
    return cfg.model_copy(update={"quant": quant})


@pytest.fixture(scope="module")
def lake() -> Lake:
    raw = os.environ.get("ATLAS__DATA__LAKE_PATH")
    if not raw or not Path(raw).expanduser().exists():
        pytest.skip("ATLAS__DATA__LAKE_PATH does not point at a populated lake")
    lake = Lake(Path(raw).expanduser())
    for table in ("prices_daily", "instruments", "fundamentals_facts", "index_membership"):
        if not lake.has_table(table):
            pytest.skip(f"the lake has no {table} table")
    return lake


@pytest.fixture(scope="module")
def built(lake: Lake) -> tuple[AtlasConfig, list, FactorHistory]:
    cfg = _config()
    fm = cfg.quant.risk_model.factor_model
    xs = load_cross_sections(lake, START, END, cfg)
    hist = fit_factor_history(xs, FactorSet.from_config(fm), fm, on_short="skip")
    return cfg, xs, hist


@pytest.fixture(scope="module")
def year_end(lake: Lake, built) -> tuple[DataView, RiskModel]:
    cfg, _, hist = built
    cal = TradingCalendar(config=cfg.calendar)
    last = hist.factor_returns.index[-1].date()
    view = DataView(lake, cal.next_session(last), cfg)
    return view, build_model_from_view(view, hist)


def test_daily_regressions_are_sane(built) -> None:
    _, xs, hist = built
    assert len(hist.factor_returns) >= 0.95 * len(xs)
    assert hist.n_names.min() >= 300
    assert 0.1 < hist.r2.mean() < 0.9
    market = hist.factor_returns["market"]
    assert 0.05 < market.std() * np.sqrt(252) < 0.6  # annualised market vol
    assert not hist.factor_returns.isna().any().any()  # every sector present every day


def test_model_at_year_end_is_valid_and_adds_up(built, year_end) -> None:
    _, _, hist = built
    _, model = year_end
    assert model.as_of == hist.factor_returns.index[-1].date() and len(model.symbols) >= 300
    assert np.linalg.eigvalsh(model.cov().to_numpy()).min() > 0
    w = pd.Series(1.0 / len(model.symbols), index=list(model.symbols))
    r = portfolio_risk(model, w)
    assert r.factor_var + r.specific_var == pytest.approx(r.total_var, rel=1e-10)
    assert float(r.by_factor.sum()) == pytest.approx(r.factor_var, rel=1e-10)
    assert float(r.contribution.sum()) == pytest.approx(r.total_vol, rel=1e-10)
    assert 0.05 < r.annual_vol() < 0.5  # an equal-weighted S&P 500 portfolio
    assert r.factor_var > r.specific_var  # a diversified portfolio is mostly factor risk
    _log.info(
        "risk_model.real_lake",
        names=len(model.symbols),
        dropped=len(model.dropped),
        equal_weight_vol=round(r.annual_vol(), 4),
    )


def test_persisted_model_round_trips(built, year_end, tmp_path: Path) -> None:
    cfg, _, hist = built
    _, model = year_end
    cal = TradingCalendar(config=cfg.calendar)
    store = RiskModelStore(Lake(tmp_path / "lake"), cfg)
    store.write_factor_returns(hist)
    store.write_snapshot(model)
    got = store.read_snapshot(cal.next_session(model.as_of))
    assert got is not None
    np.testing.assert_array_equal(got.cov().to_numpy(), model.cov().to_numpy())
    assert store.read_snapshot(model.as_of) is None  # not known on the morning of its data date


def test_etf_exposures_on_real_data(built, year_end) -> None:
    _, _, hist = built
    view, model = year_end
    etfs = etf_exposures_from_view(view, hist, ["SPY", "XLE", "XLK"])
    assert set(etfs.betas.index) == {"SPY", "XLE", "XLK"}
    assert etfs.betas.loc["SPY", "market"] == pytest.approx(1.0, abs=0.15)
    assert etfs.r2["SPY"] > 0.95
    assert etfs.betas.loc["XLE", "Energy"] > 0.5
    assert etfs.betas.loc["XLK", "Information Technology"] > 0.5
    ext = extend_model(model, etfs)
    spy = ext.vol(["SPY"]).iloc[0] * np.sqrt(252)
    assert 0.05 < spy < 0.5


@pytest.mark.skipif(not _FULL, reason="set ATLAS_RISK_MODEL_FULL=1 for the 2018-2024 calibration")
def test_bias_statistics_2018_2024(lake: Lake, built) -> None:
    cfg, xs, hist = built
    cal = TradingCalendar(config=cfg.calendar)
    rets = pd.DataFrame({c.day: c.returns for c in xs}).T
    rets.index = pd.DatetimeIndex(rets.index)
    realised: dict[str, list[pd.Series]] = {"cap_weighted": [], "equal_weighted": []}
    predicted: dict[str, list[pd.Series]] = {"cap_weighted": [], "equal_weighted": []}
    for stamp in hist.factor_returns.index[252::126][:-1]:
        d = stamp.date()
        model = build_model_from_view(DataView(lake, cal.next_session(d), cfg), hist.up_to(d))
        later = rets.loc[(rets.index > stamp) & (rets.index <= pd.Timestamp(cal.offset(d, 21)))]
        block = later.reindex(columns=list(model.symbols)).fillna(0.0)
        caps = np.exp(model.exposures["size"])
        for name, w in (
            ("cap_weighted", caps / caps.sum()),
            ("equal_weighted", pd.Series(1 / len(caps), index=caps.index)),
        ):
            r = block @ w
            realised[name].append(r)
            predicted[name].append(pd.Series(portfolio_risk(model, w).total_vol, index=r.index))
    for name in realised:
        stat = bias_statistic(pd.concat(realised[name]), pd.concat(predicted[name]))
        _log.info("risk_model.bias", portfolio=name, bias=round(stat, 3))
        assert 0.6 < stat < 1.6, (name, stat)
